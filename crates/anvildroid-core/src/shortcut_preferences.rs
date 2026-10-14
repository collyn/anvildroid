//! Persistent policy for Waydroid's automatically generated menu entries.
use crate::{config, desktop, error::AnvilError};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    path::{Path, PathBuf},
    process::Command,
    sync::Mutex,
    thread::{self, JoinHandle},
    time::Duration,
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
/// Filesystem monitor owned by the GUI. The eventfd wakes the blocking poll on
/// shutdown, so dropping it releases the inotify watches and singleton lock.
pub struct ShortcutMonitor {
    stop: OwnedFd,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl ShortcutMonitor {
    pub fn start() -> Result<Self, AnvilError> {
        let root =
            dirs::data_dir().ok_or_else(|| AnvilError::Io("Missing data directory".into()))?;
        Self::start_at(
            config::config_dir(),
            root,
            true,
            desktop::refresh_menu_cache,
        )
    }

    fn start_at(
        dir: PathBuf,
        root: PathBuf,
        migrate: bool,
        refresh: fn(),
    ) -> Result<Self, AnvilError> {
        let fd = unsafe { libc::eventfd(0, libc::EFD_CLOEXEC | libc::EFD_NONBLOCK) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        let stop = unsafe { OwnedFd::from_raw_fd(fd) };
        let worker_stop = stop.try_clone()?;
        let worker = thread::Builder::new()
            .name("shortcut-monitor".into())
            .spawn(move || {
                if migrate {
                    if let Err(error) = migrate_legacy_monitor(&dir) {
                        eprintln!("Shortcut monitor migration: {error}");
                    }
                }
                if let Err(error) = monitor_at(&dir, &root, &worker_stop, refresh) {
                    eprintln!("Shortcut monitor: {error}");
                }
            })?;
        Ok(Self {
            stop,
            thread: Mutex::new(Some(worker)),
        })
    }
    pub fn stop(&self) {
        let value = 1u64;
        unsafe {
            libc::write(self.stop.as_raw_fd(), (&value as *const u64).cast(), 8);
        }
        if let Some(worker) = self.thread.lock().unwrap_or_else(|e| e.into_inner()).take() {
            let _ = worker.join();
        }
    }
}

impl Drop for ShortcutMonitor {
    fn drop(&mut self) {
        self.stop();
    }
}

// None means stop requested; false is a timeout, true an inotify event.
fn wait(
    stop: &OwnedFd,
    events: Option<&OwnedFd>,
    timeout: i32,
) -> Result<Option<bool>, AnvilError> {
    let mut fds = [
        libc::pollfd {
            fd: stop.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        },
        libc::pollfd {
            fd: events.map_or(-1, AsRawFd::as_raw_fd),
            events: libc::POLLIN,
            revents: 0,
        },
    ];
    loop {
        if unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as _, timeout) } < 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return Err(error.into());
        }
        if fds[0].revents != 0 {
            return Ok(None);
        }
        if fds[1].revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) != 0 {
            return Err(AnvilError::Io("Shortcut watch was disconnected".into()));
        }
        return Ok(Some(fds[1].revents & libc::POLLIN != 0));
    }
}

fn monitor_at(dir: &Path, root: &Path, stop: &OwnedFd, refresh: fn()) -> Result<(), AnvilError> {
    fs::create_dir_all(dir)?;
    let instance = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(dir.join("shortcut-monitor.lock"))?;
    loop {
        if unsafe { libc::flock(instance.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0 {
            break;
        }
        let error = std::io::Error::last_os_error();
        if error.kind() != std::io::ErrorKind::WouldBlock {
            return Err(error.into());
        }
        // Another GUI owns the watch. Take over if it exits; remain interruptible.
        if wait(stop, None, 1000)?.is_none() {
            return Ok(());
        }
    }
    if wait(stop, None, 0)?.is_none() {
        return Ok(());
    }
    fs::create_dir_all(root.join("applications"))?;
    let fd = unsafe { libc::inotify_init1(libc::IN_CLOEXEC | libc::IN_NONBLOCK) };
    if fd < 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    let mut watches = Vec::new();
    for path in [dir, &root.join("applications")] {
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
    if reconcile(dir, root)? {
        refresh();
    }
    let mut buffer = [0u8; 16384];
    while wait(stop, Some(&fd), -1)?.is_some() {
        let count = unsafe { libc::read(fd.as_raw_fd(), buffer.as_mut_ptr().cast(), buffer.len()) };
        if count < 0 {
            let error = std::io::Error::last_os_error();
            if matches!(
                error.kind(),
                std::io::ErrorKind::Interrupted | std::io::ErrorKind::WouldBlock
            ) {
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
            match reconcile(dir, root) {
                Ok(true) => refresh(),
                Err(e) => eprintln!("Shortcut policy: {e}"),
                _ => {}
            }
        }
    }
    Ok(())
}

/// Remove only the old launcher entries generated by AnvilDroid. This runs
/// in the monitor thread, never on the window's startup path.
fn migrate_legacy_monitor(dir: &Path) -> Result<(), AnvilError> {
    let migrated = migrate_legacy_at(dir, |args| {
        let output = crate::launch::output_timeout(
            Command::new("systemctl").args(args),
            Duration::from_secs(5),
        )?;
        if !output.status.success() {
            return Err(AnvilError::Io(format!(
                "Cannot migrate shortcut service: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            )));
        }
        Ok(())
    })?;
    if migrated {
        stop_legacy_processes();
    }
    Ok(())
}

fn migrate_legacy_at(
    dir: &Path,
    mut systemctl: impl FnMut(&[&str]) -> Result<(), AnvilError>,
) -> Result<bool, AnvilError> {
    let config_root = dir
        .parent()
        .ok_or_else(|| AnvilError::Io("Missing config root".into()))?;
    let unit = config_root.join("systemd/user/anvildroid-shortcuts.service");
    let autostart = config_root.join("autostart/anvildroid-shortcuts.desktop");
    let mut migrated = false;
    if fs::read_to_string(&autostart).is_ok_and(|text| {
        text.contains("X-AnvilDroid-Managed=true") && text.contains("--shortcut-monitor")
    }) {
        fs::remove_file(autostart)?;
        migrated = true;
    }
    if fs::read_to_string(&unit).is_ok_and(|text| {
        text.contains("Description=AnvilDroid shortcut preferences")
            && text.contains("--shortcut-monitor")
    }) {
        systemctl(&["--user", "disable", "--now", "anvildroid-shortcuts.service"])?;
        fs::remove_file(unit)?;
        systemctl(&["--user", "daemon-reload"])?;
        migrated = true;
    }
    Ok(migrated)
}

// The old XDG fallback used a detached GUI executable with exactly this flag.
// Do not terminate a normal GUI, shortcut launch, or another user's process.
fn stop_legacy_processes() {
    use std::os::unix::fs::MetadataExt;
    let Ok(entries) = fs::read_dir("/proc") else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|s| s.parse::<i32>().ok())
        else {
            continue;
        };
        if pid <= 1 || pid == std::process::id() as i32 {
            continue;
        }
        if !fs::metadata(&path).is_ok_and(|meta| meta.uid() == unsafe { libc::getuid() }) {
            continue;
        }
        let Ok(executable) = fs::read_link(path.join("exe")) else {
            continue;
        };
        let exe = executable.to_string_lossy();
        let exe = exe.strip_suffix(" (deleted)").unwrap_or(&exe);
        if Path::new(exe)
            .file_name()
            .is_none_or(|name| name != "anvildroid-gui")
        {
            continue;
        }
        let Ok(args) = fs::read(path.join("cmdline")) else {
            continue;
        };
        if legacy_monitor_args(&args) {
            unsafe {
                libc::kill(pid, libc::SIGTERM);
            }
        }
    }
}

fn legacy_monitor_args(args: &[u8]) -> bool {
    let args: Vec<_> = args
        .split(|byte| *byte == 0)
        .filter(|arg| !arg.is_empty())
        .collect();
    args.len() == 2 && args[1] == b"--shortcut-monitor"
}

#[cfg(test)]
mod tests {
    use super::*;

    fn eventually(mut condition: impl FnMut() -> bool) {
        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        while !condition() {
            assert!(
                std::time::Instant::now() < deadline,
                "monitor did not process the event"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    #[test]
    fn gui_monitor_handles_events_stops_and_transfers_ownership() {
        let temp =
            std::env::temp_dir().join(format!("anvil-shortcut-watch-{}", std::process::id()));
        let dir = temp.join("config");
        let root = temp.join("data");
        fs::create_dir_all(root.join("applications")).unwrap();
        mutate(&dir, |prefs| {
            prefs.auto_create = false;
            Ok(())
        })
        .unwrap();
        let first = ShortcutMonitor::start_at(dir.clone(), root.clone(), false, || {}).unwrap();
        let write = |package: &str| {
            fs::write(root.join("applications").join(format!("waydroid.{package}.desktop")),
                format!("[Desktop Entry]\nType=Application\nExec=waydroid app launch {package}\nCategories=X-WayDroid-App;\n")).unwrap();
        };
        let hidden = |package: &str| {
            desktop::native_shortcut(&root, package)
                .unwrap()
                .is_some_and(|(_, text)| !desktop::desktop_visible(&text))
        };
        write("com.example.new");
        eventually(|| hidden("com.example.new"));
        write("com.example.new"); // Waydroid rewrites a removed shortcut.
        eventually(|| hidden("com.example.new"));
        mutate(&dir, |prefs| {
            prefs.auto_create = true;
            Ok(())
        })
        .unwrap();
        write("com.example.visible");
        eventually(|| read(&dir).unwrap().visibility.get("com.example.visible") == Some(&true));
        assert!(!hidden("com.example.visible"));
        mutate(&dir, |prefs| {
            prefs.visibility.insert("com.example.visible".into(), false);
            Ok(())
        })
        .unwrap();
        eventually(|| hidden("com.example.visible"));

        let second = ShortcutMonitor::start_at(dir.clone(), root.clone(), false, || {}).unwrap();
        let stopped = std::time::Instant::now();
        drop(first);
        assert!(stopped.elapsed() < Duration::from_secs(1));
        write("com.example.visible");
        eventually(|| hidden("com.example.visible")); // Second GUI takes over.
        drop(second);
        write("com.example.visible");
        assert!(!hidden("com.example.visible")); // No detached watcher remains.
        let third = ShortcutMonitor::start_at(dir.clone(), root.clone(), false, || {}).unwrap();
        eventually(|| hidden("com.example.visible")); // Catch up on next GUI open.
        drop(third);

        // Shutdown also interrupts waiting for another window's singleton lock.
        let lock = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(dir.join("shortcut-monitor.lock"))
            .unwrap();
        assert_eq!(
            unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) },
            0
        );
        let waiting = ShortcutMonitor::start_at(dir.clone(), root.clone(), false, || {}).unwrap();
        let stopped = std::time::Instant::now();
        drop(waiting);
        assert!(stopped.elapsed() < Duration::from_secs(1));
        drop(lock);
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn legacy_migration_is_scoped_idempotent_and_keeps_failed_units_for_retry() {
        let temp =
            std::env::temp_dir().join(format!("anvil-shortcut-migration-{}", std::process::id()));
        let dir = temp.join("anvildroid");
        let unit = temp.join("systemd/user/anvildroid-shortcuts.service");
        let autostart = temp.join("autostart/anvildroid-shortcuts.desktop");
        fs::create_dir_all(unit.parent().unwrap()).unwrap();
        fs::create_dir_all(autostart.parent().unwrap()).unwrap();
        let unrelated = temp.join("autostart/org.anvildroid.gui.desktop");
        fs::write(&unrelated, "GUI Auto startup").unwrap();
        fs::write(&unit, "Description=Custom user service\nExecStart=other").unwrap();
        fs::write(&autostart, "Exec=other").unwrap();
        migrate_legacy_at(&dir, |_| panic!("unowned entries must not run systemctl")).unwrap();
        assert!(unit.exists());
        assert!(autostart.exists());
        fs::write(
            &unit,
            "Description=AnvilDroid shortcut preferences\nExecStart=gui --shortcut-monitor",
        )
        .unwrap();
        fs::write(
            &autostart,
            "X-AnvilDroid-Managed=true\nExec=gui --shortcut-monitor",
        )
        .unwrap();
        assert!(
            migrate_legacy_at(&dir, |_| Err(AnvilError::Io("bus unavailable".into()))).is_err()
        );
        assert!(unit.exists()); // Do not forget a service that could still run.
        assert!(!autostart.exists());
        let mut calls = Vec::new();
        migrate_legacy_at(&dir, |args| {
            calls.push(args.iter().map(|s| s.to_string()).collect::<Vec<_>>());
            Ok(())
        })
        .unwrap();
        assert_eq!(
            calls[0],
            ["--user", "disable", "--now", "anvildroid-shortcuts.service"]
        );
        assert_eq!(calls[1], ["--user", "daemon-reload"]);
        assert!(!unit.exists());
        assert!(unrelated.exists());
        migrate_legacy_at(&dir, |_| panic!("migration should run once")).unwrap();
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn legacy_process_match_never_selects_normal_gui_or_shortcut_launch() {
        assert!(legacy_monitor_args(
            b"/usr/bin/anvildroid-gui\0--shortcut-monitor\0"
        ));
        for args in [
            b"gui\0".as_slice(),
            b"gui\0--autostart\0",
            b"gui\0--launch-app\0com.example.app\0",
            b"gui\0--shortcut-monitor\0--extra\0",
        ] {
            assert!(!legacy_monitor_args(args));
        }
    }
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
