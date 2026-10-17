use anvildroid_core::library;
use std::{fs, process::Command};

#[test]
fn preferences_survive_process_restart() {
    let root =
        std::env::temp_dir().join(format!("anvildroid-library-process-{}", std::process::id()));
    fs::create_dir_all(&root).unwrap();
    for action in ["write", "read"] {
        let result = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "library_worker", "--nocapture"])
            .env("XDG_CONFIG_HOME", &root)
            .env("ANVIL_LIBRARY_TEST", action)
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
    }
    fs::remove_dir_all(root).unwrap();
}
#[test]
fn library_worker() {
    let Ok(action) = std::env::var("ANVIL_LIBRARY_TEST") else {
        return;
    };
    if action == "write" {
        library::favorite("com.example.test", true).unwrap();
        library::launched("com.example.test").unwrap();
        library::sort("recent").unwrap();
    } else {
        let state = library::read().unwrap();
        assert!(state.favorites.contains("com.example.test"));
        assert!(state.recent["com.example.test"] > 0);
        assert_eq!(state.sort, "recent");
    }
}
