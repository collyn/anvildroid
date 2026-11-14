//! Custom image ZIP download from an HTTPS URL, then controller import.
//!
//! States: Downloading -> (Cancelling -> Cancelled) | Importing -> Ready | Failed.
//! Network I/O never runs while the job mutex is held. Each job has an id, and
//! a worker only publishes into the job it started, never into a newer one.
//! Cancel kills and reaps curl; the partial file is kept so the same URL can
//! resume. Import (validation + controller copy) cannot be cancelled safely,
//! so Cancel is refused during that phase.
//!
//! Cache: `~/.anvildroid-image-downloads/<sha256(url)>/` holding `source-url`,
//! `image.zip` (partial or complete) and `response-headers` from the GET that
//! produced those bytes. A partial is resumed only with a strong validator
//! (strong ETag or Last-Modified) sent as If-Range; otherwise it is discarded,
//! so bytes from two different server versions are never joined.
//!
//! Several connections: a one-byte range request shows whether the server
//! serves byte ranges and names the object with a strong validator. If so, the
//! ZIP is fetched as fixed-size chunks by one parallel curl into `parts/`, each
//! chunk guarded by If-Range, then appended to `image.zip` in order. `segments`
//! records which object the chunks belong to, so a later attempt keeps what
//! arrived, including unfinished chunks: curl writes each request to
//! `<chunk>.at-<offset>`, which is then joined onto `<chunk>` at that offset.
//! Without range support the single-connection path is used.

use std::os::unix::fs::{DirBuilderExt, MetadataExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use sha2::{Digest, Sha256};

const ACTIVE: [&str; 3] = ["Downloading", "Cancelling", "Importing"];
/// curl: "HTTP server doesn't seem to support byte ranges", also returned when
/// If-Range no longer matches and the server answers with the full body.
const CURL_CANNOT_RESUME: i32 = 33;
const CHUNK: u64 = 8 * 1024 * 1024;
pub const MAX_CONNECTIONS: u8 = 16;

#[derive(Clone)]
pub struct Job {
    id: u64,
    path: PathBuf,
    total: u64,
    status: String,
    phase: String,
    error: Option<String>,
    result: Option<serde_json::Value>,
}

#[derive(Default)]
pub struct State {
    job: Option<Job>,
    child: Option<Child>,
}

static STATE: OnceLock<Arc<Mutex<State>>> = OnceLock::new();
static NEXT_ID: AtomicU64 = AtomicU64::new(1);

fn state() -> &'static Arc<Mutex<State>> {
    STATE.get_or_init(|| Arc::new(Mutex::new(State::default())))
}

fn lock(state: &Mutex<State>) -> Result<std::sync::MutexGuard<'_, State>, String> {
    state.lock().map_err(|_| "Custom image job state is unavailable".to_string())
}

/// Stable across builds and Rust versions (unlike DefaultHasher).
pub fn cache_key(url: &str) -> String {
    Sha256::digest(url.as_bytes()).iter().map(|byte| format!("{byte:02x}")).collect()
}

fn secure_directory(directory: &Path, owner: u32) -> Result<(), String> {
    match std::fs::DirBuilder::new().mode(0o700).create(directory) {
        Ok(()) => (),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
        Err(error) => return Err(error.to_string()),
    }
    let info = std::fs::symlink_metadata(directory).map_err(|e| e.to_string())?;
    if !info.is_dir() || info.uid() != owner || info.mode() & 0o077 != 0 {
        return Err("Unsafe custom image download directory".into());
    }
    Ok(())
}

/// Per-URL cache directory. Directories from builds that keyed the cache with
/// a 64-bit DefaultHasher are adopted when their `source-url` matches exactly,
/// so partial downloads survive the change.
pub fn url_directory(cache: &Path, url: &str, owner: u32) -> Result<PathBuf, String> {
    secure_directory(cache, owner)?;
    let directory = cache.join(cache_key(url));
    if std::fs::symlink_metadata(&directory).is_err() {
        if let Ok(entries) = std::fs::read_dir(cache) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                if name.len() != 16 || !name.bytes().all(|b| b.is_ascii_hexdigit()) { continue; }
                let legacy = entry.path();
                let Ok(info) = std::fs::symlink_metadata(&legacy) else { continue };
                if !info.is_dir() || info.uid() != owner || info.mode() & 0o077 != 0 { continue; }
                if std::fs::read_to_string(legacy.join("source-url")).ok().as_deref() == Some(url) {
                    std::fs::rename(&legacy, &directory).map_err(|e| e.to_string())?;
                    break;
                }
            }
        }
    }
    secure_directory(&directory, owner)?;
    let source = directory.join("source-url");
    match std::fs::read_to_string(&source) {
        Ok(saved) if saved == url => (),
        Ok(_) => return Err("Download cache URL mismatch".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            std::fs::write(&source, url).map_err(|e| e.to_string())?
        }
        Err(error) => return Err(error.to_string()),
    }
    let path = directory.join("image.zip");
    if let Ok(info) = std::fs::symlink_metadata(&path) {
        if !info.is_file() || info.uid() != owner || info.nlink() != 1 {
            return Err("Unsafe partial image download".into());
        }
    }
    Ok(directory)
}

/// Total size from the final response in a curl header dump.
pub fn download_total(headers: &str) -> Option<u64> {
    let mut length = None;
    let mut range = None;
    let mut success = false;
    for line in headers.lines() {
        if line.starts_with("HTTP/") {
            success = matches!(line.split_whitespace().nth(1), Some("200" | "206"));
            length = None;
            range = None;
        } else if let Some((name, value)) = line.split_once(':') {
            if name.eq_ignore_ascii_case("content-length") { length = value.trim().parse().ok(); }
            if name.eq_ignore_ascii_case("content-range") {
                range = value.rsplit_once('/').and_then(|(_, total)| total.trim().parse().ok());
            }
        }
    }
    if success { range.or(length) } else { None }
}

/// If-Range value identifying the bytes already on disk: a strong ETag, else
/// Last-Modified. Taken from the last response in the dump.
pub fn resume_validator(headers: &str) -> Option<String> {
    let last = headers.rsplit("HTTP/").next().unwrap_or("");
    let field = |key: &str| last.lines().find_map(|line| {
        let (name, value) = line.split_once(':')?;
        name.trim().eq_ignore_ascii_case(key).then(|| value.trim().to_owned())
    });
    field("etag").filter(|value| !value.starts_with("W/") && !value.is_empty())
        .or_else(|| field("last-modified").filter(|value| !value.is_empty()))
}

/// Total size and validator when the last response answered a `bytes=0-0`
/// request with exactly that byte: the server serves ranges of one known object.
pub fn range_support(headers: &str) -> Option<(u64, String)> {
    let last = headers.rsplit("HTTP/").next().unwrap_or("");
    if last.split_whitespace().nth(1) != Some("206") { return None; }
    let total = last.lines().find_map(|line| {
        let (name, value) = line.split_once(':')?;
        if !name.trim().eq_ignore_ascii_case("content-range") { return None; }
        value.trim().strip_prefix("bytes 0-0/")?.parse::<u64>().ok()
    })?;
    Some((total, resume_validator(headers)?))
}

/// Double-quoted curl config value. Control characters would end the line and
/// let a URL or header smuggle in further options, so they are refused.
fn quoted(value: &str) -> Option<String> {
    if value.chars().any(char::is_control) { return None; }
    Some(format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\"")))
}

fn part_path(parts: &Path, index: u64) -> PathBuf {
    parts.join(format!("{index:06}"))
}

/// Join every `<chunk>.at-<offset>` download onto its chunk file. The chunk is
/// first cut back to that offset, so repeating an interrupted join is harmless.
fn join_fetched(parts: &Path) -> std::io::Result<()> {
    for entry in std::fs::read_dir(parts)?.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let Some((chunk, offset)) = name.split_once(".at-") else { continue };
        if let Ok(offset) = offset.parse::<u64>() {
            let part = parts.join(chunk);
            if std::fs::metadata(&part).map(|m| m.len()).unwrap_or(0) >= offset {
                let mut output = std::fs::OpenOptions::new().create(true).truncate(false).write(true).open(&part)?;
                output.set_len(offset)?;
                use std::io::Seek;
                output.seek(std::io::SeekFrom::End(0))?;
                std::io::copy(&mut std::fs::File::open(entry.path())?, &mut output)?;
                output.sync_data()?;
            }
        }
        std::fs::remove_file(entry.path())?;
    }
    Ok(())
}

/// curl config fetching the rest of each listed (chunk, bytes already held)
/// into `parts`. A server that ignores the range or no longer has the same
/// object answers with the whole file, which exceeds the request's size limit
/// and fails instead of being stored.
pub fn segment_config(url: &str, validator: &str, parts: &Path, total: u64, chunks: &[(u64, u64)]) -> Option<String> {
    let (url, condition) = (quoted(url)?, quoted(&format!("If-Range: {validator}"))?);
    let mut config = String::new();
    for (position, &(index, held)) in chunks.iter().enumerate() {
        let start = index * CHUNK + held;
        let end = total.min((index + 1) * CHUNK) - 1;
        if position > 0 { config.push_str("next\n"); }
        config.push_str(&format!(
            "url = {url}\noutput = {}\nrange = \"{start}-{end}\"\nheader = {condition}\nmax-filesize = {}\n\
             fail\nsilent\nshow-error\nlocation\nproto = \"=https\"\nproto-redir = \"=https\"\nconnect-timeout = 15\n\
             retry = 5\nretry-delay = 2\nspeed-limit = 1024\nspeed-time = 60\n",
            quoted(&format!("{}.at-{held}", part_path(parts, index).to_str()?))?, end - start + 1));
    }
    Some(config)
}

/// Start a download job. Returns immediately; poll `status`.
pub fn start(url: String, name: String, connections: u8) -> Result<serde_json::Value, String> {
    if !(1..=MAX_CONNECTIONS).contains(&connections) {
        return Err(format!("Download connections must be between 1 and {MAX_CONNECTIONS}"));
    }
    if !url.starts_with("https://") || url.len() > 2048 {
        return Err("Custom image URL must be an HTTPS URL up to 2048 characters".into());
    }
    let home = std::env::var_os("HOME").ok_or("Home directory is unavailable")?;
    let owner = std::fs::metadata("/proc/self").map_err(|e| e.to_string())?.uid();
    let shared = state().clone();
    let id = {
        let mut guard = lock(&shared)?;
        if guard.job.as_ref().is_some_and(|job| ACTIVE.contains(&job.status.as_str())) {
            return Err("A custom image download is already running".into());
        }
        // Reserve the job before any filesystem or network work.
        let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        guard.job = Some(Job { id, path: PathBuf::new(), total: 0, status: "Downloading".into(),
            phase: "Connecting".into(), error: None, result: None });
        guard.child = None;
        id
    };
    let directory = match url_directory(&PathBuf::from(home).join(".anvildroid-image-downloads"), &url, owner) {
        Ok(directory) => directory,
        Err(error) => {
            publish(&shared, id, |job| { job.status = "Failed".into(); job.phase = "Download failed".into(); job.error = Some(error.clone()); });
            return Err(error);
        }
    };
    let path = directory.join("image.zip");
    publish(&shared, id, |job| job.path = path.clone());
    std::thread::spawn(move || run(shared, id, url, name, path, connections));
    Ok(serde_json::json!({"status":"Downloading","phase":"Connecting","total_bytes":0,"received_bytes":0,"percent":0}))
}

/// Update only the job this worker started.
fn publish(shared: &Mutex<State>, id: u64, update: impl FnOnce(&mut Job)) -> bool {
    let Ok(mut guard) = shared.lock() else { return false };
    match guard.job.as_mut() {
        Some(job) if job.id == id => { update(job); true }
        _ => false,
    }
}

fn cancelled(shared: &Mutex<State>, id: u64) -> bool {
    shared.lock().map(|g| g.job.as_ref().is_some_and(|job| job.id == id && job.status == "Cancelling")).unwrap_or(true)
}

/// Run one child under the job slot so Cancel can kill it; always reaped.
/// Returns (exit code, stderr) or Err when cancelled before/while running.
// The child is stored for Cancel and reaped by the try_wait() loop; clippy
// cannot follow it through the shared state.
#[allow(clippy::zombie_processes)]
pub fn supervise(shared: &Mutex<State>, id: u64, mut command: Command) -> Result<(Option<i32>, String), String> {
    command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::piped());
    {
        let mut guard = lock(shared)?;
        if !guard.job.as_ref().is_some_and(|job| job.id == id && job.status == "Downloading") {
            return Err("cancelled".into());
        }
        let child = command.spawn().map_err(|e| e.to_string())?;
        guard.child = Some(child);
    }
    loop {
        let mut guard = lock(shared)?;
        let child = guard.child.as_mut().expect("supervised child");
        match child.try_wait().map_err(|e| e.to_string())? {
            Some(status) => {
                let mut child = guard.child.take().expect("supervised child");
                drop(guard);
                let mut stderr = String::new();
                if let Some(mut pipe) = child.stderr.take() {
                    use std::io::Read;
                    let _ = pipe.by_ref().take(4096).read_to_string(&mut stderr);
                }
                return Ok((status.code(), stderr.chars().take(1024).collect()));
            }
            None => {
                drop(guard);
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
        }
    }
}

fn curl(args: &[&str]) -> Command {
    let mut command = Command::new("curl");
    command.args(["-fsSL", "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "15"]).args(args);
    command
}

fn remove_segments(path: &Path) {
    let _ = std::fs::remove_dir_all(path.with_file_name("parts"));
    let _ = std::fs::remove_file(path.with_file_name("segments"));
    let _ = std::fs::remove_file(path.with_file_name("segments.curl"));
}

/// Download `path` as chunks over several connections. `Ok(false)` means the
/// server would not serve chunks at all and nothing is kept, so the caller
/// falls back to one connection. Errors keep the finished chunks for a resume.
fn segmented(shared: &Mutex<State>, id: u64, url: &str, path: &Path, total: u64, validator: &str,
        connections: u8) -> Result<bool, String> {
    let (parts, marker) = (path.with_file_name("parts"), path.with_file_name("segments"));
    let (config_path, headers) = (path.with_file_name("segments.curl"), path.with_file_name("response-headers"));
    let count = total.div_ceil(CHUNK);
    let length = |index: u64| CHUNK.min(total - index * CHUNK);
    let held = |index: u64| std::fs::metadata(part_path(&parts, index)).map(|m| m.len()).unwrap_or(0);
    if segment_config(url, validator, &parts, total, &[(0, 0)]).is_none() { return Ok(false); }

    // Keep earlier bytes only when they provably belong to this same object.
    let identity = format!("{total}\n{validator}");
    if std::fs::read_to_string(&marker).ok().as_deref() != Some(identity.as_str()) {
        let _ = std::fs::remove_dir_all(&parts);
        let single = std::fs::read_to_string(&headers).ok().and_then(|text| resume_validator(&text));
        if single.as_deref() != Some(validator) { let _ = std::fs::remove_file(path); }
    }
    let _ = std::fs::remove_file(&headers);
    match std::fs::DirBuilder::new().mode(0o700).create(&parts) {
        Err(error) if error.kind() != std::io::ErrorKind::AlreadyExists => return Err(error.to_string()),
        _ => (),
    }
    std::fs::write(&marker, &identity).map_err(|e| e.to_string())?;

    // `image.zip` holds the chunks already appended, cut back to a whole chunk.
    let have = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);
    let assembled = if have == total { count } else if have > total { 0 } else { have / CHUNK };
    if have != total && have != assembled * CHUNK {
        std::fs::OpenOptions::new().write(true).open(path).and_then(|file| file.set_len(assembled * CHUNK))
            .map_err(|e| e.to_string())?;
    }

    let mut stalled = 0;
    loop {
        join_fetched(&parts).map_err(|e| e.to_string())?;
        let mut pending = Vec::new();
        for index in assembled..count {
            let size = held(index);
            if size > length(index) { let _ = std::fs::remove_file(part_path(&parts, index)); }
            if size != length(index) { pending.push((index, if size > length(index) { 0 } else { size })); }
        }
        if pending.is_empty() { break; }
        let before: u64 = pending.iter().map(|(_, size)| size).sum();
        let config = segment_config(url, validator, &parts, total, &pending).ok_or("Unsupported download location")?;
        std::fs::write(&config_path, config).map_err(|e| e.to_string())?;
        let mut command = Command::new("curl");
        command.args(["--parallel", "--parallel-max", &connections.to_string(), "--config"]).arg(&config_path);
        let (_, stderr) = supervise(shared, id, command)?;
        join_fetched(&parts).map_err(|e| e.to_string())?;
        if cancelled(shared, id) { return Err("cancelled".into()); }
        let after: u64 = pending.iter().map(|(index, _)| held(*index)).sum();
        stalled = if after > before { 0 } else { stalled + 1 };
        if stalled >= 2 {
            if assembled == 0 && (0..count).all(|index| held(index) == 0) {
                remove_segments(path);
                let _ = std::fs::remove_file(path);
                return Ok(false);
            }
            let detail = stderr.lines().last().unwrap_or("").trim();
            return Err(format!("Download interrupted{}. Start it again to resume.",
                if detail.is_empty() { String::new() } else { format!(" ({detail})") }));
        }
    }

    publish(shared, id, |job| job.phase = "Assembling ZIP".into());
    let mut output = std::fs::OpenOptions::new().create(true).append(true).open(path).map_err(|e| e.to_string())?;
    for index in assembled..count {
        if cancelled(shared, id) { return Err("cancelled".into()); }
        let part = part_path(&parts, index);
        let mut input = std::fs::File::open(&part).map_err(|e| e.to_string())?;
        std::io::copy(&mut input, &mut output).map_err(|e| e.to_string())?;
        // A chunk leaves `parts` only once `image.zip` durably contains it.
        output.sync_data().map_err(|e| e.to_string())?;
        let _ = std::fs::remove_file(&part);
    }
    if std::fs::metadata(path).map(|m| m.len()).ok() != Some(total) {
        return Err("Downloaded ZIP has an unexpected size; start the download again".into());
    }
    remove_segments(path);
    Ok(true)
}

fn run(shared: Arc<Mutex<State>>, id: u64, url: String, name: String, path: PathBuf, connections: u8) {
    let headers = path.with_file_name("response-headers");
    let head = path.with_file_name("head-headers");
    let outcome = (|| -> Result<serde_json::Value, String> {
        // One-byte range request: gives the size for progress and shows whether
        // the server can serve chunks. A server without range support answers
        // with the whole file, which the size limit refuses.
        let mut probe = curl(&["--range", "0-0", "--max-filesize", "1", "--max-time", "20", "-o", "/dev/null", "--dump-header"]);
        probe.arg(&head).arg("--").arg(&url);
        let _ = supervise(&shared, id, probe);
        let probed = std::fs::read_to_string(&head).unwrap_or_default();
        let _ = std::fs::remove_file(&head);
        publish(&shared, id, |job| job.total = download_total(&probed).unwrap_or(0));
        if cancelled(&shared, id) { return Err("cancelled".into()); }
        let mut complete = false;
        if let Some((total, validator)) = range_support(&probed).filter(|(total, _)| connections > 1 && *total > CHUNK) {
            publish(&shared, id, |job| job.phase = format!("Downloading ZIP over {connections} connections"));
            complete = segmented(&shared, id, &url, &path, total, &validator, connections)?;
        }
        if !complete {
            remove_segments(&path);
            publish(&shared, id, |job| job.phase = "Downloading ZIP".into());
        }
        for attempt in 0..(if complete { 0 } else { 2 }) {
            let partial = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0) > 0;
            let validator = partial.then(|| std::fs::read_to_string(&headers).ok().and_then(|t| resume_validator(&t))).flatten();
            if partial && validator.is_none() {
                // Cannot prove the partial matches the current server object.
                let _ = std::fs::remove_file(&path);
            }
            // No overall time limit: only a connection that stops moving is given up.
            let mut command = curl(&["--retry", "5", "--retry-delay", "2", "--speed-limit", "1024", "--speed-time", "60",
                "--max-filesize", "34359738368", "--dump-header"]);
            command.arg(&headers);
            if let Some(value) = &validator {
                command.arg("--continue-at").arg("-").arg("--header").arg(format!("If-Range: {value}"));
            }
            command.arg("-o").arg(&path).arg("--").arg(&url);
            let (code, stderr) = supervise(&shared, id, command)?;
            match code {
                Some(0) => break,
                Some(CURL_CANNOT_RESUME) if attempt == 0 && validator.is_some() => {
                    // Server changed the object or ignores ranges: restart cleanly.
                    let _ = std::fs::remove_file(&path);
                    let _ = std::fs::remove_file(&headers);
                }
                _ if cancelled(&shared, id) => return Err("cancelled".into()),
                _ => return Err(if stderr.trim().is_empty() { format!("curl exited with {code:?}") } else { stderr }),
            }
        }
        // Download complete: from here the controller import must not be interrupted.
        let proceed = {
            let mut guard = lock(&shared)?;
            match guard.job.as_mut() {
                Some(job) if job.id == id && job.status == "Downloading" => {
                    job.status = "Importing".into();
                    job.phase = "Validating and importing with Waydroid CLI".into();
                    true
                }
                _ => false,
            }
        };
        if !proceed { return Err("cancelled".into()); }
        let result = anvildroid_core::controller::import_image(&path, &name)?;
        // The controller duplicates the descriptor before acknowledging import.
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_file(&headers);
        Ok(result)
    })();
    publish(&shared, id, |job| match outcome {
        Ok(result) => { job.status = "Ready".into(); job.phase = "Custom image imported".into(); job.result = Some(result); }
        Err(_) if job.status == "Cancelling" => {
            job.status = "Cancelled".into();
            job.phase = "Download cancelled".into();
        }
        Err(error) => {
            job.phase = if job.status == "Importing" { "Import failed".into() } else { "Download failed".into() };
            job.status = "Failed".into();
            job.error = Some(error);
        }
    });
}

/// Cancel the running download. Refused while the image is being imported.
pub fn cancel() -> Result<serde_json::Value, String> {
    let shared = state().clone();
    let mut guard = lock(&shared)?;
    let status = guard.job.as_ref().map(|job| job.status.clone());
    match status.as_deref() {
        Some("Downloading") => {
            if let Some(job) = guard.job.as_mut() { job.status = "Cancelling".into(); job.phase = "Stopping download".into(); }
            if let Some(child) = guard.child.as_mut() { let _ = child.kill(); }
            Ok(serde_json::json!({"status":"Cancelling"}))
        }
        Some("Cancelling") => Ok(serde_json::json!({"status":"Cancelling"})),
        Some("Importing") => Err("The download is complete and the image is being validated and imported; this step cannot be cancelled safely.".into()),
        _ => Err("No custom image download is running".into()),
    }
}

pub fn status() -> Result<serde_json::Value, String> {
    let guard = lock(state())?;
    let Some(job) = guard.job.as_ref() else { return Ok(serde_json::json!({"status":"Idle","percent":0})); };
    let chunks = std::fs::read_dir(job.path.with_file_name("parts")).map(|entries| entries.flatten()
        .filter_map(|entry| entry.metadata().ok()).map(|info| info.len()).sum::<u64>()).unwrap_or(0);
    let received = std::fs::metadata(&job.path).map(|m| m.len()).unwrap_or(0) + chunks;
    let headers = std::fs::read_to_string(job.path.with_file_name("response-headers")).unwrap_or_default();
    let total = download_total(&headers).unwrap_or(job.total);
    let percent = if total > 0 { Some(((received.min(total) as f64 / total as f64) * 100.0).floor() as u64) } else { None };
    Ok(serde_json::json!({"status":job.status,"phase":job.phase,"total_bytes":total,"received_bytes":received,
        "percent":percent,"error":job.error,"result":job.result,"can_cancel":job.status == "Downloading"}))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!("anvil-custom-dl-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        path
    }

    fn owner() -> u32 { std::fs::metadata("/proc/self").unwrap().uid() }

    #[test]
    fn cache_key_is_a_fixed_sha256() {
        assert_eq!(cache_key("https://example.com/a.zip").len(), 64);
        // Known SHA-256 vector: the key never depends on the compiler's hasher.
        assert_eq!(cache_key("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }

    #[test]
    fn legacy_directory_is_adopted_only_for_the_same_url() {
        use std::os::unix::fs::PermissionsExt;
        let cache = temp("legacy");
        std::fs::create_dir_all(&cache).unwrap();
        std::fs::set_permissions(&cache, std::fs::Permissions::from_mode(0o700)).unwrap();
        let legacy = cache.join("0123456789abcdef");
        std::fs::create_dir(&legacy).unwrap();
        std::fs::set_permissions(&legacy, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::write(legacy.join("source-url"), "https://example.com/a.zip").unwrap();
        std::fs::write(legacy.join("image.zip"), b"partial").unwrap();
        let other = url_directory(&cache, "https://example.com/b.zip", owner()).unwrap();
        assert!(legacy.exists() && !other.join("image.zip").exists());
        let adopted = url_directory(&cache, "https://example.com/a.zip", owner()).unwrap();
        assert_eq!(adopted, cache.join(cache_key("https://example.com/a.zip")));
        assert_eq!(std::fs::read(adopted.join("image.zip")).unwrap(), b"partial");
        assert!(!legacy.exists());
        std::fs::write(adopted.join("source-url"), "https://evil.example/").unwrap();
        assert!(url_directory(&cache, "https://example.com/a.zip", owner()).is_err());
        std::fs::remove_dir_all(cache).unwrap();
    }

    #[test]
    fn resume_requires_a_strong_validator_from_the_last_response() {
        let redirect = "HTTP/1.1 302 Found\r\nETag: \"old\"\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 10\r\nETag: \"v2\"\r\n";
        assert_eq!(resume_validator(redirect).as_deref(), Some("\"v2\""));
        assert_eq!(resume_validator("HTTP/1.1 200 OK\r\nETag: W/\"weak\"\r\nLast-Modified: Wed, 21 Oct 2015 07:28:00 GMT\r\n").as_deref(),
            Some("Wed, 21 Oct 2015 07:28:00 GMT"));
        assert_eq!(resume_validator("HTTP/1.1 200 OK\r\nETag: W/\"weak\"\r\n"), None);
        assert_eq!(download_total("HTTP/1.1 206 Partial\r\nContent-Range: bytes 5-9/10\r\nContent-Length: 5\r\n"), Some(10));
        assert_eq!(download_total("HTTP/1.1 404 Not Found\r\nContent-Length: 5\r\n"), None);
    }

    #[test]
    fn chunked_download_needs_a_ranged_reply_with_a_strong_validator() {
        let ranged = "HTTP/2 302 \r\ncontent-length: 7\r\n\r\nHTTP/1.1 206 Partial Content\r\nContent-Length: 1\r\n\
                      etag: \"6ad9-b5\"\r\nContent-Range: bytes 0-0/189818691\r\n";
        assert_eq!(range_support(ranged), Some((189818691, "\"6ad9-b5\"".to_string())));
        assert_eq!(download_total(ranged), Some(189818691));
        assert_eq!(range_support("HTTP/1.1 200 OK\r\nContent-Length: 10\r\nETag: \"v\"\r\n"), None);
        assert_eq!(range_support("HTTP/1.1 206 Partial Content\r\nContent-Range: bytes 0-0/10\r\nETag: W/\"weak\"\r\n"), None);
        assert_eq!(range_support("HTTP/1.1 206 Partial Content\r\nContent-Range: bytes 0-9/10\r\nETag: \"v\"\r\n"), None);
    }

    #[test]
    fn chunk_config_covers_the_file_and_cannot_be_extended_by_its_inputs() {
        let total = CHUNK * 2 + 5;
        let config = segment_config("https://example.com/a \"b\".zip", "\"v1\"", Path::new("/cache/parts"), total, &[(0, 100), (2, 0)]).unwrap();
        assert_eq!(config.matches("\nnext\n").count(), 1);
        assert!(config.contains("url = \"https://example.com/a \\\"b\\\".zip\"\n"));
        assert!(config.contains("header = \"If-Range: \\\"v1\\\"\"\n"));
        assert!(config.contains(&format!("output = \"/cache/parts/000000.at-100\"\nrange = \"100-{}\"", CHUNK - 1)));
        assert!(config.contains(&format!("max-filesize = {}\n", CHUNK - 100)));
        assert!(config.contains(&format!("output = \"/cache/parts/000002.at-0\"\nrange = \"{}-{}\"", CHUNK * 2, total - 1)));
        assert!(config.contains("max-filesize = 5\n"));
        assert!(segment_config("https://example.com/a\noutput = \"/etc/x\"", "\"v\"", Path::new("/p"), total, &[(0, 0)]).is_none());
        assert!(segment_config("https://example.com/a", "v\r\nnext", Path::new("/p"), total, &[(0, 0)]).is_none());
    }

    #[test]
    fn fetched_pieces_join_their_chunk_and_a_repeated_join_adds_nothing_twice() {
        let parts = temp("join");
        std::fs::create_dir_all(&parts).unwrap();
        std::fs::write(parts.join("000001.at-0"), b"abc").unwrap();
        join_fetched(&parts).unwrap();
        assert_eq!(std::fs::read(parts.join("000001")).unwrap(), b"abc");
        // A join that stopped half way left extra bytes after offset 3.
        std::fs::write(parts.join("000001"), b"abcde").unwrap();
        std::fs::write(parts.join("000001.at-3"), b"defg").unwrap();
        // A piece that does not connect to what is held is dropped.
        std::fs::write(parts.join("000002.at-9"), b"zz").unwrap();
        join_fetched(&parts).unwrap();
        assert_eq!(std::fs::read(parts.join("000001")).unwrap(), b"abcdefg");
        assert!(!parts.join("000002").exists());
        assert_eq!(std::fs::read_dir(&parts).unwrap().count(), 1);
        std::fs::remove_dir_all(parts).unwrap();
    }

    #[test]
    fn cancel_kills_and_reaps_the_child_and_stale_workers_cannot_publish() {
        let shared = Mutex::new(State {
            job: Some(Job { id: 7, path: PathBuf::new(), total: 0, status: "Downloading".into(),
                phase: String::new(), error: None, result: None }),
            child: None,
        });
        std::thread::scope(|scope| {
            let worker = scope.spawn(|| {
                let mut command = Command::new("sleep");
                command.arg("30");
                supervise(&shared, 7, command)
            });
            // Wait for the child, then cancel exactly as `cancel()` does.
            let started = std::time::Instant::now();
            let pid = loop {
                if let Some(pid) = shared.lock().unwrap().child.as_ref().map(|c| c.id()) { break pid; }
                assert!(started.elapsed().as_secs() < 5);
                std::thread::sleep(std::time::Duration::from_millis(10));
            };
            {
                let mut guard = shared.lock().unwrap();
                guard.job.as_mut().unwrap().status = "Cancelling".into();
                guard.child.as_mut().unwrap().kill().unwrap();
            }
            let (code, _) = worker.join().unwrap().unwrap();
            assert_eq!(code, None, "killed by signal");
            assert!(started.elapsed().as_secs() < 5);
            assert!(shared.lock().unwrap().child.is_none());
            // Reaped: no zombie remains for this pid.
            assert!(!Path::new(&format!("/proc/{pid}")).exists());
        });
        assert!(cancelled(&shared, 7));
        // A worker from an older job never writes into the current one.
        assert!(!publish(&shared, 6, |job| job.status = "Ready".into()));
        assert_eq!(shared.lock().unwrap().job.as_ref().unwrap().status, "Cancelling");
        // A new child cannot start once cancellation began.
        assert!(supervise(&shared, 7, Command::new("true")).is_err());
    }
}
