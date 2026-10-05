# AnvilDroid

Ứng dụng Linux quản lý và chạy Android qua Waydroid, gồm GUI Rust/Tauri,
controller Python và bridge cửa sổ native. Dự án đang phát triển; khả năng
chạy app phụ thuộc image Android, GPU và ARM translation.

## Chức năng

- Tạo nhiều runtime cách ly, start/stop độc lập, chọn Desktop hoặc Headless.
- Dùng image official hoặc nhập image Waydroid tùy chỉnh từ ZIP/thư mục.
- Quản lý app theo runtime: cài APK, mở, force-stop, gỡ, tạo shortcut và yêu thích.
  Runtime chưa chạy hiển thị thao tác Start thay vì danh sách app.
- Cửa sổ desktop hỗ trợ resize, bàn phím/IME host, clipboard và key mapping.
- Advanced settings chia theo nhóm: graphics, tài nguyên, ARM và lưu trữ.
- Chọn GPU, giới hạn RAM/CPU, chuyển vị trí lưu runtime và xem diagnostics.
- Google services: đọc/copy GSF Android ID và mở trang đăng ký thiết bị Google.

## Yêu cầu host

- Linux x86_64; ưu tiên desktop Wayland. X11 còn experimental.
- Kernel hỗ trợ Binder/BinderFS, namespace và cgroup; Waydroid và công cụ LXC.
- GPU/driver Mesa tương thích, như Intel hoặc AMD. NVIDIA chưa được hỗ trợ.
- Internet để tải image/thư viện và đủ dung lượng cho image, cache, dữ liệu app.
- Quyền quản trị cho cài controller và thiết lập host. Chạy GUI bằng user desktop.

Controller quản lý runtime do app tạo. Existing runtime là phiên Waydroid hệ
thống, có phạm vi quản lý riêng; không coi hai loại runtime hoàn toàn tương đương.

## Bắt đầu

Mở **AnvilDroid > Runtimes**. Nếu cần, dùng **Start runtime controller** và
luồng thiết lập Waydroid trong GUI; chọn VANILLA hoặc GAPPS nếu cần Google Play.
Sau đó tạo runtime, chờ **Prepare** hoàn tất rồi **Start**. Chọn runtime đang
chạy trong trang Apps để cài và mở APK.

### Image tùy chỉnh

Trong **New runtime**, chọn **Custom images from this computer**:

- Thư mục chứa trực tiếp `system.img` và `vendor.img`.
- ZIP chứa hai image; có thể nằm trong thư mục con, không kèm file khác.

Hỗ trợ image Waydroid x86_64 dạng raw ext4, EROFS và Android sparse;
sparse được mở rộng thành raw, tối đa 16 GiB/image. EROFS cần kernel host
hỗ trợ. Prepare yêu cầu SDK 33 trở lên cùng framework/composer Waydroid tương
thích; Android mới hơn vẫn experimental. Prepare thành công chưa chứng minh
mọi app hoặc native bridge tương thích. Chỉ nhập image từ nguồn đáng tin cậy.

### ARM translation

Dừng managed runtime, mở **Advanced settings > ARM translation**, chọn engine
và version, tải/import thư viện rồi lưu. Cấu hình áp dụng khi Start lại.
Catalog hiện có:

| Engine | Version |
| --- | --- |
| libndk_translation | 0.2.3 |
| libhoudini | 14.0.0 GoogleGame com1.3 |

Có thể nhập nguồn tùy chỉnh qua URL, ZIP hoặc thư mục theo giao diện. Gói
catalog được kiểm tra checksum. Khả năng tương thích tùy Android, CPU và app;
ARM translation không bảo đảm game hoạt động hoặc vượt kiểm tra thiết bị.

### Existing runtime

Chạy Existing runtime rồi dùng **Enable management** để xác minh tài khoản sở
hữu phiên Waydroid. Dừng Android trước khi sửa GPU, RAM/CPU hoặc ARM.
Existing hỗ trợ bật/tắt libndk_translation đã có, chưa hỗ trợ thay engine như
managed runtime. Bản sao cấu hình trước sửa được giữ trong controller store.

Muốn Desktop/Headless và quản lý lưu trữ độc lập, dừng Existing rồi chọn
**Import managed copy…**. Giữ bản gốc dừng trong quá trình sao chép và Prepare;
Start bản sao khi Prepare thành công. Bản gốc được giữ lại. Phiên đăng nhập
và khóa gắn thiết bị cần kiểm tra riêng theo app.

### Google Play

Với image GAPPS, dùng **Check Google services** để đọc GSF Android ID, copy ID
và mở [trang đăng ký Google](https://www.google.com/android/uncertified/).
AnvilDroid không xác minh được trạng thái đăng ký. Play Protect certification
xem trong Play Store > Settings > About.
[Hướng dẫn Waydroid](https://docs.waydro.id/faq/google-play-certification).

### Graphics và chẩn đoán

**Check GPU** đọc renderer SurfaceFlinger và GPU device đang mở. Kết quả chỉ
xác minh compositor Android, không xác minh renderer từng game. CPU cao khi
dịch ARM không đồng nghĩa chuyển sang CPU rendering. Tắt Vulkan cũng không
đồng nghĩa mọi app GLES render bằng CPU.

Software compatibility dùng CPU và chỉ xuất hiện với image hỗ trợ.
NVIDIA/Venus chưa được tích hợp. Đổi GPU hoặc ARM cần stop/start runtime.

## Build bản Debian

### Chuẩn bị môi trường build

Cần Rust/Cargo, Python 3.11 trở lên, C/C++ toolchain, `pkg-config`, GTK3/WebKitGTK 4.1 development
files, Clang/LLD, JDK (`javac`, `java`, `jar`, `keytool`), `aapt`,
`apksigner`, `dpkg-deb`, `sha256sum` và `flock`. Gói chạy yêu cầu
`patchelf >= 0.18`; tên package hệ thống tùy distro.

Build hiện dùng Android SDK stubs tại:

```text
/usr/lib/android-sdk/platforms/android-23/android.jar
```

Chuẩn bị R8 đã pin version; build script kiểm tra SHA-256 trước khi chạy:

```bash
mkdir -p target/tools
curl -fL https://dl.google.com/dl/android/maven2/com/android/tools/r8/8.3.37/r8-8.3.37.jar \
  -o target/tools/r8-8.3.37.jar
```

Cần hai file từ image Waydroid tương thích: `system/etc/init/init.waydroid.rc`
và `system/framework/framework-res.apk`. Build đọc từ `/var/lib/waydroid/rootfs/`;
nếu không có rootfs đã mount, đặt bản sao tại `target/native/init.waydroid.rc`
và `target/native/framework-res.apk`. Không commit image hoặc khóa ký development.

### Build và cài

Build không cần sudo:

```bash
./scripts/build-deb.sh
# Hoặc đặt version:
./scripts/build-deb.sh 0.1.0
```

Script build GUI release, native bridge, IME, shutdown helper rồi tạo:

```text
target/releases/anvildroid-controller_VERSION_amd64.deb
```

Version mặc định lấy từ `[workspace.package].version` trong `Cargo.toml`.
GUI, core và CLI cùng kế thừa version này; GUI hiển thị ở cuối sidebar.
Script không tự cài. Gói chứa GUI và controller, không cài CLI `anvildroid`.
Dừng runtime và đóng GUI trước khi cài vì post-install restart controller:

```bash
sudo apt install ./target/releases/anvildroid-controller_0.1.0_amd64.deb
anvildroid-gui
```

`.deb` dùng thư viện hệ thống; build trên distro phù hợp máy đích. Với AppImage
baseline Ubuntu 24.04/glibc 2.39, xem script `scripts/package-appimage-2404.sh`
(cần Docker). Portable bundle dùng `scripts/package-portable.sh`.

## Phát triển

| Thư mục | Nội dung |
| --- | --- |
| `crates/anvildroid-gui/` | GUI Tauri và frontend |
| `crates/anvildroid-core/` | Thư viện Rust, giao tiếp controller |
| `crates/anvildroid-cli/` | CLI cho Waydroid hệ thống; managed launch có chọn runtime |
| `services/` | Controller, worker, image, GPU, ARM, network, storage |
| `native/` | Bridge Android/Wayland, IME và runtime helpers |
| `scripts/` | Build, cài đặt và đóng gói |
| `packaging/` | Debian, AppImage và systemd |

```bash
cargo check --workspace --locked
cargo build -p anvildroid-cli --locked
./target/debug/anvildroid --help
```

`docs/`, test suite độc lập, fixture, script thử nghiệm, build artifacts và cấu
hình agent local được giữ ngoài Git. Unit test nằm cùng mã nguồn vẫn được giữ.
Touch Probe và IME activity chẩn đoán là thành phần được app/build tham chiếu,
không loại bằng quy tắc ignore chung cho mọi file có tên “probe”.

Controller store mặc định: `/var/lib/anvildroid-controller/`.
Xem log service bằng `journalctl -u anvildroid-controller`; log worker nằm trong
`instances/<runtime-id>/worker.log`. Diagnostics và thao tác app phải dùng đúng
runtime đích; không xóa store để xử lý lỗi thông thường.

## Release tự động trên GitHub

Workflow `.github/workflows/release.yml` chạy khi push tag `v*`. Runner Ubuntu
24.04 build gói `.deb` x86_64 và tạo GitHub Release kèm `SHA256SUMS` cùng release
notes tự sinh. Workflow chưa build AppImage. Không cần secret riêng: dùng
`GITHUB_TOKEN` với quyền ghi release chỉ trong job publish.

1. Sửa `[workspace.package].version` trong `Cargo.toml`, ví dụ `0.1.1`.
2. Chạy `cargo check --workspace` để cập nhật `Cargo.lock`, rồi commit cả hai
   file cùng thay đổi cần phát hành.
3. Tạo và push tag khớp chính xác version:

```bash
git tag -a v0.1.1 -m "Release 0.1.1"
git push origin v0.1.1
```

Tag khác version sẽ thất bại trước khi tải/build. Hỗ trợ version ổn định
`X.Y.Z` và prerelease `X.Y.Z-alpha.N`, `X.Y.Z-beta.N`, `X.Y.Z-rc.N`. Prerelease
được đánh dấu trên GitHub và đổi dấu `-` thành `~` trong version Debian để
nâng cấp đúng thứ tự. Không ghi đè release đã tồn tại; dùng version/tag mới.

CI tải R8 và image Waydroid đã pin SHA-256 qua
`scripts/prepare-ci-inputs.sh`, trích file build bằng `debugfs` mà không boot
Android. Build không kiểm chứng GPU, game hoặc runtime trên máy người dùng.
Có thể chạy helper này trên Linux có `curl`, `unzip`, `debugfs`, `sha256sum`
để chuẩn bị file đầu vào cho build local.

Bản `.deb` cũ dùng version theo ngày (`20261004.*`) có thứ tự Debian cao hơn
`0.1.0`; chuyển lần đầu sang version mới cần cài đúng file bằng `sudo dpkg -i`.
Dừng runtime trước khi nâng/hạ bản vì cài gói restart controller.
