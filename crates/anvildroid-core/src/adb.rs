//! Host ADB runs as the desktop user, sharing the SDK server and auth keys.
use std::{path::PathBuf, process::Command, time::Duration};
use serde::Serialize;
use crate::{error::AnvilError, launch::output_timeout};

#[derive(Serialize)]
pub struct HostReport {
    pub available: bool,
    pub executable: Option<String>,
    pub devices: Vec<Device>,
    pub message: String,
}
#[derive(Serialize)]
pub struct Device { pub endpoint: String, pub state: String }

fn executable(custom: Option<&str>) -> Result<PathBuf, AnvilError> {
    let mut paths = Vec::new();
    if let Some(value) = custom.filter(|s| !s.trim().is_empty()) {
        let path = PathBuf::from(value.trim());
        if !path.is_absolute() || path.file_name().and_then(|n| n.to_str()) != Some("adb") || !path.is_file() {
            return Err(AnvilError::Io("Choose the absolute path to platform-tools/adb".into()));
        }
        return Ok(path);
    }
    for name in ["ANDROID_HOME", "ANDROID_SDK_ROOT"] {
        if let Some(root) = std::env::var_os(name) { paths.push(PathBuf::from(root).join("platform-tools/adb")); }
    }
    if let Some(home) = std::env::var_os("HOME") { paths.push(PathBuf::from(home).join("Android/Sdk/platform-tools/adb")); }
    if let Some(path) = std::env::var_os("PATH") {
        paths.extend(std::env::split_paths(&path).map(|p| p.join("adb")));
    }
    paths.into_iter().find(|p| p.is_absolute() && p.is_file())
        .ok_or_else(|| AnvilError::Io("ADB not found. Install Android SDK Platform-Tools or select its adb path.".into()))
}
fn valid_endpoint(endpoint: &str) -> bool {
    endpoint.strip_prefix("127.0.0.1:")
        .and_then(|p| p.parse::<u16>().ok()).is_some_and(|p| p > 0)
}
fn run(path: &PathBuf, args: &[&str]) -> Result<String, AnvilError> {
    let output = output_timeout(Command::new(path).args(args), Duration::from_secs(12))?;
    let text = format!("{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    if !output.status.success() { return Err(AnvilError::Io(text.trim().to_string())); }
    Ok(text.trim().to_string())
}
pub fn status(custom: Option<&str>) -> Result<HostReport, AnvilError> {
    let path = match executable(custom) {
        Ok(path) => path,
        Err(error) => return Ok(HostReport {available:false, executable:None, devices:vec![], message:error.to_string()}),
    };
    let output = run(&path, &["devices", "-l"])?;
    let devices = output.lines().filter_map(|line| {
        let mut parts = line.split_whitespace();
        let endpoint = parts.next()?;
        let state = parts.next()?;
        if !valid_endpoint(endpoint) || !["device", "offline", "unauthorized", "authorizing"].contains(&state) { return None; }
        Some(Device {endpoint:endpoint.into(), state:state.into()})
    }).collect();
    Ok(HostReport {available:true, executable:Some(path.to_string_lossy().into()), devices, message:String::new()})
}
pub fn action(action: &str, endpoint: &str, custom: Option<&str>) -> Result<String, AnvilError> {
    if !["connect", "disconnect"].contains(&action) || !valid_endpoint(endpoint) {
        return Err(AnvilError::Io("Invalid local ADB action".into()));
    }
    run(&executable(custom)?, &[action, endpoint])
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn endpoints_only_allow_local_tcp_and_a_real_port() {
        assert!(valid_endpoint("127.0.0.1:40000"));
        for value in ["127.0.0.1:0", "127.0.0.1:65536", "192.168.1.1:5555", "-a", "127.0.0.1:1 shell", "localhost:5555"] {
            assert!(!valid_endpoint(value), "{value}");
        }
    }
    #[test]
    fn rejects_relative_or_non_adb_custom_path() {
        assert!(executable(Some("adb")).is_err());
        assert!(executable(Some("/bin/sh")).is_err());
    }
}
