//! Narrow client for the root-owned controller. No caller-selected socket or UID.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::time::Duration;

#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "op", rename_all = "lowercase", deny_unknown_fields)]
pub enum Request {
    List {},
    #[serde(rename = "existing_claim")]
    ExistingClaim {
        id: String,
    },
    #[serde(rename = "existing_import")]
    ExistingImport {
        id: String,
        name: String,
    },
    #[serde(rename = "start_on_boot")]
    StartOnBoot {
        id: String,
        enabled: bool,
    },
    Delete {
        id: String,
        confirmed_name: String,
    },
    Install {
        id: String,
    },
    #[serde(rename = "display_info")]
    DisplayInfo {
        id: String,
    },
    #[serde(rename = "display_set")]
    DisplaySet {
        id: String,
        mode: String,
    },
    #[serde(rename = "arm_info")]
    ArmInfo {
        id: String,
    },
    #[serde(rename = "arm_sources")]
    ArmSources {},
    #[serde(rename = "arm_set")]
    ArmSet {
        id: String,
        enabled: bool,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        archive_sha256: Option<String>,
    },
    #[serde(rename = "gpu_info")]
    GpuInfo {
        id: String,
    },
    #[serde(rename = "gpu_set")]
    GpuSet {
        id: String,
        node: String,
    },
    #[serde(rename = "resources_info")]
    ResourcesInfo {
        id: String,
    },
    #[serde(rename = "resources_set")]
    ResourcesSet {
        id: String,
        memory_mib: u64,
        cpu_count: u64,
    },
    Requirements {},
    #[serde(rename = "image_catalog")]
    ImageCatalog {
        refresh: bool,
    },
    #[serde(rename = "image_download")]
    ImageDownload {
        flavor: String,
        system_sha256: String,
        vendor_sha256: String,
    },
    #[serde(rename = "image_download_status")]
    ImageDownloadStatus {},
    #[serde(rename = "image_select")]
    ImageSelect {
        image_id: String,
    },
    #[serde(rename = "image_rename")]
    ImageRename {
        image_id: String,
        name: String,
    },
    #[serde(rename = "image_download_cancel")]
    ImageDownloadCancel {
        job_id: String,
    },
    #[serde(rename = "google_services")]
    GoogleServices {
        id: String,
    },
    #[serde(rename = "app_action")]
    AppAction {
        id: String,
        action: String,
        package: String,
    },
    #[serde(rename = "app_job")]
    AppJob {
        id: String,
    },
    Apps {
        id: String,
    },
    #[serde(rename = "app_states")]
    AppStates {
        id: String,
    },
    Keymaps {
        id: String,
    },
    Health {
        id: String,
    },
    #[serde(rename = "storage_info")]
    StorageInfo {
        id: String,
    },
    Move {
        id: String,
        destination: String,
    },
    #[serde(rename = "discard_previous")]
    DiscardPrevious {
        id: String,
        confirmed_name: String,
    },
    Create {
        name: String,
    },
    #[serde(rename = "create_image")]
    CreateImage {
        name: String,
        flavor: String,
        system_sha256: String,
        vendor_sha256: String,
    },
    #[serde(rename = "image_import")]
    ImageImport { name: String },
    #[serde(rename = "image_import_folder")]
    ImageImportFolder { name: String },
    #[serde(rename = "arm_import")]
    ArmImport {},
    Rename {
        id: String,
        name: String,
    },
    Prepare {
        id: String,
    },
    Reinstall {
        id: String,
        confirmed_name: String,
        flavor: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        system_sha256: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        vendor_sha256: Option<String>,
    },
    Start {
        id: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        display: Option<String>,
    },
    Stop {
        id: String,
    },
    Refresh {
        id: String,
    },
}

fn display_basename(value: &str, uid: u32) -> Option<String> {
    let path = std::path::Path::new(value);
    if path.is_absolute() {
        let expected = std::path::PathBuf::from(format!("/run/user/{uid}"));
        if path.parent() != Some(expected.as_path()) {
            return None;
        }
    } else if path.components().count() != 1 {
        return None;
    }
    let name = path.file_name()?.to_str()?;
    if !name.starts_with("wayland-")
        || !name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
    {
        return None;
    }
    Some(name.to_owned())
}

pub(crate) fn x11_display(value: &str) -> Option<String> {
    if value.len() > 64 {
        return None;
    }
    let path = std::path::Path::new(value);
    if path.is_absolute() {
        let name = path.file_name()?.to_str()?;
        let number = name.strip_prefix('X')?;
        return (path.parent() == Some(std::path::Path::new("/tmp/.X11-unix"))
            && !number.is_empty()
            && number.bytes().all(|b| b.is_ascii_digit()))
        .then(|| value.to_owned());
    }
    let rest = value
        .strip_prefix("localhost:")
        .or_else(|| value.strip_prefix("unix:"))
        .or_else(|| value.strip_prefix(':'))?;
    let mut parts = rest.split('.');
    let display_number = parts.next()?;
    if display_number.is_empty() || !display_number.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    match parts.next() {
        None => Some(value.to_owned()),
        Some(screen) if !screen.is_empty()
            && screen.bytes().all(|b| b.is_ascii_digit())
            && parts.next().is_none() =>
        {
            Some(value.to_owned())
        }
        _ => None,
    }
}

fn session_display(wayland: Option<&str>, display: Option<&str>, uid: u32) -> Option<String> {
    if let Some(value) = wayland {
        if let Some(name) = display_basename(value, uid) {
            return Some(name);
        }
    }
    if let Some(value) = display {
        if x11_display(value).is_some() {
            return Some(format!("x11:{value}"));
        }
    }
    None
}

pub fn desktop_session_display() -> Option<String> {
    // getuid reads the GUI caller's identity; it does not change credentials.
    session_display(
        std::env::var("WAYLAND_DISPLAY").ok().as_deref(),
        std::env::var("DISPLAY").ok().as_deref(),
        unsafe { libc::getuid() },
    )
}

pub fn request(request: Request) -> Result<Value, String> {
    let socket = UnixStream::connect("/run/anvildroid/control.sock")
        .map_err(|e| format!("Runtime controller unavailable: {e}"))?;
    exchange(socket, request).map_err(|e| format!("Runtime controller: {e}"))
}

fn exchange(socket: UnixStream, request: Request) -> Result<Value, String> {
    exchange_file(socket, request, None)
}

pub fn install(id: &str, path: &std::path::Path) -> Result<Value, String> {
    if !crate::library::valid_managed_runtime(id) {
        return Err("Invalid runtime ID".into());
    }
    use std::os::unix::fs::OpenOptionsExt;
    let file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK)
        .open(path)
        .map_err(|e| format!("Cannot read APK: {e}"))?;
    let metadata = file.metadata().map_err(|e| e.to_string())?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > 2 * 1024 * 1024 * 1024 {
        return Err("APK must be a regular file between 1 byte and 2 GiB".into());
    }
    let socket = UnixStream::connect("/run/anvildroid/control.sock")
        .map_err(|e| format!("Runtime controller unavailable: {e}"))?;
    exchange_file(socket, Request::Install { id: id.into() }, Some(&file))
}

pub fn import_image(path: &std::path::Path, name: &str) -> Result<Value, String> {
    use std::os::unix::fs::OpenOptionsExt;
    let file = std::fs::OpenOptions::new().read(true).custom_flags(libc::O_NONBLOCK)
        .open(path).map_err(|e| format!("Cannot read custom image ZIP: {e}"))?;
    let metadata = file.metadata().map_err(|e| e.to_string())?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > 32 * 1024 * 1024 * 1024 {
        return Err("Custom image ZIP must be a regular file up to 32 GiB".into());
    }
    let socket = UnixStream::connect("/run/anvildroid/control.sock")
        .map_err(|e| format!("Runtime controller unavailable: {e}"))?;
    exchange_file(socket, Request::ImageImport { name: name.to_owned() }, Some(&file))
}

pub fn import_image_folder(path: &std::path::Path, name: &str) -> Result<Value, String> {
    use std::os::unix::fs::OpenOptionsExt;
    let mut files = Vec::new();
    for name in ["system.img", "vendor.img"] {
        let file = std::fs::OpenOptions::new().read(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW).open(path.join(name))
            .map_err(|e| format!("Cannot read {name} in selected folder: {e}"))?;
        let metadata = file.metadata().map_err(|e| e.to_string())?;
        if !metadata.is_file() || metadata.len() == 0 || metadata.len() > 16 * 1024 * 1024 * 1024 {
            return Err(format!("{name} must be a regular raw image up to 16 GiB"));
        }
        files.push(file);
    }
    let socket = UnixStream::connect("/run/anvildroid/control.sock")
        .map_err(|e| format!("Runtime controller unavailable: {e}"))?;
    exchange_files(socket, Request::ImageImportFolder { name: name.to_owned() }, &[&files[0], &files[1]])
}

pub fn import_arm_source(kind: &str, source: &str, expected_sha256: &str) -> Result<Value, String> {
    use std::os::fd::FromRawFd;
    use std::process::{Command, Stdio};
    use std::io::{Seek, SeekFrom};
    if !matches!(kind, "builtin" | "url" | "folder" | "archive") {
        return Err("Unsupported ARM translation source".into());
    }
    // Anonymous, user-owned output; no temporary path crosses the root boundary.
    let descriptor = unsafe { libc::memfd_create(c"anvildroid-arm-source".as_ptr(), libc::MFD_CLOEXEC) };
    if descriptor < 0 { return Err(std::io::Error::last_os_error().to_string()); }
    let mut file = unsafe { std::fs::File::from_raw_fd(descriptor) };
    let output = Command::new("/usr/bin/python3")
        .args(["-I", "-c", include_str!("../../../services/runtime-arm-source.py"), kind, source, expected_sha256])
        .stdin(Stdio::null()).stdout(Stdio::from(file.try_clone().map_err(|e| e.to_string())?))
        .stderr(Stdio::piped()).output().map_err(|e| format!("Cannot collect ARM source: {e}"))?;
    if !output.status.success() {
        return Err(format!("Cannot collect ARM source: {}", String::from_utf8_lossy(&output.stderr).trim()));
    }
    file.seek(SeekFrom::Start(0)).map_err(|e| format!("Cannot rewind ARM source: {e}"))?;
    let metadata = file.metadata().map_err(|e| e.to_string())?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > 256 * 1024 * 1024 {
        return Err("ARM translation archive must be a regular file up to 256 MiB".into());
    }
    let socket = UnixStream::connect("/run/anvildroid/control.sock")
        .map_err(|e| format!("Runtime controller unavailable: {e}"))?;
    exchange_file(socket, Request::ArmImport {}, Some(&file))
}

fn exchange_file(socket: UnixStream, request: Request, file: Option<&std::fs::File>) -> Result<Value, String> {
    let files: Vec<_> = file.into_iter().collect();
    exchange_files(socket, request, &files)
}

fn exchange_files(
    mut socket: UnixStream,
    request: Request,
    files: &[&std::fs::File],
) -> Result<Value, String> {
    let mut credentials = libc::ucred {
        pid: 0,
        uid: u32::MAX,
        gid: u32::MAX,
    };
    let mut length = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    // The buffer and its size remain valid for the entire getsockopt call.
    let result = unsafe {
        libc::getsockopt(
            socket.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&mut credentials as *mut libc::ucred).cast(),
            &mut length,
        )
    };
    if result != 0 || length as usize != std::mem::size_of::<libc::ucred>() || credentials.uid != 0
    {
        return Err("Refusing endpoint not owned by root".into());
    }
    // ARM staging fsyncs hundreds of library files before publishing a version.
    let reply_timeout = if matches!(&request, Request::ArmImport {} | Request::ArmSet { .. }) {
        180
    } else {
        5
    };
    socket
        .set_read_timeout(Some(Duration::from_secs(reply_timeout)))
        .map_err(|e| e.to_string())?;
    socket
        .set_write_timeout(Some(Duration::from_secs(5)))
        .map_err(|e| e.to_string())?;
    let mut payload = serde_json::to_vec(&request).map_err(|e| e.to_string())?;
    if payload.len() >= 16384 {
        return Err("Request too large".into());
    }
    payload.push(b'\n');
    if !files.is_empty() {
        send_files(&mut socket, &payload, files)?;
    } else {
        socket.write_all(&payload).map_err(|e| e.to_string())?;
    }
    decode(BufReader::new(socket))
}

fn send_files(socket: &mut UnixStream, payload: &[u8], files: &[&std::fs::File]) -> Result<(), String> {
    let descriptor_bytes = std::mem::size_of::<libc::c_int>() * files.len();
    // cmsghdr-aligned storage; descriptors are sent only after root peer validation.
    let space = unsafe { libc::CMSG_SPACE(descriptor_bytes as _) } as usize;
    let mut control = vec![0usize; space.div_ceil(std::mem::size_of::<usize>())];
    let mut iov = libc::iovec {
        iov_base: payload.as_ptr() as *mut _,
        iov_len: payload.len(),
    };
    let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
    message.msg_iov = &mut iov;
    message.msg_iovlen = 1;
    message.msg_control = control.as_mut_ptr().cast();
    message.msg_controllen = space;
    let sent = unsafe {
        let header = libc::CMSG_FIRSTHDR(&message);
        (*header).cmsg_level = libc::SOL_SOCKET;
        (*header).cmsg_type = libc::SCM_RIGHTS;
        (*header).cmsg_len = libc::CMSG_LEN(descriptor_bytes as _) as usize;
        for (index, file) in files.iter().enumerate() {
            std::ptr::write_unaligned(libc::CMSG_DATA(header).cast::<libc::c_int>().add(index), file.as_raw_fd());
        }
        libc::sendmsg(socket.as_raw_fd(), &message, libc::MSG_NOSIGNAL)
    };
    if sent < 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    socket
        .write_all(&payload[sent as usize..])
        .map_err(|e| e.to_string())
}

fn decode(reader: impl BufRead) -> Result<Value, String> {
    let mut data = Vec::new();
    reader
        .take(4 * 1024 * 1024 + 1)
        .read_until(b'\n', &mut data)
        .map_err(|e| e.to_string())?;
    if data.len() > 4 * 1024 * 1024 || data.last() != Some(&b'\n') {
        return Err("Invalid or oversized response".into());
    }
    let response: Value = serde_json::from_slice(&data).map_err(|e| e.to_string())?;
    match response.get("ok").and_then(Value::as_bool) {
        Some(true) => response
            .get("result")
            .cloned()
            .ok_or_else(|| "Missing result".into()),
        Some(false) => Err(response
            .pointer("/error/message")
            .and_then(Value::as_str)
            .unwrap_or("Request failed")
            .to_owned()),
        None => Err("Invalid response envelope".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn custom_import_uses_descriptor_not_privileged_path() {
        assert!(serde_json::from_str::<Request>(r#"{"op":"image_import","name":"Custom"}"#).is_ok());
        assert!(serde_json::from_str::<Request>(r#"{"op":"image_import","path":"/etc/shadow"}"#).is_err());
        assert_eq!(serde_json::to_value(Request::ImageImport { name: "Custom".into() }).unwrap(), serde_json::json!({"op":"image_import","name":"Custom"}));
        assert_eq!(serde_json::to_value(Request::ArmImport {}).unwrap(), serde_json::json!({"op":"arm_import"}));
    }
    #[test]
    fn existing_management_requests_reject_privileged_fields() {
        for request in [
            r#"{"op":"existing_claim","id":"default"}"#,
            r#"{"op":"existing_import","id":"default","name":"Managed copy"}"#,
        ] {
            assert!(serde_json::from_str::<Request>(request).is_ok());
        }
        for request in [
            r#"{"op":"existing_claim","id":"default","uid":0}"#,
            r#"{"op":"existing_import","id":"default","name":"Managed copy","source":"/etc"}"#,
        ] {
            assert!(serde_json::from_str::<Request>(request).is_err());
        }
    }
    #[test]
    fn app_states_is_typed_and_scoped() {
        let request: Request = serde_json::from_str(r#"{"op":"app_states","id":"r-test"}"#).unwrap();
        assert!(matches!(request, Request::AppStates { ref id } if id == "r-test"));
        assert!(serde_json::from_str::<Request>(r#"{"op":"app_states","id":"r-test","uid":0}"#).is_err());
    }
    #[test]
    fn apk_descriptor_transfer_preserves_bytes_without_sending_a_path() {
        use std::os::fd::FromRawFd;
        use std::os::unix::fs::FileExt;
        let path = std::env::temp_dir().join(format!("anvil-fd-{}.apk", std::process::id()));
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&path)
            .unwrap();
        std::fs::remove_file(path).unwrap();
        file.write_all_at(b"PK\x03\x04test", 0).unwrap();
        let (mut client, server) = UnixStream::pair().unwrap();
        send_files(&mut client, b"{\"op\":\"install\"}\n", &[&file]).unwrap();
        let mut bytes = [0u8; 100];
        let mut control = [0usize; 16];
        let mut iov = libc::iovec {
            iov_base: bytes.as_mut_ptr().cast(),
            iov_len: bytes.len(),
        };
        let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
        message.msg_iov = &mut iov;
        message.msg_iovlen = 1;
        message.msg_control = control.as_mut_ptr().cast();
        message.msg_controllen = std::mem::size_of_val(&control);
        let received =
            unsafe { libc::recvmsg(server.as_raw_fd(), &mut message, libc::MSG_CMSG_CLOEXEC) };
        assert_eq!(&bytes[..received as usize], b"{\"op\":\"install\"}\n");
        let fd = unsafe {
            let header = libc::CMSG_FIRSTHDR(&message);
            assert!(!header.is_null());
            assert_eq!((*header).cmsg_type, libc::SCM_RIGHTS);
            std::ptr::read_unaligned(libc::CMSG_DATA(header).cast::<libc::c_int>())
        };
        let received_file = unsafe { std::fs::File::from_raw_fd(fd) };
        let mut content = [0u8; 8];
        received_file.read_exact_at(&mut content, 0).unwrap();
        assert_eq!(&content, b"PK\x03\x04test");
    }
    #[test]
    fn display_names_stay_within_the_callers_session() {
        assert_eq!(
            display_basename("wayland-0", 1000).as_deref(),
            Some("wayland-0")
        );
        assert_eq!(
            display_basename("/run/user/1000/wayland-0", 1000).as_deref(),
            Some("wayland-0")
        );
        for value in [
            "/run/user/2000/wayland-0",
            "../wayland-0",
            "/tmp/wayland-0",
            "folder/wayland-0",
            "x11:0",
        ] {
            assert!(display_basename(value, 1000).is_none());
        }
    }
    #[test]
    fn x11_display_accepts_only_local_forms() {
        for value in [
            ":0",
            ":0.1",
            "unix:0",
            "localhost:10.0",
            "/tmp/.X11-unix/X0",
        ] {
            assert_eq!(x11_display(value).as_deref(), Some(value), "accept {value}");
        }
        for value in [
            "host.example:0",
            "/tmp/X11-unix/X0",
            "/tmp/.X11-unix/X",
            "/tmp/.X11-unix/X0/..",
            "x11:0",
            ":0.1.2",
            ":",
            "",
            "wayland-0",
        ] {
            assert!(x11_display(value).is_none(), "reject {value}");
        }
    }
    #[test]
    fn session_display_prefers_wayland_over_x11() {
        let uid = 1000;
        assert_eq!(session_display(Some("wayland-0"), Some(":0"), uid).as_deref(), Some("wayland-0"));
        assert_eq!(session_display(None, Some(":0"), uid).as_deref(), Some("x11::0"));
        assert_eq!(session_display(Some("wayland-0"), None, uid).as_deref(), Some("wayland-0"));
        assert_eq!(session_display(None, None, uid), None);
        assert_eq!(session_display(None, Some("bogus"), uid), None);
        assert_eq!(
            session_display(Some("/run/user/2000/wayland-0"), Some(":0"), uid).as_deref(),
            Some("x11::0")
        );
    }
    #[test]
    fn rejects_unframed_or_invalid_responses() {
        for data in [
            b"{\"ok\":true,\"result\":[]}".as_slice(),
            b"{}\n",
            b"{\"ok\":true}\n",
        ] {
            assert!(decode(data).is_err());
        }
        assert!(decode(vec![b'x'; 4 * 1024 * 1024 + 1].as_slice()).is_err());
        assert_eq!(
            decode(b"{\"ok\":true,\"result\":[]}\n".as_slice()).unwrap(),
            serde_json::json!([])
        );
        assert_eq!(
            decode(b"{\"ok\":false,\"error\":{\"message\":\"Busy\"}}\n".as_slice()).unwrap_err(),
            "Busy"
        );
    }
    #[test]
    fn caller_cannot_supply_privileged_fields() {
        for data in [
            r#"{"op":"list","uid":0}"#,
            r#"{"op":"shell"}"#,
            r#"{"op":"start","id":"r-1","socket":"/tmp/fake"}"#,
        ] {
            assert!(serde_json::from_str::<Request>(data).is_err());
        }
    }
    #[test]
    fn rejects_unprivileged_server_before_sending() {
        if unsafe { libc::geteuid() } == 0 {
            return;
        }
        let (client, mut server) = UnixStream::pair().unwrap();
        assert!(exchange(client, Request::List {})
            .unwrap_err()
            .contains("not owned by root"));
        let mut bytes = Vec::new();
        std::io::Read::read_to_end(&mut server, &mut bytes).unwrap();
        assert!(bytes.is_empty());
    }
}
