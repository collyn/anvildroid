//! Bounded GUI startup and launch. Callers serialize runtime mutations.
use std::io::Read;
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

pub(crate) fn check_output(output: Output) -> Result<String, AnvilError> {
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    if !output.status.success() || runtime::launch_error(&text).is_some() {
        return Err(AnvilError::BackendFailed(format!(
            "Waydroid failed ({}): {}",
            output.status,
            text.trim()
        )));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

/// This probe uses IPlatform, so it also checks that the Android bridge responds.
pub fn android_ready(timeout: Duration) -> Result<bool, AnvilError> {
    check_output(waydroid(&["prop", "get", "sys.boot_completed"], timeout)?)
        .map(|value| value == "1")
}

/// Start only a stopped runtime. Never stop/recover an existing session implicitly.
pub fn ensure_ready(timeout: Duration) -> Result<(), AnvilError> {
    let deadline = Instant::now() + timeout;
    let mut session = None;
    let mut session_exited_at = None;
    let mut session_exit_error = None;
    let mut status = check_output(waydroid(&["status"], PROBE_TIMEOUT)?)?;
    let stale_session = |value: &str| {
        value.lines().any(|line| line.split_once(':').is_some_and(|(k, v)| k.trim() == "Session" && v.trim() == "RUNNING"))
            && value.lines().any(|line| line.split_once(':').is_some_and(|(k, v)| k.trim() == "Container" && v.trim() == "STOPPED"))
    };
    if stale_session(&status) {
        // A crashed container can leave the per-user Waydroid session alive.
        // Clear it before starting a new session instead of requiring manual recovery.
        let _ = waydroid(&["session", "stop"], PROBE_TIMEOUT);
        let stale_deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < stale_deadline {
            thread::sleep(Duration::from_millis(250));
            status = check_output(waydroid(&["status"], PROBE_TIMEOUT)?)?;
            if !stale_session(&status) { break; }
        }
        if stale_session(&status) {
            return Err(AnvilError::BackendFailed(
                "Stale Waydroid session could not be stopped; retry after checking waydroid.log".into(),
            ));
        }
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
            let log = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(state_dir.join("session.log"))?;
            let mut child = Command::new("waydroid")
                .args(["session", "start"])
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
                let detail = std::fs::read_to_string(
                    dirs::state_dir()
                        .map(|path| path.join("anvildroid/session.log"))
                        .unwrap_or_else(|| std::path::PathBuf::from("anvildroid/session.log")),
                )
                .ok()
                .map(|text| {
                    let mut lines: Vec<&str> = text.lines().collect();
                    if lines.len() > 12 { lines.drain(..lines.len() - 12); }
                    lines.join("\n")
                })
                .filter(|text| !text.trim().is_empty())
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

/// Ask Android's package installer to confirm removal. Sending this intent is
/// not evidence of successful uninstall; callers must verify the app disappears.
pub(crate) fn request_android_uninstall(package: &str) -> Result<(), AnvilError> {
    if !backend::valid_package(package) || backend::uninstall_block_reason(package).is_some() {
        return Err(AnvilError::InvalidArgument(
            "Package cannot be removed from the app library".into(),
        ));
    }
    runtime::launch_preflight()?;
    check_output(waydroid(
        &[
            "app",
            "intent",
            "android.intent.action.DELETE",
            &format!("package:{package}"),
        ],
        Duration::from_secs(15),
    )?)?;
    Ok(())
}
