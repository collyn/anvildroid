# Nghiên cứu editor keymap: BlueStacks, LDPlayer và waydroid-helper

Ngày khảo sát: 2026-10-20. AnvilDroid đối chiếu tại commit `1cafd1f`.
Đây là khảo sát tài liệu chính thức, ảnh hướng dẫn và mã nguồn công khai;
chưa chạy BlueStacks/LDPlayer để đo độ trễ hoặc so sánh cảm giác chuột.
Các mục đề xuất bên dưới chưa được triển khai bởi lần khảo sát này.

## Kết luận

AnvilDroid đã có phần lớn cơ chế touch nền: tap/hold, joystick vuốt giữ,
swipe giữ điểm cuối, chuột thành ngón tay, relative mouse capture, nhiều ngón
đồng thời và hủy input khi mất focus. Điểm cần ưu tiên là cách tác giả tạo,
hiểu và thử một layout; sau đó mới bổ sung các hành vi game còn thiếu.

Nên học quy trình **chọn công cụ → đặt lên nút game → gán phím ngay tại
control → chỉnh thuộc tính có tên rõ → thử → lưu**. Tiếp tục dùng editor
gắn với cửa sổ app và tab Game Controls hiện có.

## Những gì đã kiểm chứng

| Hạng mục | BlueStacks | LDPlayer | waydroid-helper |
| --- | --- | --- | --- |
| Tạo control | Kéo công cụ từ bảng vào màn hình game [B1] | Kéo biểu tượng hoặc click màn hình, nhập phím tại điểm [L1] | Chuột phải vùng trống chọn widget, kéo và resize trực tiếp [W1, W2] |
| Đổi binding | Trường Key trong thuộc tính control [B1] | Click binding hiện có rồi gõ phím [L1] | Double-click để capture bàn phím/chuột/tổ hợp [W1] |
| Thuộc tính | Chuột phải control, nội dung riêng theo loại [B1] | Control được trình bày bằng nhóm biểu tượng trong editor [L1] | Bánh răng mở panel cấu hình theo widget [W1] |
| Di chuyển | Đổi từng hướng, radius, tốc độ vuốt, phím thay radius để đi/chạy [B1] | Đổi từng hướng; có chế độ di chuyển bằng chuột phải [L1] | Đổi từng hướng, Slide/Click control; macro thay hệ số radius [W1, W4] |
| Ngắm FPS | Aim/Pan/Shoot, bật/tắt khóa chuột, tạm nhả chuột, độ nhạy và trục Y, điểm bắn riêng [B1, B2] | View, Cursor, Fire và Free Look được giải thích riêng [L1] | Aim dùng relative motion và pointer lock; Fire phối hợp với Aim [W3, W5] |
| MOBA | Skill pad có radius và điểm/phím hủy [B1] | Skill và di chuyển chuột phải [L1] | Skill có radius, nhả để cast/cast ngay/bật-tắt, điểm hủy; Right Click to Walk [W1, W6] |
| Layout | Preset, import/export scheme [B1] | Scheme mặc định/tùy chỉnh, Save/Restore [L1] | Save/Load JSON kèm kích thước màn hình, scale khi load [W7] |

Các trang thương mại mô tả hành vi và quy trình sử dụng; chúng không chứng
minh thuật toán xử lý nội bộ của BlueStacks hoặc LDPlayer.

### Các chi tiết quan trọng trong waydroid-helper

- Đây là **key mapper thực sự**, không chỉ thuộc tính FakeTouch của Waydroid.
  Controller gửi touch qua scrcpy; README và guide đều có phần Key Mapper.
- Aim dùng delta chuột **không tăng tốc**, áp dụng sensitivity rồi cập nhật
  ngón tay trong vùng đã đặt. Khi ra ngoài vùng, nó gửi UP rồi DOWN lại ở tâm
  [W3]. Đây là một mẫu thực tế để hiểu ngắm liên tục bằng touch.
- D-pad bắt đầu DOWN ở tâm, chuyển ra hướng đang giữ và UP khi nhả hết phím.
  Slide/Click trong source chọn nội suy mượt hoặc chuyển tức thời; không nên
  diễn giải Click là bốn nút touch độc lập [W4].
- Guide nói Fire cố định chuột trái, nhưng source khảo sát có chọn trái/phải
  và tùy chọn **drag shot**: Fire tạm nhận quyền relative pointer từ Aim,
  kéo chính ngón tay bắn, rồi trả quyền khi kết thúc [W5]. Vì vậy cần đối chiếu
  source khi tài liệu và phiên bản triển khai khác nhau.
- Guide hướng dẫn đặt cửa sổ game sau overlay [W1]. AnvilDroid nên giữ editor
  gắn với đúng cửa sổ/task, tọa độ theo vùng Android và profile theo runtime/app.

## Đối chiếu AnvilDroid hiện tại

Nguồn nội bộ: [GAME-CONTROLS.md](GAME-CONTROLS.md),
[editor](../native/bridge/keymap-editor-ui.h),
[model](../native/bridge/keymap-model.h),
[input](../native/bridge/scrcpy-mapping.inc),
[thư viện profile](../crates/anvildroid-gui/src-frontend/keymap-library.js).

| Phần | Đã có | Khoảng thiếu hoặc khó dùng |
| --- | --- | --- |
| Tạo và sửa | Add → click → capture phím; picker 9 loại; kéo điểm/handle | Đổi binding còn qua Binding → Key/Mouse; thuộc tính phải chuyển trang; Add/type hiện còn hai vai trò dễ nhầm |
| Chạm/giữ | Basic với Release/Hold, bind M1–M5; Mouse touch theo con trỏ | Chưa thể hiện ngay trên control sự khác nhau giữa chạm ngắn, giữ theo phím và chuột chạm tại vị trí con trỏ |
| Di chuyển | WASD, một ngón joystick, chéo chuẩn hóa, radius và ramp | Cố định W/A/S/D; thiếu đổi từng hướng, modifier đi/chạy và tên chế độ vuốt rõ ràng |
| Camera | Look Hold/Capture, vùng nhìn, sensitivity X/Y, invert Y, thoát bằng Escape | Aim hiện lại là ngắm theo tọa độ tuyệt đối; hai tên dễ khiến người chơi chọn sai cơ chế FPS; thiếu phím tạm nhả capture rồi trở lại |
| Bắn | Basic gán chuột; Aim có ngón bắn riêng | Chưa có luồng FPS hướng dẫn nối vùng nhìn và điểm bắn; chưa có drag shot trên ngón bắn |
| Kỹ năng | Giữ phím, kéo theo con trỏ, nhả để kết thúc | Thiếu radius hướng kỹ năng, điểm/phím hủy, các thời điểm cast và di chuyển kiểu MOBA bằng chuột phải |
| Profile | Lưu/reload, Cancel giữ nguyên file; thư viện theo runtime/app | Native editor hiện chưa có quản lý nhiều scheme/app, clone, import/export hay lịch sử Undo/Redo |

**Mouse touch đã có**, cần làm nó dễ tìm và dễ hiểu. Không thêm một setting
FakeTouch/keymap mới trùng với Compatibility → Mouse as touch. Hai cơ chế này
có phạm vi và đường input khác nhau, xem tài liệu game controls hiện có.

## Thứ tự đề xuất

### 1. Sửa quy trình editor trước

- Bảng công cụ gọn: Chạm, Di chuyển, Vuốt, Ngắm chuột, Kỹ năng; Repeat/Macro
  nằm trong nhóm nâng cao. Mỗi công cụ có tên và mô tả một dòng.
- Chọn công cụ rồi click để đặt; hỗ trợ kéo-thả sau đó. Click nhãn phím hoặc
  double-click control để gán lại. Một vùng capture nhận cả keyboard và mouse;
  click dùng để mở capture không được trở thành binding.
- Chọn control thì hiện đúng các trường cần dùng trong một inspector có nhãn.
  Bỏ chuyển vòng Binding/Timing/Region/Sensitivity. Trên cửa sổ nhỏ dùng panel
  có thể thu gọn và cuộn, không tăng header của tab quản lý.
- Vẽ hình đúng ý nghĩa: vòng joystick, mũi tên swipe, khung camera, tâm kỹ năng,
  điểm bắn. Cho biết control nào đang chọn, thiếu phím hay xung đột.
- Undo/Redo, xóa và nhân bản; thêm **Thử** để dùng draft trong app. Vào thử phải
  nhả hết contact; quay lại sửa phải dừng input. Thử không tự ghi đè profile;
  Save và Cancel vẫn là ranh giới lưu/hủy rõ ràng.

Tiêu chí: một người chưa biết phím tắt có thể đặt nút Jump, bind Space,
đổi sang chuột phải, kéo vị trí, thử rồi lưu trong cùng editor. Cần đo bằng
thao tác thực tế thay vì suy ra từ số test tự động đã pass.

### 2. Hoàn thiện Di chuyển và Ngắm/Bắn

- Mở rộng control Di chuyển hiện có: đổi từng hướng; preset WASD/phím mũi tên;
  chọn vuốt từ tâm với thời gian ramp hoặc chuyển hướng tức thời. Cho kéo radius
  và xem đường đi của ngón tay. Thêm modifier đổi radius để đi/chạy sau khi các
  mode cơ bản được kiểm chứng.
- Nếu game cần bốn nút rời, biểu diễn bằng bốn binding Chạm/Giữ; không giả định
  joystick có thể thay thế mọi D-pad trong game.
- Trình bày FPS thành **Ngắm bằng chuột**: vùng camera, phím bật/tắt capture,
  độ nhạy X/Y, đảo Y, điểm bắn M1 và phím tạm hiện con trỏ. Có hướng dẫn đặt
  vùng camera ở chỗ không đè nút HUD.
- Tái dùng Look Capture và Basic mouse đang có. Có thể cung cấp một preset tạo
  các control này, không cần gộp chúng thành định dạng profile mới ngay. Giữ
  được Aim tuyệt đối cũ bằng tên/mô tả riêng; không đổi ngầm ngữ nghĩa file cũ.
- Drag shot là tùy chọn tiếp theo cho game xoay camera bằng chính nút bắn.
  Cần chuyển quyền pointer rõ ràng giữa camera và fire, tránh hai control cùng
  xử lý một delta hoặc một nút chuột.

Ở cạnh vùng camera, phải nhả và đặt ngón lại để tiếp tục xoay. AnvilDroid đã có
cơ chế này nhưng giới hạn một lần duyệt vùng/event. Cần đo hành vi khi rê nhanh,
độ mượt và delta bị giới hạn; không suy ra chất lượng aim từ việc MotionEvent
được gửi thành công. Sensitivity giữa các emulator cũng không có cùng đơn vị.

### 3. Mở rộng Skill và profile trong luồng hiện có

- Thêm radius, nhả để cast/cast ngay/bật-tắt và điểm hủy vào Skill hiện có.
- Chỉ thêm Right Click to Walk khi triển khai và test được chuyển vị trí chuột
  thành hướng joystick; không coi nó là một tap ở tọa độ chuột.
- Thêm nhiều scheme cho cùng app, clone, reset, import/export trong Game
  Controls và editor hiện có. Giữ runtime/app ownership, metadata kích thước và
  kiểm tra phiên bản; chỉ công bố hỗ trợ import định dạng đã có parser/validator.
- Preset ban đầu là khung FPS/MOBA để người dùng đặt vị trí. Preset theo game
  cần ghi package, phiên bản game, layout HUD, orientation và kết quả nghiệm thu.
  Tọa độ chuẩn hóa không tự bảo đảm đúng nút khi HUD đổi bố cục.

### 4. Nghiệm thu bằng game và input thật

| Ca | Điều phải quan sát |
| --- | --- |
| Tạo/sửa | Đặt, gán lại bàn phím/chuột, kéo, resize, Undo/Redo, thử, Save/reopen, Cancel; cửa sổ nhỏ vẫn dùng được |
| Game bỏ qua mouse | Mouse touch tạo thao tác trong game; click thường và fixed M1 không sinh hai contact; rê hover không thành touch |
| Vuốt giữ di chuyển | DOWN ở tâm → MOVE ra hướng → giữ → UP khi nhả; đổi hướng, chéo, đối hướng và đi/chạy đúng hành vi game |
| FPS | Vừa đi vừa quay vừa bắn; rê nhanh/chậm, qua biên vùng nhiều lần, tạm nhả con trỏ, vào menu rồi trở lại; kiểm tra cả trạng thái camera và đạn |
| MOBA | Giữ/kéo/nhả kỹ năng đúng hướng và tầm, hủy không cast, timing từng mode; chuột phải đi đúng hướng |
| Khôi phục và phạm vi | Alt-Tab, resize, tắt mapping, mở editor, reconnect: không kẹt ngón; runtime/app khác không nhận nhầm input |

Mỗi lần nghiệm thu cần ghi game/build, package, phiên bản Android, runtime,
Wayland/X11, profile và video/log tương ứng. Kết hợp probe để xác nhận contact
với kết quả nhìn thấy trong game và cảm giác dùng chuột vật lý. Không công bố
PASS cho FPS/MOBA hay Arch chỉ dựa trên probe hoặc một game khác.

## Nguồn

- [B1 — BlueStacks: Game Controls and Keymapping](https://www.bluestacks.com/features/game-controls.html).
  Đã đọc hướng dẫn editor, Tap, D-pad, Swipe and hold, Aim/Pan/Shoot, MOBA,
  import/export và thuộc tính cụ thể.
- [B2 — BlueStacks: Shooting Mode](https://www.bluestacks.com/features/shooting-mode.html).
  Đã đọc Start/Stop, Suspend, sensitivity, Free Look và điểm Fire.
- [L1 — LDPlayer: Keymapping on Emulator — How to Use Keyboard Mapping](https://www.ldplayer.net/blog/introduction-to-keyboard-mapping.html).
  Đã đọc bài đầy đủ và xem ảnh editor. Blog trả HTTP 403 khi tải trực tiếp;
  nội dung bài được đọc qua [bản trích xuất văn bản](https://r.jina.ai/https://www.ldplayer.net/blog/introduction-to-keyboard-mapping.html),
  ảnh được tải trực tiếp từ CDN của LDPlayer. Không dùng snippet tìm kiếm để
  suy diễn thuật toán của sản phẩm.
- [W1 — waydroid-helper: Key Mapping Guide](https://github.com/waydroid-helper/waydroid-helper/blob/17f5f326de9facdbb3d9f6b02354a4dcb0f628fd/docs/KEY_MAPPING.md).
- [W2 — Menu tạo widget](https://github.com/waydroid-helper/waydroid-helper/blob/17f5f326de9facdbb3d9f6b02354a4dcb0f628fd/waydroid_helper/controller/ui/menus.py).
- [W3 — Aim, relative motion và đặt lại ngón ở tâm](https://github.com/waydroid-helper/waydroid-helper/blob/17f5f326de9facdbb3d9f6b02354a4dcb0f628fd/waydroid_helper/controller/widgets/components/aim.py#L290).
- [W4 — Directional Pad](https://github.com/waydroid-helper/waydroid-helper/blob/17f5f326de9facdbb3d9f6b02354a4dcb0f628fd/waydroid_helper/controller/widgets/components/directional_pad.py).
- [W5 — Fire và drag shot](https://github.com/waydroid-helper/waydroid-helper/blob/17f5f326de9facdbb3d9f6b02354a4dcb0f628fd/waydroid_helper/controller/widgets/components/fire.py).
- [W6 — Skill Casting](https://github.com/waydroid-helper/waydroid-helper/blob/17f5f326de9facdbb3d9f6b02354a4dcb0f628fd/waydroid_helper/controller/widgets/components/skill_casting.py).
- [W7 — Lưu/load và scale layout](https://github.com/waydroid-helper/waydroid-helper/blob/17f5f326de9facdbb3d9f6b02354a4dcb0f628fd/waydroid_helper/controller/app/widget_layout_service.py).

waydroid-helper được khảo sát tại commit
`17f5f326de9facdbb3d9f6b02354a4dcb0f628fd` (2026-09-12), giấy phép
[GPL-3.0](https://github.com/waydroid-helper/waydroid-helper/blob/17f5f326de9facdbb3d9f6b02354a4dcb0f628fd/COPYING).
Các đề xuất là tham khảo hành vi và thiết kế; bản triển khai AnvilDroid tiếp tục
viết mã gốc theo MIT. Khảo sát này không nhập source, tài nguyên hoặc preset
của sản phẩm khác vào AnvilDroid.
