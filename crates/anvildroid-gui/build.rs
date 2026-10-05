use std::path::Path;
use std::process::Command;

fn main() {
    // The desktop window controller is loaded by the Android runtime, so it
    // must be rebuilt alongside the GUI release.  Keeping this here makes a
    // plain `cargo build --release` produce the same CSD-enabled artifact as
    // the native release helper, without installing anything system-wide.
    // Read CARGO_MANIFEST_DIR at run time: env!() would bake the absolute
    // path into the cached build-script binary, which breaks relocated
    // builds such as the Ubuntu 24.04 container packaging.
    let manifest_dir_value =
        std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR missing");
    let manifest_dir = Path::new(&manifest_dir_value);
    let workspace_root = manifest_dir
        .parent()
        .and_then(Path::parent)
        .expect("GUI crate must live two directories below the workspace root");

    println!("cargo:rerun-if-changed=../../scripts/build-runtime-desktop.sh");
    println!("cargo:rerun-if-changed=../../scripts/build-native.sh");
    println!("cargo:rerun-if-changed=../../native/bridge");
    println!("cargo:rerun-if-changed=../../native/ime");
    println!("cargo:rerun-if-changed=../../native/overlay");

    let status = Command::new("sh")
        .arg("scripts/build-runtime-desktop.sh")
        .current_dir(workspace_root)
        .status()
        .expect("failed to start native CSD build");
    if !status.success() {
        panic!("native CSD build failed with status {status}");
    }

    println!("cargo:rerun-if-changed=../../native/touch-probe");
    println!("cargo:rerun-if-changed=../../scripts/build-touch-probe.sh");
    let status = Command::new("sh").arg("scripts/build-touch-probe.sh")
        .current_dir(workspace_root).status().expect("start probe build");
    assert!(status.success(), "Touch Probe build failed");
    std::fs::copy(workspace_root.join("target/touch-probe/AnvilDroidTouchProbe.apk"),
        Path::new(&std::env::var("OUT_DIR").unwrap()).join("touch-probe.apk")).expect("bundle probe");
    tauri_build::build();
}
