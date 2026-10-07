use std::sync::{Arc, Mutex, OnceLock};

use anvildroid_core::error::AnvilError;
use anvildroid_core::models::{AppInfo, HealthReport, OperationResult};
use anvildroid_core::{apps, backend, desktop, launch, library, runtime, scope};

use tauri::State;

#[derive(Clone)]
struct CustomImageJob {
    path: std::path::PathBuf,
    total: u64,
    status: String,
    phase: String,
    error: Option<String>,
    result: Option<serde_json::Value>,
}

static CUSTOM_IMAGE_JOB: OnceLock<Arc<Mutex<Option<CustomImageJob>>>> = OnceLock::new();

fn custom_image_job() -> &'static Arc<Mutex<Option<CustomImageJob>>> {
    CUSTOM_IMAGE_JOB.get_or_init(|| Arc::new(Mutex::new(None)))
}

#[tauri::command]
fn get_app_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Guard to prevent concurrent backend mutations (start/stop/recover/install).
struct OpGuard(Arc<Mutex<()>>);

fn op_result_ok(message: impl Into<String>) -> OperationResult {
    OperationResult {
        success: true,
        message: message.into(),
        error_code: None,
        recovery_hint: None,
        pending_confirmation: false,
    }
}

fn op_result_err(e: AnvilError) -> OperationResult {
    OperationResult {
        success: false,
        message: e.to_string(),
        error_code: Some(e.code().into()),
        recovery_hint: e.recovery_hint().map(String::from),
        pending_confirmation: false,
    }
}

fn busy_result() -> OperationResult {
    OperationResult {
        success: false,
        message: "Another operation is in progress.".into(),
        error_code: Some("BUSY".into()),
        recovery_hint: Some("Wait for the current operation to complete.".into()),
        pending_confirmation: false,
    }
}

/// Helper: run a closure on a blocking thread so it never blocks the main/UI thread.
async fn blocking<F, T>(f: F) -> T
where
    F: FnOnce() -> T + Send + 'static,
    T: Send + 'static,
{
    tauri::async_runtime::spawn_blocking(f)
        .await
        .expect("blocking task panicked")
}

// --- Query commands (async, off main thread) ---

#[tauri::command]
async fn get_health_report(runtime_id: String) -> Result<HealthReport, String> {
    blocking(move || {
        scope::ensure_legacy(&runtime_id).map_err(|e| e.to_string())?;
        Ok(runtime::health_report())
    })
    .await
}

#[tauri::command]
async fn get_apps(runtime_id: String) -> Result<Vec<AppInfo>, String> {
    blocking(move || {
        scope::ensure_legacy(&runtime_id).map_err(|e| e.to_string())?;
        backend::list_apps_if_running().map_err(|e| e.to_string())
    })
    .await
}

#[tauri::command]
async fn get_app_details(
    runtime_id: String,
    package: String,
    guard: State<'_, OpGuard>,
) -> Result<apps::AppDetails, String> {
    let guard = guard.0.clone();
    blocking(move || {
        scope::ensure_legacy(&runtime_id).map_err(|e| e.to_string())?;
        let _operation = guard
            .try_lock()
            .map_err(|_| "Another operation is in progress.".to_string())?;
        launch::ensure_ready(launch::BOOT_TIMEOUT).map_err(|e| e.to_string())?;
        apps::details(&package).map_err(|e| e.to_string())
    })
    .await
}
#[tauri::command]
async fn get_library(runtime_id: String) -> Result<library::Library, String> {
    blocking(move || {
        if runtime_id == scope::LEGACY_ID {
            scope::ensure_legacy(&runtime_id).map_err(|e| e.to_string())?;
            library::read().map_err(|e| e.to_string())
        } else {
            ensure_managed_library(&runtime_id)?;
            library::read_runtime(&runtime_id).map_err(|e| e.to_string())
        }
    })
    .await
}
#[tauri::command]
async fn set_favorite(
    runtime_id: String,
    package: String,
    enabled: bool,
) -> Result<library::Library, String> {
    blocking(move || {
        if runtime_id == scope::LEGACY_ID {
            scope::ensure_legacy(&runtime_id).map_err(|e| e.to_string())?;
            library::favorite(&package, enabled).map_err(|e| e.to_string())
        } else {
            ensure_managed_library(&runtime_id)?;
            library::favorite_runtime(&runtime_id, &package, enabled).map_err(|e| e.to_string())
        }
    })
    .await
}
fn ensure_managed_library(id: &str) -> Result<(), String> {
    if !library::valid_managed_runtime(id) {
        return Err("Invalid managed runtime ID".into());
    }
    anvildroid_core::controller::request(anvildroid_core::controller::Request::DisplayInfo {
        id: id.into(),
    })?;
    Ok(())
}
fn validate_library_launch(
    job: &serde_json::Value,
    id: &str,
    package: &str,
    job_id: &str,
) -> Result<(), String> {
    if job["id"].as_str() != Some(job_id)
        || job["runtime_id"].as_str() != Some(id)
        || job["package"].as_str() != Some(package)
        || job["action"] != "launch"
        || job["status"] != "Succeeded"
    {
        return Err("Launch job changed or did not succeed; recent history was not updated".into());
    }
    Ok(())
}
#[tauri::command]
async fn record_runtime_launch(
    runtime_id: String,
    package: String,
    job_id: String,
) -> Result<library::Library, String> {
    blocking(move || {
        if !library::valid_managed_runtime(&runtime_id) {
            return Err("Invalid managed runtime ID".into());
        }
        let job =
            anvildroid_core::controller::request(anvildroid_core::controller::Request::AppJob {
                id: runtime_id.clone(),
            })?;
        validate_library_launch(&job, &runtime_id, &package, &job_id)?;
        library::launched_runtime(&runtime_id, &package).map_err(|e| e.to_string())
    })
    .await
}
#[tauri::command]
async fn set_library_sort(runtime_id: String, sort: String) -> Result<library::Library, String> {
    blocking(move || {
        scope::ensure_legacy(&runtime_id).map_err(|e| e.to_string())?;
        library::sort(&sort).map_err(|e| e.to_string())
    })
    .await
}
#[tauri::command]
async fn force_stop_app(
    runtime_id: String,
    package: String,
    guard: State<'_, OpGuard>,
) -> Result<OperationResult, ()> {
    let guard = guard.0.clone();
    Ok(blocking(move || {
        if let Err(e) = scope::ensure_legacy(&runtime_id) {
            return op_result_err(e);
        }
        let Ok(_operation) = guard.try_lock() else {
            return busy_result();
        };
        match apps::force_stop(&package) {
            Ok(()) => op_result_ok(format!(
                "Force-stopped {package}; Android data is retained."
            )),
            Err(e) => op_result_err(e),
        }
    })
    .await)
}

#[tauri::command]
async fn get_runtime_catalog() -> Result<anvildroid_core::registry::Catalog, String> {
    blocking(|| anvildroid_core::registry::catalog().map_err(|e| e.to_string())).await
}

#[tauri::command]
async fn runtime_controller(
    mut request: anvildroid_core::controller::Request,
    guard: State<'_, OpGuard>,
) -> Result<serde_json::Value, String> {
    if let anvildroid_core::controller::Request::Start { display, .. } = &mut request {
        // Desktop session belongs to this GUI process, not a WebView-provided path.
        *display = anvildroid_core::controller::desktop_session_display();
    }
    let guard = guard.0.clone();
    blocking(move || {
        use anvildroid_core::controller::Request;
        let changes_existing = match &request {
            Request::ResourcesSet { id, .. } | Request::GpuSet { id, .. }
            | Request::ArmSet { id, .. } | Request::ExistingClaim { id }
            | Request::ExistingImport { id, .. }
            | Request::Rename { id, .. } => id == scope::LEGACY_ID,
            _ => false,
        };
        let _operation = if changes_existing {
            scope::ensure_legacy(scope::LEGACY_ID).map_err(|e| e.to_string())?;
            Some(guard.try_lock().map_err(|_| "Another operation is in progress.".to_string())?)
        } else { None };
        if let Request::AppAction { id, action, package } = &request {
            if action == "launch" && library::valid_managed_runtime(id) {
                let exe = std::env::current_exe().map_err(|e| e.to_string())?;
                desktop::register_managed_window(id, package, &exe).map_err(|e| e.to_string())?;
            }
        }
        anvildroid_core::controller::request(request)
    }).await
}

// --- Bundled controller launcher (pkexec; no systemd service) ---

#[derive(serde::Serialize)]
struct StartControllerResult {
    ok: bool,
    message: String,
}

const CONTROLLER_HELPER: &str = "scripts/start-runtime-controller.py";
const SETUP_HELPER: &str = "scripts/setup-waydroid.py";

/// Locate the bundle root: the first ancestor of the executable that contains
/// the controller helper (covers both the AppImage usr/bin/ layout and dev
/// builds), falling back to the APPDIR env var exported by the custom AppRun.
fn resolve_bundle_root(exe: &std::path::Path, appdir: Option<&str>) -> Option<std::path::PathBuf> {
    for ancestor in exe.ancestors() {
        if ancestor.join(CONTROLLER_HELPER).is_file() {
            return Some(ancestor.to_path_buf());
        }
    }
    if let Some(dir) = appdir {
        let path = std::path::Path::new(dir);
        if path.join(CONTROLLER_HELPER).is_file() {
            return Some(path.to_path_buf());
        }
    }
    None
}

fn resolve_setup_root(exe: &std::path::Path, appdir: Option<&str>) -> Option<std::path::PathBuf> {
    for ancestor in exe.ancestors() {
        if ancestor.join(SETUP_HELPER).is_file() {
            return Some(ancestor.to_path_buf());
        }
    }
    if let Some(dir) = appdir {
        let path = std::path::Path::new(dir);
        if path.join(SETUP_HELPER).is_file() {
            return Some(path.to_path_buf());
        }
    }
    let installed = std::path::Path::new("/usr/local/lib/anvildroid-controller");
    installed.join(SETUP_HELPER).is_file().then(|| installed.to_path_buf())
}

fn copy_stage_tree(bundle: &std::path::Path, stage: &std::path::Path, relative: &str) -> Result<(), String> {
    let source_dir = bundle.join(relative);
    for entry in std::fs::read_dir(&source_dir)
        .map_err(|e| format!("Bundled controller payload is missing: {relative} ({e})"))?
    {
        let entry = entry.map_err(|e| e.to_string())?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let target = stage.join(relative).join(&name);
        if entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
            std::fs::create_dir_all(&target).map_err(|e| format!("Cannot stage {relative}/{name}: {e}"))?;
            copy_stage_tree(bundle, stage, &format!("{relative}/{name}"))?;
        } else {
            // fs::copy does not create parent directories; a file directly
            // under the copied root would otherwise fail before any subdir
            // entry has created that parent.
            if let Some(parent) = target.parent() {
                std::fs::create_dir_all(parent).map_err(|e| format!("Cannot stage {relative}/{name}: {e}"))?;
            }
            std::fs::copy(entry.path(), &target)
                .map_err(|e| format!("Cannot stage {relative}/{name}: {e}"))?;
        }
    }
    Ok(())
}

/// Copy the controller payload out of the AppImage mount into a 0700 directory
/// under /tmp. A pkexec-elevated process cannot read the FUSE mount (root has
/// no access to user-owned FUSE mounts), so the helper must run from the stage.
fn stage_controller_payload(bundle: &std::path::Path) -> Result<std::path::PathBuf, String> {
    use std::os::unix::fs::PermissionsExt;
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or_default();
    let stage = std::path::PathBuf::from(format!(
        "/tmp/anvildroid-controller-stage-{}-{}",
        std::process::id(),
        nonce
    ));
    std::fs::create_dir(&stage).map_err(|e| format!("Cannot create stage directory: {e}"))?;
    std::fs::set_permissions(&stage, std::fs::Permissions::from_mode(0o700))
        .map_err(|e| format!("Cannot secure stage directory: {e}"))?;
    let result = (|| -> Result<(), String> {
    let copy = |relative: &str| -> Result<(), String> {
        let source = bundle.join(relative);
        if !source.is_file() {
            return Err(format!("Bundled controller payload is missing: {relative}"));
        }
        let target = stage.join(relative);
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("Cannot stage {relative}: {e}"))?;
        }
        std::fs::copy(&source, &target).map_err(|e| format!("Cannot stage {relative}: {e}"))?;
        Ok(())
    };
    for file in [
        "scripts/start-runtime-controller.py",
        "scripts/setup-waydroid.py",
        "scripts/install-runtime-controller.py",
        "target/native/libanvildroid-runtime-window.so",
        "target/native/overlay-key.p12",
        "target/ime/AnvilDroidIme.apk",
        "target/shutdown/anvildroid-shutdown.jar",
        "native/anvildroid-apps.rc",
        "native/anvildroid-tasks.rc",
        "native/anvildroid-apps.sh",
        "native/anvildroid-tasks.sh",
    ] {
        copy(file)?;
    }
    for entry in std::fs::read_dir(bundle.join("services"))
        .map_err(|e| format!("Bundled controller payload is missing: services ({e})"))?
    {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry.path().extension().is_some_and(|ext| ext == "py") {
            copy(&format!("services/{}", entry.file_name().to_string_lossy()))?;
        }
    }
    copy_stage_tree(bundle, &stage, "native/overlay")?;
    Ok(())
    })();
    if let Err(error) = result {
        let _ = std::fs::remove_dir_all(&stage);
        return Err(error);
    }
    Ok(stage)
}

fn stage_setup_payload(bundle: &std::path::Path) -> Result<std::path::PathBuf, String> {
    use std::os::unix::fs::PermissionsExt;
    let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or_default();
    let stage = std::path::PathBuf::from(format!("/tmp/anvildroid-setup-stage-{}-{}", std::process::id(), nonce));
    std::fs::create_dir(&stage).map_err(|e| format!("Cannot create setup stage: {e}"))?;
    std::fs::set_permissions(&stage, std::fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
    let result = (|| -> Result<(), String> {
        for relative in ["scripts/setup-waydroid.py", "services/runtime-host.py", "services/runtime-arm.py", "services/runtime-arm-source.py"] {
            let source = bundle.join(relative);
            if !source.is_file() { return Err(format!("Bundled setup payload is missing: {relative}")); }
            let target = stage.join(relative);
            if let Some(parent) = target.parent() { std::fs::create_dir_all(parent).map_err(|e| e.to_string())?; }
            std::fs::copy(source, target).map_err(|e| e.to_string())?;
        }
        Ok(())
    })();
    if result.is_err() { let _ = std::fs::remove_dir_all(&stage); }
    result.map(|_| stage)
}

fn run_pkexec(
    script: &std::path::Path,
    args: &[&str],
    deadline_duration: std::time::Duration,
) -> Result<StartControllerResult, String> {
    use std::io::Read;
    use std::process::{Command, Stdio};
    let mut child = Command::new("pkexec")
        // This is a host executable, not an AppImage-bundled executable.
        .env_remove("LD_LIBRARY_PATH")
        .env_remove("LD_PRELOAD")
        .arg("/usr/bin/python3")
        .arg("-B")
        .arg(script)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!(
            "pkexec is not available ({e}). Install polkit (policykit-1), or run \
             `sudo python3 scripts/install-runtime-controller.py` from a release directory."
        ))?;
    let deadline = std::time::Instant::now() + deadline_duration;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if std::time::Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(
                    "Timed out waiting for the administrator authentication dialog. If it \
                     completed anyway, the next click will report the current progress."
                        .to_string(),
                );
            }
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(100)),
            Err(e) => return Err(format!("Cannot wait for pkexec: {e}")),
        }
    };
    let mut output = String::new();
    if let Some(mut stdout) = child.stdout.take() {
        let _ = stdout.read_to_string(&mut output);
    }
    if let Some(mut stderr) = child.stderr.take() {
        let _ = stderr.read_to_string(&mut output);
    }
    let tail = |text: &str| {
        let mut chars: Vec<char> = text.chars().collect();
        if chars.len() > 1000 {
            chars = chars.split_off(chars.len() - 1000);
        }
        chars.into_iter().collect::<String>()
    };
    match status.code() {
        Some(0) => Ok(StartControllerResult {
            ok: true,
            message: tail(output.trim()),
        }),
        Some(126) | Some(127) => Ok(StartControllerResult {
            ok: false,
            message: format!(
                "Administrator authentication was cancelled or the authentication agent \
                 failed; the controller was not started.{}",
                if output.trim().is_empty() { String::new() } else { format!("\n{output}") }
            ),
        }),
        Some(code) => Ok(StartControllerResult {
            ok: false,
            message: format!("pkexec failed with status {code}:\n{}", tail(&output)),
        }),
        None => Ok(StartControllerResult {
            ok: false,
            message: format!("pkexec was terminated by a signal:\n{}", tail(&output)),
        }),
    }
}

#[tauri::command]
async fn start_runtime_controller() -> Result<StartControllerResult, String> {
    blocking(|| {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let appdir = std::env::var("APPDIR").ok();
        let bundle = resolve_bundle_root(&exe, appdir.as_deref()).ok_or_else(|| {
            "Controller helper not found; this build does not bundle the runtime controller. \
             Use `sudo python3 scripts/install-runtime-controller.py` from a release directory, \
             or run the packaged AppImage."
                .to_string()
        })?;
        let stage = stage_controller_payload(&bundle)?;
        let result = run_pkexec(
            &stage.join(CONTROLLER_HELPER),
            &[],
            std::time::Duration::from_secs(300),
        );
        let _ = std::fs::remove_dir_all(&stage);
        result
    })
    .await
}

#[tauri::command]
async fn setup_waydroid(flavor: String) -> Result<StartControllerResult, String> {
    blocking(move || {
        if !matches!(flavor.as_str(), "VANILLA" | "GAPPS") {
            return Err("Flavor must be VANILLA or GAPPS".into());
        }
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let appdir = std::env::var("APPDIR").ok();
        let bundle = resolve_setup_root(&exe, appdir.as_deref()).ok_or_else(|| {
            "Waydroid setup helper not found; this build does not bundle it. \
             Use a packaged AppImage or portable release."
                .to_string()
        })?;
        let stage = stage_setup_payload(&bundle)?;
        let result = run_pkexec(
            &stage.join(SETUP_HELPER),
            &["--flavor", flavor.as_str()],
            std::time::Duration::from_secs(1200),
        );
        let _ = std::fs::remove_dir_all(&stage);
        result
    })
    .await
}

#[tauri::command]
async fn waydroid_init_status() -> Result<serde_json::Value, String> {
    blocking(|| {
        match std::fs::read_to_string("/run/anvildroid/waydroid-init.status") {
            Ok(text) => Ok(serde_json::from_str(&text)
                .unwrap_or(serde_json::json!({ "state": "none" }))),
            Err(_) => Ok(serde_json::json!({ "state": "none" })),
        }
    })
    .await
}

#[tauri::command]
fn open_waydroid_setup_guide() -> Result<(), String> {
    std::process::Command::new("xdg-open")
        .arg("https://docs.waydro.id/usage/install-on-desktops")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Cannot open browser: {error}"))
}

#[tauri::command]
fn open_google_play_guide() -> Result<(), String> {
    std::process::Command::new("xdg-open")
        .arg("https://docs.waydro.id/faq/google-play-certification")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Cannot open browser: {error}"))
}

// --- Mutation commands (async, guarded, off main thread) ---

#[tauri::command]
fn open_google_device_registration() -> Result<(), String> {
    std::process::Command::new("xdg-open")
        .arg("https://www.google.com/android/uncertified/")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Cannot open browser: {error}"))
}

#[tauri::command]
async fn start_runtime(
    runtime_id: String,
    guard: State<'_, OpGuard>,
) -> Result<OperationResult, ()> {
    let guard = guard.0.clone();
    Ok(blocking(move || {
        if let Err(e) = scope::ensure_legacy(&runtime_id) {
            return op_result_err(e);
        }
        let Ok(_operation) = guard.try_lock() else {
            return busy_result();
        };
        match launch::ensure_ready(launch::BOOT_TIMEOUT) {
            Ok(()) => op_result_ok("Android is ready."),
            Err(e) => op_result_err(e),
        }
    })
    .await)
}

#[tauri::command]
async fn stop_runtime(
    runtime_id: String,
    guard: State<'_, OpGuard>,
) -> Result<OperationResult, ()> {
    let guard = guard.0.clone();
    Ok(blocking(move || {
        if let Err(e) = scope::ensure_legacy(&runtime_id) {
            return op_result_err(e);
        }
        let Ok(_operation) = guard.try_lock() else {
            return busy_result();
        };
        match std::process::Command::new("waydroid")
            .args(["session", "stop"])
            .output()
        {
            Ok(output) if output.status.success() => {
                op_result_ok("Session stopped. App data is retained.")
            }
            Ok(output) => op_result_err(AnvilError::BackendFailed(format!(
                "Session stop failed (exit {})",
                output.status.code().unwrap_or(-1)
            ))),
            Err(e) => op_result_err(AnvilError::BackendNotFound(format!(
                "Cannot execute Waydroid: {e}"
            ))),
        }
    })
    .await)
}

#[tauri::command]
async fn recover_runtime(
    runtime_id: String,
    guard: State<'_, OpGuard>,
) -> Result<OperationResult, ()> {
    let guard = guard.0.clone();
    Ok(blocking(move || {
        if let Err(e) = scope::ensure_legacy(&runtime_id) {
            return op_result_err(e);
        }
        let Ok(_operation) = guard.try_lock() else {
            return busy_result();
        };
        let status = runtime::runtime_status();
        let stale_session = status.session_state.as_deref() == Some("RUNNING")
            && status.container_state.as_deref() == Some("STOPPED");
        match runtime::session_owner() {
            Ok(true) if !stale_session => {
                return op_result_ok("Session service is already present; no recovery needed.")
            }
            Ok(_) => {}
            Err(e) => return op_result_err(e),
        }
        let stop = std::process::Command::new("waydroid")
            .args(["session", "stop"])
            .output();
        match stop {
            Ok(o) if o.status.success() => {}
            Ok(_) => {
                return op_result_err(AnvilError::BackendFailed(
                    "Session stop failed during recovery.".into(),
                ))
            }
            Err(e) => {
                return op_result_err(AnvilError::BackendFailed(format!(
                    "Cannot stop session: {e}"
                )))
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
        match launch::ensure_ready(launch::BOOT_TIMEOUT) {
            Ok(()) => op_result_ok("Android is ready."),
            Err(e) => op_result_err(e),
        }
    })
    .await)
}

#[tauri::command]
async fn launch_app(
    runtime_id: String,
    package: String,
    guard: State<'_, OpGuard>,
) -> Result<OperationResult, ()> {
    let guard = guard.0.clone();
    Ok(blocking(move || {
        if let Err(e) = scope::ensure_legacy(&runtime_id) {
            return op_result_err(e);
        }
        let Ok(_operation) = guard.try_lock() else {
            return busy_result();
        };
        match launch::launch_app(&package) {
            Ok(()) => {
                let note = library::launched(&package)
                    .err()
                    .map(|e| format!("; could not save recent apps: {e}"))
                    .unwrap_or_default();
                op_result_ok(format!("Launch request sent for {package}{note}"))
            }
            Err(e) => op_result_err(e),
        }
    })
    .await)
}

#[tauri::command]
async fn open_app_info(
    runtime_id: String,
    package: String,
) -> Result<OperationResult, ()> {
    Ok(blocking(move || {
        if let Err(e) = scope::ensure_legacy(&runtime_id) {
            return op_result_err(e);
        }
        match launch::open_app_info(&package) {
            Ok(()) => op_result_ok(format!("Android App Info requested for {package}")),
            Err(e) => op_result_err(e),
        }
    })
    .await)
}

#[tauri::command]
async fn create_app_shortcut(
    runtime_id: String,
    package: String,
    guard: State<'_, OpGuard>,
) -> Result<OperationResult, ()> {
    let guard = guard.0.clone();
    Ok(blocking(move || {
        let Ok(_operation) = guard.try_lock() else {
            return busy_result();
        };
        let result = std::env::current_exe()
            .map_err(AnvilError::from)
            .and_then(|exe| {
                if library::valid_managed_runtime(&runtime_id) {
                    desktop::create_managed_shortcut(&runtime_id, &package, &exe)
                } else {
                    scope::ensure_legacy(&runtime_id)?;
                    desktop::create_shortcut(&package, &exe)
                }
            });
        match result {
            Ok(_) => op_result_ok(format!(
                "Shortcut for {package} created/updated in the application menu (AnvilDroid)."
            )),
            Err(e) => op_result_err(e),
        }
    })
    .await)
}

#[tauri::command]
async fn install_apk(
    runtime_id: String,
    path: String,
    guard: State<'_, OpGuard>,
) -> Result<OperationResult, ()> {
    let guard = guard.0.clone();
    Ok(blocking(move || {
        let Ok(_operation) = guard.try_lock() else {
            return busy_result();
        };
        if library::valid_managed_runtime(&runtime_id) {
            return match anvildroid_core::managed::install(&runtime_id, std::path::Path::new(&path)) {
                Ok(()) => op_result_ok("APK installed successfully in the selected runtime. Android confirmed installation."),
                Err(error) => op_result_err(error),
            };
        }
        if let Err(error) = scope::ensure_legacy(&runtime_id) { return op_result_err(error); }
        match apps::install(std::path::Path::new(&path)) {
            Ok(info) => op_result_ok(format!(
                "Installed {} — version {} (code {}). Verified in Android.",
                info.package,
                info.version_name.unwrap_or_else(|| "unknown".into()),
                info.version_code.unwrap_or_default()
            )),
            Err(e) => op_result_err(e),
        }
    })
    .await)
}

#[tauri::command]
async fn import_runtime_image(path: String, name: String) -> Result<serde_json::Value, String> {
    blocking(move || anvildroid_core::controller::import_image(std::path::Path::new(&path), &name)).await
}

#[tauri::command]
async fn import_runtime_image_folder(path: String, name: String) -> Result<serde_json::Value, String> {
    blocking(move || anvildroid_core::controller::import_image_folder(std::path::Path::new(&path), &name)).await
}

#[tauri::command]
async fn import_runtime_image_url(url: String, name: String) -> Result<serde_json::Value, String> {
    blocking(move || {
        if !url.starts_with("https://") || url.len() > 2048 {
            return Err("Custom image URL must be an HTTPS URL up to 2048 characters".into());
        }
        let jobs = custom_image_job().clone();
        let mut guard = jobs.lock().map_err(|_| "Custom image job state is unavailable".to_string())?;
        if guard.as_ref().is_some_and(|job| job.status == "Downloading") {
            return Err("A custom image download is already running".into());
        }
        use std::os::unix::fs::{DirBuilderExt, MetadataExt};
        use std::hash::{Hash, Hasher};
        let home = std::env::var_os("HOME").ok_or("Home directory is unavailable")?;
        let cache = std::path::PathBuf::from(home).join(".anvildroid-image-downloads");
        let owner = std::fs::metadata("/proc/self").map_err(|e| e.to_string())?.uid();
        let secure_directory = |directory: &std::path::Path| -> Result<(), String> {
            match std::fs::DirBuilder::new().mode(0o700).create(directory) {
                Ok(()) => (),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
                Err(error) => return Err(error.to_string()),
            }
            let info = std::fs::symlink_metadata(directory).map_err(|e| e.to_string())?;
            if !info.is_dir() || info.uid() != owner || info.mode() & 0o077 != 0 {
                return Err("Unsafe custom image download directory".into());
            }
            Ok(())
        };
        secure_directory(&cache)?;
        let mut hash = std::collections::hash_map::DefaultHasher::new();
        url.hash(&mut hash);
        let directory = cache.join(format!("{:016x}", hash.finish()));
        secure_directory(&directory)?;
        let source = directory.join("source-url");
        if source.exists() {
            if std::fs::read_to_string(&source).map_err(|e| e.to_string())? != url { return Err("Download cache URL mismatch".into()); }
        } else { std::fs::write(&source, &url).map_err(|e| e.to_string())?; }
        let path = directory.join("image.zip");
        if let Ok(info) = std::fs::symlink_metadata(&path) {
            if !info.is_file() || info.uid() != owner || info.nlink() != 1 { return Err("Unsafe partial image download".into()); }
        }
        let header_path = path.with_file_name("response-headers");
        let total = std::process::Command::new("curl")
            .args(["-fsSLI", "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "15", "--max-time", "30", "--dump-header"])
            .arg(&header_path).arg("--").arg(&url).output()
            .ok()
            .and_then(|output| if output.status.success() { std::fs::read_to_string(&header_path).ok().and_then(|text| custom_download_total(&text)) } else { None })
            .unwrap_or(0);
        *guard = Some(CustomImageJob { path: path.clone(), total, status: "Downloading".into(), phase: "Downloading ZIP".into(), error: None, result: None });
        drop(guard);
        std::thread::spawn(move || {
            let headers = path.with_file_name("response-headers");
            let previous_headers = std::fs::read_to_string(&headers).unwrap_or_default();
            let validator = previous_headers.lines().rev().find_map(|line| {
                let (name, value) = line.split_once(':')?;
                if name.eq_ignore_ascii_case("etag") && !value.trim().starts_with("W/") {
                    Some(value.trim().to_owned())
                } else { None }
            });
            let mut command = std::process::Command::new("curl");
            if let Some(value) = validator { command.arg("--header").arg(format!("If-Range: {value}")); }
            let outcome = command
                .args(["-fsSL", "--retry", "5", "--retry-delay", "2", "--continue-at", "-",
                    "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "15",
                    "--max-time", "1800", "--max-filesize", "34359738368", "--dump-header"])
                .arg(&headers).arg("-o").arg(&path).arg("--").arg(&url).output()
                .map_err(|e| e.to_string())
                .and_then(|output| if output.status.success() { Ok(()) } else {
                    Err(String::from_utf8_lossy(&output.stderr).chars().take(1024).collect::<String>())
                })
                .and_then(|_| {
                    if let Ok(mut state) = jobs.lock() {
                        if let Some(job) = state.as_mut() { job.phase = "Validating and importing with Waydroid CLI".into(); }
                    }
                    anvildroid_core::controller::import_image(&path, &name)
                });
            if outcome.is_ok() {
                // The controller duplicates the descriptor before acknowledging import.
                let _ = std::fs::remove_file(&path);
            }
            if let Ok(mut state) = jobs.lock() {
                if let Some(job) = state.as_mut() { job.status = if outcome.is_ok() { "Ready".into() } else { "Failed".into() }; job.phase = if outcome.is_ok() { "Custom image imported".into() } else { "Import failed".into() }; job.error = outcome.as_ref().err().map(|e| e.to_string()); job.result = outcome.ok(); }
            }
        });
        Ok(serde_json::json!({"status":"Downloading","total_bytes":total,"received_bytes":0,"percent":0}))
    }).await
}

fn custom_download_total(headers: &str) -> Option<u64> {
    let mut length = None;
    let mut range = None;
    let mut success = false;
    for line in headers.lines() {
        if line.starts_with("HTTP/") {
            success = matches!(line.split_whitespace().nth(1), Some("200" | "206"));
            length = None;
            range = None;
        } else if let Some((name, value)) = line.split_once(':') {
            if name.eq_ignore_ascii_case("content-length") { length = value.trim().parse().ok(); }
            if name.eq_ignore_ascii_case("content-range") {
                range = value.rsplit_once('/').and_then(|(_, total)| total.trim().parse().ok());
            }
        }
    }
    if success { range.or(length) } else { None }
}

#[tauri::command]
async fn custom_image_url_status() -> Result<serde_json::Value, String> {
    blocking(|| {
        let guard = custom_image_job().lock().map_err(|_| "Custom image job state is unavailable".to_string())?;
        let Some(job) = guard.as_ref() else { return Ok(serde_json::json!({"status":"Idle","percent":0})); };
        let received = std::fs::metadata(&job.path).map(|m| m.len()).unwrap_or(0);
        let headers = std::fs::read_to_string(job.path.with_file_name("response-headers")).unwrap_or_default();
        let total = custom_download_total(&headers).unwrap_or(job.total);
        let percent = if total > 0 { Some(((received.min(total) as f64 / total as f64) * 100.0).floor() as u64) } else { None };
        Ok(serde_json::json!({"status":job.status,"phase":job.phase,"total_bytes":total,"received_bytes":received,"percent":percent,"error":job.error,"result":job.result}))
    }).await
}

#[tauri::command]
async fn import_arm_translation(source: String, source_kind: String, expected_sha256: String) -> Result<serde_json::Value, String> {
    blocking(move || anvildroid_core::controller::import_arm_source(&source_kind, &source, &expected_sha256)).await
}

#[tauri::command]
async fn install_touch_probe(runtime_id: String, guard: State<'_, OpGuard>) -> Result<OperationResult, ()> {
    let guard=guard.0.clone();
    Ok(blocking(move || {
        let Ok(_operation)=guard.try_lock() else { return busy_result(); };
        if !library::valid_managed_runtime(&runtime_id) {
            return op_result_err(AnvilError::InvalidArgument("Select a managed runtime".into()));
        }
        let result=(|| -> Result<(), AnvilError> {
            use std::io::Write;
            use std::os::unix::fs::OpenOptionsExt;
            let nonce=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
            let path=std::env::temp_dir().join(format!("anvildroid-probe-{}-{nonce}.apk",std::process::id()));
            let mut file=std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600)
                .open(&path).map_err(|e| AnvilError::Io(e.to_string()))?;
            let outcome=(|| {
                file.write_all(include_bytes!(concat!(env!("OUT_DIR"),"/touch-probe.apk")))
                    .map_err(|e| AnvilError::Io(e.to_string()))?;
                file.sync_all().map_err(|e| AnvilError::Io(e.to_string()))?;
                anvildroid_core::managed::install(&runtime_id,&path)
            })();
            drop(file);
            let _=std::fs::remove_file(&path);
            outcome
        })();
        match result {
            Ok(())=>op_result_ok("Touch Probe installed. Open it from this runtime's app library. Touch playback is not enabled by installation."),
            Err(e)=>op_result_err(e),
        }
    }).await)
}

#[tauri::command]
async fn open_waydroid(guard: State<'_, OpGuard>) -> Result<OperationResult, ()> {
    let guard=guard.0.clone();
    Ok(blocking(move || {
        let Ok(_operation)=guard.try_lock() else {return busy_result();};
        if let Err(e)=scope::ensure_legacy("default") {return op_result_err(e);}
        match std::process::Command::new("waydroid").arg("show-full-ui")
            .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null()).spawn() {
            Ok(mut child)=>{std::thread::spawn(move || {let _=child.wait();});op_result_ok("Waydroid full-window launch requested for Existing runtime.")},
            Err(e)=>op_result_err(AnvilError::Io(e.to_string())),
        }
    }).await)
}

#[tauri::command]
async fn uninstall_app(
    runtime_id: String,
    package: String,
    guard: State<'_, OpGuard>,
) -> Result<OperationResult, ()> {
    let guard = guard.0.clone();
    Ok(blocking(move || {
        if let Err(e) = scope::ensure_legacy(&runtime_id) { return op_result_err(e); }
        let Ok(_operation) = guard.try_lock() else {
            return busy_result();
        };
        match backend::uninstall_app(&package) {
            Ok(backend::UninstallOutcome::Removed) => uninstall_completed(&package),
            Ok(backend::UninstallOutcome::AndroidConfirmationRequired) => {
                let mut result = op_result_ok(
                    "Uninstall request sent to Android. Confirm any Android dialog; waiting to verify removal.",
                );
                result.pending_confirmation = true;
                result
            }
            Err(e) => op_result_err(e),
        }
    })
    .await)
}

fn uninstall_completed(package: &str) -> OperationResult {
    match desktop::remove_shortcut(package) {
        Ok(()) => op_result_ok(format!("Uninstalled {package}")),
        Err(e) => op_result_ok(format!(
            "Uninstalled {package}, but its menu shortcut could not be removed: {e}"
        )),
    }
}

#[tauri::command]
async fn check_uninstall(
    runtime_id: String,
    package: String,
    guard: State<'_, OpGuard>,
) -> Result<Option<OperationResult>, String> {
    let guard = guard.0.clone();
    blocking(move || {
        scope::ensure_legacy(&runtime_id).map_err(|e| e.to_string())?;
        if !backend::valid_package(&package) || backend::uninstall_block_reason(&package).is_some()
        {
            return Err("Invalid or protected package".into());
        }
        let Ok(_operation) = guard.try_lock() else {
            return Ok(None);
        };
        let details = apps::details(&package).map_err(|e| e.to_string())?;
        if details.installed {
            Ok(None)
        } else {
            Ok(Some(uninstall_completed(&package)))
        }
    })
    .await
}

fn main() {
    // Desktop entries reuse the same boot-aware backend without opening a GUI.
    // Parse before Tauri/GTK initialization so shortcuts do not need a webview.
    let raw_args: Vec<_> = std::env::args_os().skip(1).collect();
    let open_settings = raw_args.len() == 1 && raw_args[0] == "--settings";
    let open_controls = raw_args.len() == 1 && raw_args[0] == "--controls";
    if !raw_args.is_empty() && !open_settings && !open_controls {
        let result = (|| -> Result<String, AnvilError> {
            let (runtime_id, args) = scope::parse_selector(&raw_args)?;
            let args: Vec<String> = args
                .into_iter()
                .map(|s| {
                    s.into_string()
                        .map_err(|_| AnvilError::InvalidArgument("Arguments must be UTF-8".into()))
                })
                .collect::<Result<_, _>>()?;
            if args.len() != 2 || !backend::valid_package(&args[1]) {
                Err(AnvilError::InvalidArgument(
                "Usage: anvildroid-gui [--runtime ID] [--launch-app|--app-info|--create-shortcut PACKAGE]".into(),
            ))
            } else if library::valid_managed_runtime(&runtime_id) {
                match args[0].as_str() {
                    "--launch-app" => anvildroid_core::managed::launch(&runtime_id, &args[1])
                        .map(|()| format!("Opened {} in {runtime_id}", args[1])),
                    "--create-shortcut" => std::env::current_exe()
                        .map_err(AnvilError::from)
                        .and_then(|exe| {
                            desktop::create_managed_shortcut(&runtime_id, &args[1], &exe)
                        })
                        .map(|path| format!("Created/updated {}", path.display())),
                    _ => Err(AnvilError::InvalidArgument(
                        "Unsupported managed runtime desktop action".into(),
                    )),
                }
            } else {
                scope::ensure_legacy(&runtime_id)?;
                match args[0].as_str() {
                    "--launch-app" => launch::launch_app(&args[1]).map(|()| {
                        let _ = library::launched(&args[1]);
                        format!("Launch request sent for {}", args[1])
                    }),
                    "--app-info" => launch::open_app_info(&args[1])
                        .map(|()| format!("Android App Info requested for {}", args[1])),
                    "--create-shortcut" => std::env::current_exe()
                        .map_err(AnvilError::from)
                        .and_then(|exe| desktop::create_shortcut(&args[1], &exe))
                        .map(|path| format!("Created/updated {}", path.display())),
                    _ => Err(AnvilError::InvalidArgument("Unknown desktop action".into())),
                }
            }
        })();
        match result {
            Ok(message) => {
                println!("{message}");
                return;
            }
            Err(e) => {
                eprintln!("{}: {e}", e.code());
                // A menu launch has no terminal; surface the failure on desktop.
                let _ = std::process::Command::new("notify-send")
                    .args([
                        "--app-name=AnvilDroid",
                        "--",
                        "AnvilDroid action failed",
                        &e.to_string(),
                    ])
                    .stdin(std::process::Stdio::null())
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .spawn();
                std::process::exit(1);
            }
        }
    }
    let mut context = tauri::generate_context!();
    if open_settings {
        context.config_mut().app.windows[0].url =
            tauri::WebviewUrl::App("index.html?view=settings".into());
    }
    if open_controls {
        context.config_mut().app.windows[0].title = "AnvilDroid — Game Controls".into();
        context.config_mut().app.windows[0].url =
            tauri::WebviewUrl::App("index.html?view=controls".into());
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(OpGuard(Arc::new(Mutex::new(()))))
        .register_asynchronous_uri_scheme_protocol("appicon", |_ctx, request, responder| {
            let path = percent_decode(request.uri().path());
            if let Some(package) = icon_package(&path) {
                if let Some(icon_dir) = backend::waydroid_icon_dir() {
                    let icon_file = icon_dir.join(format!("{package}.png"));
                    if icon_file.is_file() {
                        if let Ok(data) = std::fs::read(&icon_file) {
                            let response = tauri::http::Response::builder()
                                .status(200)
                                .header("Content-Type", "image/png")
                                .body(data)
                                .unwrap();
                            responder.respond(response);
                            return;
                        }
                    }
                }
            }
            let response = tauri::http::Response::builder()
                .status(404)
                .body(Vec::new())
                .unwrap();
            responder.respond(response);
        })
        .invoke_handler(tauri::generate_handler![
            get_app_version,
            get_health_report,
            get_apps,
            get_app_details,
            get_library,
            get_runtime_catalog,
            runtime_controller,
            start_runtime_controller,
            setup_waydroid,
            waydroid_init_status,
            open_waydroid_setup_guide,
            open_google_play_guide,
            open_google_device_registration,
            set_favorite,
            record_runtime_launch,
            set_library_sort,
            force_stop_app,
            start_runtime,
            stop_runtime,
            recover_runtime,
            launch_app,
            open_app_info,
            create_app_shortcut,
            install_apk,
            import_runtime_image,
            import_runtime_image_folder,
            import_runtime_image_url,
            custom_image_url_status,
            import_arm_translation,
            install_touch_probe,
            open_waydroid,
            uninstall_app,
            check_uninstall,
        ])
        .run(context)
        .expect("error while running AnvilDroid GUI");
}

fn icon_package(path: &str) -> Option<&str> {
    let (id, filename) = path.strip_prefix('/')?.split_once('/')?;
    // A URI for another runtime must never read default's icon directory.
    scope::ensure_legacy(id).ok()?;
    filename
        .strip_suffix(".png")
        .filter(|p| backend::valid_package(p))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn recent_history_requires_the_exact_successful_launch() {
        let good = serde_json::json!({"id":"job", "runtime_id":"r-one", "package":"com.example.app", "action":"launch", "status":"Succeeded"});
        assert!(validate_library_launch(&good, "r-one", "com.example.app", "job").is_ok());
        for (key, value) in [
            ("id", "other"),
            ("runtime_id", "r-two"),
            ("package", "com.other.app"),
            ("action", "force_stop"),
            ("status", "Failed"),
            ("status", "Running"),
        ] {
            let mut bad = good.clone();
            bad[key] = value.into();
            assert!(validate_library_launch(&bad, "r-one", "com.example.app", "job").is_err());
        }
    }
    #[test]
    fn bundle_root_found_from_usr_bin_layout_and_appdir_fallback() {
        let root = std::env::temp_dir().join(format!("anvildroid-bundle-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("usr/bin")).unwrap();
        std::fs::create_dir_all(root.join("scripts")).unwrap();
        std::fs::write(root.join("scripts/start-runtime-controller.py"), "# helper\n").unwrap();
        // AppImage layout: executable lives at <bundle>/usr/bin/anvildroid-gui.
        let exe = root.join("usr/bin/anvildroid-gui");
        assert_eq!(
            resolve_bundle_root(&exe, None),
            Some(root.clone())
        );
        // Bare dev path without the helper falls back to APPDIR.
        let bare = std::env::temp_dir().join("not-a-bundle");
        assert_eq!(resolve_bundle_root(&bare, None), None);
        assert_eq!(
            resolve_bundle_root(&bare, Some(root.to_str().unwrap())),
            Some(root.clone())
        );
        let _ = std::fs::remove_dir_all(&root);
    }
    #[test]
    fn staging_copies_files_alongside_subdirectories() {
        // Regression test: a file directly under native/overlay must stage
        // correctly even when the subdirectory entry is processed after it.
        let root = std::env::temp_dir().join(format!("anvildroid-stage-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("scripts")).unwrap();
        std::fs::create_dir_all(root.join("services")).unwrap();
        std::fs::create_dir_all(root.join("target/native")).unwrap();
        std::fs::create_dir_all(root.join("target/ime")).unwrap();
        std::fs::create_dir_all(root.join("target/shutdown")).unwrap();
        std::fs::create_dir_all(root.join("native/overlay/res")).unwrap();
        for relative in [
            "scripts/start-runtime-controller.py",
            "scripts/setup-waydroid.py",
            "scripts/install-runtime-controller.py",
            "services/runtime-controller.py",
            "target/native/libanvildroid-runtime-window.so",
            "target/native/overlay-key.p12",
            "target/ime/AnvilDroidIme.apk",
            "target/shutdown/anvildroid-shutdown.jar",
            "native/anvildroid-apps.rc",
            "native/anvildroid-tasks.rc",
            "native/anvildroid-apps.sh",
            "native/anvildroid-tasks.sh",
            "native/overlay/AndroidManifest.xml",
            "native/overlay/res/values.xml",
        ] {
            std::fs::write(root.join(relative), b"fixture").unwrap();
        }
        let stage = stage_controller_payload(&root).unwrap();
        assert!(stage.join("native/overlay/AndroidManifest.xml").is_file());
        assert!(stage.join("native/overlay/res/values.xml").is_file());
        assert!(stage.join("scripts/start-runtime-controller.py").is_file());
        let _ = std::fs::remove_dir_all(&stage);
        let _ = std::fs::remove_dir_all(&root);
    }
    #[test]
    fn unsupported_targets_reject_queries_and_preference_writes() {
        tauri::async_runtime::block_on(async {
            assert!(get_health_report("second".into()).await.is_err());
            assert!(get_apps("second".into()).await.is_err());
            assert!(get_library("second".into()).await.is_err());
            assert!(
                set_favorite("second".into(), "com.example.game".into(), true)
                    .await
                    .is_err()
            );
            assert!(set_library_sort("second".into(), "recent".into())
                .await
                .is_err());
        });
        assert!(icon_package("/second/com.example.game.png").is_none());
        assert!(icon_package("/com.example.game.png").is_none());
        assert!(icon_package("/default/../../secret.png").is_none());
    }
}

fn percent_decode(input: &str) -> String {
    let mut result = String::with_capacity(input.len());
    let mut chars = input.bytes();
    while let Some(b) = chars.next() {
        if b == b'%' {
            let hi = chars.next().unwrap_or(b'0');
            let lo = chars.next().unwrap_or(b'0');
            let val = hex_val(hi) * 16 + hex_val(lo);
            result.push(val as char);
        } else {
            result.push(b as char);
        }
    }
    result
}

fn hex_val(b: u8) -> u8 {
    match b {
        b'0'..=b'9' => b - b'0',
        b'a'..=b'f' => b - b'a' + 10,
        b'A'..=b'F' => b - b'A' + 10,
        _ => 0,
    }
}
