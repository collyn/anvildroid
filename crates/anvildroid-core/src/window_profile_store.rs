//! User-owned profile files. One atomic file per runtime/package; no shared
//! read-modify-write index, so independent apps cannot lose each other's updates.
use crate::window_profile::{ContentSize, WindowProfile, SCHEMA_VERSION};
use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    os::unix::fs::{DirBuilderExt, OpenOptionsExt},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

static NEXT: AtomicU64 = AtomicU64::new(0);
const MAX_BYTES: u64 = 4096;

fn invalid() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "Invalid window profile or scope",
    )
}

/// The parent of this directory must be trusted user-owned application state.
/// This is not an API for privileged writes into untrusted directories.
pub struct WindowProfileStore {
    root: PathBuf,
}

impl WindowProfileStore {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }

    pub fn user_default() -> Self {
        Self::new(crate::config::state_dir().join("window-profiles"))
    }

    fn path(&self, runtime: &str, package: &str) -> io::Result<PathBuf> {
        let scope = WindowProfile {
            version: SCHEMA_VERSION,
            runtime: runtime.into(),
            package: package.into(),
            normal_content: ContentSize {
                width: 64,
                height: 64,
            },
        };
        if !scope.valid() {
            return Err(invalid());
        }
        // No extension: a valid 255-byte package remains within NAME_MAX.
        Ok(self.root.join(runtime).join(package))
    }

    fn directory(path: &Path, create: bool) -> io::Result<()> {
        if create {
            match fs::DirBuilder::new().mode(0o700).create(path) {
                Ok(()) => (),
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => (),
                Err(e) => return Err(e),
            }
        }
        if !fs::symlink_metadata(path)?.file_type().is_dir() {
            return Err(invalid());
        }
        Ok(())
    }

    pub fn load(&self, runtime: &str, package: &str) -> io::Result<Option<WindowProfile>> {
        let path = self.path(runtime, package)?;
        let read = || -> io::Result<WindowProfile> {
            Self::directory(&self.root, false)?;
            Self::directory(path.parent().unwrap(), false)?;
            let file = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
                .open(&path)?;
            if !file.metadata()?.is_file() || file.metadata()?.len() > MAX_BYTES {
                return Err(invalid());
            }
            let mut bytes = Vec::new();
            file.take(MAX_BYTES + 1).read_to_end(&mut bytes)?;
            if bytes.len() as u64 > MAX_BYTES {
                return Err(invalid());
            }
            let profile: WindowProfile = serde_json::from_slice(&bytes).map_err(|_| invalid())?;
            if !profile.valid() || profile.runtime != runtime || profile.package != package {
                return Err(invalid());
            }
            Ok(profile)
        };
        match read() {
            Ok(profile) => Ok(Some(profile)),
            Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e), // Corrupt/unknown schema is not silently replaced.
        }
    }

    pub fn save(&self, profile: &WindowProfile) -> io::Result<()> {
        if !profile.valid() {
            return Err(invalid());
        }
        let path = self.path(&profile.runtime, &profile.package)?;
        let bytes = serde_json::to_vec(profile).map_err(|_| invalid())?;
        if bytes.len() as u64 > MAX_BYTES {
            return Err(invalid());
        }
        Self::directory(&self.root, true)?;
        let parent = path.parent().unwrap();
        Self::directory(parent, true)?;
        // Require the existing profile to be readable/valid before replacing it.
        self.load(&profile.runtime, &profile.package)?;
        let (temporary, mut file) = loop {
            let candidate = parent.join(format!(
                ".pending-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            match OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&candidate)
            {
                Ok(file) => break (candidate, file),
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e),
            }
        };
        let result = (|| {
            file.write_all(&bytes)?;
            file.sync_all()?;
            fs::rename(&temporary, &path)?;
            File::open(parent)?.sync_all()
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let p = std::env::temp_dir().join(format!(
                "anvil-profile-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&p).unwrap();
            Self(p)
        }
        fn store(&self) -> WindowProfileStore {
            WindowProfileStore::new(self.0.join("profiles"))
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn profile() -> WindowProfile {
        WindowProfile {
            version: 1,
            runtime: "r-one".into(),
            package: "com.test".into(),
            normal_content: ContentSize {
                width: 960,
                height: 720,
            },
        }
    }
    #[test]
    fn persists_across_instances_and_preserves_other_scopes() {
        let f = Fixture::new();
        let s = f.store();
        let mut p = profile();
        assert_eq!(s.load(&p.runtime, &p.package).unwrap(), None);
        s.save(&p).unwrap();
        assert_eq!(
            f.store().load(&p.runtime, &p.package).unwrap(),
            Some(p.clone())
        );
        p.runtime = "r-two".into();
        p.normal_content.width = 800;
        s.save(&p).unwrap();
        assert_eq!(s.load("r-one", "com.test").unwrap(), Some(profile()));
        p.normal_content.width = 1200;
        s.save(&p).unwrap();
        assert_eq!(s.load("r-two", "com.test").unwrap(), Some(p));
        assert_eq!(fs::read_dir(s.root.join("r-two")).unwrap().count(), 1);
    }
    #[test]
    fn rejects_corruption_and_unknown_version_without_overwriting() {
        let f = Fixture::new();
        let s = f.store();
        let p = profile();
        s.save(&p).unwrap();
        let path = s.path(&p.runtime, &p.package).unwrap();
        for data in [
            b"broken".to_vec(),
            vec![b' '; 4097],
            serde_json::to_vec(&WindowProfile {
                version: 2,
                ..p.clone()
            })
            .unwrap(),
        ] {
            fs::write(&path, &data).unwrap();
            assert!(s.load(&p.runtime, &p.package).is_err());
            assert!(s.save(&p).is_err());
            assert_eq!(fs::read(&path).unwrap(), data);
        }
    }
    #[test]
    fn rejects_traversal_symlink_and_mismatched_scope() {
        let f = Fixture::new();
        let s = f.store();
        let p = profile();
        s.save(&p).unwrap();
        assert!(s.load("../escape", "com.test").is_err());
        let path = s.path(&p.runtime, &p.package).unwrap();
        fs::write(
            &path,
            serde_json::to_vec(&WindowProfile {
                runtime: "r-other".into(),
                ..p.clone()
            })
            .unwrap(),
        )
        .unwrap();
        assert!(s.load(&p.runtime, &p.package).is_err());
        fs::remove_file(&path).unwrap();
        symlink(f.0.join("outside"), &path).unwrap();
        assert!(s.save(&p).is_err());
        assert!(!f.0.join("outside").exists());
    }
}
