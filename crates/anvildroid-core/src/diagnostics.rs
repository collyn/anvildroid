//! Export a user-requested report without giving the privileged service a path.
use serde_json::Value;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

pub fn export(id: &str, path: &Path, include_logs: bool) -> Result<Value, String> {
    let mut report = crate::controller::request(crate::controller::Request::DiagnosticsCollect {
        id: id.into(),
        include_logs,
    })?;
    report
        .as_object_mut()
        .ok_or("Diagnostic service returned an invalid report")?
        .insert(
            "anvildroid_version".into(),
            env!("CARGO_PKG_VERSION").into(),
        );
    let bytes = write_report(&report, id, path)?;
    Ok(serde_json::json!({"runtime_id":id,"path":path,"bytes":bytes}))
}

fn write_report(report: &Value, id: &str, path: &Path) -> Result<usize, String> {
    if !path.is_absolute() || path.file_name().is_none() {
        return Err("Choose an absolute diagnostic report file path".into());
    }
    if report["schema_version"] != 1 || report["runtime_id"].as_str() != Some(id) {
        return Err("Diagnostic report does not match the selected runtime".into());
    }
    let mut bytes = serde_json::to_vec_pretty(report).map_err(|e| e.to_string())?;
    bytes.push(b'\n');
    if bytes.len() > 2 * 1024 * 1024 {
        return Err("Diagnostic report exceeds the export size limit".into());
    }
    // create_new refuses existing files, symlinks, FIFOs and device nodes. The
    // GUI can choose a fresh name after an error; it never overwrites a report.
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)
        .map_err(|e| format!("Choose a new diagnostic report filename: {e}"))?;
    if let Err(error) = file.write_all(&bytes).and_then(|_| file.sync_all()) {
        // Remove only the inode we created, even if a user replaces its name.
        use std::os::unix::fs::MetadataExt;
        if let (Ok(opened), Ok(named)) = (file.metadata(), path.symlink_metadata()) {
            if (opened.dev(), opened.ino()) == (named.dev(), named.ino()) {
                let _ = std::fs::remove_file(path);
            }
        }
        return Err(format!("Cannot write diagnostic report: {error}"));
    }
    Ok(bytes.len())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, PermissionsExt};
    #[test]
    fn exports_private_report_and_refuses_overwrite_symlink_or_wrong_runtime() {
        let root =
            std::env::temp_dir().join(format!("anvil-diagnostic-test-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let destination = root.join("report.json");
        let report = serde_json::json!({"schema_version":1,"runtime_id":"default","sections":{}});
        write_report(&report, "default", &destination).unwrap();
        assert_eq!(
            std::fs::metadata(&destination)
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        assert!(write_report(&report, "default", &destination).is_err());
        assert!(write_report(&report, "another", &root.join("wrong.json")).is_err());
        let link = root.join("link.json");
        symlink(&destination, &link).unwrap();
        assert!(write_report(&report, "default", &link).is_err());
        assert!(write_report(&report, "default", Path::new("relative.json")).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_invalid_schema_oversize_and_unwritable_destinations_before_export() {
        let root =
            std::env::temp_dir().join(format!("anvil-diagnostic-limits-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let destination = root.join("report.json");
        let mut report = serde_json::json!({"schema_version":2,"runtime_id":"default"});
        assert!(write_report(&report, "default", &destination).is_err());
        assert!(!destination.exists());
        report["schema_version"] = 1.into();
        report["payload"] = "x".repeat(2 * 1024 * 1024).into();
        assert!(write_report(&report, "default", &destination).is_err());
        assert!(!destination.exists());
        report.as_object_mut().unwrap().remove("payload");
        assert!(write_report(&report, "default", &root.join("missing/report.json")).is_err());
        assert!(write_report(&report, "default", &root).is_err());
        let written = write_report(&report, "default", &destination).unwrap();
        assert_eq!(written, std::fs::read(&destination).unwrap().len());
        std::fs::remove_dir_all(root).unwrap();
    }
}
