# Experimental NVIDIA Venus backend

AnvilDroid integrates the host/guest ABI from quinovax/waydroid-nvidia v0.1.2.
It does not run upstream's global installer, change host drivers, or replace
an image's SurfaceFlinger. Upstream feature claims are not AnvilDroid hardware
acceptance results.

## Included in native installers

Debian, RPM and Arch packages include the pinned host/guest payload. Users do
not need a separate download command. The package build prepares it under
`target/nvidia-v0.1.2`, downloading cached release archives when necessary and
checking archive SHA-256, ELF ABI and extracted file hashes. Installation is
local and requires no NVIDIA download or upstream setup script.

Payload path: `/usr/local/lib/anvildroid-controller/nvidia-v0.1.2`.
Upstream licensing and release notices are included in its `notices` directory.
Host dependencies include Vulkan loader, libepoxy, DRM/GBM, X11 and expat. The
NVIDIA kernel/userspace driver and device permissions remain host configuration;
installing AnvilDroid does not change them. NVIDIA remains explicit experimental
selection, not the Auto default.

For source-only controller installations, the maintenance installer remains
available: `sudo python3 scripts/install-nvidia-payload.py --download`.
It refuses to overwrite an existing payload. Package-managed files should be
repaired or upgraded through the package manager instead.

## Initial compatibility boundary

- Native Wayland desktop, exactly one NVIDIA physical GPU; closed or open
  NVIDIA module, upstream minimum driver 535+, DRM modeset enabled.
- Desktop user needs access to the NVIDIA devices and `/dev/udmabuf`.
- Android 13 (SDK 33) image with the native Waydroid minigbm/HWC libraries and
  both x86 and x86_64 guest library mount targets. Other image ABIs fail early.
- Select the NVIDIA render node explicitly under Graphics; Auto still uses
  supported Mesa devices or CPU, never an unvalidated experimental backend.
- Upstream selects LINEAR versus block-linear presentation from topology.
  Hybrid/DE buffer import, multi-window, ARM32/64 and games need live testing.
- The worker binds guest replacements read-only for that boot only. Renderer
  and socket belong to its private namespaces; cleanup kills its process group.
- Socket readiness is not Vulkan readiness. Android must boot and its renderer
  must be inspected. Even an ANGLE/Venus/NVIDIA string alone is not proof that
  a specific host physical GPU processed the frame.

Inspect the instance's `nvidia-renderer.log` and `worker.log` for failures.
Switching back to CPU removes Venus properties and ephemeral library mounts.
No host driver/module installation or global Waydroid service changes occur.

## Validation status

Pinned release payload extracted and validated (14 ELF files); installed copy
passed root ownership and file-hash verification. Unit coverage includes
closed-module detection, malformed payloads, CPU switch-back and group cleanup.
The development host exposes AMD/Intel render nodes, no NVIDIA DRM render node;
NVIDIA graphics boot and desktop presentation have NOT been validated here.
