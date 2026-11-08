//! Managed runtime commands. Every request goes to the root controller for the
//! selected `r-…` runtime; nothing here falls back to the singleton Waydroid
//! backend (System Waydroid / Default).
//!
//! Exit codes:
//!   0 success · 1 failure · 2 usage · 3 runtime not found or not yours ·
//!   4 busy, wrong state or setup missing · 5 timed out waiting (--wait)

use std::ffi::OsString;
use std::time::{Duration, Instant};

use anvildroid_core::controller::Request;
use serde_json::Value;

pub const USAGE: u8 = 2;
pub const NOT_FOUND: u8 = 3;
pub const STATE: u8 = 4;
pub const TIMEOUT: u8 = 5;

pub const HELP: &str = "Managed runtimes (created in the AnvilDroid GUI):
  managed-runtimes [--json]              List your managed runtimes
  --runtime ID status [--json]           Saved state of one runtime
  --runtime ID start [--wait] [--timeout SECONDS]
                                         Start Android in this desktop session
  --runtime ID stop [--wait] [--timeout SECONDS]
                                         Stop Android, keep app data
  --runtime ID apps [--json]             Installed apps (runtime must be running)
  --runtime ID install FILE.apk          Install an APK and wait for Android to confirm
  --runtime ID health [--json]           Health checks; exit 1 when a check failed
  --runtime ID run PACKAGE               Launch an app (starts the runtime if needed)
Without --wait, start/stop return once the controller accepted the job.
Exit codes: 0 ok, 1 failed, 2 usage, 3 not found, 4 busy/invalid state, 5 timeout.";

/// A failure with its documented exit code.
pub struct Failure {
    pub code: u8,
    pub message: String,
}

impl Failure {
    fn usage(message: impl Into<String>) -> Self { Self { code: USAGE, message: message.into() } }
}

/// Map a controller error (code from the response envelope) to an exit code.
pub fn exit_code(controller_code: Option<&str>) -> u8 {
    match controller_code {
        Some("NOT_FOUND" | "FORBIDDEN") => NOT_FOUND,
        Some("BUSY" | "INVALID_STATE" | "SETUP_REQUIRED" | "IMAGE_NOT_READY" | "UNSUPPORTED") => STATE,
        _ => 1,
    }
}

/// Controller transport, injectable for tests.
pub trait Controller {
    fn send(&mut self, request: Request) -> Result<Value, Failure>;
    fn display(&self) -> Option<String>;
    fn sleep(&mut self, duration: Duration);
}

pub struct Live;

impl Controller for Live {
    fn send(&mut self, request: Request) -> Result<Value, Failure> {
        anvildroid_core::controller::request(request).map_err(|message| Failure {
            code: exit_code(anvildroid_core::controller::last_error_code().as_deref()),
            message,
        })
    }
    fn display(&self) -> Option<String> { anvildroid_core::controller::desktop_session_display() }
    fn sleep(&mut self, duration: Duration) { std::thread::sleep(duration) }
}

fn print_json(value: &Value) {
    println!("{}", serde_json::to_string_pretty(value).unwrap_or_default());
}

struct Options { json: bool, wait: bool, timeout: Duration, rest: Vec<String> }

fn options(args: &[OsString]) -> Result<Options, Failure> {
    let mut parsed = Options { json: false, wait: false, timeout: Duration::from_secs(360), rest: Vec::new() };
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        let arg = arg.to_str().ok_or_else(|| Failure::usage("Arguments must be UTF-8"))?;
        match arg {
            "--json" => parsed.json = true,
            "--wait" => parsed.wait = true,
            "--timeout" => {
                let value = iter.next().and_then(|v| v.to_str()).and_then(|v| v.parse::<u64>().ok())
                    .filter(|v| (1..=3600).contains(v))
                    .ok_or_else(|| Failure::usage("--timeout expects 1-3600 seconds"))?;
                parsed.timeout = Duration::from_secs(value);
            }
            other => parsed.rest.push(other.to_owned()),
        }
    }
    Ok(parsed)
}

/// `managed-runtimes [--json]`
pub fn list(controller: &mut impl Controller, args: &[OsString]) -> Result<u8, Failure> {
    let options = options(args)?;
    if options.rest.len() != 1 || options.wait {
        return Err(Failure::usage("Usage: anvildroid managed-runtimes [--json]"));
    }
    let records = controller.send(Request::List {})?;
    if options.json {
        print_json(&records);
    } else if records.as_array().is_none_or(|r| r.is_empty()) {
        println!("No managed runtimes. Create one in the AnvilDroid GUI.");
    } else {
        for record in records.as_array().into_iter().flatten() {
            println!("{}  {:<10}  {}", record["id"].as_str().unwrap_or("?"),
                record["state"].as_str().unwrap_or("?"), record["name"].as_str().unwrap_or(""));
        }
    }
    Ok(0)
}

fn wait_for(controller: &mut impl Controller, id: &str, target: &[&str], timeout: Duration) -> Result<Value, Failure> {
    let deadline = Instant::now() + timeout;
    loop {
        let record = controller.send(Request::Refresh { id: id.into() })?;
        let state = record["state"].as_str().unwrap_or("");
        if target.contains(&state) { return Ok(record); }
        if state == "Error" {
            let error = record.pointer("/job/error").and_then(Value::as_str).unwrap_or("Runtime operation failed");
            return Err(Failure { code: 1, message: error.to_owned() });
        }
        if Instant::now() >= deadline {
            return Err(Failure { code: TIMEOUT, message: format!("Timed out waiting for {id}; last state {state}") });
        }
        controller.sleep(Duration::from_millis(500));
    }
}

/// Commands addressed with `--runtime r-…`.
pub fn run(controller: &mut impl Controller, id: &str, args: &[OsString]) -> Result<u8, Failure> {
    let options = options(args)?;
    let command = options.rest.first().map(String::as_str).unwrap_or("");
    let extra = &options.rest[options.rest.len().min(1)..];
    let waits = matches!(command, "start" | "stop");
    if options.wait && !waits {
        return Err(Failure::usage(format!("--wait is only valid with start or stop, not {command}")));
    }
    let arity = |count: usize, usage: &str| if extra.len() == count { Ok(()) } else { Err(Failure::usage(format!("Usage: anvildroid --runtime ID {usage}"))) };
    match command {
        "status" => {
            arity(0, "status [--json]")?;
            let record = controller.send(Request::Refresh { id: id.into() })?;
            if options.json { print_json(&record) } else {
                println!("{}  {}  {}", id, record["state"].as_str().unwrap_or("?"), record["name"].as_str().unwrap_or(""));
            }
            Ok(0)
        }
        "start" => {
            arity(0, "start [--wait] [--timeout SECONDS]")?;
            // Same rule as the GUI: Android windows need the caller's graphical session.
            let display = controller.display().ok_or(Failure {
                code: STATE,
                message: "Start needs an X11 or Wayland desktop session; run this from your desktop terminal".into(),
            })?;
            let record = controller.send(Request::Refresh { id: id.into() })?;
            if !matches!(record["state"].as_str(), Some("Running" | "Frozen")) {
                controller.send(Request::Start { id: id.into(), display: Some(display) })?;
            }
            if options.wait {
                wait_for(controller, id, &["Running", "Frozen"], options.timeout)?;
                println!("{id} is running");
            } else {
                println!("Start accepted for {id}; use status or --wait to follow it");
            }
            Ok(0)
        }
        "stop" => {
            arity(0, "stop [--wait] [--timeout SECONDS]")?;
            let record = controller.send(Request::Refresh { id: id.into() })?;
            if record["state"].as_str() != Some("Stopped") {
                controller.send(Request::Stop { id: id.into() })?;
            }
            if options.wait {
                wait_for(controller, id, &["Stopped"], options.timeout)?;
                println!("{id} is stopped");
            } else {
                println!("Stop accepted for {id}; use status or --wait to follow it");
            }
            Ok(0)
        }
        "apps" => {
            arity(0, "apps [--json]")?;
            let apps = controller.send(Request::Apps { id: id.into() })?;
            if options.json { print_json(&apps) } else {
                for app in apps["apps"].as_array().into_iter().flatten() {
                    println!("{}\t{}", app["package"].as_str().unwrap_or("?"), app["label"].as_str().unwrap_or(""));
                }
            }
            Ok(0)
        }
        "health" => {
            arity(0, "health [--json]")?;
            let report = controller.send(Request::Health { id: id.into() })?;
            let failed = report["checks"].as_array().into_iter().flatten().any(|check| check["status"] == "error");
            if options.json { print_json(&report) } else {
                for check in report["checks"].as_array().into_iter().flatten() {
                    println!("[{}] {}: {}", check["status"].as_str().unwrap_or("?"),
                        check["name"].as_str().unwrap_or("?"), check["detail"].as_str().unwrap_or(""));
                }
            }
            Ok(if failed { 1 } else { 0 })
        }
        "" => Err(Failure::usage(HELP)),
        other => Err(Failure::usage(format!("'{other}' is not available for managed runtimes.\n{HELP}"))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fake { sent: Vec<String>, states: Vec<&'static str>, display: Option<String>, fail: Option<(u8, String)> }

    impl Controller for Fake {
        fn send(&mut self, request: Request) -> Result<Value, Failure> {
            let text = serde_json::to_string(&request).unwrap();
            self.sent.push(text);
            if let Some((code, message)) = &self.fail { return Err(Failure { code: *code, message: message.clone() }); }
            let state = if self.states.len() > 1 { self.states.remove(0) } else { self.states[0] };
            Ok(serde_json::json!({"id": "r-1", "name": "Games", "state": state, "apps": [], "checks": []}))
        }
        fn display(&self) -> Option<String> { self.display.clone() }
        fn sleep(&mut self, _: Duration) {}
    }

    fn args(values: &[&str]) -> Vec<OsString> { values.iter().map(OsString::from).collect() }
    fn fake(states: Vec<&'static str>) -> Fake { Fake { sent: vec![], states, display: Some("x11::0".into()), fail: None } }
    const ID: &str = "r-11111111111111111111111111111111";

    #[test]
    fn start_routes_only_to_the_selected_managed_runtime_with_the_desktop_session() {
        let mut controller = fake(vec!["Stopped", "Starting", "Running"]);
        assert_eq!(run(&mut controller, ID, &args(&["start", "--wait"])).ok(), Some(0));
        assert!(controller.sent.iter().all(|r| r.contains(ID) && !r.contains("\"default\"")));
        assert!(controller.sent[1].contains("\"op\":\"start\"") && controller.sent[1].contains("x11::0"));
    }

    #[test]
    fn start_without_a_graphical_session_never_reaches_the_controller() {
        let mut controller = fake(vec!["Stopped"]);
        controller.display = None;
        let error = run(&mut controller, ID, &args(&["start"])).err().unwrap();
        assert_eq!(error.code, STATE);
        assert!(controller.sent.is_empty());
    }

    #[test]
    fn waiting_reports_timeouts_and_failed_jobs_with_distinct_codes() {
        let mut controller = fake(vec!["Stopping"]);
        let error = run(&mut controller, ID, &args(&["stop", "--wait", "--timeout", "1"]));
        // Fake sleep returns immediately; the deadline is wall-clock.
        let started = Instant::now();
        let error = error.err().unwrap_or_else(|| panic!("expected timeout after {:?}", started.elapsed()));
        assert_eq!(error.code, TIMEOUT);
        let mut controller = fake(vec!["Stopped", "Starting", "Error"]);
        assert_eq!(run(&mut controller, ID, &args(&["start", "--wait"])).err().unwrap().code, 1);
    }

    #[test]
    fn already_running_or_stopped_runtimes_are_not_restarted() {
        let mut controller = fake(vec!["Running"]);
        run(&mut controller, ID, &args(&["start"])).ok().unwrap();
        assert!(!controller.sent.iter().any(|r| r.contains("\"op\":\"start\"")));
        let mut controller = fake(vec!["Stopped"]);
        run(&mut controller, ID, &args(&["stop"])).ok().unwrap();
        assert!(!controller.sent.iter().any(|r| r.contains("\"op\":\"stop\"")));
    }

    #[test]
    fn usage_and_controller_errors_map_to_documented_exit_codes() {
        let mut controller = fake(vec!["Running"]);
        assert_eq!(run(&mut controller, ID, &args(&["apps", "--wait"])).err().unwrap().code, USAGE);
        assert_eq!(run(&mut controller, ID, &args(&["status", "extra"])).err().unwrap().code, USAGE);
        assert_eq!(run(&mut controller, ID, &args(&["multi-window"])).err().unwrap().code, USAGE);
        assert_eq!(run(&mut controller, ID, &args(&["start", "--timeout", "0"])).err().unwrap().code, USAGE);
        assert_eq!(exit_code(Some("NOT_FOUND")), NOT_FOUND);
        assert_eq!(exit_code(Some("BUSY")), STATE);
        assert_eq!(exit_code(Some("STORAGE")), 1);
        let mut controller = fake(vec!["Running"]);
        controller.fail = Some((NOT_FOUND, "Runtime not found for this user".into()));
        assert_eq!(run(&mut controller, ID, &args(&["status"])).err().unwrap().code, NOT_FOUND);
    }

    #[test]
    fn health_exit_code_reflects_failed_checks() {
        struct Health;
        impl Controller for Health {
            fn send(&mut self, _: Request) -> Result<Value, Failure> {
                Ok(serde_json::json!({"checks": [{"name": "Storage", "status": "ok", "detail": ""}, {"name": "Android worker", "status": "error", "detail": "down"}]}))
            }
            fn display(&self) -> Option<String> { None }
            fn sleep(&mut self, _: Duration) {}
        }
        assert_eq!(run(&mut Health, ID, &args(&["health"])).ok(), Some(1));
    }
}
