//! Explicit routing boundary for the singleton adapter. Never fall back to the
//! current Android session when a caller asks for an unsupported runtime.
use crate::{config, error::AnvilError, registry::Registry};
use std::{ffi::OsString, fs, path::Path};

pub const LEGACY_ID: &str = "default";

/// Resolve before any backend call or preference write. Missing registry is
/// compatible with pre-registry installations; existing invalid files fail closed.
pub fn ensure_legacy(runtime_id: &str) -> Result<(), AnvilError> {
    let data = dirs::data_dir()
        .ok_or_else(|| AnvilError::Config("Cannot locate user data directory".into()))?
        .join("waydroid/data");
    ensure_at(
        runtime_id,
        &config::config_dir().join("runtimes.json"),
        &data,
    )
}

fn ensure_at(id: &str, registry_path: &Path, data: &Path) -> Result<(), AnvilError> {
    if id != LEGACY_ID {
        return Err(AnvilError::InvalidArgument(format!(
            "Runtime '{id}' is not supported by the legacy backend; no operation was sent to default"
        )));
    }
    let bytes = match fs::read(registry_path) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return Err(e.into()),
    };
    let registry: Registry = serde_json::from_slice(&bytes)
        .map_err(|e| AnvilError::Config(format!("Invalid runtime registry; file retained: {e}")))?;
    let record = registry.resolve_legacy(id)?;
    if record.data_dir != data {
        return Err(AnvilError::Config(
            "Registered runtime data path differs from this user's Waydroid data; operation refused".into(),
        ));
    }
    Ok(())
}

/// Parse one explicit selector anywhere in CLI arguments. Legacy invocations and
/// old desktop entries stay pinned to default, even if the selected runtime changes.
pub fn parse_selector(args: &[OsString]) -> Result<(String, Vec<OsString>), AnvilError> {
    let mut selected = None;
    let mut rest = Vec::new();
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        if arg.to_str().is_some_and(|s| s.starts_with("--runtime=")) {
            return Err(AnvilError::InvalidArgument(
                "Use --runtime ID (with a space)".into(),
            ));
        }
        if arg == "--runtime" {
            if selected.is_some() {
                return Err(AnvilError::InvalidArgument(
                    "Duplicate --runtime selector".into(),
                ));
            }
            let id = iter
                .next()
                .and_then(|v| v.to_str())
                .filter(|id| {
                    !id.is_empty()
                        && id.len() <= 64
                        && id
                            .bytes()
                            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
                        && !id.starts_with('-')
                })
                .ok_or_else(|| {
                    AnvilError::InvalidArgument("--runtime requires a runtime ID".into())
                })?;
            selected = Some(id.to_string());
        } else {
            rest.push(arg.clone());
        }
    }
    Ok((selected.unwrap_or_else(|| LEGACY_ID.into()), rest))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn selectors_never_silently_fall_back() {
        let parse =
            |args: &[&str]| parse_selector(&args.iter().map(OsString::from).collect::<Vec<_>>());
        assert_eq!(parse(&["run", "com.example.app"]).unwrap().0, LEGACY_ID);
        let (id, rest) = parse(&["run", "--runtime", "second", "com.example.app"]).unwrap();
        assert_eq!(id, "second");
        assert_eq!(
            rest,
            vec![OsString::from("run"), OsString::from("com.example.app")]
        );
        for args in [
            vec!["--runtime"],
            vec!["--runtime", "../bad"],
            vec!["--runtime", "--json"],
            vec!["--runtime=second"],
            vec!["--runtime", "default", "--runtime", "second"],
        ] {
            assert!(parse(&args).is_err());
        }
        assert!(ensure_at("second", Path::new("/missing"), Path::new("/data")).is_err());
    }
    #[test]
    fn corrupted_or_redirected_registry_cannot_route_to_default() {
        let root = std::env::temp_dir().join(format!("anvil-scope-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("runtimes.json");
        assert!(ensure_at("default", &path, Path::new("/data")).is_ok());
        crate::registry::load_or_import(&root, "/data".into()).unwrap();
        assert!(ensure_at("default", &path, Path::new("/data")).is_ok());
        assert!(ensure_at("default", &path, Path::new("/other")).is_err());
        fs::write(&path, "broken").unwrap();
        assert!(ensure_at("default", &path, Path::new("/data")).is_err());
        assert_eq!(fs::read_to_string(&path).unwrap(), "broken");
        fs::remove_dir_all(root).unwrap();
    }
}
