//! Persistent policy for Waydroid's automatically generated menu entries.
use crate::{config, desktop, error::AnvilError};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    path::Path,
    process::Command,
};

#[derive(Default, Serialize, Deserialize)]
#[serde(default)]
struct Preferences {
    #[serde(default = "enabled")]
    auto_create: bool,
    visibility: BTreeMap<String, bool>,
}
fn enabled() -> bool {
    true
}
fn read(dir: &Path) -> Result<Preferences, AnvilError> {
    match fs::read(dir.join("shortcuts.json")) {
        Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| AnvilError::Config(e.to_string())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Preferences {
            auto_create: true,
            visibility: BTreeMap::new(),
        }),
        Err(e) => Err(e.into()),
    }
}
fn mutate(
    dir: &Path,
    apply: impl FnOnce(&mut Preferences) -> Result<(), AnvilError>,
) -> Result<(), AnvilError> {
    fs::create_dir_all(dir)?;
    let lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(dir.join("shortcuts.lock"))?;
    if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let mut prefs = read(dir)?;
    let before = serde_json::to_vec(&prefs).map_err(|e| AnvilError::Config(e.to_string()))?;
    apply(&mut prefs)?;
    let after = serde_json::to_vec(&prefs).map_err(|e| AnvilError::Config(e.to_string()))?;
    if before != after || !dir.join("shortcuts.json").exists() {
        fs::write(dir.join(".shortcuts.json.tmp"), after)?;
        fs::rename(dir.join(".shortcuts.json.tmp"), dir.join("shortcuts.json"))?;
    }
    Ok(())
}
fn inventory(root: &Path) -> Result<Vec<(String, bool)>, AnvilError> {
    let mut entries = Vec::new();
    for entry in fs::read_dir(root.join("applications"))? {
        let name = entry?.file_name().to_string_lossy().into_owned();
        let Some(package) = name
            .strip_prefix("waydroid.")
            .and_then(|n| n.strip_suffix(".desktop"))
        else {
            continue;
        };
        if !crate::backend::valid_package(package) {
            continue;
        }
        if let Some((_, text)) = desktop::native_shortcut(root, package)? {
            entries.push((package.to_owned(), desktop::desktop_visible(&text)));
        }
    }
    Ok(entries)
}
pub fn automatic() -> Result<bool, AnvilError> {
    Ok(read(&config::config_dir())?.auto_create)
}
pub fn set_automatic(value: bool) -> Result<(), AnvilError> {
    let root = dirs::data_dir().ok_or_else(|| AnvilError::Io("Missing data directory".into()))?;
    fs::create_dir_all(root.join("applications"))?;
    mutate(&config::config_dir(), |prefs| {
        // Keep current entries as they are. The new policy applies to new apps.
        for (package, visible) in inventory(&root)? {
            prefs.visibility.entry(package).or_insert(visible);
        }
        prefs.auto_create = value;
        Ok(())
    })
}
pub fn hide(package: &str) -> Result<(), AnvilError> {
    if !crate::backend::valid_package(package) {
        return Err(AnvilError::InvalidArgument("Invalid package".into()));
    }
    mutate(&config::config_dir(), |prefs| {
        prefs.visibility.insert(package.into(), false);
        Ok(())
    })
}
fn reconcile(dir: &Path, root: &Path) -> Result<bool, AnvilError> {
    let mut changed = false;
    mutate(dir, |prefs| {
        for (package, visible) in inventory(root)? {
            let desired = *prefs
                .visibility
                .entry(package.clone())
                .or_insert(visible && prefs.auto_create);
            // Do not unhide entries that Android/Waydroid intentionally hides.
            if !desired && visible {
                desktop::set_native_visibility(root, &package, false)?;
                changed = true;
            }
        }
        Ok(())
    })?;
    Ok(changed)
}
/// A user service watches filesystem events; it does no periodic polling/Android work.
pub fn monitor() -> Result<(), AnvilError> {
    let dir = config::config_dir();
    let root = dirs::data_dir().ok_or_else(|| AnvilError::Io("Missing data directory".into()))?;
    fs::create_dir_all(&dir)?;
    let instance = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(dir.join("shortcut-monitor.lock"))?;
    if unsafe { libc::flock(instance.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        let error = std::io::Error::last_os_error();
        if error.kind() == std::io::ErrorKind::WouldBlock {
            return Ok(());
        }
        return Err(error.into());
    }
    fs::create_dir_all(root.join("applications"))?;
    let fd = unsafe { libc::inotify_init1(libc::IN_CLOEXEC) };
    if fd < 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    let mut watches = Vec::new();
    for path in [&dir, &root.join("applications")] {
        use std::os::unix::ffi::OsStrExt;
        let path = std::ffi::CString::new(path.as_os_str().as_bytes())
            .map_err(|e| AnvilError::Io(e.to_string()))?;
        let watch = unsafe {
            libc::inotify_add_watch(
                fd.as_raw_fd(),
                path.as_ptr(),
                libc::IN_CLOSE_WRITE | libc::IN_MOVED_TO | libc::IN_DELETE,
            )
        };
        if watch < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        watches.push(watch);
    }
    if reconcile(&dir, &root)? {
        desktop::refresh_menu_cache();
    }
    let mut buffer = [0u8; 16384];
    loop {
        let count = unsafe { libc::read(fd.as_raw_fd(), buffer.as_mut_ptr().cast(), buffer.len()) };
        if count < 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return Err(error.into());
        }
        let mut relevant = false;
        let mut offset = 0;
        while offset + std::mem::size_of::<libc::inotify_event>() <= count as usize {
            let event = unsafe {
                std::ptr::read_unaligned(buffer.as_ptr().add(offset).cast::<libc::inotify_event>())
            };
            let start = offset + std::mem::size_of::<libc::inotify_event>();
            let end = start + event.len as usize;
            if end > count as usize {
                break;
            }
            let name = &buffer[start..end];
            let name = std::str::from_utf8(
                &name[..name.iter().position(|b| *b == 0).unwrap_or(name.len())],
            )
            .unwrap_or("");
            relevant |= event.mask & libc::IN_Q_OVERFLOW != 0
                || (event.wd == watches[0] && name == "shortcuts.json")
                || (event.wd == watches[1]
                    && name.starts_with("waydroid.")
                    && name.ends_with(".desktop"));
            offset = end;
        }
        if relevant {
            match reconcile(&dir, &root) {
                Ok(true) => desktop::refresh_menu_cache(),
                Err(e) => eprintln!("Shortcut policy: {e}"),
                _ => {}
            }
        }
    }
}
pub fn ensure_service(launcher: &Path) -> Result<(), AnvilError> {
    let launcher = desktop::installed_launcher(launcher)?;
    if let Err(error) = setup_systemd(&launcher) {
        eprintln!("Shortcut systemd service unavailable; using XDG Autostart: {error}");
        let dir = dirs::config_dir()
            .ok_or_else(|| AnvilError::Io("Missing config directory".into()))?
            .join("autostart");
        let content = format!("[Desktop Entry]\nType=Application\nName=AnvilDroid shortcut preferences\nExec={} --shortcut-monitor\nNoDisplay=true\nX-AnvilDroid-Managed=true\n", desktop::exec_path(&launcher)?);
        desktop::write_content(dir.join("anvildroid-shortcuts.desktop"), &content)?;
        use std::os::unix::process::CommandExt;
        let mut child = Command::new(&launcher)
            .arg("--shortcut-monitor")
            .process_group(0)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()?;
        std::thread::spawn(move || {
            let _ = child.wait();
        });
    }
    Ok(())
}

fn setup_systemd(launcher: &Path) -> Result<(), AnvilError> {
    let dir = dirs::config_dir()
        .ok_or_else(|| AnvilError::Io("Missing config directory".into()))?
        .join("systemd/user");
    fs::create_dir_all(&dir)?;
    let path = launcher
        .to_str()
        .filter(|s| launcher.is_absolute() && !s.chars().any(char::is_control))
        .ok_or_else(|| AnvilError::InvalidArgument("Invalid shortcut monitor executable".into()))?;
    let escaped = path
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('%', "%%")
        .replace('$', "$$");
    let unit = format!("[Unit]\nDescription=AnvilDroid shortcut preferences\n[Service]\nExecStart=\"{escaped}\" --shortcut-monitor\nRestart=on-failure\nRestartSec=3\n[Install]\nWantedBy=default.target\n");
    let file = dir.join("anvildroid-shortcuts.service");
    if fs::read_to_string(&file).ok().as_deref() != Some(&unit) {
        fs::write(&file, unit)?;
    }
    for args in [
        vec!["--user", "daemon-reload"],
        vec!["--user", "enable", "--now", "anvildroid-shortcuts.service"],
    ] {
        let result = Command::new("systemctl").args(args).output()?;
        if !result.status.success() {
            return Err(AnvilError::Io(
                String::from_utf8_lossy(&result.stderr).trim().into(),
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn automatic_policy_preserves_existing_and_hides_new_and_removed_apps() {
        let temp =
            std::env::temp_dir().join(format!("anvil-shortcut-policy-{}", std::process::id()));
        let root = temp.join("data");
        let dir = temp.join("config");
        fs::create_dir_all(root.join("applications")).unwrap();
        let write = |package: &str| {
            fs::write(root.join("applications").join(format!("waydroid.{package}.desktop")),
            format!("[Desktop Entry]\nType=Application\nExec=waydroid app launch {package}\nCategories=X-WayDroid-App;\n")).unwrap()
        };
        write("com.example.existing");
        mutate(&dir, |p| {
            p.visibility.insert("com.example.existing".into(), true);
            p.auto_create = false;
            Ok(())
        })
        .unwrap();
        write("com.example.new");
        reconcile(&dir, &root).unwrap();
        assert!(desktop::desktop_visible(
            &desktop::native_shortcut(&root, "com.example.existing")
                .unwrap()
                .unwrap()
                .1
        ));
        assert!(!desktop::desktop_visible(
            &desktop::native_shortcut(&root, "com.example.new")
                .unwrap()
                .unwrap()
                .1
        ));
        write("com.example.new");
        reconcile(&dir, &root).unwrap(); // Recreated by Waydroid.
        assert!(!desktop::desktop_visible(
            &desktop::native_shortcut(&root, "com.example.new")
                .unwrap()
                .unwrap()
                .1
        ));
        mutate(&dir, |p| {
            p.auto_create = true;
            Ok(())
        })
        .unwrap();
        write("com.example.next");
        reconcile(&dir, &root).unwrap();
        assert!(desktop::desktop_visible(
            &desktop::native_shortcut(&root, "com.example.next")
                .unwrap()
                .unwrap()
                .1
        ));
        fs::remove_dir_all(temp).unwrap();
    }
}
