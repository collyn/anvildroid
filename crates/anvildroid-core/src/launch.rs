//! Bounded GUI startup and launch. Callers serialize runtime mutations.
use std::io::{Read, Seek, SeekFrom};
use std::process::{Command, Output, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use crate::{backend, error::AnvilError, runtime};

pub const BOOT_TIMEOUT: Duration = Duration::from_secs(90);
const PROBE_TIMEOUT: Duration = Duration::from_secs(3);

/// Drain both pipes while waiting, so a verbose backend cannot deadlock us.
pub(crate) fn output_timeout(
    command: &mut Command,
    timeout: Duration,
) -> Result<Output, AnvilError> {
    let description = format!("{command:?}");
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| AnvilError::BackendNotFound(format!("Cannot execute {description}: {e}")))?;
    let mut stdout = child.stdout.take().unwrap();
    let mut stderr = child.stderr.take().unwrap();
    let (out_tx, out) = std::sync::mpsc::channel();
    let (err_tx, err) = std::sync::mpsc::channel();
    thread::spawn(move || {
        let mut data = Vec::new();
        let _ = out_tx.send(stdout.read_to_end(&mut data).map(|_| data));
    });
    thread::spawn(move || {
        let mut data = Vec::new();
        let _ = err_tx.send(stderr.read_to_end(&mut data).map(|_| data));
    });
    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
            result => {
                let _ = child.kill();
                let _ = child.wait();
                break Err(match result {
                    Err(e) => AnvilError::Io(format!("Cannot wait for {description}: {e}")),
                    _ => AnvilError::Timeout(format!("Timed out running {description}")),
                });
            }
        }
    };
    // On failure do not wait on pipes possibly inherited by descendants.
    let status = status?;
    let stdout = out
        .recv_timeout(deadline.saturating_duration_since(Instant::now()))
        .map_err(|_| {
            AnvilError::Timeout(format!("Timed out reading stdout from {description}"))
        })??;
    let stderr = err
        .recv_timeout(deadline.saturating_duration_since(Instant::now()))
        .map_err(|_| {
            AnvilError::Timeout(format!("Timed out reading stderr from {description}"))
        })??;
    Ok(Output {
        status,
        stdout,
        stderr,
    })
}

fn waydroid(args: &[&str], timeout: Duration) -> Result<Output, AnvilError> {
    output_timeout(
        Command::new("waydroid").args(args).env("LC_ALL", "C"),
        timeout,
    )
}

pub(crate) fn native_wayland_available() -> bool {
    use std::os::unix::fs::FileTypeExt;
    let display = std::env::var_os("WAYLAND_DISPLAY")
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "wayland-0".into());
    let path = std::path::PathBuf::from(display);
    let socket = if path.is_absolute() {
        Some(path)
    } else {
        std::env::var_os("XDG_RUNTIME_DIR").map(|dir| std::path::PathBuf::from(dir).join(path))
    };
    socket.is_some_and(|path| {
        path.metadata()
            .is_ok_and(|info| info.file_type().is_socket())
    })
}

pub(crate) fn needs_x11_adapter() -> bool {
    !native_wayland_available()
        && std::env::var("DISPLAY")
            .ok()
            .is_some_and(|display| crate::controller::x11_display(&display).is_some())
}

/// Default Waydroid uses the shared native surface adapter on an X11 desktop.
/// Keep this command shared by GUI startup and explicit session recovery.
pub fn session_start_command() -> Command {
    if needs_x11_adapter() {
        let mut command = Command::new("python3");
        command.args([
            "-u",
            "-c",
            include_str!("../../../services/waydroid-session.py"),
        ]);
        command
    } else {
        let mut command = Command::new("waydroid");
        command.args(["session", "start"]);
        command
    }
}

pub(crate) fn check_output(output: Output) -> Result<String, AnvilError> {
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let failure = runtime::launch_error(&text);
    if !output.status.success() || failure.is_some() {
        return Err(AnvilError::BackendFailed(format!(
            "Waydroid failed ({}): {}{}",
            output.status,
            text.trim(),
            failure.map(|reason| format!("\n{reason}")).unwrap_or_default()
        )));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

/// This probe uses IPlatform, so it also checks that the Android bridge responds.
pub fn android_ready(timeout: Duration) -> Result<bool, AnvilError> {
    check_output(waydroid(&["prop", "get", "sys.boot_completed"], timeout)?)
        .map(|value| value == "1")
}

fn session_attempt_log(path: &std::path::Path, offset: u64) -> Option<String> {
    let mut file = std::fs::File::open(path).ok()?;
    let length = file.metadata().ok()?.len();
    let offset = if length < offset { 0 } else { offset };
    file.seek(SeekFrom::Start(
        offset.max(length.saturating_sub(64 * 1024)),
    ))
    .ok()?;
    let mut bytes = Vec::new();
    file.take(64 * 1024).read_to_end(&mut bytes).ok()?;
    let text = String::from_utf8_lossy(&bytes);
    let mut lines: Vec<&str> = text.lines().collect();
    if lines.len() > 12 {
        lines.drain(..lines.len() - 12);
    }
    let detail = lines.join("\n");
    (!detail.trim().is_empty()).then_some(detail)
}

/// Start only a stopped runtime. Never stop/recover an existing session implicitly.
pub fn ensure_ready(timeout: Duration) -> Result<(), AnvilError> {
    let deadline = Instant::now() + timeout;
    let mut session = None;
    let mut session_exited_at = None;
    let mut session_exit_error = None;
    let mut session_log = None;
    let mut status = check_output(waydroid(&["status"], PROBE_TIMEOUT)?)?;
    let stale_session = |value: &str| {
        value.lines().any(|line| {
            line.split_once(':')
                .is_some_and(|(k, v)| k.trim() == "Session" && v.trim() == "RUNNING")
        }) && value.lines().any(|line| {
            line.split_once(':')
                .is_some_and(|(k, v)| k.trim() == "Container" && v.trim() == "STOPPED")
        })
    };
    if stale_session(&status) {
        // A crashed container can leave the per-user Waydroid session alive.
        // Clear it before starting a new session instead of requiring manual recovery.
        let _ = waydroid(&["session", "stop"], PROBE_TIMEOUT);
        let stale_deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < stale_deadline {
            thread::sleep(Duration::from_millis(250));
            status = check_output(waydroid(&["status"], PROBE_TIMEOUT)?)?;
            if !stale_session(&status) {
                break;
            }
        }
        if stale_session(&status) {
            return Err(AnvilError::BackendFailed(
                "Stale Waydroid session could not be stopped; retry after checking waydroid.log"
                    .into(),
            ));
        }
    }
    if status.lines().any(|line| {
        line.split_once(':')
            .is_some_and(|(key, value)| key.trim() == "Container" && value.trim() == "FROZEN")
    }) {
        // An explicit Start/Open owns this wakeup. Passive health/library
        // polling keeps the container asleep.
        check_output(output_timeout(
            Command::new("gdbus").args([
                "call",
                "--system",
                "--dest",
                "id.waydro.Container",
                "--object-path",
                "/ContainerManager",
                "--method",
                "id.waydro.ContainerManager.Unfreeze",
            ]),
            PROBE_TIMEOUT,
        )?)?;
    }
    if !runtime::session_owner()? {
        if !status.lines().any(|line| {
            line.split_once(':')
                .is_some_and(|(k, v)| k.trim() == "Session" && v.trim() == "STOPPED")
        }) {
            return Err(AnvilError::SessionMissing(format!(
                "Cannot start a second session. {}",
                runtime::RECOVERY
            )));
        }
        // Another desktop launcher may have acquired the name during the probe.
        if !runtime::session_owner()? {
            let state_dir = dirs::state_dir()
                .ok_or_else(|| AnvilError::Io("Cannot locate state directory".into()))?
                .join("anvildroid");
            std::fs::create_dir_all(&state_dir)?;
            let log_path = state_dir.join("session.log");
            let log = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&log_path)?;
            session_log = Some((log_path, log.metadata()?.len()));
            let mut child = session_start_command()
                .stdin(Stdio::null())
                .stdout(log.try_clone()?)
                .stderr(log)
                .spawn()
                .map_err(|e| AnvilError::BackendNotFound(format!("Cannot start Waydroid: {e}")))?;
            let (sender, receiver) = std::sync::mpsc::channel();
            // Reap the long-lived session without tying its lifetime to the GUI task.
            thread::spawn(move || {
                let _ = sender.send(child.wait());
            });
            session = Some(receiver);
        }
    }

    let mut last_error = "Android has not completed boot".to_string();
    while Instant::now() < deadline {
        if let Some(receiver) = &session {
            if let Ok(result) = receiver.try_recv() {
                // Waydroid can hand startup to its container service and exit
                // cleanly before the session name appears on D-Bus. Allow a
                // short handoff window, then report the captured log instead
                // of hiding the real startup failure behind ExitStatus(0).
                session_exited_at = Some(Instant::now());
                session_exit_error = Some(result);
                session = None;
            }
        }
        if runtime::session_owner()? {
            match android_ready(
                PROBE_TIMEOUT.min(deadline.saturating_duration_since(Instant::now())),
            ) {
                Ok(true) => return Ok(()),
                Ok(false) => last_error = "Android has not completed boot".into(),
                Err(AnvilError::BackendNotFound(e)) => return Err(AnvilError::BackendNotFound(e)),
                Err(e) => last_error = e.to_string(),
            }
        } else if let Some(exited_at) = session_exited_at {
            if exited_at.elapsed() >= Duration::from_secs(5) {
                let detail = session_log
                    .as_ref()
                    .and_then(|(path, offset)| session_attempt_log(path, *offset))
                    .unwrap_or_else(|| format!("session process exited: {:?}", session_exit_error));
                return Err(AnvilError::BackendFailed(format!(
                    "Waydroid session exited before Android was ready. {detail}"
                )));
            }
        }
        thread::sleep(
            Duration::from_millis(250).min(deadline.saturating_duration_since(Instant::now())),
        );
    }
    Err(AnvilError::Timeout(format!(
        "Timed out waiting for Android: {last_error}"
    )))
}

pub fn launch_app(package: &str) -> Result<(), AnvilError> {
    if !backend::valid_package(package) {
        return Err(AnvilError::InvalidArgument(
            "Invalid Android package name".into(),
        ));
    }
    ensure_ready(BOOT_TIMEOUT)?;
    runtime::launch_preflight()?;
    check_output(waydroid(
        &["app", "launch", package],
        Duration::from_secs(15),
    )?)?;
    Ok(())
}

/// Request Default's full Android window after the session is responsive.
/// Command completion confirms the request only, not desktop window mapping.
pub fn open_full_ui(activation_token: Option<&str>) -> Result<(), AnvilError> {
    ensure_ready(BOOT_TIMEOUT)?;
    runtime::launch_preflight()?;
    let mut command = Command::new("waydroid");
    command.arg("show-full-ui").env("LC_ALL", "C")
        .env_remove("XDG_ACTIVATION_TOKEN");
    if let Some(token) = activation_token.filter(|token| {
        !token.is_empty() && token.len() <= 512
            && token.bytes().all(|byte| byte > 32 && byte < 127)
    }) {
        command.env("XDG_ACTIVATION_TOKEN", token);
    }
    check_output(output_timeout(&mut command, Duration::from_secs(15))?)?;
    Ok(())
}

/// Open Android's real App Info screen for this package.
pub fn open_app_info(package: &str) -> Result<(), AnvilError> {
    if !backend::valid_package(package) {
        return Err(AnvilError::InvalidArgument(
            "Invalid Android package name".into(),
        ));
    }
    ensure_ready(BOOT_TIMEOUT)?;
    runtime::launch_preflight()?;
    check_output(waydroid(
        &[
            "app",
            "intent",
            "android.settings.APPLICATION_DETAILS_SETTINGS",
            &format!("package:{package}"),
        ],
        Duration::from_secs(15),
    )?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::session_attempt_log;
    use std::io::Write;

    #[test]
    fn startup_error_reads_only_the_current_attempt() {
        let root = std::env::temp_dir().join(format!(
            "anvil-session-log-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&root).unwrap();
        let path = root.join("session.log");
        let old = "x11_toplevel_mapped=old-session\n".repeat(5000);
        std::fs::write(&path, &old).unwrap();
        let offset = old.len() as u64;
        assert_eq!(session_attempt_log(&path, offset), None);
        let mut file = std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap();
        for index in 1..=20 {
            writeln!(file, "new attempt line {index}").unwrap();
        }
        let detail = session_attempt_log(&path, offset).unwrap();
        assert_eq!(detail.lines().count(), 12);
        assert!(detail.starts_with("new attempt line 9\n"));
        assert!(detail.ends_with("new attempt line 20"));
        assert!(!detail.contains("old-session"));
        std::fs::write(&path, "attempt after log rotation\n").unwrap();
        assert_eq!(
            session_attempt_log(&path, offset).unwrap(),
            "attempt after log rotation"
        );
        std::fs::remove_dir_all(root).unwrap();
    }
}
