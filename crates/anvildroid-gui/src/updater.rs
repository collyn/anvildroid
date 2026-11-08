//! In-app updates from GitHub Releases, installed the same way AnvilDroid was:
//! a .deb/.rpm/Arch package through pkexec, or an AppImage replaced in place.
//! Every download is checked against the release's SHA256SUMS before use.
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::Mutex,
    time::Duration,
};

const REPO: &str = "anvildroid/anvildroid";
/// Asset links may still name the repository's previous owner while GitHub
/// redirects it; both belong to this project.
const ASSET_REPOS: [&str; 2] = [REPO, "collyn/anvildroid"];
/// Signed APT/RPM/Arch repositories published by CI (scripts/publish-repos.sh).
pub const PAGES: &str = "https://anvildroid.github.io/packages";
const PACKAGE: &str = "anvildroid-controller";

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Deb,
    Rpm,
    Pacman,
    Appimage,
    Portable,
}

/// How this copy was installed. `root` is "/" except in tests.
fn kind_at(appimage: Option<PathBuf>, root: &Path, rpm_installed: impl Fn() -> bool) -> Kind {
    if appimage.is_some_and(|path| path.is_absolute() && path.is_file()) {
        return Kind::Appimage;
    }
    if root.join("var/lib/dpkg/info").join(format!("{PACKAGE}.list")).is_file() {
        return Kind::Deb;
    }
    if fs::read_dir(root.join("var/lib/pacman/local")).is_ok_and(|entries| {
        entries.flatten().any(|entry| {
            entry.file_name().to_string_lossy().strip_prefix(&format!("{PACKAGE}-"))
                .is_some_and(|rest| rest.starts_with(|c: char| c.is_ascii_digit()))
        })
    }) {
        return Kind::Pacman;
    }
    if rpm_installed() {
        return Kind::Rpm;
    }
    Kind::Portable
}

pub fn install_kind() -> Kind {
    kind_at(std::env::var_os("APPIMAGE").map(PathBuf::from), Path::new("/"), || {
        Command::new("rpm").args(["-q", PACKAGE]).stdout(Stdio::null()).stderr(Stdio::null())
            .status().is_ok_and(|status| status.success())
    })
}

/// The release asset for this install kind, by the names CI publishes.
fn asset_matches(kind: Kind, name: &str) -> bool {
    match kind {
        Kind::Deb => name.starts_with(&format!("{PACKAGE}_")) && name.ends_with("_amd64.deb"),
        Kind::Rpm => name.starts_with(&format!("{PACKAGE}-")) && name.ends_with(".x86_64.rpm"),
        Kind::Pacman => name.starts_with(&format!("{PACKAGE}-")) && name.ends_with("-x86_64.pkg.tar.zst"),
        Kind::Appimage => name.starts_with("AnvilDroid-") && name.ends_with("-x86_64.AppImage"),
        Kind::Portable => false,
    }
}

/// `latest` is newer than `current` ("v0.1.7" > "0.1.6"; "0.2.0" > "0.2.0-rc.1").
pub fn newer(latest: &str, current: &str) -> bool {
    fn parse(version: &str) -> (Vec<u64>, Option<Vec<String>>) {
        let version = version.trim().trim_start_matches('v');
        let (core, pre) = match version.split_once(['-', '~']) {
            Some((core, pre)) => (core, Some(pre.split('.').map(str::to_string).collect())),
            None => (version, None),
        };
        (core.split('.').map(|part| part.parse().unwrap_or(0)).collect(), pre)
    }
    let (mut a, pa) = parse(latest);
    let (mut b, pb) = parse(current);
    let width = a.len().max(b.len());
    a.resize(width, 0);
    b.resize(width, 0);
    if a != b {
        return a > b;
    }
    match (pa, pb) {
        (None, Some(_)) => true, // a release supersedes its prereleases
        (Some(x), Some(y)) => {
            for (p, q) in x.iter().zip(&y) {
                let ordering = match (p.parse::<u64>(), q.parse::<u64>()) {
                    (Ok(m), Ok(n)) => m.cmp(&n),
                    _ => p.cmp(q),
                };
                if ordering != std::cmp::Ordering::Equal {
                    return ordering.is_gt();
                }
            }
            x.len() > y.len()
        }
        _ => false,
    }
}

fn curl() -> Command {
    let mut command = Command::new("curl");
    command
        // AppImage libraries must not leak into the host curl.
        .env_remove("LD_LIBRARY_PATH")
        .args(["-fsSL", "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "15",
               // GitHub's release CDN returns occasional 5xx; retry those.
               "--retry", "4", "--retry-delay", "3"]);
    command
}

fn latest_release() -> Result<Value, String> {
    let output = curl()
        .args(["--max-time", "30", "-H", "Accept: application/vnd.github+json",
               "-H", "User-Agent: AnvilDroid-updater"])
        .arg(format!("https://api.github.com/repos/{REPO}/releases/latest"))
        .output()
        .map_err(|e| format!("curl is required to check for updates: {e}"))?;
    if !output.status.success() {
        return Err(format!("Cannot reach GitHub Releases: {}",
                           String::from_utf8_lossy(&output.stderr).trim()));
    }
    serde_json::from_slice(&output.stdout).map_err(|e| format!("Invalid release data: {e}"))
}

/// Pick the asset and its checksum file from a release JSON.
fn plan(release: &Value, kind: Kind) -> Option<(String, String, u64, String)> {
    let assets = release.get("assets")?.as_array()?;
    // Prefer the API asset endpoint (served even when github.com pages are
    // degraded); fall back to the browser download link. Both must belong to
    // this repository.
    let ours = |url: &&str, prefix: &str| {
        ASSET_REPOS.iter().any(|repo| url.starts_with(&prefix.replace("{repo}", repo)))
    };
    let url = |asset: &Value| {
        asset.get("url").and_then(Value::as_str)
            .filter(|url| ours(url, "https://api.github.com/repos/{repo}/releases/assets/"))
            .or_else(|| asset.get("browser_download_url").and_then(Value::as_str)
                .filter(|url| ours(url, "https://github.com/{repo}/releases/download/")))
            .map(str::to_string)
    };
    let package = assets.iter().find(|asset| {
        asset.get("name").and_then(Value::as_str).is_some_and(|name| asset_matches(kind, name))
    })?;
    let sums = assets.iter().find(|asset| asset.get("name").and_then(Value::as_str) == Some("SHA256SUMS"))?;
    let name = package.get("name")?.as_str()?.to_string();
    let (package_url, sums_url) = (url(package)?, url(sums)?);
    if name.contains(['/', '\0']) || name.starts_with('.') {
        return None;
    }
    Some((name, package_url, package.get("size").and_then(Value::as_u64).unwrap_or(0), sums_url))
}

pub fn check(current: &str) -> Result<Value, String> {
    let release = latest_release()?;
    let latest = release.get("tag_name").and_then(Value::as_str).unwrap_or("").to_string();
    let kind = install_kind();
    let asset = plan(&release, kind);
    let notes: String = release.get("body").and_then(Value::as_str).unwrap_or("").chars().take(4000).collect();
    Ok(json!({
        "current": current,
        "latest": latest.trim_start_matches('v'),
        "update_available": !latest.is_empty() && newer(&latest, current),
        "kind": kind,
        "installable": asset.is_some(),
        "asset": asset.as_ref().map(|plan| plan.0.clone()),
        "notes": notes,
        "url": release.get("html_url").and_then(Value::as_str),
        "pages": PAGES,
    }))
}

static JOB: Mutex<Option<Value>> = Mutex::new(None);

fn publish(value: Value) {
    *JOB.lock().unwrap_or_else(|e| e.into_inner()) = Some(value);
}

pub fn status() -> Value {
    JOB.lock().unwrap_or_else(|e| e.into_inner()).clone().unwrap_or(json!({"status": "Idle"}))
}

fn running() -> bool {
    matches!(status().get("status").and_then(Value::as_str), Some("Downloading" | "Verifying" | "Installing"))
}

pub fn start() -> Result<Value, String> {
    if running() {
        return Ok(status());
    }
    let kind = install_kind();
    if kind == Kind::Portable {
        return Err("This copy was not installed from a package or AppImage; download the new release manually.".into());
    }
    publish(json!({"status": "Downloading", "percent": 0, "phase": "Checking the latest release"}));
    std::thread::spawn(move || {
        if let Err(error) = run(kind) {
            publish(json!({"status": "Failed", "error": error}));
        }
    });
    Ok(status())
}

fn cache_dir() -> Result<PathBuf, String> {
    let base = std::env::var_os("XDG_CACHE_HOME").map(PathBuf::from)
        .filter(|path| path.is_absolute())
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".cache")))
        .ok_or("Cannot find a cache directory")?;
    let dir = base.join("anvildroid/updates");
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
    Ok(dir)
}

fn sha256(path: &Path) -> Result<String, String> {
    let mut file = fs::File::open(path).map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1 << 16];
    loop {
        let read = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

/// The digest SHA256SUMS lists for `name` (both "hash  name" and "hash  ./name").
fn listed_digest(sums: &str, name: &str) -> Option<String> {
    sums.lines().find_map(|line| {
        let (digest, file) = line.split_once(char::is_whitespace)?;
        let file = file.trim().trim_start_matches('*').trim_start_matches("./");
        (file == name && digest.len() == 64 && digest.chars().all(|c| c.is_ascii_hexdigit()))
            .then(|| digest.to_ascii_lowercase())
    })
}

fn run(kind: Kind) -> Result<(), String> {
    let (version, target) = fetch_verified(kind)?;
    publish(json!({"status": "Installing", "percent": 100, "phase": "Waiting for administrator approval", "version": version}));
    install(kind, &target)?;
    publish(json!({"status": "Installed", "percent": 100, "version": version,
                   "phase": "Restart AnvilDroid to use the new version"}));
    Ok(())
}

/// Download the latest release's package for `kind` and check it against
/// the release's SHA256SUMS. Returns (version, verified file).
fn fetch_verified(kind: Kind) -> Result<(String, PathBuf), String> {
    let release = latest_release()?;
    let version = release.get("tag_name").and_then(Value::as_str).unwrap_or("").trim_start_matches('v').to_string();
    let (name, url, size, sums_url) = plan(&release, kind)
        .ok_or("This release has no package for this installation type")?;
    let dir = cache_dir()?;
    let target = dir.join(&name);
    let partial = dir.join(format!(".{name}.part"));
    let _ = fs::remove_file(&partial);
    publish(json!({"status": "Downloading", "percent": 0, "phase": format!("Downloading {name}"), "version": version}));
    let mut child = curl().args(["--max-time", "1800", "-H", "Accept: application/octet-stream", "-o"]).arg(&partial).arg(&url)
        .stdout(Stdio::null()).stderr(Stdio::piped()).spawn()
        .map_err(|e| format!("Cannot start the download: {e}"))?;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            break status;
        }
        let received = fs::metadata(&partial).map(|m| m.len()).unwrap_or(0);
        let percent = (received * 100).checked_div(size).unwrap_or(0).min(99);
        publish(json!({"status": "Downloading", "percent": percent, "phase": format!("Downloading {name}"),
                       "received": received, "total": size, "version": version}));
        std::thread::sleep(Duration::from_millis(250));
    };
    if !status.success() {
        let mut detail = String::new();
        if let Some(mut stderr) = child.stderr.take() {
            let _ = stderr.read_to_string(&mut detail);
        }
        let _ = fs::remove_file(&partial);
        return Err(format!("Download failed: {}", detail.trim()));
    }
    publish(json!({"status": "Verifying", "percent": 100, "phase": "Verifying SHA-256", "version": version}));
    let sums = curl().args(["--max-time", "60", "-H", "Accept: application/octet-stream"]).arg(&sums_url)
        .output().map_err(|e| e.to_string())?;
    let expected = listed_digest(&String::from_utf8_lossy(&sums.stdout), &name)
        .ok_or("The release checksum list does not include this package")?;
    if sha256(&partial)? != expected {
        let _ = fs::remove_file(&partial);
        return Err("Checksum mismatch; the download was discarded".into());
    }
    fs::rename(&partial, &target).map_err(|e| e.to_string())?;
    Ok((version, target))
}

fn install(kind: Kind, package: &Path) -> Result<(), String> {
    let path = package.to_string_lossy().into_owned();
    let found = |tool: &str| ["/usr/bin", "/bin", "/usr/sbin"].iter().any(|dir| Path::new(dir).join(tool).is_file());
    let (program, args): (&str, Vec<String>) = match kind {
        // apt-get resolves new dependencies from the configured repositories.
        Kind::Deb => ("/usr/bin/apt-get", vec!["install".into(), "-y".into(), "--allow-downgrades".into(), path]),
        Kind::Rpm if found("dnf") => ("/usr/bin/dnf", vec!["install".into(), "-y".into(), path]),
        Kind::Rpm => ("/usr/bin/zypper", vec!["--non-interactive".into(), "install".into(), "--allow-unsigned-rpm".into(), path]),
        Kind::Pacman => ("/usr/bin/pacman", vec!["-U".into(), "--noconfirm".into(), path]),
        Kind::Appimage => return replace_appimage(package),
        Kind::Portable => return Err("Portable copies are updated manually".into()),
    };
    let result = crate::run_privileged(program, &args, Duration::from_secs(1800))?;
    if result.ok { Ok(()) } else { Err(result.message) }
}

/// Swap the running AppImage file atomically; the running copy keeps its
/// mounted image until restart.
fn replace_appimage(downloaded: &Path) -> Result<(), String> {
    let current = std::env::var_os("APPIMAGE").map(PathBuf::from)
        .filter(|path| path.is_absolute() && path.is_file())
        .ok_or("APPIMAGE is not set")?;
    let staged = current.with_extension("AppImage.new");
    fs::copy(downloaded, &staged).map_err(|e| format!("Cannot write next to {}: {e}", current.display()))?;
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(&staged, fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
    fs::rename(&staged, &current).map_err(|e| {
        let _ = fs::remove_file(&staged);
        format!("Cannot replace {}: {e}", current.display())
    })
}

/// Executable to start after an update (never the deleted, replaced binary).
pub fn relaunch_path() -> Option<PathBuf> {
    match install_kind() {
        Kind::Appimage => std::env::var_os("APPIMAGE").map(PathBuf::from),
        Kind::Portable => std::env::current_exe().ok(),
        _ => Some(PathBuf::from("/usr/bin/anvildroid-gui")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_compare_like_releases() {
        assert!(newer("v0.1.7", "0.1.6"));
        assert!(newer("0.2.0", "0.1.99"));
        assert!(newer("0.2.0", "0.2.0-rc.1"));
        assert!(newer("0.2.0-rc.10", "0.2.0-rc.9"));
        assert!(newer("0.2.0~rc.2", "0.2.0-rc.1"));
        assert!(!newer("v0.1.6", "0.1.6"));
        assert!(!newer("0.1.5", "0.1.6"));
        assert!(!newer("0.2.0-rc.1", "0.2.0"));
        assert!(newer("0.1.6.1", "0.1.6"));
    }

    #[test]
    fn installation_kind_follows_the_package_database() {
        let root = std::env::temp_dir().join(format!("anvil-update-kind-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("var/lib/dpkg/info")).unwrap();
        fs::create_dir_all(root.join("var/lib/pacman/local")).unwrap();
        assert_eq!(kind_at(None, &root, || false), Kind::Portable);
        assert_eq!(kind_at(None, &root, || true), Kind::Rpm);
        fs::create_dir(root.join("var/lib/pacman/local/anvildroid-controller-tools-1")).unwrap();
        assert_eq!(kind_at(None, &root, || false), Kind::Portable); // another package
        fs::create_dir(root.join("var/lib/pacman/local/anvildroid-controller-0.1.6-1")).unwrap();
        assert_eq!(kind_at(None, &root, || false), Kind::Pacman);
        fs::write(root.join("var/lib/dpkg/info/anvildroid-controller.list"), "").unwrap();
        assert_eq!(kind_at(None, &root, || false), Kind::Deb);
        let image = root.join("AnvilDroid.AppImage");
        fs::write(&image, "").unwrap();
        assert_eq!(kind_at(Some(image), &root, || false), Kind::Appimage);
        assert_eq!(kind_at(Some(PathBuf::from("relative.AppImage")), &root, || false), Kind::Deb);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn plan_picks_only_this_repositorys_matching_asset() {
        let base = "https://github.com/collyn/anvildroid/releases/download/v0.1.7/";
        let release = json!({"assets": [
            {"name": "anvildroid-controller_0.1.7_amd64.deb", "size": 10, "browser_download_url": format!("{base}anvildroid-controller_0.1.7_amd64.deb")},
            {"name": "anvildroid-controller-0.1.7-1.x86_64.rpm", "size": 11, "browser_download_url": format!("{base}anvildroid-controller-0.1.7-1.x86_64.rpm")},
            {"name": "anvildroid-controller-0.1.7-1-x86_64.pkg.tar.zst", "size": 12, "browser_download_url": format!("{base}anvildroid-controller-0.1.7-1-x86_64.pkg.tar.zst")},
            {"name": "AnvilDroid-0.1.7-x86_64.AppImage", "size": 13, "browser_download_url": format!("{base}AnvilDroid-0.1.7-x86_64.AppImage")},
            {"name": "SHA256SUMS", "size": 1, "browser_download_url": format!("{base}SHA256SUMS")}]});
        assert_eq!(plan(&release, Kind::Deb).unwrap().0, "anvildroid-controller_0.1.7_amd64.deb");
        assert_eq!(plan(&release, Kind::Rpm).unwrap().2, 11);
        assert_eq!(plan(&release, Kind::Pacman).unwrap().0, "anvildroid-controller-0.1.7-1-x86_64.pkg.tar.zst");
        assert_eq!(plan(&release, Kind::Appimage).unwrap().0, "AnvilDroid-0.1.7-x86_64.AppImage");
        assert!(plan(&release, Kind::Portable).is_none());
        let api = json!({"assets": [
            {"name": "anvildroid-controller_0.1.7_amd64.deb", "url": "https://api.github.com/repos/collyn/anvildroid/releases/assets/1",
             "browser_download_url": format!("{base}anvildroid-controller_0.1.7_amd64.deb")},
            {"name": "SHA256SUMS", "url": "https://api.github.com/repos/collyn/anvildroid/releases/assets/2"}]});
        let chosen = plan(&api, Kind::Deb).unwrap();
        assert_eq!((chosen.1.as_str(), chosen.3.as_str()), ("https://api.github.com/repos/collyn/anvildroid/releases/assets/1",
                                                            "https://api.github.com/repos/collyn/anvildroid/releases/assets/2"));
        let other_repo = json!({"assets": [
            {"name": "anvildroid-controller_0.1.7_amd64.deb", "url": "https://api.github.com/repos/evil/anvildroid/releases/assets/1"},
            {"name": "SHA256SUMS", "url": "https://api.github.com/repos/collyn/anvildroid/releases/assets/2"}]});
        assert!(plan(&other_repo, Kind::Deb).is_none());
        let moved = json!({"assets": [
            {"name": "anvildroid-controller_0.1.7_amd64.deb", "url": "https://api.github.com/repos/anvildroid/anvildroid/releases/assets/1"},
            {"name": "SHA256SUMS", "url": "https://api.github.com/repos/anvildroid/anvildroid/releases/assets/2"}]});
        assert!(plan(&moved, Kind::Deb).is_some());
        let foreign = json!({"assets": [
            {"name": "anvildroid-controller_0.1.7_amd64.deb", "browser_download_url": "https://example.com/x.deb"},
            {"name": "SHA256SUMS", "browser_download_url": format!("{base}SHA256SUMS")}]});
        assert!(plan(&foreign, Kind::Deb).is_none());
        let unsigned = json!({"assets": [release["assets"][0].clone()]});
        assert!(plan(&unsigned, Kind::Deb).is_none()); // no checksum list, no install
    }

    #[test]
    fn checksum_lookup_accepts_sha256sum_formats_only_for_the_exact_file() {
        let digest = "a".repeat(64);
        let sums = format!("{digest}  ./anvildroid-controller_0.1.7_amd64.deb\n{}  other.deb\n", "b".repeat(64));
        assert_eq!(listed_digest(&sums, "anvildroid-controller_0.1.7_amd64.deb").as_deref(), Some(digest.as_str()));
        assert_eq!(listed_digest(&format!("{digest} *x.deb"), "x.deb").as_deref(), Some(digest.as_str()));
        assert!(listed_digest(&sums, "anvildroid-controller_0.1.7_amd64.deb.sig").is_none());
        assert!(listed_digest("zz  x.deb", "x.deb").is_none());
    }
}

#[cfg(test)]
mod live {
    #[test]
    #[ignore = "contacts api.github.com"]
    fn live_release_check() {
        let report = super::check("0.1.5").expect("GitHub release check");
        println!("{report}");
        assert_eq!(report["update_available"], true);
    }

    #[test]
    #[ignore = "downloads the latest .deb from GitHub"]
    fn live_download_is_verified() {
        let (version, path) = super::fetch_verified(super::Kind::Deb).expect("download and verify");
        println!("{version} {}", path.display());
        assert!(path.is_file());
        assert!(super::status()["status"] == "Verifying");
        std::fs::remove_file(path).unwrap();
    }
}
