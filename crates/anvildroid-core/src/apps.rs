//! Narrow Android shell-UID companion; no privileged host process or shell text.
use crate::{backend, config, error::AnvilError, launch};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::Path,
    process::Command,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppDetails {
    pub package: String,
    pub installed: bool,
    pub version_code: Option<u64>,
    pub version_name: Option<String>,
    pub abi: Option<String>,
    pub stopped: Option<bool>,
    pub profile: Option<String>,
}

fn field<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    text.lines()
        .map(str::trim)
        .find_map(|line| line.strip_prefix(key))
}

pub fn parse_details(package: &str, text: &str) -> AppDetails {
    let marker = format!("Package [{package}]");
    let section = text.find(&marker).map(|i| &text[i..]);
    let section = section
        .and_then(|s| s.split("\n  Package [").next())
        .unwrap_or("");
    let user = field(section, "User 0:").unwrap_or("");
    AppDetails {
        package: package.into(),
        installed: !section.is_empty() && user.split_whitespace().any(|s| s == "installed=true"),
        version_code: field(section, "versionCode=")
            .and_then(|v| v.split_whitespace().next()?.parse().ok()),
        version_name: field(section, "versionName=")
            .filter(|v| *v != "null")
            .map(String::from),
        abi: field(section, "primaryCpuAbi=")
            .filter(|v| *v != "null")
            .map(String::from),
        stopped: user
            .split_whitespace()
            .find_map(|s| s.strip_prefix("stopped="))
            .and_then(|s| s.parse().ok()),
        profile: None,
    }
}

fn request(action: &str, package: &str, apk: Option<&Path>) -> Result<String, AnvilError> {
    if !backend::valid_package(package) {
        return Err(AnvilError::InvalidArgument("Invalid package".into()));
    }
    let base = dirs::data_dir()
        .ok_or_else(|| AnvilError::Config("No user data directory".into()))?
        .join("waydroid/data/waydroid_tmp/anvildroid/apps");
    if !base.is_dir() {
        return Err(AnvilError::BackendFailed("App-management companion is unavailable. Install the P5.2 native release and restart Waydroid.".into()));
    }
    let id = format!(
        "job-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    );
    let staging = base.join(format!("{id}.staging"));
    let ready = base.join(format!("{id}.ready"));
    let working = base.join(format!("{id}.working"));
    fs::create_dir(&staging)?;
    // The shell service needs group access to the host-created request.
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(&staging, fs::Permissions::from_mode(0o2770))?;
    let result = (|| {
        fs::write(staging.join("action"), format!("{action}\n"))?;
        fs::write(staging.join("package"), format!("{package}\n"))?;
        if let Some(apk) = apk {
            fs::copy(apk, staging.join("input.apk"))?;
        }
        for file in fs::read_dir(&staging)? {
            fs::set_permissions(file?.path(), fs::Permissions::from_mode(0o660))?;
        }
        fs::rename(&staging, &ready)?;
        let timeout = if action == "install" { 120 } else { 15 };
        let started = Instant::now();
        loop {
            match fs::read_to_string(working.join("status")) {
                Ok(status) => {
                    let text = fs::read_to_string(working.join("output"))?;
                    if status.trim() != "0" {
                        return Err(AnvilError::BackendFailed(format!(
                            "Android {action} failed: {}",
                            text.trim()
                        )));
                    }
                    return Ok(text);
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => return Err(e.into()),
            }
            if started.elapsed() >= Duration::from_secs(timeout) {
                // Do not delete an in-flight install or claim that it was cancelled.
                return Err(AnvilError::Timeout(format!(
                    "Android {action} timed out; it may still complete. Refresh before retrying."
                )));
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    })();
    let _ = fs::remove_dir_all(staging);
    if working.join("status").is_file() {
        let _ = fs::remove_dir_all(working);
    }
    result
}

pub fn details(package: &str) -> Result<AppDetails, AnvilError> {
    let text = request("details", package, None)?;
    if !text.contains(&format!("Package [{package}]"))
        && !text
            .lines()
            .any(|line| line.trim() == format!("Unable to find package: {package}"))
    {
        return Err(AnvilError::BackendFailed(
            "Android returned an unrecognized package query; installation state is unknown.".into(),
        ));
    }
    Ok(parse_details(package, &text))
}

pub fn force_stop(package: &str) -> Result<(), AnvilError> {
    if let Some(reason) = backend::uninstall_block_reason(package) {
        return Err(AnvilError::InvalidArgument(reason.into()));
    }
    launch::ensure_ready(launch::BOOT_TIMEOUT)?;
    if !details(package)?.installed {
        return Err(AnvilError::InvalidArgument("App is not installed".into()));
    }
    request("force-stop", package, None)?;
    if details(package)?.stopped != Some(true) {
        return Err(AnvilError::BackendFailed(
            "Force-stop could not be confirmed".into(),
        ));
    }
    Ok(())
}

pub fn apk_identity(text: &str) -> Result<(String, u64), AnvilError> {
    let line = text
        .lines()
        .find(|s| s.starts_with("package: "))
        .ok_or_else(|| AnvilError::InvalidArgument("Cannot read APK manifest".into()))?;
    let attr = |key: &str| {
        line.split_once(&format!("{key}='"))
            .and_then(|(_, s)| s.split('\'').next())
    };
    let package = attr("name")
        .filter(|p| backend::valid_package(p))
        .ok_or_else(|| AnvilError::InvalidArgument("Invalid APK package".into()))?;
    let version: u64 = attr("versionCode")
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| AnvilError::InvalidArgument("Invalid APK versionCode".into()))?;
    let major: u64 = attr("versionCodeMajor")
        .map(str::parse)
        .transpose()
        .map_err(|_| AnvilError::InvalidArgument("Invalid versionCodeMajor".into()))?
        .unwrap_or(0);
    if major > u32::MAX as u64 || version > u32::MAX as u64 {
        return Err(AnvilError::InvalidArgument(
            "APK version out of range".into(),
        ));
    }
    Ok((package.into(), (major << 32) | version))
}

pub fn install(path: &Path) -> Result<AppDetails, AnvilError> {
    backend::backend_args(&["install".into(), path.as_os_str().to_owned()])?;
    let path = path.canonicalize()?;
    // Snapshot before inspection to ensure the installed bytes match the manifest.
    let staging = config::cache_dir().join(format!(
        "apk-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    fs::create_dir_all(&staging)?;
    let apk = staging.join("input.apk");
    let result = (|| {
        fs::copy(path, &apk)?;
        let output = launch::output_timeout(
            Command::new("aapt").args(["dump", "badging"]).arg(&apk),
            Duration::from_secs(15),
        )?;
        if !output.status.success() {
            return Err(AnvilError::InvalidArgument(format!(
                "Invalid APK: {}",
                String::from_utf8_lossy(&output.stderr)
            )));
        }
        let (package, version) = apk_identity(&String::from_utf8_lossy(&output.stdout))?;
        if backend::uninstall_block_reason(&package).is_some() {
            return Err(AnvilError::InvalidArgument(
                "Runtime components must be updated through the runtime installer.".into(),
            ));
        }
        launch::ensure_ready(launch::BOOT_TIMEOUT)?;
        let response = request("install", &package, Some(&apk))?;
        if !response.lines().any(|s| s.trim() == "Success") {
            return Err(AnvilError::BackendFailed(format!(
                "Installation was not confirmed: {}",
                response.trim()
            )));
        }
        let info = details(&package)?;
        if !info.installed || info.version_code != Some(version) {
            return Err(AnvilError::BackendFailed(
                "Installed package/version does not match the APK. Refresh Android App Info."
                    .into(),
            ));
        }
        Ok(info)
    })();
    let _ = fs::remove_dir_all(staging);
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn metadata_and_user_state() {
        let text = "Packages:\n  Package [com.example.test] (abc):\n    versionCode=23 minSdk=23\n    versionName=2.3\n    primaryCpuAbi=null\n    User 0: installed=true hidden=false stopped=true\n";
        let d = parse_details("com.example.test", text);
        assert!(d.installed);
        assert_eq!(d.version_code, Some(23));
        assert_eq!(d.abi, None);
        assert_eq!(d.stopped, Some(true));
        assert!(!parse_details("com.other.app", text).installed);
        assert!(
            !parse_details(
                "com.example.test",
                &text.replace("installed=true", "installed=false")
            )
            .installed
        );
    }
    #[test]
    fn manifest_identity() {
        assert_eq!(
            apk_identity("package: name='com.example.test' versionCode='12' versionName='x'")
                .unwrap(),
            ("com.example.test".into(), 12)
        );
        assert!(apk_identity("package: name='bad;cmd' versionCode='12'").is_err());
        assert!(apk_identity("garbage").is_err());
    }
}
