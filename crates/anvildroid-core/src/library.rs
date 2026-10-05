use crate::{backend, config, error::AnvilError};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, OpenOptions},
    os::fd::AsRawFd,
    path::{Path, PathBuf},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Library {
    pub schema_version: u32,
    pub favorites: BTreeSet<String>,
    pub recent: BTreeMap<String, u64>,
    pub sort: String,
}
impl Default for Library {
    fn default() -> Self {
        Self {
            schema_version: 1,
            favorites: BTreeSet::new(),
            recent: BTreeMap::new(),
            sort: "name-asc".into(),
        }
    }
}
fn read_at(path: &Path) -> Result<Library, AnvilError> {
    let data = match fs::read(path) {
        Ok(d) => d,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Library::default()),
        Err(e) => return Err(e.into()),
    };
    let state: Library = serde_json::from_slice(&data).map_err(|e| {
        AnvilError::Config(format!(
            "Library preferences are invalid; original file retained: {e}"
        ))
    })?;
    if state.schema_version != 1
        || !valid_sort(&state.sort)
        || state.favorites.len() > 10000
        || state.recent.len() > 10000
        || state
            .favorites
            .iter()
            .chain(state.recent.keys())
            .any(|p| !backend::valid_package(p))
    {
        return Err(AnvilError::Config(
            "Unsupported library preferences; original file retained".into(),
        ));
    }
    Ok(state)
}
pub fn read() -> Result<Library, AnvilError> {
    read_at(&config::config_dir().join("library.json"))
}
fn valid_sort(value: &str) -> bool {
    matches!(
        value,
        "name-asc" | "name-desc" | "package" | "recent" | "favorites"
    )
}
fn mutate_at(dir: &Path, f: impl FnOnce(&mut Library)) -> Result<Library, AnvilError> {
    fs::create_dir_all(dir)?;
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(dir.join("library.lock"))?;
    unsafe extern "C" {
        fn flock(fd: i32, operation: i32) -> i32;
    }
    let start = Instant::now();
    while unsafe { flock(lock.as_raw_fd(), 2 | 4) } != 0 {
        if start.elapsed() > Duration::from_secs(2) {
            return Err(AnvilError::Busy("Library preferences are busy".into()));
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let path = dir.join("library.json");
    let mut state = read_at(&path)?;
    f(&mut state);
    let temp = dir.join("library.json.tmp");
    fs::write(
        &temp,
        serde_json::to_vec_pretty(&state).map_err(|e| AnvilError::Config(e.to_string()))?,
    )?;
    fs::rename(temp, path)?;
    Ok(state) // Closing the file releases the advisory lock, including errors.
}
pub fn favorite(package: &str, enabled: bool) -> Result<Library, AnvilError> {
    if !backend::valid_package(package) {
        return Err(AnvilError::InvalidArgument("Invalid package".into()));
    }
    favorite_at(&config::config_dir(), package, enabled)
}
fn favorite_at(dir: &Path, package: &str, enabled: bool) -> Result<Library, AnvilError> {
    if !backend::valid_package(package) {
        return Err(AnvilError::InvalidArgument("Invalid package".into()));
    }
    mutate_at(dir, |s| {
        if enabled {
            s.favorites.insert(package.into());
        } else {
            s.favorites.remove(package);
        }
    })
}
pub fn sort(value: &str) -> Result<Library, AnvilError> {
    if !valid_sort(value) {
        return Err(AnvilError::InvalidArgument("Invalid sort order".into()));
    }
    mutate_at(&config::config_dir(), |s| s.sort = value.into())
}
pub fn launched(package: &str) -> Result<Library, AnvilError> {
    if !backend::valid_package(package) {
        return Err(AnvilError::InvalidArgument("Invalid package".into()));
    }
    launched_at(&config::config_dir(), package)
}
fn launched_at(dir: &Path, package: &str) -> Result<Library, AnvilError> {
    if !backend::valid_package(package) {
        return Err(AnvilError::InvalidArgument("Invalid package".into()));
    }
    mutate_at(dir, |s| {
        s.recent.insert(
            package.into(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis() as u64,
        );
        if s.recent.len() > 1000 {
            if let Some(old) = s
                .recent
                .iter()
                .min_by_key(|(_, t)| *t)
                .map(|(p, _)| p.clone())
            {
                s.recent.remove(&old);
            }
        }
    })
}
pub fn valid_managed_runtime(id: &str) -> bool {
    id.len() == 34
        && id.starts_with("r-")
        && id[2..]
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn runtime_dir_at(base: &Path, id: &str) -> Result<PathBuf, AnvilError> {
    if !valid_managed_runtime(id) {
        return Err(AnvilError::InvalidArgument(
            "Invalid managed runtime ID".into(),
        ));
    }
    Ok(base.join("runtime-library").join(id))
}
// The caller verifies controller ownership before accessing managed preferences.
pub fn read_runtime(id: &str) -> Result<Library, AnvilError> {
    read_at(&runtime_dir_at(&config::config_dir(), id)?.join("library.json"))
}
pub fn favorite_runtime(id: &str, package: &str, enabled: bool) -> Result<Library, AnvilError> {
    favorite_at(
        &runtime_dir_at(&config::config_dir(), id)?,
        package,
        enabled,
    )
}
pub fn launched_runtime(id: &str, package: &str) -> Result<Library, AnvilError> {
    launched_at(&runtime_dir_at(&config::config_dir(), id)?, package)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn managed_preferences_stay_separate_and_survive_reopen() {
        let base =
            std::env::temp_dir().join(format!("anvil-scoped-library-{}", std::process::id()));
        let one = runtime_dir_at(&base, &format!("r-{}", "1".repeat(32))).unwrap();
        let two = runtime_dir_at(&base, &format!("r-{}", "2".repeat(32))).unwrap();
        favorite_at(&base, "com.example.same", true).unwrap();
        favorite_at(&one, "com.example.same", true).unwrap();
        launched_at(&one, "com.example.same").unwrap();
        assert!(read_at(&one.join("library.json"))
            .unwrap()
            .recent
            .contains_key("com.example.same"));
        assert!(read_at(&two.join("library.json"))
            .unwrap()
            .favorites
            .is_empty());
        favorite_at(&one, "com.example.same", false).unwrap();
        assert!(read_at(&base.join("library.json"))
            .unwrap()
            .favorites
            .contains("com.example.same"));
        fs::write(one.join("library.json"), b"broken").unwrap();
        assert!(favorite_at(&one, "com.example.same", true).is_err());
        favorite_at(&two, "com.example.same", true).unwrap();
        assert_eq!(fs::read(one.join("library.json")).unwrap(), b"broken");
        for invalid in [
            "default",
            "../other",
            "r-bad",
            "r-../../../../../../../../../../x",
        ] {
            assert!(runtime_dir_at(&base, invalid).is_err());
        }
        fs::remove_dir_all(base).unwrap();
    }
    #[test]
    fn persistence_and_corrupt_file_preservation() {
        let dir = std::env::temp_dir().join(format!("anvil-library-{}", std::process::id()));
        let state = mutate_at(&dir, |s| {
            s.favorites.insert("com.example.test".into());
            s.recent.insert("com.example.test".into(), 42);
            s.sort = "recent".into();
        })
        .unwrap();
        assert_eq!(
            read_at(&dir.join("library.json")).unwrap().favorites,
            state.favorites
        );
        assert_eq!(
            read_at(&dir.join("library.json")).unwrap().recent["com.example.test"],
            42
        );
        assert_eq!(read_at(&dir.join("library.json")).unwrap().sort, "recent");
        fs::write(dir.join("library.json"), b"broken").unwrap();
        assert!(mutate_at(&dir, |_| {}).is_err());
        assert_eq!(fs::read(dir.join("library.json")).unwrap(), b"broken");
        fs::remove_dir_all(dir).unwrap();
    }
}
