use std::fs;
use std::path::PathBuf;

use crate::error::AnvilError;
use crate::models::SCHEMA_VERSION;

/// Default configuration with schema version.
const DEFAULT_CONFIG: &str = r#"{"schema_version":1}"#;

/// Resolve an XDG directory with a fallback to `$HOME/.config` (or similar).
fn xdg_dir(env_var: &str, fallback_suffix: &str) -> PathBuf {
    std::env::var_os(env_var)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| dirs::home_dir().map(|h| h.join(fallback_suffix)))
        .unwrap_or_else(|| PathBuf::from(fallback_suffix))
}

/// `$XDG_CONFIG_HOME/anvildroid/`
pub fn config_dir() -> PathBuf {
    xdg_dir("XDG_CONFIG_HOME", ".config").join("anvildroid")
}

/// `$XDG_CACHE_HOME/anvildroid/`
pub fn cache_dir() -> PathBuf {
    xdg_dir("XDG_CACHE_HOME", ".cache").join("anvildroid")
}

/// `$XDG_STATE_HOME/anvildroid/`
pub fn state_dir() -> PathBuf {
    xdg_dir("XDG_STATE_HOME", ".local/state").join("anvildroid")
}

/// Path to the main configuration file.
pub fn config_path() -> PathBuf {
    config_dir().join("config.json")
}

/// Path to the icon cache directory.
pub fn icon_cache_dir() -> PathBuf {
    cache_dir().join("icons")
}

/// Path to the log directory.
pub fn log_dir() -> PathBuf {
    state_dir().join("logs")
}

/// Ensure all XDG directories exist.
pub fn ensure_dirs() -> Result<(), AnvilError> {
    for dir in [config_dir(), icon_cache_dir(), log_dir()] {
        fs::create_dir_all(&dir).map_err(|e| {
            AnvilError::Config(format!("Cannot create directory {}: {e}", dir.display()))
        })?;
    }
    Ok(())
}

/// Read the configuration file. Returns default config if the file does not
/// exist or is corrupt (logs a warning to stderr for corrupt files).
pub fn read_config() -> serde_json::Value {
    let path = config_path();
    match fs::read_to_string(&path) {
        Ok(content) => match serde_json::from_str::<serde_json::Value>(&content) {
            Ok(mut value) => {
                // Migrate schema if needed.
                if let Some(v) = value.get("schema_version").and_then(|v| v.as_u64()) {
                    if v < SCHEMA_VERSION as u64 {
                        eprintln!(
                            "anvildroid: migrating config from schema v{v} to v{SCHEMA_VERSION}"
                        );
                        value["schema_version"] = serde_json::Value::Number(SCHEMA_VERSION.into());
                        let _ = write_config(&value);
                    }
                }
                value
            }
            Err(e) => {
                eprintln!(
                    "anvildroid: config file corrupt ({}), using defaults: {e}",
                    path.display()
                );
                default_config()
            }
        },
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => default_config(),
        Err(e) => {
            eprintln!(
                "anvildroid: cannot read config ({}): {e}, using defaults",
                path.display()
            );
            default_config()
        }
    }
}

/// Atomically write the configuration file (write to temp + rename).
pub fn write_config(value: &serde_json::Value) -> Result<(), AnvilError> {
    let path = config_path();
    let dir = path
        .parent()
        .ok_or_else(|| AnvilError::Config(format!("Invalid config path: {}", path.display())))?;
    fs::create_dir_all(dir)?;

    let content = serde_json::to_string_pretty(value)
        .map_err(|e| AnvilError::Config(format!("Cannot serialize config: {e}")))?;

    let tmp = dir.join(".config.json.tmp");
    fs::write(&tmp, &content)?;
    fs::rename(&tmp, &path).map_err(|e| {
        // Clean up temp file on rename failure.
        let _ = fs::remove_file(&tmp);
        AnvilError::Config(format!("Cannot update config ({}): {e}", path.display()))
    })?;
    Ok(())
}

fn default_config() -> serde_json::Value {
    serde_json::from_str(DEFAULT_CONFIG).unwrap()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_has_schema_version() {
        let config = default_config();
        assert_eq!(
            config["schema_version"].as_u64().unwrap(),
            SCHEMA_VERSION as u64
        );
    }

    #[test]
    fn xdg_dirs_are_under_anvildroid() {
        assert!(config_dir().ends_with("anvildroid"));
        assert!(cache_dir().ends_with("anvildroid"));
        assert!(state_dir().ends_with("anvildroid"));
    }

    #[test]
    fn write_and_read_config_roundtrip() {
        let tmp = std::env::temp_dir().join(format!("anvildroid-cfg-test-{}", std::process::id()));
        std::fs::create_dir_all(&tmp).unwrap();

        // Temporarily override XDG_CONFIG_HOME to use temp dir.
        let prev = std::env::var_os("XDG_CONFIG_HOME");
        std::env::set_var("XDG_CONFIG_HOME", &tmp);

        let mut value = default_config();
        value["test_key"] = serde_json::Value::String("hello".into());
        write_config(&value).unwrap();

        let read_back = read_config();
        assert_eq!(read_back["test_key"].as_str().unwrap(), "hello");
        assert_eq!(
            read_back["schema_version"].as_u64().unwrap(),
            SCHEMA_VERSION as u64
        );

        // Restore and clean up.
        match prev {
            Some(v) => std::env::set_var("XDG_CONFIG_HOME", v),
            None => std::env::remove_var("XDG_CONFIG_HOME"),
        }
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
