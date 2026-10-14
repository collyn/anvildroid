//! GUI-only preferences. Autostart uses the freedesktop login convention;
//! neither hiding nor exiting this GUI changes any Android runtime.
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

const TRAY_ID: &str = "anvildroid-panel";
const AUTOSTART_FILE: &str = "org.anvildroid.gui.desktop";

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Preferences {
    minimize_to_panel: bool,
    startup_minimize: bool,
    auto_startup: bool,
}

pub struct ApplicationPreferences {
    preferences: Mutex<Preferences>,
    panel_enabled: AtomicBool,
}

fn config_root() -> PathBuf {
    anvildroid_core::config::config_dir()
        .parent()
        .unwrap()
        .to_path_buf()
}

fn read_at(root: &Path) -> Result<Preferences, String> {
    let mut prefs = match fs::read(root.join("anvildroid/application.json")) {
        Ok(data) => serde_json::from_slice::<Preferences>(&data).map_err(|e| e.to_string())?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Preferences::default(),
        Err(e) => return Err(e.to_string()),
    };
    // Reflect external changes made by a desktop startup manager as well.
    prefs.auto_startup =
        fs::read_to_string(root.join("autostart").join(AUTOSTART_FILE)).is_ok_and(|text| {
            !text.lines().any(|line| {
                matches!(
                    line.trim(),
                    "Hidden=true" | "X-GNOME-Autostart-enabled=false"
                )
            })
        });
    Ok(prefs)
}

fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    let temp = path.with_extension(format!("tmp-{}", std::process::id()));
    fs::write(&temp, bytes).map_err(|e| e.to_string())?;
    fs::rename(&temp, path).map_err(|e| {
        let _ = fs::remove_file(&temp);
        e.to_string()
    })
}

fn desktop_entry(executable: &Path) -> Result<String, String> {
    let path = executable.to_str().ok_or("Executable path is not UTF-8")?;
    if path.contains(['\n', '\r', '\0']) {
        return Err("Invalid executable path".into());
    }
    // Exec quoting is followed by desktop-entry string escaping. Literal '%'
    // must be doubled so it cannot be interpreted as a field code.
    let quoted = path
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('`', "\\`")
        .replace('$', "\\$")
        .replace('%', "%%");
    let escaped = quoted.replace('\\', "\\\\");
    Ok(format!("[Desktop Entry]\nType=Application\nName=AnvilDroid\nExec=\"{escaped}\" --autostart\nIcon=org.anvildroid.gui\nTerminal=false\nX-GNOME-Autostart-enabled=true\n"))
}

fn set_autostart_at(root: &Path, enabled: bool, executable: &Path) -> Result<(), String> {
    let path = root.join("autostart").join(AUTOSTART_FILE);
    if enabled {
        write_atomic(&path, desktop_entry(executable)?.as_bytes())
    } else {
        match fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(e.to_string()),
        }
    }
}

fn show_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        #[cfg(target_os = "linux")]
        if let Ok(native) = window.gtk_window() {
            use gtk::prelude::*;
            native.present();
        }
    }
}

fn handle_panel_menu(app: &tauri::AppHandle, id: &str) {
    match id {
        "panel-open" => show_main(app),
        "panel-exit" => app.exit(0),
        _ => (),
    }
}

pub fn handle_close(window: &tauri::Window, event: &tauri::WindowEvent) {
    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
        if window.label() == "main" && minimize_to_panel(window.app_handle()) {
            if window.hide().is_ok() {
                api.prevent_close();
            }
        }
    }
}

fn set_panel(app: &tauri::AppHandle, enabled: bool) -> Result<(), String> {
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        return tray.set_visible(enabled).map_err(|e| e.to_string());
    }
    if !enabled {
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    {
        // The tray dependency otherwise panics when this optional library is
        // absent (e.g. an AppImage on a fresh desktop).
        let available = ["libayatana-appindicator3.so.1", "libappindicator3.so.1"]
            .iter()
            .any(|name| unsafe { libloading::Library::new(name).is_ok() });
        if !available {
            return Err("Install libayatana-appindicator3-1 to enable the panel icon.".into());
        }
    }
    let open = MenuItem::with_id(app, "panel-open", "Open Anvildroid", true, None::<&str>)
        .map_err(|e| e.to_string())?;
    let exit = MenuItem::with_id(app, "panel-exit", "Exit", true, None::<&str>)
        .map_err(|e| e.to_string())?;
    let menu = Menu::with_items(app, &[&open, &exit]).map_err(|e| e.to_string())?;
    let icon = app
        .default_window_icon()
        .ok_or("Missing application icon")?
        .clone();
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(icon)
        .tooltip("AnvilDroid")
        .menu(&menu)
        .on_menu_event(|app, event| handle_panel_menu(app, event.id.as_ref()))
        .build(app)
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn minimize_to_panel(app: &tauri::AppHandle) -> bool {
    app.try_state::<ApplicationPreferences>()
        .is_some_and(|state| state.panel_enabled.load(Ordering::Relaxed))
}

#[derive(Debug, PartialEq)]
enum StartupVisibility {
    Show,
    Hide,
    Minimize,
}

fn startup_visibility(
    prefs: &Preferences,
    panel_enabled: bool,
    explicit_view: bool,
) -> StartupVisibility {
    if explicit_view || !prefs.startup_minimize {
        StartupVisibility::Show
    } else if panel_enabled {
        StartupVisibility::Hide
    } else {
        StartupVisibility::Minimize
    }
}

pub fn show_at_startup(app: &tauri::AppHandle, explicit_view: bool) -> tauri::Result<()> {
    let state = app.state::<ApplicationPreferences>();
    let prefs = state.preferences.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(window) = app.get_webview_window("main") {
        match startup_visibility(&prefs, minimize_to_panel(app), explicit_view) {
            StartupVisibility::Show => window.show()?,
            StartupVisibility::Hide => {}
            StartupVisibility::Minimize => {
                window.show()?;
                window.minimize()?;
            }
        }
    }
    Ok(())
}

pub fn install(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let prefs = read_at(&config_root()).unwrap_or_else(|e| {
        eprintln!("Cannot load application preferences: {e}");
        Preferences::default()
    });
    let enabled = prefs.minimize_to_panel
        && set_panel(app.handle(), true)
            .map_err(|e| {
                eprintln!("Cannot enable panel icon: {e}");
            })
            .is_ok();
    app.manage(ApplicationPreferences {
        preferences: Mutex::new(prefs),
        panel_enabled: AtomicBool::new(enabled),
    });
    Ok(())
}

#[tauri::command]
pub fn get_application_preferences(
    app: tauri::AppHandle,
    state: tauri::State<'_, ApplicationPreferences>,
) -> Result<Preferences, String> {
    let mut prefs = state.preferences.lock().map_err(|e| e.to_string())?;
    prefs.auto_startup = read_at(&config_root())?.auto_startup;
    let mut result = prefs.clone();
    result.minimize_to_panel = minimize_to_panel(&app);
    Ok(result)
}

#[tauri::command]
pub fn set_application_preference(
    app: tauri::AppHandle,
    state: tauri::State<'_, ApplicationPreferences>,
    preference: String,
    enabled: bool,
) -> Result<Preferences, String> {
    let mut prefs = state.preferences.lock().map_err(|e| e.to_string())?;
    match preference.as_str() {
        "minimize_to_panel" => {
            let previous = minimize_to_panel(&app);
            set_panel(&app, enabled)?;
            let mut next = prefs.clone();
            next.minimize_to_panel = enabled;
            if let Err(e) = write_atomic(
                &config_root().join("anvildroid/application.json"),
                &serde_json::to_vec(&next).map_err(|e| e.to_string())?,
            ) {
                let _ = set_panel(&app, previous);
                return Err(e);
            }
            *prefs = next;
            state.panel_enabled.store(enabled, Ordering::Relaxed);
            if !enabled {
                show_main(&app);
            }
        }
        "auto_startup" => {
            // AppImage's mounted executable is temporary; use its persistent path.
            let executable = std::env::var_os("APPIMAGE")
                .map(PathBuf::from)
                .filter(|path| path.is_absolute() && path.is_file())
                .unwrap_or(std::env::current_exe().map_err(|e| e.to_string())?);
            set_autostart_at(&config_root(), enabled, &executable)?;
            prefs.auto_startup = enabled;
        }
        "startup_minimize" => {
            let mut next = prefs.clone();
            next.startup_minimize = enabled;
            write_atomic(
                &config_root().join("anvildroid/application.json"),
                &serde_json::to_vec(&next).map_err(|e| e.to_string())?,
            )?;
            *prefs = next;
        }
        _ => return Err("Unknown application preference".into()),
    }
    let mut result = prefs.clone();
    result.minimize_to_panel = minimize_to_panel(&app);
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn startup_minimize_is_independent_and_explicit_views_remain_visible() {
        let mut prefs = Preferences::default();
        assert_eq!(
            startup_visibility(&prefs, true, false),
            StartupVisibility::Show
        );
        prefs.startup_minimize = true;
        assert_eq!(
            startup_visibility(&prefs, false, false),
            StartupVisibility::Minimize
        );
        assert_eq!(
            startup_visibility(&prefs, true, false),
            StartupVisibility::Hide
        );
        assert_eq!(
            startup_visibility(&prefs, true, true),
            StartupVisibility::Show
        );
        // Older preferences default to showing the app until this new option is enabled.
        let old: Preferences = serde_json::from_str("{\"minimize_to_panel\":true}").unwrap();
        assert!(!old.startup_minimize);
    }
    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "requires GTK/WebKit, a display and a session bus"]
    fn native_panel_lifecycle() {
        use gtk::prelude::*;
        let root = std::env::temp_dir().join(format!("anvil-panel-native-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        std::env::set_var("XDG_CONFIG_HOME", &root);
        std::env::set_var("XDG_DATA_HOME", root.join("data"));
        write_atomic(
            &root.join("anvildroid/application.json"),
            b"{\"minimize_to_panel\":true,\"startup_minimize\":true}",
        )
        .unwrap();
        let mut context = tauri::generate_context!();
        context.config_mut().app.windows[0].url =
            tauri::WebviewUrl::External("about:blank".parse().unwrap());
        let app = tauri::Builder::default()
            .any_thread()
            .on_window_event(handle_close)
            .setup(|app| {
                install(app)?;
                app.manage(anvildroid_core::shortcut_preferences::ShortcutMonitor::start()?);
                crate::titlebar::install(app)?;
                let window = app.get_webview_window("main").unwrap();
                show_at_startup(app.handle(), false)?;
                assert!(!window.is_visible()?);
                show_at_startup(app.handle(), true)?;
                let handle = app.handle().clone();
                let mut stage = 0;
                gtk::glib::timeout_add_local(std::time::Duration::from_millis(200), move || {
                    let window = handle
                        .get_webview_window("main")
                        .expect("main survives hiding");
                    let native = window.gtk_window().unwrap();
                    match stage {
                        0 => {
                            assert!(window.is_visible().unwrap());
                            assert!(handle.tray_by_id(TRAY_ID).is_some());
                            let bar = native.titlebar().unwrap().downcast::<gtk::Box>().unwrap();
                            let button =
                                bar.children()[1].clone().downcast::<gtk::Button>().unwrap();
                            button.emit_clicked();
                        }
                        1 => {
                            assert!(!window.is_visible().unwrap());
                            handle_panel_menu(&handle, "panel-open");
                        }
                        2 => {
                            assert!(window.is_visible().unwrap());
                            native.close();
                        }
                        3 => {
                            assert!(!window.is_visible().unwrap());
                            let state = handle.state::<ApplicationPreferences>();
                            assert!(
                                set_application_preference(
                                    handle.clone(),
                                    state,
                                    "auto_startup".into(),
                                    true
                                )
                                .unwrap()
                                .auto_startup
                            );
                            let state = handle.state::<ApplicationPreferences>();
                            set_application_preference(
                                handle.clone(),
                                state,
                                "auto_startup".into(),
                                false,
                            )
                            .unwrap();
                            let state = handle.state::<ApplicationPreferences>();
                            set_application_preference(
                                handle.clone(),
                                state,
                                "minimize_to_panel".into(),
                                false,
                            )
                            .unwrap();
                            assert!(!minimize_to_panel(&handle));
                        }
                        4 => {
                            assert!(window.is_visible().unwrap());
                            let state = handle.state::<ApplicationPreferences>();
                            set_application_preference(
                                handle.clone(),
                                state,
                                "minimize_to_panel".into(),
                                true,
                            )
                            .unwrap();
                            native.close();
                        }
                        _ => {
                            assert!(!window.is_visible().unwrap());
                            handle_panel_menu(&handle, "panel-exit");
                            return gtk::glib::ControlFlow::Break;
                        }
                    }
                    stage += 1;
                    gtk::glib::ControlFlow::Continue
                });
                Ok(())
            })
            .build(context)
            .unwrap();
        assert_eq!(
            app.run_return(|app, event| {
                if matches!(event, tauri::RunEvent::Exit) {
                    app.state::<anvildroid_core::shortcut_preferences::ShortcutMonitor>()
                        .stop();
                }
            }),
            0
        );
        assert!(!root.join("autostart").join(AUTOSTART_FILE).exists());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn preferences_and_autostart_lifecycle() {
        let root = std::env::temp_dir().join(format!("anvil-app-prefs-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        assert!(!read_at(&root).unwrap().minimize_to_panel);
        assert!(!read_at(&root).unwrap().auto_startup);
        let prefs = Preferences {
            minimize_to_panel: true,
            auto_startup: false,
            startup_minimize: true,
        };
        write_atomic(
            &root.join("anvildroid/application.json"),
            &serde_json::to_vec(&prefs).unwrap(),
        )
        .unwrap();
        set_autostart_at(&root, true, Path::new("/opt/My App/anvildroid-gui")).unwrap();
        assert!(read_at(&root).unwrap().minimize_to_panel);
        assert!(read_at(&root).unwrap().startup_minimize);
        assert!(read_at(&root).unwrap().auto_startup);
        let desktop = root.join("autostart").join(AUTOSTART_FILE);
        let entry = fs::read_to_string(&desktop).unwrap();
        assert!(entry.contains("Exec=\"/opt/My App/anvildroid-gui\" --autostart"));
        fs::write(&desktop, format!("{entry}Hidden=true\n")).unwrap();
        assert!(!read_at(&root).unwrap().auto_startup);
        set_autostart_at(&root, false, Path::new("/usr/bin/anvildroid-gui")).unwrap();
        set_autostart_at(&root, false, Path::new("/usr/bin/anvildroid-gui")).unwrap();
        assert!(!desktop.exists());
        assert!(read_at(&root).unwrap().minimize_to_panel);
        fs::write(root.join("anvildroid/application.json"), "invalid").unwrap();
        assert!(read_at(&root).is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn startup_paths_escape_field_codes_and_reject_newlines() {
        let entry = desktop_entry(Path::new("/opt/a%f/$foo/\"bar`/app\\bin")).unwrap();
        assert!(entry.contains("%%f"));
        assert!(entry.contains("\\\\$foo"));
        assert!(entry.contains("\\\\\"bar\\\\`"));
        assert!(entry.contains("app\\\\\\\\bin"));
        assert!(desktop_entry(Path::new("/tmp/app\nHidden=true")).is_err());
    }
}
