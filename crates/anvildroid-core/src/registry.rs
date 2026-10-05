//! P5.3 foundation. Import existing Waydroid, never create/reset Android here.
use crate::{config, error::AnvilError};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    fs::{self, File, OpenOptions},
    os::fd::AsRawFd,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum ImageKind {
    Vanilla,
    Gapps,
    Foss,
    Unknown,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum Renderer {
    Vulkan,
    Gles,
    Unknown,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, Default)]
pub struct ResourceLimits {
    pub memory_mib: Option<u64>,
    pub cpu_count: Option<u32>,
    pub cpu_quota_percent: Option<u32>,
    pub disk_gib: Option<u64>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RuntimeSettings {
    pub image_kind: ImageKind,
    pub renderer: Renderer,
    pub gpu_device: Option<PathBuf>,
    pub limits: ResourceLimits,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum Backend {
    LegacyWaydroid,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RuntimeRecord {
    pub id: String,
    pub name: String,
    pub backend: Backend,
    pub work_dir: PathBuf,
    pub data_dir: PathBuf,
    /// None means no AnvilDroid settings have been requested/applied yet.
    pub desired: Option<RuntimeSettings>,
    pub last_applied: Option<RuntimeSettings>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Registry {
    pub schema_version: u32,
    pub default_runtime_id: String,
    pub runtimes: Vec<RuntimeRecord>,
}
#[derive(Clone, Debug, Serialize)]
pub struct ConfiguredSnapshot {
    pub native_bridge: Option<String>,
    pub settings: RuntimeSettings,
    pub images_dir: PathBuf,
    pub system_channel: Option<String>,
    pub system_image_timestamp: Option<u64>,
    pub vendor_image_timestamp: Option<u64>,
}
#[derive(Clone, Debug, Serialize)]
pub struct Catalog {
    pub registry: Registry,
    /// File configuration only: not a claim of actual renderer/resource limits.
    pub configured: ConfiguredSnapshot,
    pub concurrent_supported: bool,
    pub management_note: String,
    pub container_name: String,
    pub session_bus_name: String,
    pub container_bus_name: String,
}
fn invalid(message: &str) -> AnvilError {
    AnvilError::Config(message.into())
}
fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
}
fn validate_settings(s: &RuntimeSettings) -> Result<(), AnvilError> {
    let l = &s.limits;
    if l.memory_mib == Some(0)
        || l.cpu_count == Some(0)
        || l.cpu_quota_percent == Some(0)
        || l.disk_gib == Some(0)
    {
        return Err(invalid("Resource limits must be positive or unspecified"));
    }
    if let Some(gpu) = &s.gpu_device {
        if gpu.parent() != Some(Path::new("/dev/dri"))
            || !gpu.file_name().and_then(|s| s.to_str()).is_some_and(|s| {
                s.strip_prefix("renderD")
                    .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
            })
        {
            return Err(invalid("GPU must identify a /dev/dri/renderD device"));
        }
    }
    Ok(())
}
impl Registry {
    pub fn validate(&self) -> Result<(), AnvilError> {
        if self.schema_version != 1 {
            return Err(invalid(
                "Unsupported runtime registry schema; file retained",
            ));
        }
        if self.runtimes.is_empty() || self.runtimes.len() > 128 {
            return Err(invalid("Invalid runtime count"));
        }
        let mut ids = BTreeSet::new();
        for r in &self.runtimes {
            if !valid_id(&r.id)
                || !ids.insert(&r.id)
                || r.name.trim().is_empty()
                || r.name.len() > 128
                || r.name.chars().any(char::is_control)
                || !r.work_dir.is_absolute()
                || !r.data_dir.is_absolute()
            {
                return Err(invalid("Invalid or duplicate runtime record"));
            }
            for settings in [&r.desired, &r.last_applied].into_iter().flatten() {
                validate_settings(settings)?;
            }
        }
        if !ids.contains(&self.default_runtime_id) {
            return Err(invalid("Default runtime does not exist"));
        }
        Ok(())
    }
    /// Until endpoint isolation exists, fail closed instead of routing another ID
    /// into the user's live singleton Waydroid instance.
    pub fn resolve_legacy(&self, id: &str) -> Result<&RuntimeRecord, AnvilError> {
        self.validate()?;
        let record = self
            .runtimes
            .iter()
            .find(|r| r.id == id)
            .ok_or_else(|| invalid("Unknown runtime ID"))?;
        if record.id != "default" || record.work_dir != Path::new("/var/lib/waydroid") {
            return Err(invalid(
                "This runtime needs an isolated backend; the legacy adapter only serves default",
            ));
        }
        Ok(record)
    }
}
fn ini_value<'a>(content: &'a str, section: &str, key: &str) -> Option<&'a str> {
    let mut current = "";
    for line in content.lines().map(str::trim) {
        if line.starts_with('#') || line.starts_with(';') {
            continue;
        }
        if let Some(s) = line.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
            current = s;
            continue;
        }
        if current == section {
            if let Some((k, v)) = line.split_once('=') {
                if k.trim() == key {
                    return Some(v.trim());
                }
            }
        }
    }
    None
}
pub fn configured_snapshot(cfg: &str, prop: &str) -> Result<ConfiguredSnapshot, AnvilError> {
    let channel = ini_value(cfg, "waydroid", "system_ota").map(String::from);
    let image_kind = match channel.as_deref().and_then(|s| s.rsplit('/').next()) {
        Some("VANILLA.json") => ImageKind::Vanilla,
        Some("GAPPS.json") => ImageKind::Gapps,
        Some("FOSS.json") => ImageKind::Foss,
        _ => ImageKind::Unknown,
    };
    let images_dir = PathBuf::from(
        ini_value(cfg, "waydroid", "images_path")
            .ok_or_else(|| invalid("Waydroid images_path is missing"))?,
    );
    if !images_dir.is_absolute() {
        return Err(invalid("Waydroid images_path must be absolute"));
    }
    let vulkan = prop
        .lines()
        .find_map(|l| l.trim().strip_prefix("ro.hardware.vulkan="));
    let snapshot = ConfiguredSnapshot {
        native_bridge: prop
            .lines()
            .find_map(|line| line.trim().strip_prefix("ro.dalvik.vm.native.bridge="))
            .filter(|v| !v.is_empty() && *v != "0")
            .map(String::from),
        settings: RuntimeSettings {
            image_kind,
            renderer: match vulkan {
                Some("") => Renderer::Gles,
                Some(_) => Renderer::Vulkan,
                None => Renderer::Unknown,
            },
            gpu_device: ini_value(cfg, "waydroid", "drm_device")
                .filter(|s| !s.is_empty())
                .map(PathBuf::from),
            limits: ResourceLimits::default(),
        },
        images_dir,
        system_channel: channel,
        system_image_timestamp: ini_value(cfg, "waydroid", "system_datetime")
            .and_then(|s| s.parse().ok()),
        vendor_image_timestamp: ini_value(cfg, "waydroid", "vendor_datetime")
            .and_then(|s| s.parse().ok()),
    };
    validate_settings(&snapshot.settings)?;
    Ok(snapshot)
}
fn lock(dir: &Path) -> Result<File, AnvilError> {
    fs::create_dir_all(dir)?;
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(dir.join("runtimes.lock"))?;
    unsafe extern "C" {
        fn flock(fd: i32, op: i32) -> i32;
    }
    let started = Instant::now();
    while unsafe { flock(file.as_raw_fd(), 2 | 4) } != 0 {
        if started.elapsed() > Duration::from_secs(2) {
            return Err(invalid("Runtime registry is busy"));
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    Ok(file)
}
pub(crate) fn load_or_import(dir: &Path, data_dir: PathBuf) -> Result<Registry, AnvilError> {
    let _lock = lock(dir)?;
    let path = dir.join("runtimes.json");
    match fs::read(&path) {
        Ok(bytes) => {
            let registry: Registry = serde_json::from_slice(&bytes)
                .map_err(|e| invalid(&format!("Invalid runtime registry; file retained: {e}")))?;
            registry.validate()?;
            Ok(registry)
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            let registry = Registry {
                schema_version: 1,
                default_runtime_id: "default".into(),
                runtimes: vec![RuntimeRecord {
                    id: "default".into(),
                    name: "Waydroid (existing)".into(),
                    backend: Backend::LegacyWaydroid,
                    work_dir: "/var/lib/waydroid".into(),
                    data_dir,
                    desired: None,
                    last_applied: None,
                }],
            };
            registry.validate()?;
            let temp = dir.join("runtimes.json.tmp");
            fs::write(
                &temp,
                serde_json::to_vec_pretty(&registry).map_err(|e| invalid(&e.to_string()))?,
            )?;
            fs::rename(temp, path)?;
            Ok(registry)
        }
        Err(e) => Err(e.into()),
    }
}
pub fn catalog() -> Result<Catalog, AnvilError> {
    let cfg = fs::read_to_string("/var/lib/waydroid/waydroid.cfg")?;
    let prop = fs::read_to_string("/var/lib/waydroid/waydroid.prop")?;
    let configured = configured_snapshot(&cfg, &prop)?;
    let data = dirs::data_dir()
        .ok_or_else(|| invalid("No user data directory"))?
        .join("waydroid/data");
    let registry = load_or_import(&config::config_dir(), data.clone())?;
    let record = registry.resolve_legacy(&registry.default_runtime_id)?;
    if record.data_dir != data {
        return Err(invalid("Registered data path differs from the current user's Waydroid path; refusing ambiguous routing"));
    }
    Ok(Catalog { registry,configured,concurrent_supported:false,
        management_note:"Existing runtime uses system Waydroid. Its GPU, RAM/CPU and installed ARM settings are managed on the Runtime page through the runtime controller.".into(),
        container_name:"waydroid".into(),session_bus_name:"id.waydro.Session".into(),container_bus_name:"id.waydro.Container".into(),
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn import_is_idempotent_and_preserves_other_config() {
        let dir = std::env::temp_dir().join(format!("anvil-registry-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("config.json"), "original").unwrap();
        let r = load_or_import(&dir, "/home/test/data".into()).unwrap();
        assert_eq!(r.default_runtime_id, "default");
        assert!(r.runtimes[0].last_applied.is_none());
        let bytes = fs::read(dir.join("runtimes.json")).unwrap();
        load_or_import(&dir, "/different/path".into()).unwrap();
        assert_eq!(bytes, fs::read(dir.join("runtimes.json")).unwrap());
        assert_eq!(
            fs::read_to_string(dir.join("config.json")).unwrap(),
            "original"
        );
        fs::write(dir.join("runtimes.json"), "broken").unwrap();
        assert!(load_or_import(&dir, "/data".into()).is_err());
        assert_eq!(
            fs::read_to_string(dir.join("runtimes.json")).unwrap(),
            "broken"
        );
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn snapshot_does_not_invent_effective_limits() {
        let s = configured_snapshot(
            "[waydroid]\nimages_path=/images\nsystem_ota=https://example/GAPPS.json\n",
            "ro.hardware.vulkan=radeon\n",
        )
        .unwrap();
        assert_eq!(s.settings.image_kind, ImageKind::Gapps);
        assert_eq!(s.settings.renderer, Renderer::Vulkan);
        assert!(s.settings.limits.memory_mib.is_none());
        assert!(s.settings.gpu_device.is_none());
        assert!(configured_snapshot("[waydroid]\nimages_path=relative", "").is_err());
    }
    #[test]
    fn reject_unsupported_routing_and_schemas() {
        let mut r = Registry {
            schema_version: 1,
            default_runtime_id: "default".into(),
            runtimes: vec![RuntimeRecord {
                id: "default".into(),
                name: "Current".into(),
                backend: Backend::LegacyWaydroid,
                work_dir: "/var/lib/waydroid".into(),
                data_dir: "/data".into(),
                desired: None,
                last_applied: None,
            }],
        };
        assert!(r.resolve_legacy("default").is_ok());
        assert!(r.resolve_legacy("other").is_err());
        r.runtimes.push(r.runtimes[0].clone());
        assert!(r.validate().is_err());
        r.runtimes.pop();
        r.schema_version = 99;
        assert!(r.validate().is_err());
        r.schema_version = 1;
        r.runtimes[0].work_dir = "/other".into();
        assert!(r.resolve_legacy("default").is_err());
    }
}
