use std::ffi::OsString;
use std::path::Path;
use std::process::Command;

use crate::error::AnvilError;
use crate::models::{AppInfo, GpuMode};

pub const WAYDROID_PROP: &str = "/var/lib/waydroid/waydroid.prop";
pub const WAYDROID_BASE_PROP: &str = "/var/lib/waydroid/waydroid_base.prop";
const VULKAN_LINE: &str = "ro.hardware.vulkan=radeon";
const GLES_LINE: &str = "ro.hardware.vulkan=";

/// Replace the `ro.hardware.vulkan=` line in prop file content.
/// Returns (new_content, changed). Appends the line if absent.
pub fn apply_vulkan_line(content: &str, enable_vulkan: bool) -> (String, bool) {
    let replacement = if enable_vulkan {
        VULKAN_LINE
    } else {
        GLES_LINE
    };
    let mut changed = false;
    let mut lines: Vec<&str> = content.lines().collect();
    for line in lines.iter_mut() {
        if line.trim_start().starts_with("ro.hardware.vulkan=") && *line != replacement {
            *line = replacement;
            changed = true;
        }
    }
    if !lines
        .iter()
        .any(|l| l.trim_start().starts_with("ro.hardware.vulkan="))
    {
        lines.push(replacement);
        changed = true;
    }
    let trailing = content.ends_with('\n');
    let mut new_content = lines.join("\n");
    if trailing {
        new_content.push('\n');
    }
    (new_content, changed)
}

fn read_prop(path: &str) -> Result<String, AnvilError> {
    std::fs::read_to_string(path)
        .map_err(|e| AnvilError::Io(format!("Cannot read {path}: {e}. Run with sudo if needed.")))
}

fn set_prop(path: &str, content: &str) -> Result<(), AnvilError> {
    std::fs::write(path, content).map_err(|e| {
        AnvilError::Io(format!(
            "Cannot write {path}: {e}. Run with sudo if needed."
        ))
    })
}

/// Current GPU mode from waydroid.prop: true = Vulkan (hardware), false = GLES.
pub fn current_vulkan_mode() -> Result<bool, AnvilError> {
    let content = read_prop(WAYDROID_PROP)?;
    Ok(content.lines().any(|l| l.trim() == VULKAN_LINE))
}

/// Get the current GPU mode as a typed enum.
pub fn gpu_mode() -> Result<GpuMode, AnvilError> {
    current_vulkan_mode().map(|v| if v { GpuMode::Vulkan } else { GpuMode::Gles })
}

/// Apply GPU mode to both prop files.
pub fn apply_gpu_mode(enable_vulkan: bool) -> Result<(), AnvilError> {
    for path in [WAYDROID_PROP, WAYDROID_BASE_PROP] {
        let content = read_prop(path)?;
        let (new_content, changed) = apply_vulkan_line(&content, enable_vulkan);
        if changed {
            set_prop(path, &new_content)?;
        }
    }
    Ok(())
}

/// Handle `gpu` subcommand logic. Returns exit code.
pub fn gpu_command(args: &[OsString]) -> Result<u8, AnvilError> {
    if args.len() > 2 {
        return Err(AnvilError::InvalidArgument(
            "gpu accepts at most one argument: vulkan|gles".into(),
        ));
    }
    match args.get(1).and_then(|s| s.to_str()) {
        None => {
            let vulkan = current_vulkan_mode()?;
            if vulkan {
                println!("GPU mode: vulkan — AMD hardware rendering (fast; ARM32 games may crash on resize/maximize)");
            } else {
                println!(
                    "GPU mode: gles — Vulkan disabled (stable; ARM32 games render on CPU, slower)"
                );
            }
            println!("Change with: anvildroid gpu vulkan|gles (needs session/container restart).");
            Ok(0)
        }
        Some("vulkan") => {
            apply_gpu_mode(true)?;
            println!("GPU mode set to vulkan. Restart to apply:");
            print_restart_instructions();
            Ok(0)
        }
        Some("gles") => {
            apply_gpu_mode(false)?;
            println!("GPU mode set to gles. Restart to apply:");
            print_restart_instructions();
            Ok(0)
        }
        Some(other) => Err(AnvilError::InvalidArgument(format!(
            "unknown gpu mode: {other}; use vulkan or gles"
        ))),
    }
}

/// Handle `window-mode` subcommand logic. Returns exit code.
pub fn window_mode_command(args: &[OsString]) -> Result<u8, AnvilError> {
    if args.len() != 2 {
        return Err(AnvilError::InvalidArgument(
            "window-mode requires exactly one argument: desktop|immersive".into(),
        ));
    }
    let (mode, value) = match args[1].to_str() {
        Some("desktop") => ("desktop", "true"),
        Some("immersive") => ("immersive", "false"),
        Some(other) => {
            return Err(AnvilError::InvalidArgument(format!(
                "unknown window mode: {other}; use desktop or immersive"
            )))
        }
        None => {
            return Err(AnvilError::InvalidArgument(
                "window mode must be UTF-8".into(),
            ))
        }
    };
    let status = Command::new("waydroid")
        .args(["prop", "set", "persist.waydroid.multi_windows", value])
        .status()
        .map_err(|e| {
            AnvilError::BackendNotFound(format!(
                "Cannot execute Waydroid: {e}. See README.md for setup."
            ))
        })?;
    if !status.success() {
        return Ok(status.code().unwrap_or(1).try_into().unwrap_or(1));
    }
    println!("Window mode set to {mode}.");
    println!("Restart to apply:");
    print_restart_instructions();
    if mode == "immersive" {
        println!(
            "Immersive uses one Android display; KDE supplies the desktop header and window buttons."
        );
    } else {
        println!("Desktop keeps Android multi-window captions and multiple app tasks.");
    }
    Ok(0)
}

fn print_restart_instructions() {
    println!("  anvildroid stop");
    println!("  sudo waydroid container stop");
    println!("  sudo waydroid container start   # keep running in one terminal");
    println!("  anvildroid start                # another terminal; wait for Android ready");
}

/// Validate an Android package name.
pub fn valid_package(s: &str) -> bool {
    s.contains('.')
        && s.split('.').all(|part| {
            let mut chars = part.chars();
            chars.next().is_some_and(|c| c.is_ascii_alphabetic())
                && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
        })
}

/// Build backend arguments for forwarded Waydroid commands.
pub fn backend_args(args: &[OsString]) -> Result<Vec<OsString>, AnvilError> {
    let command = args.first().and_then(|s| s.to_str()).unwrap_or("");
    let fixed: &[&str] = match command {
        "status" => &["status"],
        "start" => &["session", "start"],
        "stop" => &["session", "stop"],
        "multi-window" => &["prop", "set", "persist.waydroid.multi_windows", "true"],
        "apps" => &["app", "list"],
        "log" => &["log"],
        "install" | "run" => {
            if args.len() != 2 {
                return Err(AnvilError::InvalidArgument(format!(
                    "{command} requires exactly one argument"
                )));
            }
            let value = if command == "install" {
                let path = Path::new(&args[1]);
                if path.extension().and_then(|s| s.to_str()) != Some("apk") || !path.is_file() {
                    return Err(AnvilError::InvalidArgument(
                        "install requires an existing .apk file".into(),
                    ));
                }
                path.canonicalize()
                    .map_err(|e| AnvilError::Io(e.to_string()))?
                    .into_os_string()
            } else {
                let package = args[1]
                    .to_str()
                    .ok_or_else(|| AnvilError::InvalidArgument("package must be UTF-8".into()))?;
                if !valid_package(package) {
                    return Err(AnvilError::InvalidArgument(
                        "invalid Android package name".into(),
                    ));
                }
                args[1].clone()
            };
            return Ok(vec![
                "app".into(),
                if command == "install" {
                    "install"
                } else {
                    "launch"
                }
                .into(),
                value,
            ]);
        }
        _ => {
            return Err(AnvilError::InvalidArgument(format!(
                "unknown command: {command}; use --help"
            )))
        }
    };
    if args.len() != 1 {
        return Err(AnvilError::InvalidArgument(format!(
            "{command} does not accept extra arguments"
        )));
    }
    Ok(fixed.iter().map(OsString::from).collect())
}

/// Parse the output of `waydroid app list` into structured AppInfo entries.
/// If `icon_dir` is provided, look for `{package}.png` in that directory.
pub fn parse_app_list(output: &str, icon_dir: Option<&Path>) -> Vec<AppInfo> {
    // Waydroid outputs lines like:
    //   Name: Settings
    //   packageName: com.android.settings
    //   className: com.android.settings.Settings
    let mut apps = Vec::new();
    let mut label: Option<String> = None;
    let mut package: Option<String> = None;
    let mut activity: Option<String> = None;

    for line in output.lines() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix("Name:") {
            // Flush previous entry if there's a pending package.
            if let Some(pkg) = package.take() {
                let icon_path = resolve_icon(icon_dir, &pkg);
                apps.push(AppInfo {
                    uninstall_block_reason: uninstall_block_reason(&pkg).map(String::from),
                    package: pkg,
                    label: label.take(),
                    activity: activity.take(),
                    icon_path,
                });
            }
            label = Some(rest.trim().to_string());
        } else if let Some(rest) = line.strip_prefix("packageName:") {
            package = Some(rest.trim().to_string());
        } else if let Some(rest) = line.strip_prefix("className:") {
            activity = Some(rest.trim().to_string());
        }
    }
    // Flush last entry.
    if let Some(pkg) = package {
        let icon_path = resolve_icon(icon_dir, &pkg);
        apps.push(AppInfo {
            uninstall_block_reason: uninstall_block_reason(&pkg).map(String::from),
            package: pkg,
            label,
            activity,
            icon_path,
        });
    }
    apps
}

/// Resolve icon path for a package from Waydroid's icon cache.
fn resolve_icon(icon_dir: Option<&Path>, package: &str) -> Option<String> {
    icon_dir.and_then(|dir| {
        let path = dir.join(format!("{package}.png"));
        if path.is_file() {
            Some(path.to_string_lossy().into_owned())
        } else {
            None
        }
    })
}

/// Waydroid's icon cache directory on the host.
pub fn waydroid_icon_dir() -> Option<std::path::PathBuf> {
    dirs::data_dir().map(|d| d.join("waydroid/data/icons"))
}

/// Library refresh must not start or wake a stopped/frozen session.
/// Waydroid's app-list command unfreezes a frozen container, so skip it there.
pub fn list_apps_if_running() -> Result<Vec<AppInfo>, AnvilError> {
    if crate::runtime::runtime_status().state != crate::models::RuntimeState::Running {
        return Err(AnvilError::BackendFailed(
            "Runtime is not running. Keeping the saved app list; use Start or open an app when needed.".into(),
        ));
    }
    list_apps()
}

/// Run `waydroid app list` and return structured results with icon paths.
pub fn list_apps() -> Result<Vec<AppInfo>, AnvilError> {
    let output = crate::launch::output_timeout(
        Command::new("waydroid").args(["app", "list"]),
        std::time::Duration::from_secs(10),
    )?;
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    if !output.status.success() || crate::runtime::launch_error(&combined).is_some() {
        return Err(AnvilError::BackendFailed(format!(
            "waydroid app list failed: {}",
            combined.trim()
        )));
    }
    let icon_dir = waydroid_icon_dir();
    Ok(parse_app_list(
        &String::from_utf8_lossy(&output.stdout),
        icon_dir.as_deref(),
    ))
}

/// Protect Android and AnvilDroid components from removal through the library.
pub fn uninstall_block_reason(package: &str) -> Option<&'static str> {
    if package == "android"
        || package.starts_with("com.android.")
        || package.starts_with("org.lineageos.")
        || package.starts_with("org.anvildroid.")
        || package.starts_with("id.waydro.")
    {
        Some("Android or AnvilDroid component; uninstall is disabled in the app library.")
    } else {
        None
    }
}

#[derive(Debug, PartialEq)]
pub enum UninstallOutcome {
    Removed,
    AndroidConfirmationRequired,
}

/// Uninstall and verify disappearance, or hand off to Android's confirmation UI.
pub fn uninstall_app(package: &str) -> Result<UninstallOutcome, AnvilError> {
    if !valid_package(package) {
        return Err(AnvilError::InvalidArgument(
            "Invalid Android package name".into(),
        ));
    }
    if let Some(reason) = uninstall_block_reason(package) {
        return Err(AnvilError::InvalidArgument(reason.into()));
    }
    crate::launch::ensure_ready(crate::launch::BOOT_TIMEOUT)?;
    if !list_apps()?.iter().any(|app| app.package == package) {
        return Err(AnvilError::InvalidArgument(format!(
            "{package} is no longer in the app library. Refresh the app list."
        )));
    }
    let output = crate::launch::output_timeout(
        Command::new("waydroid").args(["app", "remove", package]),
        std::time::Duration::from_secs(30),
    )?;
    let combined = format!(
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    // Waydroid's removeApp reads this from the Binder exception header, NOT
    // PackageManager's DELETE_* return code. Its Android 13 implementation
    // builds a PendingIntent without a mutability flag and throws -3.
    if combined
        .lines()
        .any(|line| line.trim_end().ends_with("Failed with code: -3"))
    {
        crate::launch::request_android_uninstall(package)?;
        return Ok(UninstallOutcome::AndroidConfirmationRequired);
    }
    crate::launch::check_output(output)?;
    for _ in 0..3 {
        if !list_apps()?.iter().any(|app| app.package == package) {
            return Ok(UninstallOutcome::Removed);
        }
        std::thread::sleep(std::time::Duration::from_millis(300));
    }
    Err(AnvilError::BackendFailed(format!(
        "Removal of {package} could not be confirmed: the app is still listed."
    )))
}

/// Run doctor checks. Returns exit code.
pub fn doctor() -> Result<u8, AnvilError> {
    println!("AnvilDroid preliminary checks (this process's environment)");
    println!("Architecture: {}", std::env::consts::ARCH);
    println!(
        "Session: {}",
        std::env::var("XDG_SESSION_TYPE").unwrap_or_else(|_| "unknown".into())
    );
    let socket = std::env::var_os("WAYLAND_DISPLAY").and_then(|display| {
        let path = std::path::PathBuf::from(display);
        if path.is_absolute() {
            Some(path)
        } else {
            std::env::var_os("XDG_RUNTIME_DIR").map(|dir| std::path::PathBuf::from(dir).join(path))
        }
    });
    let wayland = socket.as_ref().is_some_and(|p| {
        p.metadata()
            .is_ok_and(|m| std::os::unix::fs::FileTypeExt::is_socket(&m.file_type()))
    });
    println!(
        "Wayland socket: {} ({socket:?}; connectivity not tested)",
        if wayland { "FOUND" } else { "NOT FOUND" }
    );
    let x_display = std::env::var("DISPLAY").ok();
    let x11 = x_display
        .as_deref()
        .is_some_and(|d| crate::controller::x11_display(d).is_some());
    println!(
        "X11 display: {} ({})",
        x_display.as_deref().unwrap_or("unset"),
        if x11 { "FOUND" } else { "NOT FOUND" }
    );
    let render_nodes: Vec<_> = std::fs::read_dir("/dev/dri")
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .filter(|e| e.file_name().to_string_lossy().starts_with("renderD"))
        .map(|e| e.path())
        .collect();
    println!("Visible DRM render nodes: {render_nodes:?}");
    println!("GPU rendering/Binder/container isolation: UNVERIFIED (missing devices may reflect sandbox restrictions)");
    let output = match Command::new("waydroid").arg("--version").output() {
        Ok(output) => output,
        Err(e) => {
            return Err(AnvilError::BackendNotFound(format!(
                "Cannot execute Waydroid: {e}. Install backend as described in README.md"
            )))
        }
    };
    println!(
        "Waydroid: {}{}",
        String::from_utf8_lossy(&output.stdout).trim(),
        String::from_utf8_lossy(&output.stderr).trim()
    );
    if !output.status.success() {
        return Err(AnvilError::BackendFailed(
            "Waydroid version check failed".into(),
        ));
    }
    println!("Runtime/app compatibility: UNVERIFIED; next run status and the README smoke test.");
    Ok(if wayland || x11 { 0 } else { 1 })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn apply_vulkan_line_replaces_and_appends() {
        let (out, changed) = apply_vulkan_line("a=1\nro.hardware.vulkan=radeon\nb=2\n", false);
        assert!(changed);
        assert_eq!(out, "a=1\nro.hardware.vulkan=\nb=2\n");
        let (out, changed) = apply_vulkan_line("a=1\nro.hardware.vulkan=\n", true);
        assert!(changed);
        assert_eq!(out, "a=1\nro.hardware.vulkan=radeon\n");
        let (out, changed) = apply_vulkan_line("a=1\n", true);
        assert!(changed);
        assert_eq!(out, "a=1\nro.hardware.vulkan=radeon\n");
        let (_, changed) = apply_vulkan_line("ro.hardware.vulkan=radeon\n", true);
        assert!(!changed);
    }

    #[test]
    fn valid_package_accepts_and_rejects() {
        assert!(valid_package("com.android.settings"));
        assert!(valid_package("com.zeptolab.ctr.ads"));
        assert!(!valid_package("--help"));
        assert!(!valid_package("com.test;id"));
        assert!(!valid_package("com..test"));
        assert!(!valid_package("com.test/foo"));
        assert!(!valid_package("com.2test"));
        assert!(!valid_package(""));
    }

    #[test]
    fn backend_args_rejects_invalid() {
        let args = |v: &[&str]| -> Vec<OsString> { v.iter().map(OsString::from).collect() };
        assert!(backend_args(&args(&["run", "--help"])).is_err());
        assert!(backend_args(&args(&["start", "--root"])).is_err());
        assert!(backend_args(&args(&["run"])).is_err());
        assert!(backend_args(&args(&["reset"])).is_err());

        assert_eq!(
            backend_args(&args(&["run", "com.android.settings"])).unwrap(),
            args(&["app", "launch", "com.android.settings"])
        );
    }

    #[test]
    fn parse_app_list_works() {
        let output = "Name: Settings\npackageName: com.android.settings\nclassName: com.android.settings.Settings\n\nName: Gallery\npackageName: com.android.gallery3d\nclassName: com.android.gallery3d.app.GalleryActivity\n";
        let apps = parse_app_list(output, None);
        assert_eq!(apps.len(), 2);
        assert_eq!(apps[0].package, "com.android.settings");
        assert_eq!(apps[0].label.as_deref(), Some("Settings"));
        assert!(apps[0].icon_path.is_none());
        assert_eq!(apps[1].package, "com.android.gallery3d");
    }

    #[test]
    fn gpu_rejects_bad_arguments() {
        let args = |v: &[&str]| -> Vec<OsString> { v.iter().map(OsString::from).collect() };
        assert!(gpu_command(&args(&["gpu", "foo"])).is_err());
        assert!(gpu_command(&args(&["gpu", "vulkan", "extra"])).is_err());
    }

    #[test]
    fn window_mode_rejects_ambiguous_values() {
        let args = |v: &[&str]| -> Vec<OsString> { v.iter().map(OsString::from).collect() };
        assert!(window_mode_command(&args(&["window-mode"])).is_err());
        assert!(window_mode_command(&args(&["window-mode", "multi"])).is_err());
        assert!(window_mode_command(&args(&["window-mode", "desktop", "extra"])).is_err());
    }
}
