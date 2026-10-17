# Debian package acceptance — 2026-10-17

**PASS:** the rebuilt 0.1.3 package installed, reinstalled, upgraded a fully configured real 0.1.2 package, preserved a data marker, and uninstalled successfully in a disposable Ubuntu container with real systemd PID 1. No package was installed on the developer host and no personal Android runtime was started, stopped, or changed by this package test.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `anvildroid-controller_0.1.3_amd64.deb` | 26,254,558 | `56dff645cb867b117e3694d0f7b0ff683e386d38a913aed93ac52068f4187ee9` |
| Real prior `anvildroid-controller_0.1.2_amd64.deb` | 23,958,016 | `8316888744b55c432f7e7097d2a42b6bcec099c33d67732ed4f7b2ba32b04f53` |

The package accepted by this run was built from base commit `a3ab0404119a3afa762956c91a4819c691411011` plus the validated permission and lifecycle fixes. The hash above identifies that accepted build. A private copy inside the container avoided stale bind mounts when the package was rebuilt.

The image was `ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac`, reporting Ubuntu 24.04.5 LTS. Dependencies were downloaded once, then reused offline. Final acceptance began with the application package absent; dependencies and test data from earlier attempts remained in this disposable filesystem.

The final container, `anvil-deb-systemd-rw-20261017-a3ab040`, had private PID, mount, network and cgroup namespaces, no network access, no host runtime/device/cgroup mounts, and four read-only mounts containing only the two packages and two verification scripts. It was not privileged. Real systemd required `CAP_SYS_ADMIN`, container-only AppArmor/seccomp exceptions, and a read-write cgroup mount.

Before that remount, read-only checks proved its private cgroup namespace (`4026534162`) differed from the host (`4026531835`), its root was `0::/`, and Docker's host PID 37240 belonged to `/system.slice/docker-7b683645ff9aaff8466735fd3d98416aebf6423107fdb823aea1a2bc04d63d06.scope`. The namespace root contained only this Docker-created subtree. No host cgroup root was mounted into the container.

The final command completed with exit **0**:

```sh
docker exec -e ANVILDROID_APT_UPDATE=0 \
  anvil-deb-systemd-rw-20261017-a3ab040 \
  sh /tmp/verify-package-install-final.sh deb \
  /tmp/current-removal-fixed.deb 0.1.3 /packages/prior.deb
```

| Check | Result |
| --- | --- |
| Install into a legacy root-owned `0775` controller directory | Repaired to `0755`; complete installed verifier passed |
| Same-version reinstall after restoring the legacy directory mode | Repaired; complete installed verifier passed |
| Real 0.1.2 installation under systemd | `install ok installed`; controller unit active |
| In-place 0.1.2 → 0.1.3 upgrade | `install ok installed`; all installed assets safe; controller unit active; complete installed verifier passed |
| Persistent data marker across upgrade and removal | Exact contents retained |
| Package removal | GUI/CLI files absent; controller `MainPID=0`, `ActiveState=inactive`, `LoadState=not-found` |

Each complete installed verification checked native X11 manifests and hashes, Android bytecode and Python assets, NVIDIA payload hashes and notices, Java and packaged APK tools, overlay compilation/signing, CLI version/routing/exit codes, controller list/requirements over Unix IPC, and the GUI's first frontend frame under Xvfb. This did not boot an Android guest or validate hardware rendering.

Earlier attempts exposed and fixed actual defects: dpkg preserved nested legacy `0775` directories; two exact 0.1.2 resource files used `0664`; package removal lacked service lifecycle hooks. Permission regressions pass **15/15**, including unsafe owners, modes, symlinks, and an external hardlink target. Only root-owned exact legacy directory modes and the two named single-link resource files are repaired.

The first non-systemd container could not configure 0.1.2 because its old maintainer script unconditionally invoked systemctl. That result was recorded as a failed prior installation, followed by a distinct half-configured recovery attempt; neither was counted as a successful configured-version upgrade. The final real-systemd run supersedes those attempts.

Evidence is retained locally under `target/acceptance/2026-10-17-deb/`, especially `anvil-deb-removal-fixed-accept-20261017-a3ab040.log`, `final-post-removal-state.log`, the isolation inspection and systemd console log. These generated logs are not source artifacts.

Cleanup removed all three named Debian acceptance containers and the temporary prepared snapshot image. A final `docker ps -a --filter name=anvil-deb` returned no containers. The downloaded Ubuntu base image remains as a cache.

Subsequent FUSE and Wayland fixes produced the final local Debian artifact,
26,256,838 bytes, SHA-256
`5416f46d5490011920c69f958244abcf02d96408ad608b6c320234ddad01b9c7`.
That build passed separate extracted payload/dependency/CLI/image-tool checks,
private packaged controller and GUI frontend-frame smoke, and current
source/native/binary byte parity. Actual X11 controls and desktop Wayland
acceptance also used this final build. The complete package-manager lifecycle
above was not repeated on its different bytes; its maintainer scripts are
unchanged. See [feature plan](WAYDROID-HELPER-FEATURE-PLAN.md) for final results
and remaining manual gates.
