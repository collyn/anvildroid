use anvildroid_core::{backend, desktop, launch, models::RuntimeState, runtime};
use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::PathBuf,
    process::Command,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::{Duration, Instant},
};

// Each scenario runs in its own process: PATH/XDG overrides never race other tests.
#[test]
fn launch_scenarios() {
    for scenario in [
        "booting",
        "stopped",
        "x11_start",
        "x11_stale",
        "frozen",
        "launch_error",
        "nonzero",
        "timeout",
        "hung_probe",
        "lost",
        "start_failed",
        "list_error",
        "app_info",
        "shortcut",
        "protected",
        "remove_ok",
        "remove_error",
        "remove_unconfirmed",
        "remove_query_error",
    ] {
        let dir = std::env::temp_dir().join(format!(
            "anvildroid-launch-{}-{scenario}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        for (name, script) in [
            ("dbus-send", DBUS),
            ("waydroid", WAYDROID),
            ("gdbus", GDBUS),
            ("python3", X11_SESSION),
        ] {
            let path = dir.join(name);
            fs::write(&path, script).unwrap();
            fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
        }
        if !matches!(
            scenario,
            "stopped" | "x11_start" | "x11_stale" | "lost" | "start_failed"
        ) {
            fs::write(dir.join("owner"), "true").unwrap();
        }
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args(["--exact", "scenario_worker", "--nocapture"])
            .env("PATH", &dir)
            .env("XDG_STATE_HOME", &dir)
            .env("XDG_DATA_HOME", dir.join("data"))
            .env("TEST_FIXTURE", &dir)
            .env("TEST_SCENARIO", scenario)
            .env_remove("DISPLAY")
            .env_remove("WAYLAND_DISPLAY")
            .env("XDG_RUNTIME_DIR", &dir);
        let wayland_socket = dir.join("wayland-test");
        let _wayland = std::os::unix::net::UnixListener::bind(&wayland_socket).unwrap();
        command.env("WAYLAND_DISPLAY", &wayland_socket);
        if matches!(scenario, "x11_start" | "x11_stale") {
            command.env("DISPLAY", ":123").env("XDG_RUNTIME_DIR", &dir);
            if scenario == "x11_stale" {
                command.env("WAYLAND_DISPLAY", "missing-wayland");
            } else {
                command.env_remove("WAYLAND_DISPLAY");
            }
        }
        let done = Arc::new(AtomicBool::new(false));
        let companion_done = Arc::clone(&done);
        let companion_dir = dir.clone();
        let companion = thread::spawn(move || {
            let queue = companion_dir.join("data/waydroid/data/waydroid_tmp/anvildroid/apps");
            fs::create_dir_all(&queue).unwrap();
            while !companion_done.load(Ordering::Relaxed) {
                for entry in fs::read_dir(&queue).unwrap() {
                    let ready = entry.unwrap().path();
                    if ready.extension().is_none_or(|ext| ext != "ready") {
                        continue;
                    }
                    let working = ready.with_extension("working");
                    fs::rename(ready, &working).unwrap();
                    let action = fs::read_to_string(working.join("action")).unwrap();
                    let (status, output) = match action.trim() {
                        "details" if scenario == "remove_query_error" => (0, "unrecognized query\n"),
                        "details" if companion_dir.join("removed").exists() => (0, "Unable to find package: com.example.game\n"),
                        "details" => (0, "Package [com.example.game] (abc):\n    User 0: installed=true stopped=false\n"),
                        "uninstall" if scenario == "remove_error" => (1, "Failure [DELETE_FAILED_INTERNAL_ERROR]\n"),
                        "uninstall" => {
                            if scenario != "remove_unconfirmed" { fs::write(companion_dir.join("removed"), "").unwrap(); }
                            (0, "Success\n")
                        }
                        _ => panic!("unexpected companion action: {action}"),
                    };
                    fs::write(working.join("output"), output).unwrap();
                    fs::write(working.join("status"), status.to_string()).unwrap();
                }
                thread::sleep(Duration::from_millis(5));
            }
        });
        // Create the queue before the subprocess can check companion availability.
        fs::create_dir_all(dir.join("data/waydroid/data/waydroid_tmp/anvildroid/apps")).unwrap();
        let output = command.output().unwrap();
        done.store(true, Ordering::Relaxed);
        companion.join().unwrap();
        let trace = fs::read_to_string(dir.join("trace")).unwrap_or_default();
        fs::remove_dir_all(&dir).unwrap();
        assert!(
            output.status.success(),
            "{scenario}: {}\n{}\ntrace:\n{trace}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

#[test]
fn scenario_worker() {
    let Ok(scenario) = std::env::var("TEST_SCENARIO") else {
        return;
    };
    let dir = PathBuf::from(std::env::var_os("TEST_FIXTURE").unwrap());
    match scenario.as_str() {
        "timeout" | "hung_probe" => {
            let started = Instant::now();
            let error = launch::ensure_ready(Duration::from_millis(300)).unwrap_err();
            assert_eq!(error.code(), "TIMEOUT");
            assert!(started.elapsed() < Duration::from_secs(2));
        }
        "lost" | "start_failed" => {
            // A session process may exit during the five-second D-Bus handoff.
            // Let that grace period expire before asserting its failure class.
            let timeout = if scenario == "start_failed" { 8 } else { 2 };
            let error = launch::ensure_ready(Duration::from_secs(timeout)).unwrap_err();
            assert_eq!(
                error.code(),
                if scenario == "lost" {
                    "SESSION_MISSING"
                } else {
                    "BACKEND_FAILED"
                }
            );
        }
        "list_error" => {
            assert!(backend::list_apps().is_err());
        }
        "app_info" => {
            launch::open_app_info("com.example.game").unwrap();
            let trace = fs::read_to_string(dir.join("trace")).unwrap();
            assert!(trace.contains(
                "app intent android.settings.APPLICATION_DETAILS_SETTINGS package:com.example.game"
            ));
            assert!(trace.find("READY").unwrap() < trace.find("app intent").unwrap());
        }
        "shortcut" => {
            let exe = std::env::current_exe().unwrap();
            let path = desktop::create_shortcut("com.example.game", &exe).unwrap();
            assert_eq!(
                desktop::create_shortcut("com.example.game", &exe).unwrap(),
                path
            );
            assert_eq!(
                fs::read_dir(dir.join("data/applications")).unwrap().count(),
                1
            );
            let text = fs::read_to_string(&path).unwrap();
            assert!(text.contains("Name=Example Game (AnvilDroid)\n"));
            assert!(text.contains("--launch-app com.example.game --runtime default\n"));
            desktop::remove_shortcut("com.example.game").unwrap();
            assert!(!path.exists());
        }
        "protected" => {
            for package in [
                "org.anvildroid.ime",
                "org.anvildroid.caption",
                "com.android.settings",
                "id.waydro.platform",
            ] {
                assert_eq!(
                    backend::uninstall_app(package).unwrap_err().code(),
                    "INVALID_ARGUMENT"
                );
            }
            assert!(!dir.join("trace").exists());
        }
        "remove_ok" => {
            backend::uninstall_app("com.example.game").unwrap();
            assert!(dir.join("removed").exists());
            let trace = fs::read_to_string(dir.join("trace")).unwrap();
            assert!(!trace.contains("app remove"));
            assert!(!trace.contains("android.intent.action.DELETE"));
        }
        "remove_error" | "remove_unconfirmed" | "remove_query_error" => {
            assert_eq!(
                backend::uninstall_app("com.example.game")
                    .unwrap_err()
                    .code(),
                "BACKEND_FAILED"
            );
        }
        _ => {
            if scenario == "booting" {
                assert!(matches!(
                    runtime::runtime_status().state,
                    RuntimeState::Starting
                ));
            }
            if scenario == "frozen" {
                assert!(matches!(
                    runtime::runtime_status().state,
                    RuntimeState::Frozen
                ));
            }
            let result = launch::launch_app("com.android.settings");
            if matches!(scenario.as_str(), "launch_error" | "nonzero") {
                assert_eq!(result.unwrap_err().code(), "BACKEND_FAILED");
            } else {
                result.unwrap();
                let trace = fs::read_to_string(dir.join("trace")).unwrap();
                assert_eq!(
                    trace
                        .lines()
                        .filter(|s| s.starts_with("app launch"))
                        .count(),
                    1
                );
                assert!(trace.find("READY").unwrap() < trace.find("app launch").unwrap());
                assert_eq!(
                    trace.contains("nested-x11-session"),
                    matches!(scenario.as_str(), "x11_start" | "x11_stale")
                );
                assert_eq!(
                    trace.lines().filter(|s| *s == "session start").count(),
                    usize::from(matches!(
                        scenario.as_str(),
                        "stopped" | "x11_start" | "x11_stale"
                    ))
                );
            }
        }
    }
    if matches!(
        scenario.as_str(),
        "timeout" | "hung_probe" | "lost" | "start_failed"
    ) {
        assert!(!fs::read_to_string(dir.join("trace"))
            .unwrap_or_default()
            .contains("app launch"));
    }
}

const X11_SESSION: &str = r#"#!/bin/sh
[ "$1" = -u ] && [ "$2" = -c ] || exit 90
case "$3" in *'runtime-x11.py'*) ;; *) exit 91;; esac
echo nested-x11-session >> "$TEST_FIXTURE/trace"
exec "$TEST_FIXTURE/waydroid" session start
"#;

const DBUS: &str = r#"#!/bin/sh
if [ -f "$TEST_FIXTURE/owner" ]; then echo 'boolean true'; else echo 'boolean false'; fi
"#;

const GDBUS: &str = r#"#!/bin/sh
echo "$*" >> "$TEST_FIXTURE/trace"
echo '()'
"#;

const WAYDROID: &str = r#"#!/bin/sh
echo "$*" >> "$TEST_FIXTURE/trace"
case "$1 $2" in
  '--version ') echo '1.6';;
  'status ')
    if [ -f "$TEST_FIXTURE/owner" ] || [ "$TEST_SCENARIO" = lost ]; then
      echo 'Session: RUNNING'
      if [ "$TEST_SCENARIO" = frozen ]; then echo 'Container: FROZEN'; else echo 'Container: RUNNING'; fi
    else echo 'Session: STOPPED'; fi;;
  'session start')
    if [ "$TEST_SCENARIO" = start_failed ]; then exit 7; fi
    echo true > "$TEST_FIXTURE/owner"
    exec /bin/sleep 1;;
  'prop get')
    if [ "$TEST_SCENARIO" = hung_probe ]; then exec /bin/sleep 5; fi
    count=0
    if [ -f "$TEST_FIXTURE/count" ]; then read -r count < "$TEST_FIXTURE/count"; fi
    count=$((count + 1))
    echo "$count" > "$TEST_FIXTURE/count"
    if [ "$TEST_SCENARIO" = timeout ] || [ "$count" -lt 3 ]; then echo 0
    else echo READY >> "$TEST_FIXTURE/trace"; echo 1; fi;;
  'app launch')
    if [ "$TEST_SCENARIO" = launch_error ]; then echo 'Failed with code: -1' >&2; fi
    if [ "$TEST_SCENARIO" = nonzero ]; then echo 'launch refused' >&2; exit 9; fi;;
  'app intent') :;;
  'app list')
    if [ "$TEST_SCENARIO" = list_error ]; then echo 'WayDroid session is stopped' >&2
    elif [ ! -f "$TEST_FIXTURE/removed" ]; then printf 'Name: Example Game\npackageName: com.example.game\n'; fi;;
  *) exit 8;;
esac
"#;
