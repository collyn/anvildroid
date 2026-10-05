//! Desktop actions pinned to an owner-authorized controller runtime.
use crate::{
    backend,
    controller::{self, Request},
    error::AnvilError,
    library,
};
use serde_json::Value;
use std::time::{Duration, Instant};

pub fn validate(id: &str, package: &str) -> Result<(), AnvilError> {
    if !library::valid_managed_runtime(id) || !backend::valid_package(package) {
        return Err(AnvilError::InvalidArgument(
            "Invalid runtime or package".into(),
        ));
    }
    Ok(())
}

pub fn launch(id: &str, package: &str) -> Result<(), AnvilError> {
    validate(id, package)?;
    launch_with(id, package, |request| {
        if matches!(&request, Request::AppAction { action, .. } if action == "launch") {
            let exe=std::env::current_exe().map_err(|e| e.to_string())?;
            crate::desktop::register_managed_window(id, package, &exe).map_err(|e| e.to_string())?;
        }
        controller::request(request)
    }, || {
        std::thread::sleep(Duration::from_millis(500))
    })
    .map_err(AnvilError::BackendFailed)?;
    let _ = library::launched_runtime(id, package);
    Ok(())
}

fn launch_with(
    id: &str,
    package: &str,
    mut request: impl FnMut(Request) -> Result<Value, String>,
    mut wait: impl FnMut(),
) -> Result<(), String> {
    let display = request(Request::DisplayInfo { id: id.into() })?;
    if display["mode"] != "desktop"
        || display["effective_mode"]
            .as_str()
            .is_some_and(|mode| mode != "desktop")
    {
        return Err(
            "Select Desktop mode for this runtime in Settings before opening its shortcut.".into(),
        );
    }
    let deadline = Instant::now() + Duration::from_secs(360);
    let mut started = false;
    loop {
        let record = request(Request::Refresh { id: id.into() })?;
        match record["state"].as_str() {
            Some("Running") => break,
            Some("Prepared" | "Stopped") if !started => {
                request(Request::Start {
                    id: id.into(),
                    display: controller::desktop_session_display(),
                })?;
                started = true;
            }
            Some("Starting") => {}
            _ => return Err(
                "Runtime is not ready. Review its status in Settings before opening this shortcut."
                    .into(),
            ),
        }
        if Instant::now() >= deadline {
            return Err("Timed out waiting for this runtime to start.".into());
        }
        wait();
    }
    let mut job = request(Request::AppAction {
        id: id.into(),
        action: "launch".into(),
        package: package.into(),
    })?;
    let job_id = job["id"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or("Missing app job ID")?
        .to_owned();
    let deadline = Instant::now() + Duration::from_secs(45);
    loop {
        if job["id"] != job_id
            || job["runtime_id"] != id
            || job["package"] != package
            || job["action"] != "launch"
        {
            return Err("App job changed; review this runtime in Settings.".into());
        }
        match job["status"].as_str() {
            Some("Succeeded") => return Ok(()),
            Some("Running") => {}
            Some("Failed") => {
                return Err(job["error"].as_str().unwrap_or("App launch failed").into())
            }
            _ => return Err("Invalid app job status".into()),
        }
        if Instant::now() >= deadline {
            return Err("Timed out waiting for app launch.".into());
        }
        wait();
        job = request(Request::AppJob { id: id.into() })?;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn stopped_runtime_boots_and_every_request_stays_scoped() {
        let id = "r-11111111111111111111111111111111";
        let mut replies = vec![json!({"mode":"desktop"}), json!({"state":"Stopped"}), json!({}), json!({"state":"Starting"}), json!({"state":"Running"}), json!({"id":"job", "runtime_id":id,"package":"com.example.app","action":"launch","status":"Running"}), json!({"id":"job", "runtime_id":id,"package":"com.example.app","action":"launch","status":"Succeeded"})].into_iter();
        launch_with(
            id,
            "com.example.app",
            |r| {
                assert_eq!(serde_json::to_value(r).unwrap()["id"], id);
                Ok(replies.next().unwrap())
            },
            || {},
        )
        .unwrap();
        assert!(replies.next().is_none());
    }
    #[test]
    fn rejects_headless_and_replaced_jobs() {
        assert!(launch_with(
            "r-one",
            "com.example.app",
            |_| Ok(json!({"mode":"headless"})),
            || {}
        )
        .unwrap_err()
        .contains("Desktop"));
        let mut replies = vec![json!({"mode":"desktop"}),json!({"state":"Running"}),json!({"id":"job","runtime_id":"other","package":"com.example.app","action":"launch","status":"Succeeded"})].into_iter();
        assert!(launch_with(
            "r-one",
            "com.example.app",
            |_| Ok(replies.next().unwrap()),
            || {}
        )
        .unwrap_err()
        .contains("job changed"));
    }
}

/// Wait for the exact accepted install job, without following a later action.
pub fn install(id: &str, path: &std::path::Path) -> Result<(), AnvilError> {
    let mut job = controller::install(id, path).map_err(AnvilError::BackendFailed)?;
    let job_id = job["id"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| AnvilError::BackendFailed("Missing installation job ID".into()))?
        .to_owned();
    let deadline = Instant::now() + Duration::from_secs(360);
    loop {
        if job["id"] != job_id || job["runtime_id"] != id || job["action"] != "install" {
            return Err(AnvilError::BackendFailed(
                "Installation job changed; check this runtime in Settings before retrying.".into(),
            ));
        }
        match job["status"].as_str() {
            Some("Succeeded") => return Ok(()),
            Some("Running") => {}
            Some("Failed") => {
                return Err(AnvilError::BackendFailed(
                    job["error"]
                        .as_str()
                        .unwrap_or("Installation failed")
                        .into(),
                ))
            }
            _ => {
                return Err(AnvilError::BackendFailed(
                    "Invalid installation status".into(),
                ))
            }
        }
        if Instant::now() >= deadline {
            return Err(AnvilError::Timeout(
                "Installation result is unknown. Check this runtime in Settings before retrying."
                    .into(),
            ));
        }
        std::thread::sleep(Duration::from_millis(500));
        job = controller::request(Request::AppJob { id: id.into() })
            .map_err(AnvilError::BackendFailed)?;
    }
}
