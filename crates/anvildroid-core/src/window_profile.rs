//! Pure policy for saved normal-window content size in Wayland logical units.
//! Persistence and native event wiring are intentionally separate.
use serde::{Deserialize, Serialize};

pub const SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContentSize {
    pub width: u32,
    pub height: u32,
}

impl ContentSize {
    pub fn valid(self) -> bool {
        (64..=8192).contains(&self.width) && (64..=8192).contains(&self.height)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WindowProfile {
    pub version: u32,
    pub runtime: String,
    pub package: String,
    pub normal_content: ContentSize,
}

#[derive(Debug, Clone, Copy)]
pub struct WindowObservation {
    pub content: ContentSize,
    pub resizing: bool,
    pub pending_configure: bool,
    pub maximized: bool,
    pub fullscreen: bool,
    pub matching_tasks: usize,
}

impl WindowProfile {
    /// Unknown versions/invalid data are rejected, never silently migrated.
    pub fn valid(&self) -> bool {
        self.version == SCHEMA_VERSION
            && !self.runtime.is_empty()
            && self.runtime.len() <= 128
            && self
                .runtime
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
            && !self.package.is_empty()
            && self.package.len() <= 255
            && self.package.split('.').all(|part| {
                !part.is_empty() && part.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
            })
            && self.normal_content.valid()
    }

    /// Never choose between two visible tasks of the same app.
    pub fn record(&mut self, runtime: &str, package: &str, observed: WindowObservation) -> bool {
        if !self.valid()
            || self.runtime != runtime
            || self.package != package
            || !observed.content.valid()
            || observed.resizing
            || observed.pending_configure
            || observed.maximized
            || observed.fullscreen
            || observed.matching_tasks != 1
            || self.normal_content == observed.content
        {
            return false;
        }
        self.normal_content = observed.content;
        true
    }

    /// Workarea is the current content capacity, with decorations already removed.
    /// Clamping is transient: do not overwrite the saved size on a smaller output.
    pub fn restore(
        &self,
        runtime: &str,
        package: &str,
        workarea: ContentSize,
    ) -> Option<ContentSize> {
        if !self.valid() || self.runtime != runtime || self.package != package || !workarea.valid()
        {
            return None;
        }
        Some(ContentSize {
            width: self.normal_content.width.min(workarea.width),
            height: self.normal_content.height.min(workarea.height),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn profile() -> WindowProfile {
        WindowProfile {
            version: 1,
            runtime: "r-test".into(),
            package: "com.android.settings".into(),
            normal_content: ContentSize {
                width: 960,
                height: 720,
            },
        }
    }
    fn observed() -> WindowObservation {
        WindowObservation {
            content: ContentSize {
                width: 1000,
                height: 800,
            },
            resizing: false,
            pending_configure: false,
            maximized: false,
            fullscreen: false,
            matching_tasks: 1,
        }
    }
    #[test]
    fn only_stable_normal_size_is_saved() {
        for n in 0..6 {
            let mut p = profile();
            let mut o = observed();
            match n {
                0 => o.resizing = true,
                1 => o.pending_configure = true,
                2 => o.maximized = true,
                3 => o.fullscreen = true,
                4 => o.matching_tasks = 2,
                _ => o.matching_tasks = 0,
            }
            assert!(!p.record("r-test", "com.android.settings", o));
            assert_eq!(p, profile());
        }
        let mut p = profile();
        assert!(p.record("r-test", "com.android.settings", observed()));
        assert!(!p.record("r-test", "com.android.settings", observed()));
    }
    #[test]
    fn restore_is_scoped_and_clamped_without_mutation() {
        let p = profile();
        let capacity = ContentSize {
            width: 800,
            height: 600,
        };
        assert_eq!(
            p.restore("r-test", "com.android.settings", capacity),
            Some(capacity)
        );
        assert_eq!(p, profile());
        assert!(p
            .restore("r-other", "com.android.settings", capacity)
            .is_none());
        assert!(p.restore("r-test", "com.other", capacity).is_none());
        let mut p = p;
        assert!(!p.record("r-other", "com.android.settings", observed()));
    }
    #[test]
    fn invalid_versions_and_sizes_are_rejected() {
        let mut p = profile();
        p.version = 2;
        assert!(!p.valid());
        p = profile();
        p.normal_content.width = 0;
        assert!(!p.valid());
        p = profile();
        p.package = "../escape".into();
        assert!(!p.valid());
        p = profile();
        p.runtime = "../escape".into();
        assert!(!p.valid());
    }
    #[test]
    fn schema_roundtrip_and_unknown_fields() {
        let p = profile();
        assert_eq!(
            serde_json::from_str::<WindowProfile>(&serde_json::to_string(&p).unwrap()).unwrap(),
            p
        );
        let mut value = serde_json::to_value(p).unwrap();
        value["position"] = serde_json::json!([0, 0]);
        assert!(serde_json::from_value::<WindowProfile>(value).is_err());
    }
}
