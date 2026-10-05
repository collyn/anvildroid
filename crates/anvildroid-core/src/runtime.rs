use std::process::Command;
use std::{env, os::unix::fs::FileTypeExt, path::PathBuf, thread, time::Duration};

use crate::error::AnvilError;
use crate::models::{GpuMode, HealthReport, RuntimeState, RuntimeStatus};

pub const RECOVERY: &str = "Open a terminal in the same Wayland desktop, without sudo. Run anvildroid stop, then anvildroid start; wait for Android ready and keep that terminal open. Stopping interrupts Android windows but retains app data.";

/// Check whether id.waydro.Session owns a name on the desktop session D-Bus.
pub fn session_owner() -> Result<bool, AnvilError> {
    let output = crate::launch::output_timeout(
        Command::new("dbus-send").args([
            "--session",
            "--print-reply",
            "--reply-timeout=3000",
            "--dest=org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus.NameHasOwner",
            "string:id.waydro.Session",
        ]),
        Duration::from_secs(3),
    )
    .map_err(|e| {
        AnvilError::SessionProbe(format!(
            "Cannot check desktop session D-Bus (dbus-send): {e}"
        ))
    })?;
    if !output.status.success() {
        return Err(AnvilError::SessionProbe("Cannot access desktop session D-Bus; run from your Wayland desktop terminal, without sudo.".into()));
    }
    let reply = String::from_utf8_lossy(&output.stdout);
    match reply
        .lines()
        .map(str::trim)
        .find(|s| s.starts_with("boolean "))
    {
        Some("boolean true") => Ok(true),
        Some("boolean false") => Ok(false),
        _ => Err(AnvilError::SessionProbe(
            "Unrecognized D-Bus session probe response".into(),
        )),
    }
}

/// Ensure the session service is present before launching an app.
pub fn launch_preflight() -> Result<(), AnvilError> {
    if !session_owner()? {
        return Err(AnvilError::SessionMissing(format!(
            "No Waydroid session service on this desktop D-Bus. {RECOVERY}"
        )));
    }
    Ok(())
}

/// Detect backend errors that Waydroid masks with exit code 0.
pub fn launch_error(output: &str) -> Option<String> {
    if output.contains("Already tracking a session") {
        Some(format!(
            "Container tracks a session that this launcher could not use. {RECOVERY}"
        ))
    } else if [
        "Waydroid is not initialized",
        "WayDroid is not initialized",
        "Failed to access IPlatform service",
        "Failed to launch app",
        "Failed to uninstall package:",
        "WayDroid session is stopped",
        "Waydroid session is stopped",
        "Failed with code:",
        "Sending reply failed",
        "Service Manager never appeared",
        "Traceback (most recent call last)",
    ]
    .iter()
    .any(|message| output.contains(message))
    {
        Some(
            "Waydroid reported a launch failure despite its exit status; see backend output above."
                .into(),
        )
    } else {
        None
    }
}

/// Parse a field value from `waydroid status` output.
fn parse_status_field<'a>(status_output: &'a str, name: &str) -> Option<&'a str> {
    status_output.lines().find_map(|line| {
        line.split_once(':')
            .filter(|(key, _)| key.trim() == name)
            .map(|(_, value)| value.trim())
    })
}

/// Query comprehensive runtime status by probing backend + D-Bus.
pub fn runtime_status() -> RuntimeStatus {
    // Check if Waydroid is installed.
    let version_ok = crate::launch::output_timeout(
        Command::new("waydroid").arg("--version"),
        Duration::from_secs(3),
    )
    .is_ok_and(|o| o.status.success());

    if !version_ok {
        return RuntimeStatus {
            state: RuntimeState::NotInstalled,
            session_service: false,
            container_state: None,
            session_state: None,
            gpu_mode: None,
        };
    }

    // Get D-Bus session owner status.
    let session_service = session_owner().unwrap_or(false);

    // Get waydroid status output.
    let (container_state, session_state) = crate::launch::output_timeout(
        Command::new("waydroid").arg("status").env("LC_ALL", "C"),
        Duration::from_secs(3),
    )
    .ok()
    .filter(|o| o.status.success())
    .map(|o| {
        let text = String::from_utf8_lossy(&o.stdout).to_string();
        (
            parse_status_field(&text, "Container").map(String::from),
            parse_status_field(&text, "Session").map(String::from),
        )
    })
    .unwrap_or((None, None));

    // Get GPU mode.
    let gpu_mode = crate::backend::current_vulkan_mode().ok().map(|vulkan| {
        if vulkan {
            GpuMode::Vulkan
        } else {
            GpuMode::Gles
        }
    });

    // Determine composite state.
    let state = match (
        session_state.as_deref(),
        container_state.as_deref(),
        session_service,
    ) {
        (Some("RUNNING"), Some("RUNNING"), true) => {
            if crate::launch::android_ready(Duration::from_secs(2)).unwrap_or(false) {
                RuntimeState::Running
            } else {
                RuntimeState::Starting
            }
        }
        (Some("RUNNING"), Some("FROZEN"), true) => RuntimeState::Frozen,
        (Some("RUNNING"), _, false) => RuntimeState::SessionLost,
        (_, Some("RUNNING" | "FROZEN"), false) => RuntimeState::SessionLost,
        (Some("STOPPED"), _, true) | (None, _, true) => RuntimeState::Starting,
        (Some("STOPPED"), _, _) | (None, Some("STOPPED"), _) | (None, None, _) => {
            RuntimeState::Stopped
        }
        (Some(s), Some(c), _) => RuntimeState::Error(format!("session={s}, container={c}")),
        (s, c, _) => RuntimeState::Error(format!("session={s:?}, container={c:?}")),
    };

    RuntimeStatus {
        state,
        session_service,
        container_state,
        session_state,
        gpu_mode,
    }
}

/// Run `health` check: print status + D-Bus state. Returns exit code.
pub fn health() -> Result<u8, AnvilError> {
    let output = Command::new("waydroid")
        .arg("status")
        .env("LC_ALL", "C")
        .output()
        .map_err(|e| {
            AnvilError::BackendNotFound(format!(
                "Cannot execute Waydroid: {e}. See README.md for setup."
            ))
        })?;
    let status = String::from_utf8_lossy(&output.stdout);
    print!("{status}");
    if !output.status.success() {
        return Err(AnvilError::BackendFailed(
            "Waydroid status command failed".into(),
        ));
    }
    let owner = session_owner()?;
    println!(
        "Desktop session D-Bus service: {}",
        if owner { "PRESENT" } else { "MISSING" }
    );
    if !owner {
        return Err(AnvilError::SessionMissing(format!("Desktop session is unavailable; container status alone does not establish launch readiness. {RECOVERY}")));
    }
    match (
        parse_status_field(&status, "Session"),
        parse_status_field(&status, "Container"),
    ) {
        (Some("RUNNING"), Some("RUNNING")) => {
            println!("Session control services available. Android boot, network, IME and rendering remain UNVERIFIED.");
            Ok(0)
        }
        (Some("RUNNING"), Some("FROZEN")) => {
            println!("Container suspended. Waydroid app launch normally resumes it; this is not a crash diagnosis.");
            Ok(0)
        }
        _ => Err(AnvilError::BackendFailed("Runtime is stopped or its state is unknown; inspect waydroid status and start the session.".into())),
    }
}

/// Run `health` and return a structured report instead of printing.
pub fn health_report() -> HealthReport {
    let status = runtime_status();
    let (healthy, message, recovery_hint) = match &status.state {
        RuntimeState::Running => (
            true,
            "Android boot completed and the platform service responds. Network, IME and rendering remain unverified.".into(),
            None,
        ),
        RuntimeState::Frozen => (
            true,
            "Container suspended. Waydroid app launch normally resumes it.".into(),
            None,
        ),
        RuntimeState::NotInstalled => (
            false,
            "Waydroid backend is not installed.".into(),
            Some("Install Waydroid as described in README.md.".into()),
        ),
        RuntimeState::SessionLost => (
            false,
            "Container running but desktop session service is missing.".into(),
            Some(RECOVERY.into()),
        ),
        RuntimeState::Stopped => (
            false,
            "Runtime is stopped.".into(),
            Some("Run anvildroid start to begin a session.".into()),
        ),
        RuntimeState::Starting => (
            false,
            "Runtime is starting; wait for Android ready.".into(),
            None,
        ),
        RuntimeState::Error(detail) => (
            false,
            format!("Runtime is in an unexpected state: {detail}"),
            Some(RECOVERY.into()),
        ),
    };
    HealthReport {
        status,
        healthy,
        message,
        recovery_hint,
    }
}

/// Explicit recovery: restart the session only when the D-Bus service is
/// missing. Never stops a session that owns the bus name.
pub fn recover() -> Result<u8, AnvilError> {
    if session_owner()? {
        println!("Session service is present; leaving it running.");
        return health();
    }
    // Validate the target desktop before stopping an orphaned container session.
    let display = env::var_os("WAYLAND_DISPLAY")
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            AnvilError::SessionProbe(
                "WAYLAND_DISPLAY is missing; run recover from your desktop terminal".into(),
            )
        })?;
    let display = PathBuf::from(display);
    let socket = if display.is_absolute() {
        display
    } else {
        PathBuf::from(env::var_os("XDG_RUNTIME_DIR").ok_or_else(|| {
            AnvilError::SessionProbe("XDG_RUNTIME_DIR is missing; run recover without sudo".into())
        })?)
        .join(display)
    };
    if !socket.metadata().is_ok_and(|m| m.file_type().is_socket()) {
        return Err(AnvilError::SessionProbe(
            "Wayland display socket is unavailable; no session was stopped".into(),
        ));
    }
    // Recheck immediately before mutation in case another launcher just started.
    if session_owner()? {
        return health();
    }
    println!("Stopping the stale session; Android windows will close, app data is retained.");
    let stop = Command::new("waydroid")
        .args(["session", "stop"])
        .status()
        .map_err(|e| AnvilError::BackendFailed(format!("Cannot execute Waydroid: {e}")))?;
    if !stop.success() {
        return Err(AnvilError::BackendFailed(
            "Session stop failed; refusing to start a second session".into(),
        ));
    }
    for _ in 0..20 {
        if session_owner()? {
            return Err(AnvilError::BackendFailed(
                "Another session appeared during recovery; no new session was started".into(),
            ));
        }
        let output = Command::new("waydroid")
            .arg("status")
            .env("LC_ALL", "C")
            .output()
            .map_err(|e| {
                AnvilError::BackendFailed(format!("Cannot verify stopped session: {e}"))
            })?;
        if !output.status.success() {
            return Err(AnvilError::BackendFailed(
                "Cannot verify stopped session; refusing to start".into(),
            ));
        }
        let stopped = String::from_utf8_lossy(&output.stdout).lines().any(|line| {
            line.split_once(':')
                .is_some_and(|(key, value)| key.trim() == "Session" && value.trim() == "STOPPED")
        });
        if stopped {
            println!("Starting foreground session. Keep this terminal open; wait for Android ready, then run anvildroid health in another terminal.");
            let status = Command::new("waydroid")
                .args(["session", "start"])
                .status()
                .map_err(|e| AnvilError::BackendFailed(format!("Cannot start Waydroid: {e}")))?;
            return Ok(status
                .code()
                .and_then(|c| u8::try_from(c).ok())
                .unwrap_or(1));
        }
        thread::sleep(Duration::from_millis(250));
    }
    Err(AnvilError::BackendFailed(
        "Session did not stop within the polling window; no new session was started".into(),
    ))
}
