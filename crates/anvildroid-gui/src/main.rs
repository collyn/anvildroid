use std::sync::{Arc, Mutex, OnceLock};

use anvildroid_core::error::AnvilError;
use anvildroid_core::models::{AppInfo, HealthReport, OperationResult};
use anvildroid_core::{apps, backend, desktop, launch, library, runtime, scope};

use tauri::{Manager, State};

#[cfg(target_os = "linux")]
mod titlebar;
mod preferences;
mod custom_download;
mod updater;

static STARTUP_TRACE: OnceLock<Option<std::time::Instant>> = OnceLock::new();

fn startup_mark(stage: &str) {
    if let Some(Some(started)) = STARTUP_TRACE.get() {
        eprintln!("AnvilDroid startup: {} ms · {stage}", started.elapsed().as_millis());
    }
}

#[tauri::command]
fn set_window_theme(window: tauri::WebviewWindow, light: bool) {
    #[cfg(target_os = "linux")]
    {
        // GTK widgets must be touched on the main thread.
        let target = window.clone();
        let _ = window.run_on_main_thread(move || titlebar::set_light(&target, light));
    }
    #[cfg(not(target_os = "linux"))]
    let _ = (window, light);
}

#[tauri::command]
fn get_app_version() -> &'static str {
    startup_mark("frontend-script");
    env!("CARGO_PKG_VERSION")
}

#[tauri::command]
fn startup_ready(resources: Option<serde_json::Value>) {
    if let Some(resources) = resources {
        startup_mark("dom-ready");
        if matches!(STARTUP_TRACE.get(), Some(Some(_))) {
            eprintln!("AnvilDroid startup resources: {resources}");
        }
    } else {
        startup_mark("frontend-first-frame");
    }
}

/// Guard to prevent concurrent backend mutations (start/stop/recover/install).
struct OpGuard(Arc<Mutex<()>>);

fn op_result_ok(message: impl Into<String>) -> OperationResult {
    OperationResult {
        success: true,
        message: message.into(),
        error_code: None,
        recovery_hint: None,
    }
}

fn op_result_err(e: AnvilError) -> OperationResult {
    OperationResult {
        success: false,
        message: e.to_string(),
        error_code: Some(e.code().into()),
        recovery_hint: e.recovery_hint().map(String::from),
    }
}

fn busy_result() -> OperationResult {
    OperationResult {
        success: false,
        message: "Another operation is in progress.".into(),
        error_code: Some("BUSY".into()),
        recovery_hint: Some("Wait for the current operation to complete.".into()),
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

// Controller mutations of Default share the guard used by legacy app/boot actions.
// The root controller cannot see this GUI process's in-flight legacy operations.
fn requires_legacy_operation_guard(request: &anvildroid_core::controller::Request) -> bool {
    use anvildroid_core::controller::Request;
    match request {
        Request::ResourcesSet { id, .. } | Request::GpuSet { id, .. }
        | Request::DeviceSet { id, .. }
        | Request::ArmSet { id, .. } | Request::ExistingClaim { id } | Request::SystemIntegration { id }
        | Request::ExistingImport { id, .. } | Request::Rename { id, .. }
        | Request::CompatibilitySet { id, .. } | Request::CompatibilityReset { id, .. }
        | Request::ComponentsSet { id, .. } | Request::SharesSet { id, .. }
        | Request::SharesRemove { id, .. } => id == scope::LEGACY_ID,
        _ => false,
    }
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
        let changes_existing = requires_legacy_operation_guard(&request);
        let _operation = if changes_existing {
            scope::ensure_legacy(scope::LEGACY_ID).map_err(|e| e.to_string())?;
            Some(guard.try_lock().map_err(|_| "Another operation is in progress.".to_string())?)
        } else { None };
        if let Request::Start { id, .. } = &request {
            if library::valid_managed_runtime(id) {
                let display = anvildroid_core::controller::request(Request::DisplayInfo { id: id.clone() })?;
                if display["backend"] == "native-x11" || display["display_backend"] == "native-x11" {
                    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
                    desktop::register_managed_window(id, "org.anvildroid.desktop", &exe).map_err(|e| e.to_string())?;
                }
            }
        }
        if let Request::AppAction { id, action, package, .. } = &request {
            let register = if action == "full_ui" && library::valid_managed_runtime(id) {
                let display = anvildroid_core::controller::request(Request::DisplayInfo { id: id.clone() })?;
                display["backend"] == "native-x11"
            } else { action == "launch" && library::valid_managed_runtime(id) };
            if register {
                let exe = std::env::current_exe().map_err(|e| e.to_string())?;
                desktop::register_managed_window(id, package, &exe).map_err(|e| e.to_string())?;
            }
        }
        anvildroid_core::controller::request(request)
    }).await
}

#[tauri::command]
async fn export_runtime_diagnostics(id: String, path: String, include_logs: bool) -> Result<serde_json::Value, String> {
    blocking(move || anvildroid_core::diagnostics::export(&id, std::path::Path::new(&path), include_logs)).await
}

#[tauri::command]
async fn export_keymap_scheme(id: String, package: String, scheme_id: String, revision: String, path: String) -> Result<serde_json::Value, String> {
    blocking(move || anvildroid_core::keymap_export::export(&id, &package, &scheme_id, &revision, std::path::Path::new(&path))).await
}

#[tauri::command]
async fn adb_host_status(adb_path: Option<String>) -> Result<anvildroid_core::adb::HostReport, String> {
    blocking(move || anvildroid_core::adb::status(adb_path.as_deref()).map_err(|e| e.to_string())).await
}

#[tauri::command]
async fn adb_host_action(action: String, endpoint: String, adb_path: Option<String>) -> Result<String, String> {
    blocking(move || anvildroid_core::adb::action(&action, &endpoint, adb_path.as_deref()).map_err(|e| e.to_string())).await
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

/// Shared with scripts/install-runtime-controller.py, which reads the staged
/// copy and refuses to install when a required path is missing.
const CONTROLLER_PAYLOAD: &str = include_str!("../../../scripts/controller-payload.txt");

/// (relative path, whole directory tree, optional) entries of the payload contract.
fn controller_payload_entries() -> Vec<(&'static str, bool, bool)> {
    CONTROLLER_PAYLOAD
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .map(|line| {
            let optional = line.starts_with('?');
            let path = line.trim_start_matches('?');
            (path.trim_end_matches('/'), path.ends_with('/'), optional)
        })
        .collect()
}

/// Copy the controller payload out of the AppImage mount into a 0700 directory
/// under /tmp. A pkexec-elevated process cannot read the FUSE mount (root has
/// no access to user-owned FUSE mounts), so the helper must run from the stage.
/// Every missing required path is reported before administrator access is requested.
fn stage_controller_payload(bundle: &std::path::Path) -> Result<std::path::PathBuf, String> {
    use std::os::unix::fs::PermissionsExt;
    let entries = controller_payload_entries();
    let missing: Vec<String> = entries
        .iter()
        .filter(|(relative, tree, optional)| {
            let source = bundle.join(relative);
            !optional && !(if *tree { source.is_dir() } else { source.is_file() })
        })
        .map(|(relative, tree, _)| format!("{relative}{}", if *tree { "/" } else { "" }))
        .collect();
    if !missing.is_empty() {
        return Err(format!("Bundled controller payload is missing: {}", missing.join(", ")));
    }
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
        for (relative, tree, _) in entries {
            let source = bundle.join(relative);
            if tree {
                if source.is_dir() {
                    std::fs::create_dir_all(stage.join(relative))
                        .map_err(|e| format!("Cannot stage {relative}: {e}"))?;
                    copy_stage_tree(bundle, &stage, relative)?;
                }
                continue;
            }
            if !source.is_file() {
                continue; // optional and absent
            }
            let target = stage.join(relative);
            if let Some(parent) = target.parent() {
                std::fs::create_dir_all(parent).map_err(|e| format!("Cannot stage {relative}: {e}"))?;
            }
            std::fs::copy(&source, &target).map_err(|e| format!("Cannot stage {relative}: {e}"))?;
        }
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
        let mut files = vec!["scripts/setup-waydroid.py".to_owned()];
        for entry in std::fs::read_dir(bundle.join("services")).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            if entry.path().extension().is_some_and(|ext| ext == "py") {
                files.push(format!("services/{}", entry.file_name().to_string_lossy()));
            }
        }
        for relative in files {
            let source = bundle.join(&relative);
            if !source.is_file() { return Err(format!("Bundled setup payload is missing: {relative}")); }
            let target = stage.join(&relative);
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
    let mut arguments = vec!["-B".to_string(), script.to_string_lossy().into_owned()];
    arguments.extend(args.iter().map(|arg| (*arg).to_string()));
    run_privileged("/usr/bin/python3", &arguments, deadline_duration)
}

fn run_privileged(
    program: &str,
    args: &[String],
    deadline_duration: std::time::Duration,
) -> Result<StartControllerResult, String> {
    use std::io::Read;
    use std::process::{Command, Stdio};
    let mut child = Command::new("pkexec")
        // This is a host executable, not an AppImage-bundled executable.
        .env_remove("LD_LIBRARY_PATH")
        .env_remove("LD_PRELOAD")
        .arg(program)
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
                 failed; the operation was not performed.{}",
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

// Fixed service and executable: the frontend cannot supply privileged commands.
#[tauri::command]
async fn restart_runtime_controller() -> Result<StartControllerResult, String> {
    blocking(|| {
        run_privileged(
            "/usr/bin/systemctl",
            &["restart".into(), "anvildroid-controller.service".into()],
            std::time::Duration::from_secs(540),
        )
    })
    .await
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

// Installs/repairs host runtime tools only; never initializes System Waydroid.
#[tauri::command]
async fn setup_waydroid() -> Result<StartControllerResult, String> {
    blocking(move || {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let appdir = std::env::var("APPDIR").ok();
        let bundle = resolve_setup_root(&exe, appdir.as_deref()).ok_or_else(|| {
            "Runtime tools helper not found; this build does not bundle it. \
             Use a packaged AppImage or portable release."
                .to_string()
        })?;
        let stage = stage_setup_payload(&bundle)?;
        let result = run_pkexec(&stage.join(SETUP_HELPER), &[], std::time::Duration::from_secs(1200));
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
async fn has_app_shortcut(runtime_id: String, package: String) -> Result<bool, String> {
    blocking(move || desktop::has_runtime_shortcut(&runtime_id, &package).map_err(|e| e.to_string())).await
}

#[tauri::command]
async fn get_shortcut_preferences() -> Result<bool, String> {
    blocking(|| anvildroid_core::shortcut_preferences::automatic().map_err(|e| e.to_string())).await
}

#[tauri::command]
async fn set_automatic_shortcuts(enabled: bool) -> Result<(), String> {
    blocking(move || {
        anvildroid_core::shortcut_preferences::set_automatic(enabled).map_err(|e| e.to_string())
    }).await
}

#[tauri::command]
async fn remove_app_shortcut(
    runtime_id: String,
    package: String,
    guard: State<'_, OpGuard>,
) -> Result<OperationResult, ()> {
    let guard = guard.0.clone();
    Ok(blocking(move || {
        let Ok(_operation) = guard.try_lock() else { return busy_result(); };
        match desktop::remove_runtime_shortcut(&runtime_id, &package) {
            Ok(()) => op_result_ok(format!("Menu shortcut for {package} removed.")),
            Err(e) => op_result_err(e),
        }
    }).await)
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
async fn check_update() -> Result<serde_json::Value, String> {
    blocking(|| updater::check(env!("CARGO_PKG_VERSION"))).await
}

#[tauri::command]
async fn install_update() -> Result<serde_json::Value, String> {
    blocking(updater::start).await
}

#[tauri::command]
fn update_status() -> serde_json::Value {
    updater::status()
}

/// Start the updated copy, then leave. Package updates replaced the running
/// binary, so the new one is started by its installed path.
#[tauri::command]
fn restart_after_update(app: tauri::AppHandle) -> Result<(), String> {
    use std::os::unix::process::CommandExt;
    let path = updater::relaunch_path().ok_or("Cannot find the AnvilDroid executable")?;
    std::process::Command::new(&path)
        .env_remove("APPIMAGE")
        .env_remove("APPDIR")
        .env_remove("LD_LIBRARY_PATH")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .process_group(0)
        .spawn()
        .map_err(|e| format!("Cannot start {}: {e}", path.display()))?;
    app.exit(0);
    Ok(())
}

#[tauri::command]
async fn import_runtime_image_url(url: String, name: String) -> Result<serde_json::Value, String> {
    blocking(move || custom_download::start(url, name)).await
}

#[tauri::command]
async fn custom_image_url_status() -> Result<serde_json::Value, String> {
    blocking(custom_download::status).await
}

#[tauri::command]
async fn cancel_custom_image_url() -> Result<serde_json::Value, String> {
    blocking(custom_download::cancel).await
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
            if let Err(error) = scope::ensure_legacy(&runtime_id) { return op_result_err(error); }
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
                if runtime_id == "default" {
                    apps::install(&path).map(|_| ())
                } else {
                    anvildroid_core::managed::install(&runtime_id,&path)
                }
            })();
            drop(file);
            let _=std::fs::remove_file(&path);
            outcome
        })();
        match result {
            Ok(())=>op_result_ok("Touch Probe installed."),
            Err(e)=>op_result_err(e),
        }
    }).await)
}

#[tauri::command]
async fn request_waydroid_activation(window: tauri::WebviewWindow) -> Result<Option<String>, String> {
    let (send, receive) = std::sync::mpsc::channel();
    let source = window.clone();
    window.run_on_main_thread(move || {
        #[cfg(target_os = "linux")]
        let token = {
            use gtk::prelude::*;
            use gtk::glib::translate::ToGlibPtr;
            unsafe extern "C" { fn anvil_activation_token(window: *mut std::ffi::c_void, buffer: *mut std::ffi::c_char, capacity: usize) -> i32; }
            source.gtk_window().ok().and_then(|gtk| gtk.window()).and_then(|surface| {
                let mut buffer = [0u8; 513];
                let ptr: *mut gtk::gdk::ffi::GdkWindow = surface.to_glib_none().0;
                let success = unsafe { anvil_activation_token(ptr.cast(), buffer.as_mut_ptr().cast(), buffer.len()) };
                if success == 0 { None } else {
                    std::ffi::CStr::from_bytes_until_nul(&buffer).ok().and_then(|s| s.to_str().ok()).map(String::from)
                }
            })
        };
        #[cfg(not(target_os = "linux"))]
        let token: Option<String> = None;
        let _ = send.send(token);
    }).map_err(|e| e.to_string())?;
    blocking(move || receive.recv_timeout(std::time::Duration::from_secs(1)).map_err(|e| e.to_string())).await
}

#[tauri::command]
async fn open_waydroid(activation_token: Option<String>, guard: State<'_, OpGuard>) -> Result<OperationResult, ()> {
    let guard=guard.0.clone();
    Ok(blocking(move || {
        let Ok(_operation)=guard.try_lock() else {return busy_result();};
        if let Err(e)=scope::ensure_legacy("default") {return op_result_err(e);}
        match launch::open_full_ui(activation_token.as_deref()) {
            Ok(())=>op_result_ok("Waydroid full-window launch requested for System Waydroid."),
            Err(e)=>op_result_err(e),
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
            Ok(()) => uninstall_completed(&package),
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

/// Login entry: ask the socket-activated controller to start this user's
/// "start at login" runtimes, retrying while the desktop session comes up.
fn login_start() -> i32 {
    use anvildroid_core::controller::{last_error_code, request, Request};
    for attempt in 0..15 {
        std::thread::sleep(std::time::Duration::from_secs(2));
        match request(Request::LoginStart {}) {
            Ok(result) => {
                println!("{result}");
                return 0;
            }
            Err(_) if attempt < 14 && last_error_code().as_deref() == Some("NO_DESKTOP") => continue,
            Err(error) => {
                eprintln!("Cannot start runtimes at login: {error}");
                return 1;
            }
        }
    }
    1
}

fn main() {
    let _ = STARTUP_TRACE.set(std::env::var_os("ANVILDROID_STARTUP_TRACE")
        .filter(|value| value == "1").map(|_| std::time::Instant::now()));
    startup_mark("main");
    // Desktop entries reuse the same boot-aware backend without opening a GUI.
    // Parse before Tauri/GTK initialization so shortcuts do not need a webview.
    let raw_args: Vec<_> = std::env::args_os().skip(1).collect();
    if raw_args.len() == 1 && raw_args[0] == "--shortcut-monitor" {
        // Old login entries can survive an upgrade until the next GUI launch.
        // Exit successfully so Restart=on-failure does not resurrect the service.
        eprintln!("Shortcut monitoring now runs inside the AnvilDroid GUI.");
        return;
    }
    if raw_args.len() == 1 && raw_args[0] == "--login-start" {
        std::process::exit(login_start());
    }
    let open_settings = raw_args.len() == 1 && raw_args[0] == "--settings";
    let open_controls = raw_args.len() == 1 && raw_args[0] == "--controls";
    let autostart = raw_args.len() == 1 && raw_args[0] == "--autostart";
    if !raw_args.is_empty() && !open_settings && !open_controls && !autostart {
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
    startup_mark("assets-ready");
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
        .append_invoke_initialization_script(if matches!(STARTUP_TRACE.get(), Some(Some(_))) {
            "document.addEventListener('DOMContentLoaded', () => { window.__TAURI__.core.invoke('startup_ready', {resources:performance.getEntriesByType('resource').slice(0,30).map(r=>({name:r.name.split('/').pop().slice(0,240),start:Math.round(r.startTime),duration:Math.round(r.duration)}))}); requestAnimationFrame(() => requestAnimationFrame(() => window.__TAURI__.core.invoke('startup_ready'))); }, {once:true});"
        } else { "" })
        .on_page_load(|_, payload| {
            startup_mark(match payload.event() {
                tauri::webview::PageLoadEvent::Started => "page-started",
                tauri::webview::PageLoadEvent::Finished => "page-loaded",
            });
        })
        .setup(move |app| {
            startup_mark("native-window-ready");
            preferences::install(app)?;
            app.manage(anvildroid_core::shortcut_preferences::ShortcutMonitor::start()?);
            #[cfg(target_os = "linux")]
            titlebar::install(app)?;
            preferences::show_at_startup(app.handle(), open_settings || open_controls)?;
            startup_mark("window-visibility-configured");
            Ok(())
        })
        .plugin(tauri_plugin_dialog::init())
        .on_window_event(preferences::handle_close)
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
            startup_ready,
            preferences::get_application_preferences,
            preferences::set_application_preference,
            preferences::set_default_runtime,
            preferences::set_login_start,
            get_health_report,
            get_apps,
            get_app_details,
            get_library,
            get_runtime_catalog,
            runtime_controller,
            export_runtime_diagnostics,
            export_keymap_scheme,
            adb_host_status,
            adb_host_action,
            start_runtime_controller,
            restart_runtime_controller,
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
            has_app_shortcut,
            remove_app_shortcut,
            get_shortcut_preferences,
            set_automatic_shortcuts,
            install_apk,
            import_runtime_image,
            import_runtime_image_folder,
            import_runtime_image_url,
            custom_image_url_status,
            cancel_custom_image_url,
            set_window_theme,
            check_update,
            install_update,
            update_status,
            restart_after_update,
            import_arm_translation,
            install_touch_probe,
            open_waydroid,
            request_waydroid_activation,
            uninstall_app,
        ])
        .build(context)
        .expect("error while building AnvilDroid GUI")
        .run(|app, event| {
            // Releasing the webview to the panel closes the last window; keep
            // running for the tray. Exit from the tray passes an exit code.
            if let tauri::RunEvent::ExitRequested { api, code: None, .. } = &event {
                if preferences::minimize_to_panel(app) { api.prevent_exit(); }
            }
            if matches!(event, tauri::RunEvent::Exit) {
                app.state::<anvildroid_core::shortcut_preferences::ShortcutMonitor>().stop();
            }
        });
}

fn icon_package(path: &str) -> Option<&str> {
    let (id, filename) = path.strip_prefix('/')?.split_once('/')?;
    // A URI for another runtime must never read default's icon directory.
    scope::ensure_legacy(id).ok()?;
    filename
        .strip_suffix(".png")
        .filter(|p| backend::valid_package(p))
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn new_default_mutations_share_the_legacy_operation_guard() {
        use anvildroid_core::controller::Request;
        let revision = "a".repeat(64);
        for request in [
            serde_json::json!({"op":"device_set","id":"default","profile":"pixel-7","custom":null,"revision":revision}),
            serde_json::json!({"op":"compatibility_set","id":"default","package":"com.example.app","settings":{"fake_wifi":true,"mouse_as_touch":false},"revision":revision}),
            serde_json::json!({"op":"compatibility_reset","id":"default","package":"com.example.app","revision":revision}),
            serde_json::json!({"op":"components_set","id":"default","component_id":"libndk","enabled":false,"archive_sha256":null,"revision":revision}),
            serde_json::json!({"op":"shares_set","id":"default","name":"Docs","source":"/home/user/Docs","read_only":true}),
            serde_json::json!({"op":"shares_remove","id":"default","name":"Docs"}),
        ] {
            let decoded: Request = serde_json::from_value(request.clone()).unwrap();
            assert!(requires_legacy_operation_guard(&decoded));
            let mut managed = request;
            managed["id"] = serde_json::json!("r-11111111111111111111111111111111");
            let decoded: Request = serde_json::from_value(managed).unwrap();
            assert!(!requires_legacy_operation_guard(&decoded));
        }
        for request in [
            serde_json::json!({"op":"device_info","id":"default"}),
            serde_json::json!({"op":"compatibility_get","id":"default","package":"com.example.app"}),
            serde_json::json!({"op":"components_get","id":"default"}),
            serde_json::json!({"op":"shares_get","id":"default"}),
            serde_json::json!({"op":"diagnostics_collect","id":"default","include_logs":false}),
        ] {
            let decoded: Request = serde_json::from_value(request).unwrap();
            assert!(!requires_legacy_operation_guard(&decoded));
        }
    }

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
    fn setup_stage_survives_detached_init_and_imports_real_consumers() {
        let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let stage = stage_setup_payload(&repo).unwrap();
        let result = std::process::Command::new("python3")
            .args(["-I", "-B"])
            .arg(repo.join("services/setup-test.py"))
            .arg("--stage")
            .arg(&stage)
            .output()
            .unwrap();
        let _ = std::fs::remove_dir_all(stage);
        assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    }
    #[test]
    fn controller_stage_satisfies_installer_contract_without_the_bundle() {
        // Producer -> stage -> consumer: the staged copy alone must satisfy the
        // real installer, after the original bundle has been deleted.
        let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let bundle = std::env::temp_dir().join(format!("anvildroid-controller-bundle-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&bundle);
        let helper = repo.join("services/controller-stage-test.py");
        let made = std::process::Command::new("python3").args(["-I", "-B"]).arg(&helper)
            .arg("--make-fixture").arg(&bundle).output().unwrap();
        assert!(made.status.success(), "{}", String::from_utf8_lossy(&made.stderr));
        let stage = stage_controller_payload(&bundle).unwrap();
        std::fs::remove_dir_all(&bundle).unwrap();
        let result = std::process::Command::new("python3").args(["-I", "-B"]).arg(&helper)
            .arg("--stage").arg(&stage).output().unwrap();
        let _ = std::fs::remove_dir_all(&stage);
        assert!(result.status.success(), "{}{}", String::from_utf8_lossy(&result.stdout), String::from_utf8_lossy(&result.stderr));
    }
    #[test]
    fn controller_stage_names_missing_files_before_creating_a_stage() {
        let root = std::env::temp_dir().join(format!("anvildroid-stage-missing-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("scripts")).unwrap();
        std::fs::write(root.join("scripts/start-runtime-controller.py"), b"fixture").unwrap();
        let before = std::fs::read_dir(std::env::temp_dir()).unwrap()
            .filter(|e| e.as_ref().unwrap().file_name().to_string_lossy().starts_with("anvildroid-controller-stage-")).count();
        let error = stage_controller_payload(&root).unwrap_err();
        assert!(error.contains("target/native/libanvildroid-path-guard.so"), "{error}");
        assert!(error.contains("native/anvildroid-keymap.rc"), "{error}");
        assert!(error.contains("scripts/install-runtime-apparmor.py"), "{error}");
        assert!(!error.contains("native-x11"), "optional payload reported: {error}");
        let after = std::fs::read_dir(std::env::temp_dir()).unwrap()
            .filter(|e| e.as_ref().unwrap().file_name().to_string_lossy().starts_with("anvildroid-controller-stage-")).count();
        assert!(after <= before, "a stage was left behind");
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
