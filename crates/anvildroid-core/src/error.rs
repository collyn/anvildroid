use std::fmt;

/// Unified error type for all AnvilDroid backend operations.
#[derive(Debug)]
pub enum AnvilError {
    /// Backend binary (Waydroid) not found or cannot execute.
    BackendNotFound(String),
    /// Backend command returned a failure exit code.
    BackendFailed(String),
    /// D-Bus session probe failed or returned unexpected data.
    SessionProbe(String),
    /// Session service is missing; recovery instructions attached.
    SessionMissing(String),
    /// Invalid argument supplied by the caller.
    InvalidArgument(String),
    /// File I/O error (config, prop files, APK).
    Io(String),
    /// Timeout waiting for a backend operation.
    Timeout(String),
    /// An operation is already in progress (guard conflict).
    Busy(String),
    /// Configuration file is corrupt or has incompatible schema.
    Config(String),
}

impl AnvilError {
    /// Machine-readable error code for JSON output.
    pub fn code(&self) -> &'static str {
        match self {
            Self::BackendNotFound(_) => "BACKEND_NOT_FOUND",
            Self::BackendFailed(_) => "BACKEND_FAILED",
            Self::SessionProbe(_) => "SESSION_PROBE",
            Self::SessionMissing(_) => "SESSION_MISSING",
            Self::InvalidArgument(_) => "INVALID_ARGUMENT",
            Self::Io(_) => "IO_ERROR",
            Self::Timeout(_) => "TIMEOUT",
            Self::Busy(_) => "BUSY",
            Self::Config(_) => "CONFIG_ERROR",
        }
    }

    /// Human-readable recovery hint, if applicable.
    pub fn recovery_hint(&self) -> Option<&str> {
        match self {
            Self::BackendNotFound(_) => Some("Install Waydroid as described in README.md."),
            Self::SessionMissing(hint) => Some(hint.as_str()),
            Self::Busy(_) => Some("Wait for the current operation to complete."),
            _ => None,
        }
    }
}

impl fmt::Display for AnvilError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::BackendNotFound(msg)
            | Self::BackendFailed(msg)
            | Self::SessionProbe(msg)
            | Self::SessionMissing(msg)
            | Self::InvalidArgument(msg)
            | Self::Io(msg)
            | Self::Timeout(msg)
            | Self::Busy(msg)
            | Self::Config(msg) => write!(f, "{msg}"),
        }
    }
}

impl std::error::Error for AnvilError {}

impl From<std::io::Error> for AnvilError {
    fn from(e: std::io::Error) -> Self {
        Self::Io(e.to_string())
    }
}
