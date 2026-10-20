//! Explicit user-file export. The privileged service never receives a path.
use serde_json::Value;
use std::io::Write;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::Path;

const EXPORT_LIMIT: usize = 24 * 1024;

pub fn export(id: &str, package: &str, scheme_id: &str, revision: &str, path: &Path) -> Result<Value, String> {
    let report = crate::controller::request(crate::controller::Request::KeymapScheme {
        id: id.into(), package: package.into(), action: "export".into(),
        scheme_id: scheme_id.into(), name: String::new(), revision: revision.into(), content: String::new(),
    })?;
    if report["runtime_id"].as_str() != Some(id) || report["package"].as_str() != Some(package)
        || report["revision"].as_str() != Some(revision)
        || !report["schemes"].as_array().is_some_and(|schemes| schemes.iter().any(|s| s["id"].as_str() == Some(scheme_id))) {
        return Err("Scheme export does not match the selected runtime, app or revision".into());
    }
    let content = report["content"].as_str().ok_or("Scheme service returned no export content")?;
    let bytes = write_scheme(content, id, package, path)?;
    Ok(serde_json::json!({"runtime_id":id,"package":package,"scheme_id":scheme_id,"path":path,"bytes":bytes}))
}

fn write_scheme(content: &str, id: &str, package: &str, path: &Path) -> Result<usize, String> {
    if !path.is_absolute() || path.file_name().is_none() { return Err("Choose an absolute scheme export file path".into()); }
    if content.is_empty() || content.len() > EXPORT_LIMIT { return Err("Scheme export exceeds the 24 KiB limit".into()); }
    let envelope: Value = serde_json::from_str(content).map_err(|_| "Invalid scheme export JSON")?;
    let keys = ["format", "version", "name", "profile", "reference"];
    if !envelope.as_object().is_some_and(|o| o.len() == keys.len() && keys.iter().all(|k| o.contains_key(*k)))
        || envelope["format"] != "anvildroid-scheme" || envelope["version"].as_u64() != Some(1)
        || !envelope["name"].as_str().is_some_and(|name| !name.is_empty() && name.chars().count() <= 48 && !name.chars().any(char::is_control)) {
        return Err("Unsupported scheme export format".into());
    }
    let profile = envelope["profile"].as_str().ok_or("Missing scheme profile")?;
    let header: Vec<_> = profile.lines().next().unwrap_or("").split_whitespace().collect();
    if profile.len() > 16384 || !profile.ends_with('\n') || header.len() != 5 || header[0] != "ANVIL_KEYMAP"
        || !matches!(header[1], "1" | "2" | "3" | "4") || header[2] != id || header[3] != package
        || !header[4].parse::<usize>().is_ok_and(|count| count <= 64 && profile.lines().count() == count + 1) {
        return Err("Scheme export profile does not match the selected runtime and app".into());
    }
    let reference = &envelope["reference"];
    if !reference.is_null() && !reference.as_object().is_some_and(|o| o.len() == 3
        && ["width", "height"].iter().all(|k| o.get(*k).and_then(Value::as_u64).is_some_and(|n| (1..=8192).contains(&n)))
        && matches!(o.get("orientation").and_then(Value::as_str), Some("portrait" | "landscape"))) {
        return Err("Invalid scheme reference dimensions".into());
    }
    // Match diagnostic export: choose a new local file, never overwrite or
    // follow a symlink/device/FIFO. This process runs with the GUI user's UID.
    let mut file = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK).open(path)
        .map_err(|e| format!("Choose a new scheme export filename: {e}"))?;
    if let Err(error) = file.write_all(content.as_bytes()).and_then(|_| file.write_all(b"\n")).and_then(|_| file.sync_all()) {
        if let (Ok(opened), Ok(named)) = (file.metadata(), path.symlink_metadata()) {
            if (opened.dev(), opened.ino()) == (named.dev(), named.ino()) { let _ = std::fs::remove_file(path); }
        }
        return Err(format!("Cannot write scheme export: {error}"));
    }
    Ok(content.len() + 1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, PermissionsExt};
    fn fixture() -> String {
        serde_json::json!({"format":"anvildroid-scheme","version":1,"name":"Keyboard",
            "profile":"ANVIL_KEYMAP 4 default com.test.game 0\n","reference":null}).to_string()
    }
    #[test]
    fn actual_file_export_is_private_roundtrippable_and_preserves_existing_targets() {
        let root = std::env::temp_dir().join(format!("anvil-scheme-export-{}",std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let path = root.join("scheme.json"); let content = fixture();
        let bytes = write_scheme(&content,"default","com.test.game",&path).unwrap();
        let saved = std::fs::read(&path).unwrap(); assert_eq!(bytes,saved.len());
        assert_eq!(saved,format!("{content}\n").into_bytes());
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,0o600);
        let exported: Value = serde_json::from_slice(&saved).unwrap(); assert_eq!(exported["format"],"anvildroid-scheme");
        assert!(write_scheme(&content,"default","com.test.game",&path).is_err());
        let link=root.join("link.json");symlink(&path,&link).unwrap();
        assert!(write_scheme(&content,"default","com.test.game",&link).is_err());
        assert!(write_scheme(&content,"default","com.other.app",&root.join("wrong.json")).is_err());
        assert!(write_scheme(&content,"r-other","com.test.game",&root.join("scope.json")).is_err());
        assert!(write_scheme(&content,"default","com.test.game",Path::new("relative.json")).is_err());
        assert_eq!(std::fs::read(&path).unwrap(),saved);
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn rejects_unsupported_oversized_and_malformed_export_before_creating_file() {
        let path=std::env::temp_dir().join(format!("anvil-scheme-invalid-{}.json",std::process::id()));
        for content in ["broken".to_string(),"x".repeat(EXPORT_LIMIT+1),fixture().replace("anvildroid-scheme","BlueStacks"),
            fixture().replace("\"version\":1","\"version\":true"),fixture().replace(" 0\\n"," 1\\n")] {
            assert!(write_scheme(&content,"default","com.test.game",&path).is_err()); assert!(!path.exists());
        }
    }
}
