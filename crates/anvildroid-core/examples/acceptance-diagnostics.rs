//! Exercise the public diagnostic export API against a disposable controller.
//! cargo run -p anvildroid-core --example acceptance-diagnostics -- RUNTIME_ID /absolute/report/directory
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    assert_eq!(
        args.len(),
        3,
        "runtime ID and existing report directory required"
    );
    let directory = Path::new(&args[2]);
    assert!(directory.is_absolute() && directory.is_dir());
    for include_logs in [false, true] {
        let path = directory.join(if include_logs {
            "with-logs.json"
        } else {
            "without-logs.json"
        });
        let result = anvildroid_core::diagnostics::export(&args[1], &path, include_logs)
            .expect("export through the production controller and core API");
        assert_eq!(path.metadata().unwrap().permissions().mode() & 0o777, 0o600);
        assert!(anvildroid_core::diagnostics::export(&args[1], &path, include_logs).is_err());
        println!("PASS private export and overwrite refusal: {result}");
    }
}
