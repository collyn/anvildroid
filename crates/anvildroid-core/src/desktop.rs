//! User application-menu entries owned by AnvilDroid, separate from Waydroid's.
use crate::{backend, error::AnvilError, launch, models::AppInfo};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

const OWNER: &str = "X-AnvilDroid-Managed=true";
static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

pub(crate) fn installed_launcher(path: &Path) -> std::io::Result<PathBuf> {
    match path.canonicalize() {
        Ok(path) => Ok(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            // Linux appends this suffix to /proc/self/exe when a package
            // upgrade replaces the executable while the GUI is still open.
            if let Some(replacement) = path.to_str().and_then(|p| p.strip_suffix(" (deleted)")) {
                let replacement = Path::new(replacement);
                if replacement.is_absolute() && replacement.is_file() {
                    return replacement.canonicalize();
                }
            }
            Err(error)
        }
        Err(error) => Err(error),
    }
}

fn entry_path(data_dir: &Path, package: &str) -> Result<PathBuf, AnvilError> {
    if !backend::valid_package(package) {
        return Err(AnvilError::InvalidArgument(
            "Invalid Android package name".into(),
        ));
    }
    Ok(data_dir
        .join("applications")
        .join(format!("anvildroid.{package}.desktop")))
}

fn data_dir() -> Result<PathBuf, AnvilError> {
    dirs::data_dir()
        .filter(|p| p.is_absolute())
        .ok_or_else(|| AnvilError::Io("Cannot locate an absolute XDG data directory".into()))
}

// Desktop Entry value escaping is applied before Exec argument unquoting.
// https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html
fn value(text: &str) -> String {
    text.replace('\\', "\\\\")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t")
}

pub(crate) fn exec_path(path: &Path) -> Result<String, AnvilError> {
    let text = path
        .to_str()
        .filter(|_| path.is_absolute())
        .ok_or_else(|| {
            AnvilError::InvalidArgument("Shortcut launcher must have an absolute UTF-8 path".into())
        })?;
    if text.contains('=') || text.chars().any(char::is_control) {
        return Err(AnvilError::InvalidArgument(
            "Launcher path contains characters unsupported by desktop entries".into(),
        ));
    }
    let mut quoted = String::from("\"");
    for c in text.chars() {
        match c {
            '\\' | '"' | '$' | '`' => {
                quoted.push('\\');
                quoted.push(c);
            }
            '%' => quoted.push_str("%%"),
            _ => quoted.push(c),
        }
    }
    quoted.push('"');
    Ok(value(&quoted))
}

fn entry(app: &AppInfo, launcher: &Path) -> Result<String, AnvilError> {
    if !backend::valid_package(&app.package) {
        return Err(AnvilError::InvalidArgument(
            "Invalid Android package name".into(),
        ));
    }
    let executable = exec_path(launcher)?;
    let name = value(
        app.label
            .as_deref()
            .filter(|v| !v.trim().is_empty())
            .unwrap_or(&app.package),
    );
    let icon = value(
        app.icon_path
            .as_deref()
            .unwrap_or("application-x-executable"),
    );
    Ok(format!("[Desktop Entry]\nType=Application\nVersion=1.0\nName={name} (AnvilDroid)\nComment=Open Android app through AnvilDroid\nExec={executable} --launch-app {} --runtime default\nIcon={icon}\nTerminal=false\nCategories=Utility;\nStartupNotify=false\nX-AnvilDroid-Runtime=default\nStartupWMClass=waydroid.{}\n{OWNER}\nActions=app-info;\n\n[Desktop Action app-info]\nName=Android App Info\nExec={executable} --app-info {} --runtime default\n", app.package, app.package, app.package))
}

fn check_owned(path: &Path) -> Result<(), AnvilError> {
    match fs::symlink_metadata(path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.into()),
        Ok(meta) if !meta.is_file() || meta.file_type().is_symlink() => {
            Err(AnvilError::Io(format!(
                "Refusing to replace non-regular shortcut: {}",
                path.display()
            )))
        }
        Ok(_) => {
            if fs::read_to_string(path)?.lines().any(|line| line == OWNER) {
                Ok(())
            } else {
                Err(AnvilError::Io(format!(
                    "Shortcut is not managed by AnvilDroid: {}",
                    path.display()
                )))
            }
        }
    }
}

fn write_entry(data_dir: &Path, app: &AppInfo, launcher: &Path) -> Result<PathBuf, AnvilError> {
    let path = entry_path(data_dir, &app.package)?;
    let content = entry(app, launcher)?;
    write_content(path, &content)
}

pub(crate) fn write_content(path: PathBuf, content: &str) -> Result<PathBuf, AnvilError> {
    fs::create_dir_all(path.parent().unwrap())?;
    check_owned(&path)?;
    write_atomic(path, content)
}

fn write_atomic(path: PathBuf, content: &str) -> Result<PathBuf, AnvilError> {
    let temp = path.with_extension(format!(
        "desktop.{}.{}.tmp",
        std::process::id(),
        NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
    ));
    let result = (|| -> Result<(), AnvilError> {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)?;
        file.write_all(content.as_bytes())?;
        file.sync_all()?;
        fs::rename(&temp, &path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result?;
    Ok(path)
}

pub fn create_shortcut(package: &str, launcher: &Path) -> Result<PathBuf, AnvilError> {
    if !backend::valid_package(package) {
        return Err(AnvilError::InvalidArgument(
            "Invalid Android package name".into(),
        ));
    }
    let launcher = installed_launcher(launcher)?;
    launch::ensure_ready(launch::BOOT_TIMEOUT)?;
    let app = backend::list_apps()?
        .into_iter()
        .find(|app| app.package == package)
        .ok_or_else(|| {
            AnvilError::InvalidArgument(format!(
                "{package} is no longer installed. Refresh the app list."
            ))
        })?;
    let path = write_entry(&data_dir()?, &app, &launcher)?;
    refresh_menu_cache();
    Ok(path)
}

/// Build only from the authenticated controller inventory, never UI-provided metadata.
pub fn create_managed_shortcut(
    id: &str,
    package: &str,
    launcher: &Path,
) -> Result<PathBuf, AnvilError> {
    let path = write_managed_registration(id, package, launcher, false)?;
    refresh_menu_cache();
    Ok(path)
}

/// Wayland resolves taskbar icons by the exact app_id desktop filename.
pub fn register_managed_window(id: &str, package: &str, launcher: &Path) -> Result<PathBuf, AnvilError> {
    write_managed_registration(id, package, launcher, true)
}

fn write_managed_registration(id: &str, package: &str, launcher: &Path, hidden: bool) -> Result<PathBuf, AnvilError> {
    use crate::controller::{request, Request};
    use base64::Engine;
    crate::managed::validate(id, package)?;
    let record = request(Request::Refresh { id: id.into() }).map_err(AnvilError::BackendFailed)?;
    let apps = request(Request::Apps { id: id.into() }).map_err(AnvilError::BackendFailed)?;
    let app = apps["apps"]
        .as_array()
        .and_then(|apps| {
            apps.iter()
                .find(|app| app["package"] == package && app["launchable"] == true)
        })
        .ok_or_else(|| {
            AnvilError::InvalidArgument(
                "Start this runtime and refresh its app list before creating a shortcut.".into(),
            )
        })?;
    let root = data_dir()?;
    let mut icon = "application-x-executable".to_owned();
    if let Some(encoded) = app["icon_data"].as_str().filter(|s| s.len() <= 180_000) {
        if let Ok(bytes) = base64::engine::general_purpose::STANDARD.decode(encoded) {
            if bytes.starts_with(b"\x89PNG\r\n\x1a\n") && bytes.len() <= 128 * 1024 {
                let folder = root.join("anvildroid/shortcut-icons").join(id);
                fs::create_dir_all(&folder)?;
                let path = folder.join(format!("{package}.png"));
                // Atomic replacement does not follow an existing destination symlink.
                let temp = folder.join(format!(
                    ".{package}.{}.{}.tmp",
                    std::process::id(),
                    NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
                ));
                let result = (|| -> Result<(), AnvilError> {
                    let mut file = fs::OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .open(&temp)?;
                    file.write_all(&bytes)?;
                    file.sync_all()?;
                    fs::rename(&temp, &path)?;
                    Ok(())
                })();
                if result.is_err() {
                    let _ = fs::remove_file(&temp);
                }
                result?;
                icon = path.to_string_lossy().into_owned();
            }
        }
    }
    let mut content = managed_entry(
        id,
        package,
        app["label"].as_str().unwrap_or(package),
        record["name"].as_str().unwrap_or(id),
        &icon,
        &installed_launcher(launcher)?,
    )?;
    if hidden { content.push_str("NoDisplay=true\n"); }
    let filename = if hidden {
        format!("waydroid.anvildroid.{id}.{package}.desktop")
    } else {
        format!("anvildroid.{id}.{package}.desktop")
    };
    write_content(
        root.join("applications")
            .join(filename),
        &content,
    )
}

fn managed_entry(
    id: &str,
    package: &str,
    label: &str,
    name: &str,
    icon: &str,
    launcher: &Path,
) -> Result<String, AnvilError> {
    crate::managed::validate(id, package)?;
    Ok(format!("[Desktop Entry]\nType=Application\nVersion=1.0\nName={} ({} · AnvilDroid)\nExec={} --launch-app {package} --runtime {id}\nIcon={}\nTerminal=false\nCategories=Utility;\nStartupNotify=false\nX-AnvilDroid-Runtime={id}\nStartupWMClass=waydroid.anvildroid.{id}.{package}\n{OWNER}\n", value(label), value(name), exec_path(launcher)?, value(icon)))
}

/// Remove only our launcher after confirmed uninstall. Never touches Waydroid's.
pub fn remove_shortcut(package: &str) -> Result<(), AnvilError> {
    remove_owned_shortcut(&entry_path(&data_dir()?, package)?)?;
    refresh_menu_cache();
    Ok(())
}

fn scoped_shortcut_path(root: &Path, id: &str, package: &str) -> Result<PathBuf, AnvilError> {
    if id == "default" {
        return entry_path(root, package);
    }
    crate::managed::validate(id, package)?;
    Ok(root.join("applications").join(format!("anvildroid.{id}.{package}.desktop")))
}

fn owned_shortcut_exists(path: &Path) -> Result<bool, AnvilError> {
    match fs::symlink_metadata(path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(e.into()),
        Ok(meta) if !meta.is_file() || meta.file_type().is_symlink() => Ok(false),
        Ok(_) => Ok(fs::read_to_string(path)?.lines().any(|line| line == OWNER)),
    }
}

pub fn has_runtime_shortcut(id: &str, package: &str) -> Result<bool, AnvilError> {
    let owned = owned_shortcut_exists(&scoped_shortcut_path(&data_dir()?, id, package)?)?;
    if owned || id != "default" { return Ok(owned); }
    Ok(native_shortcut(&data_dir()?, package)?.is_some_and(|(_, text)| desktop_visible(&text)))
}

/// Remove the selected runtime's visible menu entry; retain taskbar registrations.
pub fn remove_runtime_shortcut(id: &str, package: &str) -> Result<(), AnvilError> {
    let path = scoped_shortcut_path(&data_dir()?, id, package)?;
    check_owned(&path)?;
    if id == "default" {
        crate::shortcut_preferences::hide(package)?;
        set_native_visibility(&data_dir()?, package, false)?;
    }
    remove_owned_shortcut(&path)?;
    refresh_menu_cache();
    Ok(())
}

/// XDG desktops normally watch application directories. KDE additionally caches
/// service entries in sycoca; explicitly invalidate it after a user menu change.
pub(crate) fn refresh_menu_cache() {
    use std::sync::atomic::AtomicBool;
    static GENERATION: AtomicU64 = AtomicU64::new(0);
    static RUNNING: AtomicBool = AtomicBool::new(false);
    if !std::env::var("XDG_CURRENT_DESKTOP").unwrap_or_default().split(':').any(|name| name.eq_ignore_ascii_case("KDE")) { return; }
    GENERATION.fetch_add(1, Ordering::SeqCst);
    if RUNNING.swap(true, Ordering::SeqCst) { return; }
    std::thread::spawn(|| loop {
        let generation = GENERATION.load(Ordering::SeqCst);
        let run = |name| std::process::Command::new(name).arg("--noincremental")
            .stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).status();
        match run("kbuildsycoca6") {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => { let _ = run("kbuildsycoca5"); }
            _ => {}
        }
        RUNNING.store(false, Ordering::SeqCst);
        if GENERATION.load(Ordering::SeqCst) == generation || RUNNING.swap(true, Ordering::SeqCst) { break; }
    });
}

fn desktop_field<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    let mut main = false;
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with('[') { main = line == "[Desktop Entry]"; }
        if main {
            if let Some(value) = line.strip_prefix(key).and_then(|v| v.strip_prefix('=')) { return Some(value); }
        }
    }
    None
}

pub(crate) fn desktop_visible(text: &str) -> bool {
    desktop_field(text, "NoDisplay") != Some("true") && desktop_field(text, "Hidden") != Some("true")
}

pub(crate) fn native_shortcut(root: &Path, package: &str) -> Result<Option<(PathBuf, String)>, AnvilError> {
    if !backend::valid_package(package) { return Err(AnvilError::InvalidArgument("Invalid package".into())); }
    let path = root.join("applications").join(format!("waydroid.{package}.desktop"));
    match fs::symlink_metadata(&path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
        Ok(meta) if !meta.is_file() || meta.file_type().is_symlink() => return Ok(None),
        _ => {}
    }
    let text = fs::read_to_string(&path)?;
    let expected = format!("waydroid app launch {package}");
    if desktop_field(&text, "Type") != Some("Application") || desktop_field(&text, "Exec") != Some(expected.as_str()) ||
        !desktop_field(&text, "Categories").is_some_and(|v| v.split(';').any(|c| c == "X-WayDroid-App")) {
        return Ok(None);
    }
    Ok(Some((path, text)))
}

pub(crate) fn set_native_visibility(root: &Path, package: &str, visible: bool) -> Result<(), AnvilError> {
    let Some((path, text)) = native_shortcut(root, package)? else { return Ok(()); };
    if desktop_visible(&text) == visible { return Ok(()); }
    let mut lines = Vec::new();
    let mut main = false;
    for line in text.lines() {
        if line.starts_with('[') { main = line.trim() == "[Desktop Entry]"; }
        if main && line.trim().starts_with("NoDisplay=") { continue; }
        lines.push(line.to_owned());
        if line.trim() == "[Desktop Entry]" { lines.push(format!("NoDisplay={}", !visible)); }
    }
    write_atomic(path, &(lines.join("\n") + "\n"))?;
    Ok(())
}

fn remove_owned_shortcut(path: &Path) -> Result<(), AnvilError> {
    check_owned(path)?;
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_waydroid_shortcuts_are_recognized_and_hidden_without_losing_actions() {
        let root = std::env::temp_dir().join(format!("anvil-native-shortcut-{}", std::process::id()));
        fs::create_dir_all(root.join("applications")).unwrap();
        let package = "com.example.xemphim";
        let path = root.join("applications").join(format!("waydroid.{package}.desktop"));
        fs::write(&path, format!("[Desktop Entry]\nType=Application\nName=XemPhim\nExec=waydroid app launch {package}\nCategories=X-WayDroid-App;\nActions=app-settings;\n\n[Desktop Action app-settings]\nName=App Settings\nExec=waydroid app intent android.settings.APPLICATION_DETAILS_SETTINGS package:{package}\n")).unwrap();
        let (_, text) = native_shortcut(&root, package).unwrap().unwrap();
        assert!(desktop_visible(&text));
        set_native_visibility(&root, package, false).unwrap();
        let (_, text) = native_shortcut(&root, package).unwrap().unwrap();
        assert!(!desktop_visible(&text));
        assert!(text.contains("[Desktop Action app-settings]\nName=App Settings"));
        assert!(text.contains(&format!("Exec=waydroid app launch {package}")));
        fs::write(&path, "[Desktop Entry]\nType=Application\nExec=custom-launcher\nCategories=X-WayDroid-App;\n").unwrap();
        assert!(native_shortcut(&root, package).unwrap().is_none());
        set_native_visibility(&root, package, false).unwrap();
        assert!(!fs::read_to_string(&path).unwrap().contains("NoDisplay"));
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn shortcut_removal_is_scoped_and_preserves_other_entries() {
        let root = std::env::temp_dir().join(format!("anvildroid-shortcut-removal-{}", std::process::id()));
        let id = "r-11111111111111111111111111111111";
        let other = "r-22222222222222222222222222222222";
        let package = "com.example.game";
        let default = scoped_shortcut_path(&root, "default", package).unwrap();
        let managed = scoped_shortcut_path(&root, id, package).unwrap();
        let second = scoped_shortcut_path(&root, other, package).unwrap();
        let hidden = root.join("applications").join(format!("waydroid.anvildroid.{id}.{package}.desktop"));
        for path in [&default, &managed, &second, &hidden] {
            write_content(path.clone(), &format!("[Desktop Entry]\n{OWNER}\n")).unwrap();
        }
        assert!(owned_shortcut_exists(&managed).unwrap());
        remove_owned_shortcut(&managed).unwrap();
        assert!(!owned_shortcut_exists(&managed).unwrap());
        remove_owned_shortcut(&managed).unwrap(); // Idempotent after an external deletion.
        assert!(default.exists() && second.exists() && hidden.exists());
        fs::write(&managed, "[Desktop Entry]\nName=User shortcut\n").unwrap();
        assert!(!owned_shortcut_exists(&managed).unwrap());
        assert!(remove_owned_shortcut(&managed).is_err());
        assert!(managed.exists());
        fs::remove_file(&managed).unwrap();
        std::os::unix::fs::symlink(&second, &managed).unwrap();
        assert!(!owned_shortcut_exists(&managed).unwrap());
        assert!(remove_owned_shortcut(&managed).is_err());
        assert!(second.exists());
        assert!(scoped_shortcut_path(&root, "../../bad", package).is_err());
        assert!(scoped_shortcut_path(&root, id, "../../bad").is_err());
        remove_owned_shortcut(&default).unwrap();
        assert!(!owned_shortcut_exists(&default).unwrap());
        fs::remove_dir_all(root).unwrap();
    }
    fn app() -> AppInfo {
        AppInfo {
            package: "com.example.game".into(),
            label: Some("Game\nHidden=true".into()),
            activity: None,
            icon_path: None,
            uninstall_block_reason: None,
        }
    }
    #[test]
    fn managed_entries_pin_identity_and_escape_names() {
        let id = "r-11111111111111111111111111111111";
        let other = "r-22222222222222222222222222222222";
        let text = managed_entry(
            id,
            "com.example.app",
            "Calculator\nHidden=true",
            "Runtime 2",
            "application-x-executable",
            Path::new("/tmp/gui"),
        )
        .unwrap();
        assert!(text.contains(&format!("--launch-app com.example.app --runtime {id}\n")));
        assert!(text.contains(&format!(
            "StartupWMClass=waydroid.anvildroid.{id}.com.example.app"
        )));
        assert!(text.contains("Runtime 2 · AnvilDroid"));
        assert!(!text.lines().any(|line| line == "Hidden=true"));
        assert!(!text.contains("--runtime default"));
        assert_ne!(
            text,
            managed_entry(
                other,
                "com.example.app",
                "Calculator",
                "Runtime 1",
                "application-x-executable",
                Path::new("/tmp/gui")
            )
            .unwrap()
        );
        assert!(managed_entry(
            "../../bad",
            "com.example.app",
            "x",
            "x",
            "x",
            Path::new("/tmp/gui")
        )
        .is_err());
    }
    #[test]
    fn resolves_replaced_executable_after_package_upgrade() {
        let exe = std::env::current_exe().unwrap();
        let deleted = PathBuf::from(format!("{} (deleted)", exe.display()));
        assert_eq!(installed_launcher(&deleted).unwrap(), exe.canonicalize().unwrap());
        let missing = exe.with_file_name("missing-anvildroid-launcher (deleted)");
        assert!(installed_launcher(&missing).is_err());
    }
    #[test]
    fn escapes_labels_and_exec_without_a_shell() {
        let text = entry(&app(), Path::new("/tmp/My apps/$bin`x`\\\"%.exe")).unwrap();
        assert!(text.contains("Name=Game\\nHidden=true (AnvilDroid)\n"));
        assert!(!text.lines().any(|line| line == "Hidden=true"));
        assert!(text.contains("\\\\$bin\\\\`x\\\\`"));
        assert!(text.contains("%%.exe"));
        assert!(text.contains("--launch-app com.example.game --runtime default\n"));
        assert!(entry_path(Path::new("/tmp"), "../../evil").is_err());
    }
    #[test]
    fn updates_one_owned_entry_and_preserves_foreign_files() {
        let dir =
            std::env::temp_dir().join(format!("anvildroid-shortcut-test-{}", std::process::id()));
        let a = app();
        let path = write_entry(&dir, &a, Path::new("/tmp/anvildroid-gui")).unwrap();
        write_entry(&dir, &a, Path::new("/tmp/new-gui")).unwrap();
        assert_eq!(fs::read_dir(dir.join("applications")).unwrap().count(), 1);
        assert!(fs::read_to_string(&path).unwrap().contains("/tmp/new-gui"));
        fs::write(&path, "[Desktop Entry]\nName=Custom\n").unwrap();
        assert!(write_entry(&dir, &a, Path::new("/tmp/gui")).is_err());
        assert!(fs::read_to_string(path).unwrap().contains("Name=Custom"));
        fs::remove_dir_all(dir).unwrap();
    }
}
