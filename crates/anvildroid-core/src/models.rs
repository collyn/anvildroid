use serde::{Deserialize, Serialize};

/// Schema version for configuration and serialized state.
pub const SCHEMA_VERSION: u32 = 1;

/// Fine-grained runtime state that distinguishes backend installation,
/// container lifecycle, and session service availability.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", content = "detail")]
pub enum RuntimeState {
    /// Waydroid binary not found on PATH.
    NotInstalled,
    /// Container and session are stopped.
    Stopped,
    /// Session start requested but Android boot not yet confirmed.
    Starting,
    /// Session and container running; session service present on D-Bus.
    Running,
    /// Container FROZEN but session service still present on D-Bus.
    Frozen,
    /// Container running/frozen but session D-Bus service is missing.
    SessionLost,
    /// Unexpected or unrecognized backend state.
    Error(String),
}

/// Aggregated runtime status returned by status queries.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeStatus {
    pub state: RuntimeState,
    /// Whether id.waydro.Session owns a name on the desktop D-Bus.
    pub session_service: bool,
    /// Raw container state string from `waydroid status`, if available.
    pub container_state: Option<String>,
    /// Raw session state string from `waydroid status`, if available.
    pub session_state: Option<String>,
    /// Current GPU rendering mode.
    pub gpu_mode: Option<GpuMode>,
}

/// GPU rendering mode for the Waydroid container.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
pub enum GpuMode {
    /// AMD hardware Vulkan rendering (fast; ARM32 games may crash on resize).
    Vulkan,
    /// Vulkan disabled; stable but ARM32 games render on CPU.
    Gles,
}

/// Minimal app information from the Android package list.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppInfo {
    pub package: String,
    pub label: Option<String>,
    pub activity: Option<String>,
    /// Absolute path to the app icon on the host filesystem, if available.
    pub icon_path: Option<String>,
    /// An explanation when removal from the ordinary app library is blocked.
    #[serde(default)]
    pub uninstall_block_reason: Option<String>,
}

/// Result of a backend operation (install, launch, stop, etc.).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OperationResult {
    pub success: bool,
    pub message: String,
    /// Machine-readable error code for programmatic handling.
    pub error_code: Option<String>,
    /// Human-readable recovery suggestion.
    pub recovery_hint: Option<String>,
    /// The action was handed to Android and still requires user confirmation.
    #[serde(default)]
    pub pending_confirmation: bool,
}

/// Health check report combining backend and D-Bus state.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthReport {
    pub status: RuntimeStatus,
    pub healthy: bool,
    pub message: String,
    pub recovery_hint: Option<String>,
}
