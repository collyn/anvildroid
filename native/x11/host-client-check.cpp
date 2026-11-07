/* SPDX-License-Identifier: MIT — real client protocol against the isolated
 * adapter. */
#include "../bridge/csd-style.h"
#include "idle-inhibit-client.h"
#include "linux-dmabuf-client.h"
#include "text-input-client.h"
#include "viewporter-client.h"
#include "xdg-shell-client.h"
#include <X11/XKBlib.h>
#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/scrnsaver.h>
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <map>
#include <poll.h>
#include <string>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>
#include <wayland-client.h>

struct Client {
  wl_display *display;
  wl_compositor *compositor = nullptr;
  wl_subcompositor *sub = nullptr;
  wl_shm *shm = nullptr;
  xdg_wm_base *wm = nullptr;
  wp_viewporter *viewporter = nullptr;
  wl_seat *seat = nullptr;
  wl_output *output = nullptr;
  zwp_linux_dmabuf_v1 *dmabuf = nullptr;
  zwp_text_input_manager_v3 *text_manager = nullptr;
  zwp_idle_inhibit_manager_v1 *idle = nullptr;
  int width = 0, height = 0, refresh = 0;
  std::vector<uint32_t> dmabuf_formats, dmabuf_modifiers;
};
static void registry(void *data, wl_registry *r, uint32_t name,
                     const char *iface, uint32_t version) {
  auto &c = *static_cast<Client *>(data);
  if (!strcmp(iface, "wl_compositor"))
    c.compositor = (wl_compositor *)wl_registry_bind(
        r, name, &wl_compositor_interface, version < 4 ? version : 4);
  if (!strcmp(iface, "wl_subcompositor"))
    c.sub = (wl_subcompositor *)wl_registry_bind(
        r, name, &wl_subcompositor_interface, 1);
  if (!strcmp(iface, "wl_shm"))
    c.shm = (wl_shm *)wl_registry_bind(r, name, &wl_shm_interface, 1);
  if (!strcmp(iface, "xdg_wm_base"))
    c.wm = (xdg_wm_base *)wl_registry_bind(r, name, &xdg_wm_base_interface, 1);
  if (!strcmp(iface, "wp_viewporter"))
    c.viewporter =
        (wp_viewporter *)wl_registry_bind(r, name, &wp_viewporter_interface, 1);
  if (!strcmp(iface, "wl_seat"))
    c.seat = (wl_seat *)wl_registry_bind(r, name, &wl_seat_interface, 5);
  if (!strcmp(iface, "wl_output")) {
    c.output = (wl_output *)wl_registry_bind(r, name, &wl_output_interface, 3);
    static const wl_output_listener output_events = {
        [](void *, wl_output *, int32_t, int32_t, int32_t, int32_t, int32_t,
           const char *, const char *, int32_t) {},
        [](void *data, wl_output *, uint32_t flags, int32_t w, int32_t h,
           int32_t refresh) {
          if (flags & WL_OUTPUT_MODE_CURRENT) {
            auto *c = static_cast<Client *>(data);
            c->width = w;
            c->height = h;
            c->refresh = refresh;
          }
        },
        [](void *, wl_output *) {},
        [](void *, wl_output *, int32_t) {},
        nullptr,
        nullptr};
    wl_output_add_listener(c.output, &output_events, &c);
  }
  if (!strcmp(iface, "zwp_text_input_manager_v3"))
    c.text_manager = (zwp_text_input_manager_v3 *)wl_registry_bind(
        r, name, &zwp_text_input_manager_v3_interface, 1);
  if (!strcmp(iface, "zwp_idle_inhibit_manager_v1"))
    c.idle = (zwp_idle_inhibit_manager_v1 *)wl_registry_bind(
        r, name, &zwp_idle_inhibit_manager_v1_interface, 1);
  if (!strcmp(iface, "zwp_linux_dmabuf_v1")) {
    c.dmabuf = (zwp_linux_dmabuf_v1 *)wl_registry_bind(
        r, name, &zwp_linux_dmabuf_v1_interface, 3);
    static const zwp_linux_dmabuf_v1_listener events = {
        [](void *data, zwp_linux_dmabuf_v1 *, uint32_t format) {
          static_cast<Client *>(data)->dmabuf_formats.push_back(format);
        },
        [](void *data, zwp_linux_dmabuf_v1 *, uint32_t format, uint32_t hi,
           uint32_t lo) {
          // Only LINEAR is offered.
          assert(!hi && !lo);
          static_cast<Client *>(data)->dmabuf_modifiers.push_back(format);
        }};
    zwp_linux_dmabuf_v1_add_listener(c.dmabuf, &events, &c);
  }
}
static std::string committed_text;
static const zwp_text_input_v3_listener text_events = {
    [](void *, zwp_text_input_v3 *, wl_surface *) {},
    [](void *, zwp_text_input_v3 *, wl_surface *) {},
    [](void *, zwp_text_input_v3 *, const char *, int32_t, int32_t) {},
    [](void *, zwp_text_input_v3 *, const char *text) {
      committed_text += text;
    },
    [](void *, zwp_text_input_v3 *, uint32_t, uint32_t) {},
    [](void *, zwp_text_input_v3 *, uint32_t) {}};
struct Configure {
  int width = 0, height = 0;
  bool maximized = false;
};
static std::map<xdg_toplevel *, Configure> sizes;
static wl_surface *pointer_surface = nullptr, *keyboard_surface = nullptr;
static int pointer_x = 0, pointer_y = 0;
static unsigned buttons = 0, keys = 0, locked_mods = 0;
static std::vector<std::pair<uint32_t, uint32_t>> physical_keys;
static uint32_t button_serial = 0;
static std::vector<std::pair<uint32_t, wl_fixed_t>> scroll_axes;
static const wl_pointer_listener pointer_events = {
    [](void *, wl_pointer *, uint32_t, wl_surface *s, wl_fixed_t x,
       wl_fixed_t y) {
      pointer_surface = s;
      pointer_x = wl_fixed_to_int(x);
      pointer_y = wl_fixed_to_int(y);
    },
    [](void *, wl_pointer *, uint32_t, wl_surface *) {
      pointer_surface = nullptr;
    },
    [](void *, wl_pointer *, uint32_t, wl_fixed_t x, wl_fixed_t y) {
      pointer_x = wl_fixed_to_int(x);
      pointer_y = wl_fixed_to_int(y);
    },
    [](void *, wl_pointer *, uint32_t serial, uint32_t, uint32_t,
       uint32_t state) {
      ++buttons;
      if (state == WL_POINTER_BUTTON_STATE_PRESSED)
        button_serial = serial;
    },
    [](void *, wl_pointer *, uint32_t, uint32_t axis, wl_fixed_t value) {
      scroll_axes.emplace_back(axis, value);
    },
    [](void *, wl_pointer *) {},
    [](void *, wl_pointer *, uint32_t) {},
    [](void *, wl_pointer *, uint32_t, uint32_t) {},
    [](void *, wl_pointer *, uint32_t, int32_t) {},
    nullptr,
    nullptr};
static const wl_keyboard_listener keyboard_events = {
    [](void *, wl_keyboard *, uint32_t, int32_t fd, uint32_t) { close(fd); },
    [](void *, wl_keyboard *, uint32_t, wl_surface *s, wl_array *) {
      keyboard_surface = s;
    },
    [](void *, wl_keyboard *, uint32_t, wl_surface *) {
      keyboard_surface = nullptr;
    },
    [](void *, wl_keyboard *, uint32_t, uint32_t, uint32_t key, uint32_t state) {
      ++keys;
      physical_keys.emplace_back(key, state);
    },
    [](void *, wl_keyboard *, uint32_t, uint32_t, uint32_t, uint32_t locked,
       uint32_t) { locked_mods = locked; },
    [](void *, wl_keyboard *, int32_t, int32_t) {}};
static unsigned releases = 0, frames = 0, configures = 0, closed = 0;
static const wl_buffer_listener released = {
    [](void *, wl_buffer *) { ++releases; }};
static const wl_callback_listener framed = {
    [](void *, wl_callback *c, uint32_t) {
      ++frames;
      wl_callback_destroy(c);
    }};
static wl_buffer *buffer(Client &c, int w, int h, uint32_t color,
                         int header_state = -1) {
  int fd = memfd_create("test-csd", MFD_CLOEXEC);
  assert(fd >= 0);
  assert(!ftruncate(fd, w * h * 4));
  auto *p = static_cast<uint32_t *>(
      mmap(nullptr, w * h * 4, PROT_WRITE, MAP_SHARED, fd, 0));
  assert(p != MAP_FAILED);
  for (int y = 0; y < h; ++y)
    for (int x = 0; x < w; ++x)
      p[y * w + x] =
          header_state < 0 ? color : csd_header_pixel(x, y, w, h, header_state);
  munmap(p, w * h * 4);
  auto *pool = wl_shm_create_pool(c.shm, fd, w * h * 4);
  auto *b =
      wl_shm_pool_create_buffer(pool, 0, w, h, w * 4, WL_SHM_FORMAT_ARGB8888);
  wl_shm_pool_destroy(pool);
  close(fd);
  wl_buffer_add_listener(b, &released, nullptr);
  return b;
}
struct App {
  wl_surface *surface;
  xdg_surface *xdg;
  xdg_toplevel *top;
};
static App app(Client &c, const char *name, bool maximized = false) {
  App a;
  a.surface = wl_compositor_create_surface(c.compositor);
  a.xdg = xdg_wm_base_get_xdg_surface(c.wm, a.surface);
  a.top = xdg_surface_get_toplevel(a.xdg);
  static const xdg_toplevel_listener top_events = {
      [](void *, xdg_toplevel *top, int32_t w, int32_t h, wl_array *states) {
        auto &size = sizes[top];
        size = {w, h, false};
        auto *values = static_cast<uint32_t *>(states->data);
        for (size_t i = 0; i < states->size / sizeof(uint32_t); ++i)
          size.maximized |= values[i] == XDG_TOPLEVEL_STATE_MAXIMIZED;
      },
      [](void *, xdg_toplevel *) { ++closed; }, nullptr, nullptr};
  xdg_toplevel_add_listener(a.top, &top_events, nullptr);
  static const xdg_surface_listener configured = {
      [](void *, xdg_surface *s, uint32_t serial) {
        ++configures;
        xdg_surface_ack_configure(s, serial);
      }};
  xdg_surface_add_listener(a.xdg, &configured, nullptr);
  xdg_toplevel_set_app_id(a.top, name);
  xdg_toplevel_set_title(a.top, name);
  if (maximized)
    xdg_toplevel_set_maximized(a.top);
  wl_surface_commit(a.surface);
  return a;
}
static void roundtrip(Client &c) {
  assert(wl_display_roundtrip(c.display) >= 0);
}
static int server_fds() {
  const char *pid = getenv("ANVIL_TEST_HOST_PID");
  assert(pid);
  std::string path = std::string("/proc/") + pid + "/fd";
  DIR *directory = opendir(path.c_str());
  assert(directory);
  int count = 0;
  while (auto *entry = readdir(directory))
    if (entry->d_name[0] != '.') ++count;
  closedir(directory);
  return count;
}
static void dmabuf_lifetime_check(Client &c) {
  assert(c.dmabuf);
  const int baseline = server_fds();
  std::vector<zwp_linux_buffer_params_v1 *> retained;
  for (int i = 0; i < 32; ++i) {
    int fd = memfd_create("test-dmabuf-lifetime", MFD_CLOEXEC);
    assert(fd >= 0 && !ftruncate(fd, 16 * 16 * 4));
    auto *params = zwp_linux_dmabuf_v1_create_params(c.dmabuf);
    zwp_linux_buffer_params_v1_add(params, fd, 0, 0, 64, 0, 0);
    close(fd);
    auto *b = zwp_linux_buffer_params_v1_create_immed(params, 16, 16, 0x34325258, 0);
    roundtrip(c);
    assert(server_fds() == baseline + 1);
    if (i % 2) {
      // Destroying params first must not close a live buffer's allocation.
      zwp_linux_buffer_params_v1_destroy(params);
      roundtrip(c);
      assert(server_fds() == baseline + 1);
    } else {
      // Legacy HWC keeps consumed params alive after destroying the buffer.
      retained.push_back(params);
    }
    wl_buffer_destroy(b);
    roundtrip(c);
    assert(server_fds() == baseline);
  }
  for (auto *params : retained) zwp_linux_buffer_params_v1_destroy(params);
  roundtrip(c);
  assert(server_fds() == baseline);
  puts("PASS DMA-BUF allocation lifetime is independent of consumed params");
}
static uint32_t code(const char *s) {
  return uint32_t(uint8_t(s[0])) | uint32_t(uint8_t(s[1])) << 8 |
         uint32_t(uint8_t(s[2])) << 16 | uint32_t(uint8_t(s[3])) << 24;
}
struct Yuv {
  uint8_t y, u, v;
};
// Limited-range colours: top half red, bottom half blue.
static Yuv yuv_at(int y, int h, bool bt709) {
  if (bt709)
    return y < h / 2 ? Yuv{63, 102, 240} : Yuv{32, 240, 118};
  return y < h / 2 ? Yuv{81, 90, 240} : Yuv{41, 240, 110};
}
static int memfd(size_t size) {
  int fd = memfd_create("test-yuv", MFD_CLOEXEC);
  assert(fd >= 0 && !ftruncate(fd, off_t(size)));
  return fd;
}
static uint8_t *map(int fd, size_t size) {
  auto *p = static_cast<uint8_t *>(
      mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0));
  assert(p != MAP_FAILED);
  return p;
}
// Planar 4:2:0 writer. Planes may share one fd (offsets) or use separate fds.
struct PlaneOut {
  uint8_t *base;
  size_t offset, stride;
};
static void fill_420(int w, int h, bool bt709, PlaneOut y, PlaneOut u,
                     PlaneOut v, int step) {
  for (int row = 0; row < h; ++row)
    for (int x = 0; x < w; ++x)
      y.base[y.offset + row * y.stride + x] = yuv_at(row, h, bt709).y;
  for (int row = 0; row < (h + 1) / 2; ++row)
    for (int x = 0; x < (w + 1) / 2; ++x) {
      auto c = yuv_at(row * 2, h, bt709);
      u.base[u.offset + row * u.stride + x * step] = c.u;
      v.base[v.offset + row * v.stride + x * step] = c.v;
    }
}
static zwp_linux_buffer_params_v1 *params_failed = nullptr;
static unsigned failed_events = 0, created_events = 0;
static const zwp_linux_buffer_params_v1_listener params_events = {
    [](void *, zwp_linux_buffer_params_v1 *, wl_buffer *b) {
      ++created_events;
      wl_buffer_destroy(b);
    },
    [](void *, zwp_linux_buffer_params_v1 *p) {
      ++failed_events;
      params_failed = p;
    }};
// What hwcomposer.waydroid sends: one plane-0 add, then create_immed.
static wl_buffer *single(Client &c, int fd, uint32_t stride, int w, int h,
                         uint32_t format, uint64_t modifier = 0) {
  auto *params = zwp_linux_dmabuf_v1_create_params(c.dmabuf);
  zwp_linux_buffer_params_v1_add(params, fd, 0, 0, stride, modifier >> 32,
                                 modifier & 0xffffffff);
  auto *b = zwp_linux_buffer_params_v1_create_immed(params, w, h, format, 0);
  zwp_linux_buffer_params_v1_destroy(params);
  wl_buffer_add_listener(b, &released, nullptr);
  return b;
}
static void near(Display *d, Window w, int x, int y, int r, int g, int b,
                 const char *what) {
  auto *image = XGetImage(d, w, x, y, 1, 1, AllPlanes, ZPixmap);
  assert(image);
  auto p = XGetPixel(image, 0, 0);
  XDestroyImage(image);
  int ar = (p >> 16) & 255, ag = (p >> 8) & 255, ab = p & 255;
  if (abs(ar - r) > 12 || abs(ag - g) > 12 || abs(ab - b) > 12) {
    fprintf(stderr, "%s pixel %d,%d=%02x%02x%02x expected~%02x%02x%02x\n",
            what, x, y, ar, ag, ab, r, g, b);
    assert(false);
  }
}
static Window find_window(Display *d, const char *name);
static void dmabuf_formats_check(Client &c) {
  assert(c.dmabuf);
  auto &f = c.dmabuf_formats;
  auto has = [&](uint32_t x) {
    return std::find(f.begin(), f.end(), x) != f.end();
  };
  // The legacy 8888 set stays, video formats are offered, the private
  // minigbm alias never is, and each format has exactly one LINEAR modifier.
  for (const char *name : {"AR24", "XR24", "AB24", "XB24", "NV12", "NV21",
                           "YV12", "YU12"})
    assert(has(code(name)));
  assert(!has(code("9997")));
  assert(c.dmabuf_modifiers == f);
  const bool x11 = getenv("ANVIL_TEST_X11");
  auto a = app(c, "waydroid.anvildroid.test.dmabuf");
  roundtrip(c);
  roundtrip(c);
  Display *d = x11 ? XOpenDisplay(nullptr) : nullptr;
  Window window = 0;
  auto show = [&](wl_buffer *b, int w, int h, const char *what,
                  bool bt709 = false, bool black = false) {
    unsigned before = releases;
    xdg_surface_set_window_geometry(a.xdg, 0, 0, w, h);
    wl_surface_attach(a.surface, b, 0, 0);
    wl_surface_commit(a.surface);
    roundtrip(c);
    usleep(30000);
    roundtrip(c);
    // Snapshot precedes release; the guest may reuse the buffer at once.
    assert(releases == before + 1);
    if (!d)
      return;
    if (!window) {
      window = find_window(d, "waydroid.anvildroid.test.dmabuf");
      XMoveWindow(d, window, 10, 10);
      XSync(d, False);
      usleep(100000);
      roundtrip(c);
    }
    if (black) {
      near(d, window, w / 2, h / 4, 0, 0, 0, what);
      return;
    }
    // BT.601 decoding of the BT.709 samples would give red ~e90002.
    (void)bt709;
    near(d, window, w / 2, h / 4, 255, 0, 0, what);
    near(d, window, w / 2, h * 3 / 4, 0, 0, 255, what);
  };
  const int w = 90, h = 60;
  // minigbm YV12 ('9997'): stride = width (not 32-aligned), ceil chroma.
  {
    const size_t cs = (w + 1) / 2, ch = (h + 1) / 2,
                 size = size_t(w) * h + 2 * cs * ch;
    int fd = memfd(size);
    auto *m = map(fd, size);
    fill_420(w, h, false, {m, 0, size_t(w)}, {m, w * h + cs * ch, cs},
             {m, size_t(w) * h, cs}, 1);
    munmap(m, size);
    auto *b = single(c, fd, w, w, h, code("9997"));
    close(fd);
    show(b, w, h, "minigbm YV12");
    wl_buffer_destroy(b);
  }
  // gbm_gralloc/Android YV12: padded stride, ALIGN(stride / 2, 16) chroma.
  {
    const size_t s = 128, cs = 64, size = s * h + cs * h;
    int fd = memfd(size);
    auto *m = map(fd, size);
    fill_420(w, h, false, {m, 0, s}, {m, s * h + cs * (h / 2), cs},
             {m, s * h, cs}, 1);
    munmap(m, size);
    auto *b = single(c, fd, s, w, h, code("YV12"));
    close(fd);
    show(b, w, h, "Android YV12");
    wl_buffer_destroy(b);
  }
  // minigbm NV12/NV21 (YCbCr_420_888): interleaved chroma at stride * h.
  for (bool nv21 : {false, true}) {
    const size_t s = 96, size = s * h + s * (h / 2);
    int fd = memfd(size);
    auto *m = map(fd, size);
    size_t uv = s * h;
    fill_420(w, h, false, {m, 0, s}, {m, uv + (nv21 ? 1 : 0), s},
             {m, uv + (nv21 ? 0 : 1), s}, 2);
    munmap(m, size);
    auto *b = single(c, fd, s, w, h, code(nv21 ? "NV21" : "NV12"));
    close(fd);
    show(b, w, h, nv21 ? "NV21" : "NV12");
    wl_buffer_destroy(b);
  }
  // Explicit three-plane YUV420 in three allocations.
  {
    int fds[3] = {memfd(size_t(w) * h), memfd(48 * 30), memfd(48 * 30)};
    auto *y = map(fds[0], size_t(w) * h), *u = map(fds[1], 48 * 30),
         *v = map(fds[2], 48 * 30);
    fill_420(w, h, false, {y, 0, size_t(w)}, {u, 0, 48}, {v, 0, 48}, 1);
    munmap(y, size_t(w) * h);
    munmap(u, 48 * 30);
    munmap(v, 48 * 30);
    auto *params = zwp_linux_dmabuf_v1_create_params(c.dmabuf);
    zwp_linux_buffer_params_v1_add(params, fds[0], 0, 0, w, 0, 0);
    zwp_linux_buffer_params_v1_add(params, fds[1], 1, 0, 48, 0, 0);
    zwp_linux_buffer_params_v1_add(params, fds[2], 2, 0, 48, 0, 0);
    for (int fd : fds)
      close(fd);
    auto *b = zwp_linux_buffer_params_v1_create_immed(params, w, h,
                                                      code("YU12"), 0);
    zwp_linux_buffer_params_v1_destroy(params);
    wl_buffer_add_listener(b, &released, nullptr);
    show(b, w, h, "explicit YU12");
    wl_buffer_destroy(b);
  }
  // HD content is converted as BT.709.
  {
    const int W = 1280, H = 720;
    const size_t size = size_t(W) * H * 3 / 2;
    int fd = memfd(size);
    auto *m = map(fd, size);
    fill_420(W, H, true, {m, 0, size_t(W)}, {m, size_t(W) * H, size_t(W)},
             {m, size_t(W) * H + 1, size_t(W)}, 2);
    munmap(m, size);
    auto *b = single(c, fd, W, W, H, code("NV12"));
    close(fd);
    show(b, W, H, "BT.709 NV12", true);
    wl_buffer_destroy(b);
  }
  // Well-formed but unimportable buffers never kill the composer:
  // create_immed shows a black placeholder; create reports `failed`.
  {
    int fd = memfd(size_t(w) * h * 4);
    auto *b = single(c, fd, w * 2, w, h, code("GR88"));
    show(b, w, h, "unknown format placeholder", false, true);
    wl_buffer_destroy(b);
    b = single(c, fd, w * 4, w, h, code("XR24"), 0x0100000000000001ull);
    show(b, w, h, "tiled placeholder", false, true);
    wl_buffer_destroy(b);
    // A short stride for an advertised format is still non-fatal.
    b = single(c, fd, w * 2, w, h, code("XR24"));
    show(b, w, h, "short stride placeholder", false, true);
    wl_buffer_destroy(b);
    auto *params = zwp_linux_dmabuf_v1_create_params(c.dmabuf);
    zwp_linux_buffer_params_v1_add_listener(params, &params_events, nullptr);
    zwp_linux_buffer_params_v1_add(params, fd, 0, 0, w * 2, 0, 0);
    zwp_linux_buffer_params_v1_create(params, w, h, code("GR88"), 0);
    roundtrip(c);
    assert(failed_events == 1 && params_failed == params && !created_events);
    zwp_linux_buffer_params_v1_destroy(params);
    params = zwp_linux_dmabuf_v1_create_params(c.dmabuf);
    zwp_linux_buffer_params_v1_add_listener(params, &params_events, nullptr);
    zwp_linux_buffer_params_v1_add(params, fd, 0, 0, w * 4, 0, 0);
    zwp_linux_buffer_params_v1_create(params, w, h, code("XR24"), 0);
    roundtrip(c);
    assert(failed_events == 1 && created_events == 1);
    zwp_linux_buffer_params_v1_destroy(params);
    // The composer's SHM path labels non-RGBA HAL formats -EINVAL.
    auto *pool = wl_shm_create_pool(c.shm, fd, w * h * 4);
    b = wl_shm_pool_create_buffer(pool, 0, w, h, w * 4, uint32_t(-22));
    wl_shm_pool_destroy(pool);
    wl_buffer_add_listener(b, &released, nullptr);
    show(b, w, h, "SHM -EINVAL placeholder", false, true);
    wl_buffer_destroy(b);
    close(fd);
  }
  wl_surface_attach(a.surface, nullptr, 0, 0);
  wl_surface_commit(a.surface);
  xdg_toplevel_destroy(a.top);
  xdg_surface_destroy(a.xdg);
  wl_surface_destroy(a.surface);
  roundtrip(c);
  if (d)
    XCloseDisplay(d);
  puts(x11 ? "PASS minigbm/gbm_gralloc YUV DMA-BUFs import with correct planes "
             "and BT.601/709; unimportable buffers become placeholders"
           : "PASS DMA-BUF format advertisement and non-fatal unimportable "
             "buffers");
}
static Window find_window(Display *d, const char *name) {
  Window root, parent, *children = nullptr;
  unsigned count = 0;
  assert(
      XQueryTree(d, DefaultRootWindow(d), &root, &parent, &children, &count));
  Window found = 0;
  for (unsigned i = 0; i < count; ++i) {
    XClassHint hint{};
    if (XGetClassHint(d, children[i], &hint)) {
      if (hint.res_class && !strcmp(hint.res_class, name))
        found = children[i];
      if (hint.res_name)
        XFree(hint.res_name);
      if (hint.res_class)
        XFree(hint.res_class);
    }
  }
  if (children)
    XFree(children);
  assert(found);
  return found;
}
static void pixel(Display *d, Window w, int x, int y, unsigned long color) {
  auto *image = XGetImage(d, w, x, y, 1, 1, AllPlanes, ZPixmap);
  assert(image);
  auto actual = XGetPixel(image, 0, 0) & 0xffffff;
  if (actual != color)
    fprintf(stderr, "pixel actual=%lx expected=%lx\n", actual, color);
  assert(actual == color);
  XDestroyImage(image);
}
static void globals_destroy(Client &c, wl_registry *r) {
  if (c.idle)
    zwp_idle_inhibit_manager_v1_destroy(c.idle);
  zwp_linux_dmabuf_v1_destroy(c.dmabuf);
  zwp_text_input_manager_v3_destroy(c.text_manager);
  wl_output_release(c.output);
  wl_seat_release(c.seat);
  wp_viewporter_destroy(c.viewporter);
  xdg_wm_base_destroy(c.wm);
  wl_shm_destroy(c.shm);
  wl_subcompositor_destroy(c.sub);
  wl_compositor_destroy(c.compositor);
  wl_registry_destroy(r);
}
static char idle_state() {
  // The endpoint logs each transition; its newest line is the current state.
  const char *path = getenv("ANVIL_TEST_HOST_LOG");
  assert(path);
  FILE *log = fopen(path, "r");
  assert(log);
  char line[512], state = '0';
  while (fgets(line, sizeof(line), log))
    if (!strncmp(line, "x11_idle_inhibit=", 17))
      state = line[17];
  fclose(log);
  return state;
}
static void expect_idle(Client &c, char state) {
  for (int i = 0; i < 300 && idle_state() != state; ++i) {
    roundtrip(c);
    usleep(10000);
  }
  if (idle_state() != state)
    fprintf(stderr, "idle inhibit actual=%c expected=%c\n", idle_state(),
            state);
  assert(idle_state() == state);
}
static long host_cpu_ticks() {
  const char *pid = getenv("ANVIL_TEST_HOST_PID");
  assert(pid);
  FILE *stat = fopen((std::string("/proc/") + pid + "/stat").c_str(), "r");
  assert(stat);
  char text[1024]{};
  assert(fgets(text, sizeof(text), stat));
  fclose(stat);
  auto *field = strrchr(text, ')');
  assert(field);
  long user = 0, system = 0;
  // Fields 14/15 follow the state and 10 counters after the command name.
  assert(sscanf(field + 2, "%*c %*d %*d %*d %*d %*d %*u %*u %*u %*u %*u %ld %ld",
                &user, &system) == 2);
  return user + system;
}
static int saver_state(Display *d, unsigned long *idle = nullptr) {
  auto *info = XScreenSaverAllocInfo();
  assert(info && XScreenSaverQueryInfo(d, DefaultRootWindow(d), info));
  int state = info->state;
  if (idle)
    *idle = info->idle;
  XFree(info);
  return state;
}
static void idle_check(Client &c, wl_registry *r) {
  assert(c.idle);
  auto a = app(c, "waydroid.anvildroid.test.idle");
  roundtrip(c);
  roundtrip(c);
  auto *content = buffer(c, 320, 200, 0xff406080);
  wl_surface_attach(a.surface, content, 0, 0);
  wl_surface_commit(a.surface);
  roundtrip(c);
  roundtrip(c);
  expect_idle(c, '0');
  auto *first = zwp_idle_inhibit_manager_v1_create_inhibitor(c.idle, a.surface);
  expect_idle(c, '1');
  // Inhibition arms one coarse timer; the endpoint must keep sleeping.
  auto ticks = host_cpu_ticks();
  usleep(1000000);
  ticks = host_cpu_ticks() - ticks;
  if (ticks > 10)
    fprintf(stderr, "inhibited endpoint used %ld CPU ticks in 1 s\n", ticks);
  assert(ticks <= 10);
  // Guest unmap and remap.
  wl_surface_attach(a.surface, nullptr, 0, 0);
  wl_surface_commit(a.surface);
  expect_idle(c, '0');
  wl_surface_attach(a.surface, content, 0, 0);
  wl_surface_commit(a.surface);
  expect_idle(c, '1');
  if (getenv("ANVIL_TEST_X11")) {
    auto *d = XOpenDisplay(nullptr);
    assert(d);
    int event, error;
    assert(XScreenSaverQueryExtension(d, &event, &error));
    auto w = find_window(d, "waydroid.anvildroid.test.idle");
    // A 1 s private Xvfb saver must stay off while suspended and reset.
    XSetScreenSaver(d, 1, 0, DontPreferBlanking, AllowExposures);
    XSync(d, False);
    usleep(1600000);
    roundtrip(c);
    // The check shortens the 30 s reset period; desktop idle monitors read
    // this server idle time rather than the suspended saver timer.
    unsigned long idle = 0;
    assert(saver_state(d, &idle) == ScreenSaverOff);
    if (idle >= 1000)
      fprintf(stderr, "server idle time was not reset: %lu ms\n", idle);
    assert(idle < 1000);
    // Model WM minimization: an unmapped client window, then the EWMH state.
    XUnmapWindow(d, w);
    XSync(d, False);
    expect_idle(c, '0');
    usleep(1600000);
    assert(saver_state(d) == ScreenSaverOn);
    XMapWindow(d, w);
    XSync(d, False);
    expect_idle(c, '1');
    usleep(50000);
    assert(saver_state(d) == ScreenSaverOff);
    auto hidden = XInternAtom(d, "_NET_WM_STATE_HIDDEN", False);
    auto state_atom = XInternAtom(d, "_NET_WM_STATE", False);
    XChangeProperty(d, w, state_atom, XA_ATOM, 32, PropModeReplace,
                    reinterpret_cast<unsigned char *>(&hidden), 1);
    XSync(d, False);
    expect_idle(c, '0');
    XDeleteProperty(d, w, state_atom);
    XSync(d, False);
    expect_idle(c, '1');
    XSetScreenSaver(d, -1, -1, DefaultBlanking, DefaultExposures);
    XResetScreenSaver(d);
    XCloseDisplay(d);
  }
  // A visible subsurface keeps inhibition after the first inhibitor is gone.
  auto *child = wl_compositor_create_surface(c.compositor);
  auto *sub = wl_subcompositor_get_subsurface(c.sub, child, a.surface);
  auto *child_content = buffer(c, 20, 20, 0xffffffff);
  wl_surface_attach(child, child_content, 0, 0);
  wl_surface_commit(child);
  wl_surface_commit(a.surface);
  auto *second = zwp_idle_inhibit_manager_v1_create_inhibitor(c.idle, child);
  roundtrip(c);
  zwp_idle_inhibitor_v1_destroy(first);
  roundtrip(c);
  roundtrip(c);
  expect_idle(c, '1');
  zwp_idle_inhibitor_v1_destroy(second);
  expect_idle(c, '0');
  // Destroying the surface leaves an inert inhibitor that is safe to destroy.
  auto *third = zwp_idle_inhibit_manager_v1_create_inhibitor(c.idle, a.surface);
  expect_idle(c, '1');
  wl_subsurface_destroy(sub);
  wl_surface_destroy(child);
  xdg_toplevel_destroy(a.top);
  xdg_surface_destroy(a.xdg);
  wl_surface_destroy(a.surface);
  expect_idle(c, '0');
  zwp_idle_inhibitor_v1_destroy(third);
  roundtrip(c);
  // Leave one active inhibitor for client teardown to release.
  auto b = app(c, "waydroid.anvildroid.test.idle-teardown");
  roundtrip(c);
  roundtrip(c);
  wl_surface_attach(b.surface, content, 0, 0);
  wl_surface_commit(b.surface);
  auto *last = zwp_idle_inhibit_manager_v1_create_inhibitor(c.idle, b.surface);
  expect_idle(c, '1');
  wl_buffer_destroy(child_content);
  // Free client proxies without requests; disconnect performs the teardown.
  for (auto *proxy : {(wl_proxy *)last, (wl_proxy *)b.top, (wl_proxy *)b.xdg,
                      (wl_proxy *)b.surface, (wl_proxy *)content})
    wl_proxy_destroy(proxy);
  globals_destroy(c, r);
  roundtrip(c);
  puts("PASS idle inhibitor follows visible toplevels and keeps the loop idle");
}
static void chrome_check(Client &c) {
  auto *pointer = wl_seat_get_pointer(c.seat);
  wl_pointer_add_listener(pointer, &pointer_events, nullptr);
  auto a = app(c, "waydroid.anvildroid.test.chrome");
  roundtrip(c);
  roundtrip(c);
  auto *content = buffer(c, 320, 200, 0xff204080);
  auto *normal = buffer(c, 322, 33, 0, csd_style_state(0, 0, 0));
  auto *maximized = buffer(c, 322, 33, 0, csd_style_state(1, 0, 0));
  wl_buffer *buffers[] = {normal, buffer(c, 5, 238, 0), buffer(c, 5, 238, 0),
                          buffer(c, 330, 5, 0)};
  wl_surface *pieces[4];
  wl_subsurface *subs[4];
  int xs[] = {-1, -5, 320, -5}, ys[] = {-33, -33, -33, 200};
  for (int i = 0; i < 4; ++i) {
    pieces[i] = wl_compositor_create_surface(c.compositor);
    subs[i] = wl_subcompositor_get_subsurface(c.sub, pieces[i], a.surface);
    wl_subsurface_set_position(subs[i], xs[i], ys[i]);
    wl_surface_attach(pieces[i], buffers[i], 0, 0);
    wl_surface_commit(pieces[i]);
  }
  xdg_surface_set_window_geometry(a.xdg, -1, -33, 322, 234);
  wl_surface_attach(a.surface, content, 0, 0);
  wl_surface_commit(a.surface);
  auto settle = [&]() {
    roundtrip(c);
    usleep(30000);
    roundtrip(c);
  };
  settle();
  if (getenv("ANVIL_TEST_X11")) {
    auto *d = XOpenDisplay(nullptr);
    assert(d);
    auto w = find_window(d, "waydroid.anvildroid.test.chrome");
    auto dimensions = [&](int width, int height) {
      XWindowAttributes attr{};
      assert(XGetWindowAttributes(d, w, &attr));
      if (attr.width != width || attr.height != height || attr.depth != 32)
        fprintf(stderr,
                "chrome native=%dx%d depth=%d expected=%dx%d depth=32\n",
                attr.width, attr.height, attr.depth, width, height);
      assert(attr.width == width && attr.height == height && attr.depth == 32);
    };
    auto argb = [&](int x, int y, uint32_t expected) {
      auto *image = XGetImage(d, w, x, y, 1, 1, AllPlanes, ZPixmap);
      assert(image);
      auto actual = XGetPixel(image, 0, 0);
      if (actual != expected)
        fprintf(stderr, "chrome pixel %d,%d=%lx expected=%x\n", x, y, actual,
                expected);
      assert(actual == expected);
      XDestroyImage(image);
    };
    auto extents = [&](unsigned long left, unsigned long right,
                       unsigned long top, unsigned long bottom) {
      Atom type;
      int format;
      unsigned long count, remaining;
      unsigned char *bytes = nullptr;
      assert(XGetWindowProperty(d, w,
                                XInternAtom(d, "_GTK_FRAME_EXTENTS", False), 0,
                                4, False, XA_CARDINAL, &type, &format, &count,
                                &remaining, &bytes) == Success);
      assert(bytes && type == XA_CARDINAL && format == 32 && count == 4);
      auto *values = reinterpret_cast<unsigned long *>(bytes);
      assert(values[0] == left && values[1] == right && values[2] == top &&
             values[3] == bottom);
      XFree(bytes);
    };
    dimensions(330, 238);
    extents(4, 4, 0, 4);
    argb(0, 100, 0);
    argb(329, 100, 0);
    argb(100, 237, 0);
    argb(5, 0, 0); // The guest's rounded header corner.
    argb(100, 10, 0xff212121);
    argb(5, 33, 0xff204080);
    bool antialias = false;
    for (int y = 0; y < CSD_RADIUS && !antialias; ++y)
      for (int x = CSD_BORDER; x < CSD_RADIUS; ++x) {
        auto color = csd_header_pixel(x, y, 322, 33, 0);
        if ((color >> 24) > 0 && (color >> 24) < 255) {
          argb(x + 4, y, color);
          antialias = true;
          break;
        }
      }
    assert(antialias);
    assert(sizes[a.top].width == 322 && sizes[a.top].height == 234);
    auto root = DefaultRootWindow(d);
    XSelectInput(d, root, SubstructureNotifyMask);
    auto moveresize = XInternAtom(d, "_NET_WM_MOVERESIZE", False);
    auto edge = [&](int x, int y, int piece, int px, int py, uint32_t direction,
                    int wm_direction) {
      XEvent press{};
      press.xbutton.type = ButtonPress;
      press.xbutton.window = w;
      press.xbutton.x = x;
      press.xbutton.y = y;
      press.xbutton.button = 1;
      XSendEvent(d, w, False, ButtonPressMask, &press);
      XSync(d, False);
      settle();
      assert(pointer_surface == pieces[piece] && pointer_x == px &&
             pointer_y == py);
      xdg_toplevel_resize(a.top, c.seat, button_serial, direction);
      settle();
      XSync(d, False);
      unsigned requests = 0;
      while (XPending(d)) {
        XEvent event;
        XNextEvent(d, &event);
        if (event.type == ClientMessage &&
            event.xclient.message_type == moveresize) {
          assert(event.xclient.window == w &&
                 event.xclient.data.l[2] == wm_direction);
          ++requests;
        }
      }
      assert(requests == 1 && pointer_surface == nullptr);
    };
    for (int i = 0; i < 5; ++i) {
      edge(i, 100, 1, i, 100, XDG_TOPLEVEL_RESIZE_EDGE_LEFT, 7);
      edge(325 + i, 100, 2, i, 100, XDG_TOPLEVEL_RESIZE_EDGE_RIGHT, 3);
      edge(100, 233 + i, 3, 100, i, XDG_TOPLEVEL_RESIZE_EDGE_BOTTOM, 5);
      edge(100, i, 0, 96, i, XDG_TOPLEVEL_RESIZE_EDGE_TOP, 1);
    }
    edge(0, 0, 1, 0, 0, XDG_TOPLEVEL_RESIZE_EDGE_TOP_LEFT, 0);
    edge(329, 0, 2, 4, 0, XDG_TOPLEVEL_RESIZE_EDGE_TOP_RIGHT, 2);
    edge(0, 237, 3, 0, 4, XDG_TOPLEVEL_RESIZE_EDGE_BOTTOM_LEFT, 6);
    edge(329, 237, 3, 329, 4, XDG_TOPLEVEL_RESIZE_EDGE_BOTTOM_RIGHT, 4);
    // WM sizes exclude the invisible input margin before reaching the bridge.
    XResizeWindow(d, w, 370, 258);
    XSync(d, False);
    settle();
    assert(sizes[a.top].width == 362 && sizes[a.top].height == 254);
    wl_surface_commit(a.surface); // The bridge reaccepts its old aligned frame.
    settle();
    dimensions(330, 238);
    // The bridge's minimum describes xdg geometry; the WM must see the
    // drawable minimum including the transparent resize margins.
    xdg_toplevel_set_min_size(a.top, 162, 98);
    wl_surface_commit(a.surface);
    settle();
    XSizeHints hints{};
    long supplied = 0;
    assert(XGetWMNormalHints(d, w, &hints, &supplied));
    if (!(hints.flags & PMinSize) || hints.min_width != 170 ||
        hints.min_height != 102 || (hints.flags & PMaxSize))
      fprintf(stderr, "chrome normal hints flags=%lx min=%dx%d\n",
              hints.flags, hints.min_width, hints.min_height);
    assert((hints.flags & PMinSize) && hints.min_width == 170 &&
           hints.min_height == 102 && !(hints.flags & PMaxSize));
    // Muffin computes an in-grab ConfigureRequest from its frame rect, which
    // already excludes _GTK_FRAME_EXTENTS, and subtracts them again. Each
    // accepted-frame request during a move/resize grab therefore shrank the
    // window by the 8x4 input margin until it reached 1px. The WM owns the
    // size until its grab ends; only then may the accepted frame be applied.
    XEvent crossing{};
    crossing.xcrossing.type = LeaveNotify;
    crossing.xcrossing.window = w;
    crossing.xcrossing.mode = NotifyGrab;
    crossing.xcrossing.x = 100;
    crossing.xcrossing.y = 100;
    XSendEvent(d, w, False, LeaveWindowMask, &crossing);
    XSync(d, False);
    settle();
    XResizeWindow(d, w, 370, 258); // The WM's interactive size.
    XSync(d, False);
    settle();
    assert(sizes[a.top].width == 362 && sizes[a.top].height == 254);
    wl_surface_commit(a.surface); // The bridge reaccepts its old frame again.
    settle();
    dimensions(370, 258); // No ConfigureRequest while the WM grab is active.
    crossing.xcrossing.type = EnterNotify;
    crossing.xcrossing.mode = NotifyUngrab;
    XSendEvent(d, w, False, EnterWindowMask, &crossing);
    XSync(d, False);
    settle();
    wl_surface_commit(a.surface);
    settle();
    dimensions(330, 238);
    assert(sizes[a.top].width == 322 && sizes[a.top].height == 234);
    crossing.xcrossing.type = LeaveNotify;
    crossing.xcrossing.mode = NotifyNormal;
    XSendEvent(d, w, False, LeaveWindowMask, &crossing);
    XSync(d, False);
    settle();
    auto state_atom = XInternAtom(d, "_NET_WM_STATE", False);
    Atom states[] = {XInternAtom(d, "_NET_WM_STATE_MAXIMIZED_VERT", False),
                     XInternAtom(d, "_NET_WM_STATE_MAXIMIZED_HORZ", False)};
    XChangeProperty(d, w, state_atom, XA_ATOM, 32, PropModeReplace,
                    reinterpret_cast<unsigned char *>(states), 2);
    XResizeWindow(d, w, 322, 234);
    XSync(d, False);
    settle();
    assert(sizes[a.top].maximized && sizes[a.top].width == 322 &&
           sizes[a.top].height == 234);
    extents(4, 4, 0, 4); // State/configure alone must not change guest mapping.
    wl_surface_attach(pieces[0], maximized, 0, 0);
    wl_surface_commit(pieces[0]);
    wl_surface_commit(a.surface);
    settle();
    dimensions(322, 234);
    extents(0, 0, 0, 0);
    argb(1, 0, csd_header_pixel(1, 0, 322, 33, 1));
    Atom full = XInternAtom(d, "_NET_WM_STATE_FULLSCREEN", False);
    XChangeProperty(d, w, state_atom, XA_ATOM, 32, PropModeReplace,
                    reinterpret_cast<unsigned char *>(&full), 1);
    XResizeWindow(d, w, 320, 200);
    XSync(d, False);
    settle();
    for (auto *piece : pieces) {
      wl_surface_attach(piece, nullptr, 0, 0);
      wl_surface_commit(piece);
    }
    xdg_surface_set_window_geometry(a.xdg, 0, 0, 320, 200);
    wl_surface_commit(a.surface);
    settle();
    dimensions(320, 200);
    extents(0, 0, 0, 0);
    argb(0, 0, 0xff204080);
    XDeleteProperty(d, w, state_atom);
    XResizeWindow(d, w, 330, 238);
    XSync(d, False);
    settle();
    assert(!sizes[a.top].maximized && sizes[a.top].width == 322 &&
           sizes[a.top].height == 234);
    for (int i = 0; i < 4; ++i) {
      wl_surface_attach(pieces[i], buffers[i], 0, 0);
      wl_surface_commit(pieces[i]);
    }
    xdg_surface_set_window_geometry(a.xdg, -1, -33, 322, 234);
    wl_surface_commit(a.surface);
    settle();
    dimensions(330, 238);
    extents(4, 4, 0, 4);
    argb(5, 0, 0);
    argb(5, 33, 0xff204080);
    XCloseDisplay(d);
    puts("PASS guest ARGB/rounded CSD, all 5 resize pixels and corners, padded "
         "WM sizes, maximize/fullscreen/restore");
  }
  for (int i = 0; i < 4; ++i) {
    wl_subsurface_destroy(subs[i]);
    wl_surface_destroy(pieces[i]);
    wl_buffer_destroy(buffers[i]);
  }
  wl_buffer_destroy(maximized);
  wl_buffer_destroy(content);
  xdg_toplevel_destroy(a.top);
  xdg_surface_destroy(a.xdg);
  wl_surface_destroy(a.surface);
  wl_pointer_release(pointer);
  roundtrip(c);
}
int main(int argc, char **argv) {
  assert(argc == 2 || argc == 3);
  Client c;
  c.display = wl_display_connect(argv[1]);
  assert(c.display);
  auto *r = wl_display_get_registry(c.display);
  static const wl_registry_listener registry_ops = {
      registry, [](void *, wl_registry *, uint32_t) {}};
  wl_registry_add_listener(r, &registry_ops, &c);
  roundtrip(c);
  assert(c.compositor && c.sub && c.shm && c.wm && c.viewporter && c.seat);
  roundtrip(c);
  assert(c.width > 0 && c.height > 0);
  // Headless and Xvfb (zero RandR timing) keep the 60 Hz fallback.
  assert(c.refresh == 60000);
  if (argc == 3 && !strcmp(argv[2], "dmabuf-lifetime")) {
    dmabuf_lifetime_check(c);
    globals_destroy(c, r);
    roundtrip(c);
    wl_display_disconnect(c.display);
    return 0;
  }
  if (argc == 3 && !strcmp(argv[2], "dmabuf-formats")) {
    dmabuf_formats_check(c);
    globals_destroy(c, r);
    roundtrip(c);
    wl_display_disconnect(c.display);
    return 0;
  }
  if (argc == 3 && !strcmp(argv[2], "calibration")) {
    auto a = app(c, "waydroid.calibration", true);
    roundtrip(c);
    roundtrip(c);
    auto size = sizes.at(a.top);
    assert(size.maximized && size.width == c.width && size.height == c.height);
    xdg_toplevel_destroy(a.top);
    xdg_surface_destroy(a.xdg);
    wl_surface_destroy(a.surface);
    globals_destroy(c, r);
    roundtrip(c);
    wl_display_disconnect(c.display);
    puts("PASS first maximized configure carries output size for Android "
         "canvas calibration");
    return 0;
  }
  if (argc == 3 && !strcmp(argv[2], "idle-inhibit")) {
    idle_check(c, r);
    wl_display_disconnect(c.display);
    return 0;
  }
  if (argc == 3 && !strcmp(argv[2], "chrome")) {
    chrome_check(c);
    globals_destroy(c, r);
    roundtrip(c);
    wl_display_disconnect(c.display);
    return 0;
  }
  if (argc == 3) {
    auto *first = wl_compositor_create_surface(c.compositor);
    wl_surface *second = nullptr;
    wp_viewport *v1 = nullptr, *v2 = nullptr;
    wl_subsurface *s1 = nullptr, *s2 = nullptr;
    zwp_linux_buffer_params_v1 *params = nullptr;
    wl_buffer *dmabuf = nullptr;
    if (!strcmp(argv[2], "scale"))
      wl_surface_set_buffer_scale(first, 0);
    else if (!strcmp(argv[2], "dmabuf-planes")) {
      // Structural misuse stays fatal: planes 0 and 2 without plane 1.
      int fd = memfd_create("test-dmabuf-planes", MFD_CLOEXEC);
      assert(fd >= 0 && !ftruncate(fd, 4096));
      params = zwp_linux_dmabuf_v1_create_params(c.dmabuf);
      zwp_linux_buffer_params_v1_add(params, fd, 0, 0, 64, 0, 0);
      zwp_linux_buffer_params_v1_add(params, fd, 2, 0, 32, 0, 0);
      close(fd);
      dmabuf = zwp_linux_buffer_params_v1_create_immed(params, 32, 32,
                                                       code("YV12"), 0);
    } else if (!strcmp(argv[2], "dmabuf-reuse")) {
      int fd = memfd_create("test-dmabuf-reuse", MFD_CLOEXEC);
      assert(fd >= 0 && !ftruncate(fd, 4096));
      params = zwp_linux_dmabuf_v1_create_params(c.dmabuf);
      zwp_linux_buffer_params_v1_add(params, fd, 0, 0, 64, 0, 0);
      dmabuf = zwp_linux_buffer_params_v1_create_immed(params, 16, 16,
                                                       code("XR24"), 0);
      zwp_linux_buffer_params_v1_add(params, fd, 0, 0, 64, 0, 0);
      close(fd);
    } else if (!strcmp(argv[2], "viewport")) {
      v1 = wp_viewporter_get_viewport(c.viewporter, first);
      v2 = wp_viewporter_get_viewport(c.viewporter, first);
    } else {
      assert(!strcmp(argv[2], "cycle"));
      second = wl_compositor_create_surface(c.compositor);
      s1 = wl_subcompositor_get_subsurface(c.sub, first, second);
      s2 = wl_subcompositor_get_subsurface(c.sub, second, first);
    }
    assert(wl_display_roundtrip(c.display) == -1);
    if (dmabuf)
      wl_buffer_destroy(dmabuf);
    if (params)
      zwp_linux_buffer_params_v1_destroy(params);
    if (v1)
      wp_viewport_destroy(v1);
    if (v2)
      wp_viewport_destroy(v2);
    if (s1)
      wl_subsurface_destroy(s1);
    if (s2)
      wl_subsurface_destroy(s2);
    if (second)
      wl_surface_destroy(second);
    wl_surface_destroy(first);
    globals_destroy(c, r);
    wl_display_disconnect(c.display);
    puts("PASS rejected malformed client without affecting endpoint");
    return 0;
  }
  auto *pointer = wl_seat_get_pointer(c.seat);
  wl_pointer_add_listener(pointer, &pointer_events, nullptr);
  auto *keyboard = wl_seat_get_keyboard(c.seat);
  wl_keyboard_add_listener(keyboard, &keyboard_events, nullptr);
  auto *text_input =
      zwp_text_input_manager_v3_get_text_input(c.text_manager, c.seat);
  zwp_text_input_v3_add_listener(text_input, &text_events, nullptr);
  roundtrip(c); // A running endpoint alone must create no windows.
  auto a = app(c, "waydroid.anvildroid.test.com.example.one"),
       b = app(c, "waydroid.anvildroid.test.com.example.two");
  roundtrip(c);
  roundtrip(c);
  assert(configures == 2);
  auto *ab = buffer(c, 640, 480, 0xff204080),
       *bb = buffer(c, 400, 300, 0xff33aa66),
       *hb = buffer(c, 642, 33, 0xffc08040);
  auto *header = wl_compositor_create_surface(c.compositor);
  auto *sub = wl_subcompositor_get_subsurface(c.sub, header, a.surface);
  wl_subsurface_set_position(sub, -1, -33);
  wl_surface_attach(header, hb, 0, 0);
  wl_surface_commit(header);
  xdg_surface_set_window_geometry(a.xdg, -1, -33, 642, 514);
  wl_surface_attach(a.surface, ab, 0, 0);
  auto *frame = wl_surface_frame(a.surface);
  wl_callback_add_listener(frame, &framed, nullptr);
  wl_surface_commit(a.surface);
  xdg_surface_set_window_geometry(b.xdg, 0, 0, 400, 300);
  wl_surface_attach(b.surface, bb, 0, 0);
  wl_surface_commit(b.surface);
  roundtrip(c);
  roundtrip(c);
  assert(releases == 3 && frames == 1);
  if (getenv("ANVIL_TEST_X11")) {
    auto *d = XOpenDisplay(nullptr);
    assert(d);
    auto aw = find_window(d, "waydroid.anvildroid.test.com.example.one"),
         bw = find_window(d, "waydroid.anvildroid.test.com.example.two");
    XMoveWindow(d, aw, 20, 20);
    XMoveWindow(d, bw, 800, 20);
    XSync(d, False);
    usleep(100000);
    roundtrip(c);
    pixel(d, aw, 20, 10, 0xc08040);
    pixel(d, aw, 20, 50, 0x204080);
    pixel(d, bw, 20, 20, 0x33aa66);
    // The bridge can accept the same aligned width for multiple WM sizes.
    // Reacknowledging a configure need not change its window geometry. The
    // native window must still adopt that committed size, including the CSD.
    auto *av = wp_viewporter_get_viewport(c.viewporter, a.surface);
    auto *hv = wp_viewporter_get_viewport(c.viewporter, header);
    wp_viewport_set_destination(av, 512, 480);
    wp_viewport_set_destination(hv, 514, 33);
    wl_surface_commit(header);
    xdg_surface_set_window_geometry(a.xdg, -1, -33, 514, 514);
    wl_surface_commit(a.surface);
    roundtrip(c);
    usleep(50000);
    roundtrip(c);
    XResizeWindow(d, aw, 553, 537);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(sizes[a.top].width == 553);
    // No set_window_geometry: the shared bridge's accepted frame is unchanged.
    wl_surface_commit(a.surface);
    roundtrip(c);
    usleep(50000);
    roundtrip(c);
    XWindowAttributes aligned{};
    assert(XGetWindowAttributes(d, aw, &aligned));
    if (aligned.width != 514)
      fprintf(stderr, "accepted frame=514 native width=%d (right gutter)\n",
              aligned.width);
    assert(aligned.width == 514 && aligned.height == 514);
    pixel(d, aw, 512, 10, 0xc08040);
    pixel(d, aw, 512, 50, 0x204080);
    pixel(d, aw, 20, 512, 0x204080);
    // Model a WM ignoring ConfigureRequest until its interactive grab ends.
    // A sent resize request must not replace the observed native dimensions.
    auto resize_root = DefaultRootWindow(d);
    XSelectInput(d, resize_root,
                 SubstructureRedirectMask | SubstructureNotifyMask);
    XResizeWindow(d, aw, 553, 514);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    wl_surface_commit(a.surface);
    roundtrip(c);
    usleep(50000);
    assert(XGetWindowAttributes(d, aw, &aligned) && aligned.width == 553);
    XSetInputFocus(d, bw, RevertToParent, CurrentTime);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    XSetInputFocus(d, aw, RevertToParent, CurrentTime);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(sizes[a.top].width == 553); // Not the ignored request's width 514.
    XSelectInput(d, resize_root, SubstructureNotifyMask);
    XEvent ungrab{};
    ungrab.xcrossing.type = EnterNotify;
    ungrab.xcrossing.window = aw;
    ungrab.xcrossing.mode = NotifyUngrab;
    ungrab.xcrossing.x = 20;
    ungrab.xcrossing.y = 10;
    unsigned before_ungrab = configures;
    XSendEvent(d, aw, False, EnterWindowMask, &ungrab);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(configures > before_ungrab && sizes[a.top].width == 553);
    wl_surface_commit(a.surface);
    roundtrip(c);
    usleep(50000);
    roundtrip(c);
    assert(XGetWindowAttributes(d, aw, &aligned) && aligned.width == 514);
    pixel(d, aw, 512, 10, 0xc08040);
    pixel(d, aw, 512, 50, 0x204080);
    puts("PASS accepted CSD/content edges after unchanged geometry and a "
         "WM-deferred resize");
    wp_viewport_set_destination(av, -1, -1);
    wp_viewport_set_destination(hv, -1, -1);
    wl_surface_commit(header);
    xdg_surface_set_window_geometry(a.xdg, -1, -33, 642, 514);
    wl_surface_commit(a.surface);
    roundtrip(c);
    usleep(50000);
    roundtrip(c);
    wp_viewport_destroy(hv);
    wp_viewport_destroy(av);
    // Model WM maximize/restore, including removal of an empty state property.
    Atom states[] = {XInternAtom(d, "_NET_WM_STATE_MAXIMIZED_VERT", False),
                     XInternAtom(d, "_NET_WM_STATE_MAXIMIZED_HORZ", False)};
    auto state_atom = XInternAtom(d, "_NET_WM_STATE", False);
    XChangeProperty(d, aw, state_atom, XA_ATOM, 32, PropModeReplace,
                    reinterpret_cast<unsigned char *>(states), 2);
    XResizeWindow(d, aw, 800, 700);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(sizes[a.top].maximized && sizes[a.top].width == 800 &&
           sizes[a.top].height == 700);
    XDeleteProperty(d, aw, state_atom);
    XResizeWindow(d, aw, 642, 514);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(!sizes[a.top].maximized && sizes[a.top].width == 642 &&
           sizes[a.top].height == 514);
    XSetInputFocus(d, aw, RevertToParent, CurrentTime);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(keyboard_surface == a.surface);
    XkbLockModifiers(d, XkbUseCoreKbd, LockMask, LockMask);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(locked_mods != 0);
    XkbLockModifiers(d, XkbUseCoreKbd, LockMask, 0);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(locked_mods == 0);
    XEvent motion{};
    motion.xmotion.type = MotionNotify;
    motion.xmotion.window = aw;
    motion.xmotion.x = 20;
    motion.xmotion.y = 10;
    XSendEvent(d, aw, False, PointerMotionMask, &motion);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(pointer_surface == header && pointer_x == 20 && pointer_y == 10);
    // An idle guest sends no requests to wake the server. Native input alone
    // must wake the endpoint and flush its reply (no roundtrip on this path).
    for (int n = 0; n < 8; ++n) {
      while (wl_display_prepare_read(c.display) != 0)
        assert(wl_display_dispatch_pending(c.display) >= 0);
      motion.xmotion.x = 21 + n;
      XSendEvent(d, aw, False, PointerMotionMask, &motion);
      XSync(d, False);
      pollfd input{wl_display_get_fd(c.display), POLLIN, 0};
      int ready = poll(&input, 1, 1000);
      if (ready > 0 && (input.revents & POLLIN)) {
        assert(wl_display_read_events(c.display) >= 0);
        assert(wl_display_dispatch_pending(c.display) >= 0);
      } else {
        wl_display_cancel_read(c.display);
        assert(false && "Native input did not wake idle protocol endpoint");
      }
      assert(pointer_surface == header && pointer_x == 21 + n &&
             pointer_y == 10);
    }
    XEvent button{};
    button.xbutton.type = ButtonPress;
    button.xbutton.window = aw;
    button.xbutton.x = 20;
    button.xbutton.y = 10;
    button.xbutton.button = 1;
    XSendEvent(d, aw, False, ButtonPressMask, &button);
    button.xbutton.type = ButtonRelease;
    XSendEvent(d, aw, False, ButtonReleaseMask, &button);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(buttons == 2 && pointer_surface == header);
    // Act as a WM receiving move/resize requests, deliberately consuming the
    // release. The next app press must never inherit the previous CSD grab.
    auto root = DefaultRootWindow(d);
    XSelectInput(d, root, SubstructureNotifyMask);
    auto moveresize = XInternAtom(d, "_NET_WM_MOVERESIZE", False);
    auto wm_requests = [&]() {
      std::vector<Window> windows;
      while (XPending(d)) {
        XEvent event;
        XNextEvent(d, &event);
        if (event.type == ClientMessage &&
            event.xclient.message_type == moveresize)
          windows.push_back(event.xclient.window);
      }
      return windows;
    };
    auto press = [&](Window window, int x, int y) {
      button.xbutton.type = ButtonPress;
      button.xbutton.window = window;
      button.xbutton.x = x;
      button.xbutton.y = y;
      button.xbutton.state = 0;
      XSendEvent(d, window, False, ButtonPressMask, &button);
      XSync(d, False);
      usleep(30000);
      roundtrip(c);
    };
    press(aw, 20, 10);
    motion.xmotion.window = bw;
    motion.xmotion.x = 30;
    motion.xmotion.y = 10;
    motion.xmotion.state = Button1Mask;
    XSendEvent(d, bw, False, PointerMotionMask, &motion);
    XSync(d, False);
    usleep(30000);
    roundtrip(c);
    assert(pointer_surface == header && pointer_x == 810 && pointer_y == 10);
    button.xbutton.type = ButtonRelease;
    button.xbutton.window = bw;
    button.xbutton.state = Button1Mask;
    XSendEvent(d, bw, False, ButtonReleaseMask, &button);
    XSync(d, False);
    usleep(30000);
    roundtrip(c);
    for (bool resize : {false, true}) {
      press(aw, 20, 10);
      assert(pointer_surface == header);
      auto serial = button_serial;
      // Cross-window and stale serial requests must not hand a window to WM.
      xdg_toplevel_move(b.top, c.seat, serial);
      xdg_toplevel_move(a.top, c.seat, serial - 1);
      roundtrip(c);
      XSync(d, False);
      assert(wm_requests().empty());
      if (resize)
        xdg_toplevel_resize(a.top, c.seat, serial,
                            XDG_TOPLEVEL_RESIZE_EDGE_RIGHT);
      else
        xdg_toplevel_move(a.top, c.seat, serial);
      roundtrip(c);
      roundtrip(c);
      XSync(d, False);
      assert(wm_requests() == std::vector<Window>{aw});
      assert(pointer_surface == nullptr);
      press(bw, 30, 25); // No release from aw: the WM swallowed it.
      assert(pointer_surface == b.surface && pointer_x == 30 &&
             pointer_y == 25);
      xdg_toplevel_move(b.top, c.seat, button_serial);
      roundtrip(c);
      XSync(d, False);
      assert(wm_requests() == std::vector<Window>{bw});
    }
    // Also recover from an unrelated native grab swallowing a release.
    press(aw, 20, 10);
    press(bw, 35, 28);
    assert(pointer_surface == b.surface && pointer_x == 35 && pointer_y == 28);
    button.xbutton.type = ButtonRelease;
    button.xbutton.state = Button1Mask;
    XSendEvent(d, bw, False, ButtonReleaseMask, &button);
    XSetInputFocus(d, aw, RevertToParent, CurrentTime);
    XSync(d, False);
    usleep(30000);
    roundtrip(c);
    // The grabbed child may lose its parent before its next input event.
    press(aw, 20, 10);
    wl_subsurface_destroy(sub);
    roundtrip(c);
    motion.xmotion.window = aw;
    motion.xmotion.x = 20;
    motion.xmotion.y = 10;
    XSendEvent(d, aw, False, PointerMotionMask, &motion);
    XSync(d, False);
    usleep(30000);
    roundtrip(c);
    assert(pointer_surface == nullptr);
    sub = nullptr;
    XEvent key{};
    key.xkey.type = KeyPress;
    key.xkey.window = aw;
    key.xkey.display = d;
    key.xkey.keycode = 38;
    XSendEvent(d, aw, False, KeyPressMask, &key);
    key.xkey.type = KeyRelease;
    XSendEvent(d, aw, False, KeyReleaseMask, &key);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(keys == 2);
    // A real XIM UTF-8 commit must not also generate Android raw key events.
    zwp_text_input_v3_enable(text_input);
    zwp_text_input_v3_commit(text_input);
    roundtrip(c);
    const KeyCode code = 248;
    int per_key;
    auto *previous = XGetKeyboardMapping(d, code, 1, &per_key);
    KeySym symbol = 0x00e9; // e-acute; local XIM emits UTF-8, not Latin-1.
    XChangeKeyboardMapping(d, code, 1, &symbol, 1);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    key.xkey.type = KeyPress;
    key.xkey.keycode = code;
    XSendEvent(d, aw, False, KeyPressMask, &key);
    key.xkey.type = KeyRelease;
    XSendEvent(d, aw, False, KeyReleaseMask, &key);
    XSync(d, False);
    usleep(50000);
    roundtrip(c);
    assert(committed_text == u8"é" && keys == 2);
    XChangeKeyboardMapping(d, code, per_key, previous, 1);
    XFree(previous);
    zwp_text_input_v3_disable(text_input);
    zwp_text_input_v3_commit(text_input);
    roundtrip(c);
    // Opening the keymap editor disables text input. A running host XIM must
    // not swallow its physical W/A/S/D, digits, arrows or function keys.
    const auto text_before = committed_text;
    for (unsigned code : {25u, 38u, 39u, 40u, 10u, 111u, 67u}) {
      const auto before = physical_keys.size();
      key.xkey.keycode = code;
      key.xkey.type = KeyPress;
      XSendEvent(d, aw, False, KeyPressMask, &key);
      key.xkey.type = KeyRelease;
      XSendEvent(d, aw, False, KeyReleaseMask, &key);
      XSync(d, False);
      usleep(50000);
      roundtrip(c);
      assert(physical_keys.size() == before + 2);
      assert(physical_keys[before] == std::make_pair(code - 8, 1u));
      assert(physical_keys[before + 1] == std::make_pair(code - 8, 0u));
      assert(committed_text == text_before);
    }
    XEvent event{};
    event.xclient.type = ClientMessage;
    event.xclient.window = aw;
    event.xclient.message_type = XInternAtom(d, "WM_PROTOCOLS", False);
    event.xclient.format = 32;
    event.xclient.data.l[0] = XInternAtom(d, "WM_DELETE_WINDOW", False);
    XSendEvent(d, aw, False, NoEventMask, &event);
    XFlush(d);
    usleep(50000);
    roundtrip(c);
    assert(closed == 1);
    XCloseDisplay(d);
    puts("PASS native X11: identities, content/CSD pixels, maximize/restore, "
         "focus, modifiers, CSD hit-test, WM grab handoff, keyboard and close "
         "routed to the correct app");
  }
  wl_surface_attach(a.surface, nullptr, 0, 0);
  wl_surface_commit(a.surface);
  roundtrip(c);
  // Closing/unmapping one app must not end the endpoint or the other app.
  wl_surface_attach(b.surface, bb, 0, 0);
  wl_surface_commit(b.surface);
  roundtrip(c);
  assert(releases == 4);
  if (sub)
    wl_subsurface_destroy(sub);
  wl_surface_destroy(header);
  xdg_toplevel_destroy(a.top);
  xdg_surface_destroy(a.xdg);
  wl_surface_destroy(a.surface);
  roundtrip(c);
  wl_surface_attach(b.surface, bb, 0, 0);
  wl_surface_commit(b.surface);
  roundtrip(c);
  assert(releases == 5);
  // Bridge commits its cursor before assigning the role and reuses it after
  // hiding it. Both are normal cursor protocol, not a new surface role.
  auto *cursor = wl_compositor_create_surface(c.compositor);
  auto *cb = buffer(c, 7, 26, 0xffffffff);
  wl_surface_attach(cursor, cb, 0, 0);
  wl_surface_commit(cursor);
  roundtrip(c);
  wl_pointer_set_cursor(pointer, 1, cursor, 3, 2);
  wl_pointer_set_cursor(pointer, 1, nullptr, 0, 0);
  wl_pointer_set_cursor(pointer, 1, cursor, 3, 2);
  roundtrip(c);
  assert(releases == 6);
  wl_surface_destroy(cursor);
  wl_buffer_destroy(cb);
  // Unusual but legal resource destruction order must not leave a dangling
  // root.
  wl_surface_destroy(b.surface);
  roundtrip(c);
  xdg_toplevel_destroy(b.top);
  xdg_surface_destroy(b.xdg);
  roundtrip(c);
  wl_buffer_destroy(ab);
  wl_buffer_destroy(bb);
  wl_buffer_destroy(hb);
  wl_pointer_release(pointer);
  wl_keyboard_release(keyboard);
  zwp_text_input_v3_destroy(text_input);
  globals_destroy(c, r);
  roundtrip(c);
  wl_display_disconnect(c.display);
  puts("PASS real protocol: two apps, bridge CSD child, configure ACK, "
       "frame/release, independent close, teardown");
  return 0;
}
