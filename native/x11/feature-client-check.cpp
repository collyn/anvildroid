/* SPDX-License-Identifier: MIT — isolated native popup, pointer and clipboard.
 */
#define main base_client_main
#include "host-client-check.cpp"
#undef main
#include "pointer-constraints-client.h"
#include "relative-pointer-client.h"
#include "surface-tree.hpp"
#include <X11/extensions/XTest.h>
#include <fcntl.h>
#include <functional>
using x11host::Rect;
#include <signal.h>
#include <sys/wait.h>

static wl_data_device_manager *data_manager;
static zwp_pointer_constraints_v1 *constraints;
static zwp_relative_pointer_manager_v1 *relative_manager;
static wl_data_offer *selection_offer;
static std::map<wl_data_offer *, bool> offers;
static std::string payload;
struct Writer {
  int fd;
  size_t offset;
};
static std::vector<Writer> writers;
static unsigned cancelled, confined, unconfined, locked, unlocked, popup_done,
    repositioned;
static Rect popup_rect;
static void feature_registry(void *p, wl_registry *r, uint32_t name,
                             const char *iface, uint32_t version) {
  registry(p, r, name, iface, version);
  if (!strcmp(iface, "xdg_wm_base")) {
    auto &c = *static_cast<Client *>(p);
    xdg_wm_base_destroy(c.wm);
    c.wm = static_cast<xdg_wm_base *>(
        wl_registry_bind(r, name, &xdg_wm_base_interface, 3));
  }
  if (!strcmp(iface, "wl_data_device_manager"))
    data_manager = static_cast<wl_data_device_manager *>(
        wl_registry_bind(r, name, &wl_data_device_manager_interface, 3));
  if (!strcmp(iface, "zwp_pointer_constraints_v1"))
    constraints = static_cast<zwp_pointer_constraints_v1 *>(
        wl_registry_bind(r, name, &zwp_pointer_constraints_v1_interface, 1));
  if (!strcmp(iface, "zwp_relative_pointer_manager_v1"))
    relative_manager =
        static_cast<zwp_relative_pointer_manager_v1 *>(wl_registry_bind(
            r, name, &zwp_relative_pointer_manager_v1_interface, 1));
}
static void pump(Client &c, int ticks = 1) {
  for (int tick = 0; tick < ticks; ++tick) {
    roundtrip(c);
    for (auto it = writers.begin(); it != writers.end();) {
      auto n = write(it->fd, payload.data() + it->offset,
                     std::min(size_t(16384), payload.size() - it->offset));
      if (n > 0)
        it->offset += size_t(n);
      else
        assert(n < 0 && (errno == EAGAIN || errno == EINTR));
      if (it->offset == payload.size()) {
        close(it->fd);
        it = writers.erase(it);
      } else
        ++it;
    }
    usleep(2000);
  }
}
static void wait_for(Client &c, const std::function<bool()> &done) {
  for (int i = 0; i < 2500; ++i) {
    if (done())
      return;
    pump(c);
  }
  assert(false && "feature event timeout");
}
static void offer_destroy(wl_data_offer *o) {
  if (o) {
    wl_data_offer_destroy(o);
    offers.erase(o);
  }
}
static pid_t xclip(int fd, bool input) {
  auto pid = fork();
  assert(pid >= 0);
  if (!pid) {
    dup2(fd, input ? STDIN_FILENO : STDOUT_FILENO);
    close(fd);
    execlp("xclip", "xclip", "-selection", "clipboard", input ? "-in" : "-out",
           "-quiet", nullptr);
    _exit(127);
  }
  return pid;
}
int main(int argc, char **argv) {
  setvbuf(stdout, nullptr, _IOLBF, 0);
  assert(argc == 2 && getenv("ANVIL_TEST_X11"));
  signal(SIGPIPE, SIG_IGN);
  Client c{};
  c.display = wl_display_connect(argv[1]);
  assert(c.display);
  auto *registry_proxy = wl_display_get_registry(c.display);
  static const wl_registry_listener registry_events = {
      feature_registry, [](void *, wl_registry *, uint32_t) {}};
  wl_registry_add_listener(registry_proxy, &registry_events, &c);
  roundtrip(c);
  roundtrip(c);
  assert(data_manager && constraints && relative_manager);
  auto *pointer = wl_seat_get_pointer(c.seat);
  wl_pointer_add_listener(pointer, &pointer_events, nullptr);
  auto *keyboard = wl_seat_get_keyboard(c.seat);
  wl_keyboard_add_listener(keyboard, &keyboard_events, nullptr);
  auto *device = wl_data_device_manager_get_data_device(data_manager, c.seat);
  static const wl_data_offer_listener offer_events = {
      [](void *, wl_data_offer *o, const char *m) {
        if (!strcmp(m, "text/plain;charset=utf-8"))
          offers[o] = true;
      },
      [](void *, wl_data_offer *, uint32_t) {},
      [](void *, wl_data_offer *, uint32_t) {}};
  static const wl_data_device_listener device_events = {
      [](void *, wl_data_device *, wl_data_offer *o) {
        offers[o] = false;
        wl_data_offer_add_listener(o, &offer_events, nullptr);
      },
      [](void *, wl_data_device *, uint32_t, wl_surface *, wl_fixed_t,
         wl_fixed_t, wl_data_offer *) {},
      [](void *, wl_data_device *) {},
      [](void *, wl_data_device *, uint32_t, wl_fixed_t, wl_fixed_t) {},
      [](void *, wl_data_device *) {},
      [](void *, wl_data_device *, wl_data_offer *o) {
        auto *old = selection_offer;
        selection_offer = o;
        if (old != o)
          offer_destroy(old);
      }};
  wl_data_device_add_listener(device, &device_events, nullptr);
  auto a = app(c, "waydroid.anvildroid.feature-check");
  pump(c, 3);
  auto *content = buffer(c, 300, 200, 0xff206080);
  xdg_surface_set_window_geometry(a.xdg, 0, 0, 300, 200);
  wl_surface_attach(a.surface, content, 0, 0);
  wl_surface_commit(a.surface);
  pump(c, 5);
  auto *x = XOpenDisplay(nullptr);
  assert(x);
  auto w = find_window(x, "waydroid.anvildroid.feature-check");
  XMoveWindow(x, w, 100, 100);
  XSetInputFocus(x, w, RevertToParent, CurrentTime);
  XWarpPointer(x, None, w, 0, 0, 0, 0, 40, 40);
  XSync(x, False);
  pump(c, 5);
  assert(pointer_surface == a.surface && keyboard_surface == a.surface);
  auto before_buttons = buttons;
  scroll_axes.clear();
  for (int button = 4; button <= 7; ++button) {
    XTestFakeButtonEvent(x, button, True, CurrentTime);
    XTestFakeButtonEvent(x, button, False, CurrentTime);
  }
  XSync(x, False);
  wait_for(c, [] { return scroll_axes.size() >= 4; });
  pump(c, 5);
  const std::vector<std::pair<uint32_t, wl_fixed_t>> expected_scroll = {
      {WL_POINTER_AXIS_VERTICAL_SCROLL, wl_fixed_from_int(-10)},
      {WL_POINTER_AXIS_VERTICAL_SCROLL, wl_fixed_from_int(10)},
      {WL_POINTER_AXIS_HORIZONTAL_SCROLL, wl_fixed_from_int(-10)},
      {WL_POINTER_AXIS_HORIZONTAL_SCROLL, wl_fixed_from_int(10)}};
  assert(scroll_axes == expected_scroll && buttons == before_buttons);
  puts("PASS real XInput wheel up/down/left/right reaches guest pointer once "
       "per notch with correct direction");
  auto *region = wl_compositor_create_region(c.compositor);
  wl_region_add(region, 30, 30, 50, 50);
  auto *confine = zwp_pointer_constraints_v1_confine_pointer(
      constraints, a.surface, pointer, region,
      ZWP_POINTER_CONSTRAINTS_V1_LIFETIME_PERSISTENT);
  static const zwp_confined_pointer_v1_listener confine_events = {
      [](void *, zwp_confined_pointer_v1 *) { ++confined; },
      [](void *, zwp_confined_pointer_v1 *) { ++unconfined; }};
  zwp_confined_pointer_v1_add_listener(confine, &confine_events, nullptr);
  wait_for(c, [] { return confined == 1; });
  XWarpPointer(x, None, w, 0, 0, 0, 0, 250, 180);
  XSync(x, False);
  pump(c, 5);
  Window root, child;
  int rx, ry, wx, wy;
  unsigned mask;
  XQueryPointer(x, w, &root, &child, &rx, &ry, &wx, &wy, &mask);
  assert(wx >= 30 && wx < 80 && wy >= 30 && wy < 80);
  // Updating a live one-shot/ persistent region must not emit another
  // activation.
  wl_region_add(region, 80, 30, 20, 50);
  zwp_confined_pointer_v1_set_region(confine, region);
  wl_surface_commit(a.surface);
  pump(c, 5);
  assert(confined == 1 && !unconfined);
  zwp_confined_pointer_v1_destroy(confine);
  pump(c, 3);
  auto *lock = zwp_pointer_constraints_v1_lock_pointer(
      constraints, a.surface, pointer, nullptr,
      ZWP_POINTER_CONSTRAINTS_V1_LIFETIME_ONESHOT);
  static const zwp_locked_pointer_v1_listener lock_events = {
      [](void *, zwp_locked_pointer_v1 *) { ++locked; },
      [](void *, zwp_locked_pointer_v1 *) { ++unlocked; }};
  zwp_locked_pointer_v1_add_listener(lock, &lock_events, nullptr);
  wait_for(c, [] { return locked == 1; });
  unsigned relative_events = 0;
  auto *relative = zwp_relative_pointer_manager_v1_get_relative_pointer(
      relative_manager, pointer);
  static const zwp_relative_pointer_v1_listener relative_events_listener = {
      [](void *p, zwp_relative_pointer_v1 *, uint32_t, uint32_t, wl_fixed_t dx,
         wl_fixed_t dy, wl_fixed_t ux, wl_fixed_t uy) {
        if (dx || dy || ux || uy)
          ++*static_cast<unsigned *>(p);
      }};
  zwp_relative_pointer_v1_add_listener(relative, &relative_events_listener,
                                       &relative_events);
  pump(c, 2);
  auto before_x = pointer_x, before_y = pointer_y;
  XTestFakeRelativeMotionEvent(x, 8, 5, CurrentTime);
  XSync(x, False);
  pump(c, 5);
  assert(relative_events > 0 && pointer_x == before_x && pointer_y == before_y);
  zwp_relative_pointer_v1_destroy(relative);
  auto other =
      XCreateSimpleWindow(x, DefaultRootWindow(x), 600, 100, 100, 100, 0, 0, 0);
  XMapWindow(x, other);
  XSetInputFocus(x, other, RevertToParent, CurrentTime);
  XSync(x, False);
  wait_for(c, [] { return unlocked == 1; });
  XSetInputFocus(x, w, RevertToParent, CurrentTime);
  XWarpPointer(x, None, w, 0, 0, 0, 0, 40, 40);
  XSync(x, False);
  pump(c, 5);
  assert(locked == 1);
  zwp_locked_pointer_v1_destroy(lock);
  wl_region_destroy(region);
  pump(c, 2);
  puts("PASS native pointer confinement/region update and one-shot lock/focus "
       "release");

  auto *positioner = xdg_wm_base_create_positioner(c.wm);
  xdg_positioner_set_size(positioner, 80, 60);
  xdg_positioner_set_anchor_rect(positioner, 20, 20, 10, 10);
  xdg_positioner_set_anchor(positioner, XDG_POSITIONER_ANCHOR_BOTTOM_RIGHT);
  xdg_positioner_set_gravity(positioner, XDG_POSITIONER_GRAVITY_BOTTOM_RIGHT);
  auto *surface = wl_compositor_create_surface(c.compositor);
  auto *xdg = xdg_wm_base_get_xdg_surface(c.wm, surface);
  auto *popup = xdg_surface_get_popup(xdg, a.xdg, positioner);
  static const xdg_popup_listener popup_events = {
      [](void *, xdg_popup *, int32_t x, int32_t y, int32_t w, int32_t h) {
        popup_rect = {double(x), double(y), double(w), double(h)};
      },
      [](void *, xdg_popup *) { ++popup_done; },
      [](void *, xdg_popup *, uint32_t token) {
        assert(token == 77);
        ++repositioned;
      }};
  static const xdg_surface_listener popup_surface_events = {
      [](void *, xdg_surface *s, uint32_t serial) {
        xdg_surface_ack_configure(s, serial);
      }};
  xdg_surface_add_listener(xdg, &popup_surface_events, nullptr);
  xdg_popup_add_listener(popup, &popup_events, nullptr);
  wl_surface_commit(surface);
  pump(c, 3);
  assert(popup_rect.x == 30 && popup_rect.y == 30 && popup_rect.w == 80);
  auto *popup_buffer = buffer(c, 80, 60, 0xff806020);
  wl_surface_attach(surface, popup_buffer, 0, 0);
  wl_surface_commit(surface);
  pump(c, 3);
  xdg_positioner_set_offset(positioner, 7, 9);
  xdg_popup_reposition(popup, positioner, 77);
  pump(c, 3);
  assert(repositioned == 1 && popup_rect.x == 37 && popup_rect.y == 39);
  xdg_popup_destroy(popup);
  xdg_surface_destroy(xdg);
  wl_surface_destroy(surface);
  xdg_positioner_destroy(positioner);
  wl_buffer_destroy(popup_buffer);
  pump(c, 3);
  assert(!popup_done);
  puts("PASS xdg popup initial configure/map/reposition and normal destroy "
       "without dismissal");

  XWarpPointer(x, None, w, 0, 0, 0, 0, 40, 40);
  XTestFakeButtonEvent(x, 1, True, CurrentTime);
  XTestFakeButtonEvent(x, 1, False, CurrentTime);
  XSync(x, False);
  pump(c, 5);
  assert(button_serial);
  struct Popup {
    wl_surface *surface;
    xdg_surface *xdg;
    xdg_popup *popup;
    wl_buffer *buffer;
  };
  auto nested_popup = [&](xdg_surface *parent) {
    auto *p = xdg_wm_base_create_positioner(c.wm);
    xdg_positioner_set_size(p, 80, 60);
    xdg_positioner_set_anchor_rect(p, 30, 30, 1, 1);
    xdg_positioner_set_anchor(p, XDG_POSITIONER_ANCHOR_BOTTOM_RIGHT);
    xdg_positioner_set_gravity(p, XDG_POSITIONER_GRAVITY_BOTTOM_RIGHT);
    xdg_positioner_set_reactive(p);
    Popup result{};
    result.surface = wl_compositor_create_surface(c.compositor);
    result.xdg = xdg_wm_base_get_xdg_surface(c.wm, result.surface);
    result.popup = xdg_surface_get_popup(result.xdg, parent, p);
    xdg_positioner_destroy(p);
    xdg_surface_add_listener(result.xdg, &popup_surface_events, nullptr);
    xdg_popup_add_listener(result.popup, &popup_events, nullptr);
    xdg_popup_grab(result.popup, c.seat, button_serial);
    wl_surface_commit(result.surface);
    pump(c, 3);
    result.buffer = buffer(c, 80, 60, 0xff804020);
    wl_surface_attach(result.surface, result.buffer, 0, 0);
    wl_surface_commit(result.surface);
    pump(c, 3);
    return result;
  };
  auto parent_popup = nested_popup(a.xdg);
  auto child_popup = nested_popup(parent_popup.xdg);
  XWarpPointer(x, None, w, 0, 0, 0, 0, 20, 150);
  XTestFakeButtonEvent(x, 1, True, CurrentTime);
  XTestFakeButtonEvent(x, 1, False, CurrentTime);
  XSync(x, False);
  wait_for(c, [] { return popup_done == 2; });
  for (auto p : {child_popup, parent_popup}) {
    xdg_popup_destroy(p.popup);
    xdg_surface_destroy(p.xdg);
    wl_surface_destroy(p.surface);
    wl_buffer_destroy(p.buffer);
  }
  XSetInputFocus(x, w, RevertToParent, CurrentTime);
  XSync(x, False);
  pump(c, 5);
  puts("PASS nested popup grabs, outside-click dismissal and parent focus "
       "restoration");

  if (getenv("ANVIL_TEST_COMPOSITION")) {
    struct Composition {
      std::string preedit, commit;
      unsigned draws = 0;
      int cursor = 0;
    } composition;
    auto *text =
        zwp_text_input_manager_v3_get_text_input(c.text_manager, c.seat);
    static const zwp_text_input_v3_listener composition_events = {
        [](void *, zwp_text_input_v3 *, wl_surface *) {},
        [](void *, zwp_text_input_v3 *, wl_surface *) {},
        [](void *p, zwp_text_input_v3 *, const char *s, int32_t a, int32_t b) {
          auto &v = *static_cast<Composition *>(p);
          v.preedit = s ? s : "";
          v.cursor = a;
          assert(a == b && a >= 0 && size_t(a) <= v.preedit.size());
          if (!v.preedit.empty())
            ++v.draws;
        },
        [](void *p, zwp_text_input_v3 *, const char *s) {
          static_cast<Composition *>(p)->commit += s ? s : "";
        },
        [](void *, zwp_text_input_v3 *, uint32_t, uint32_t) {},
        [](void *, zwp_text_input_v3 *, uint32_t) {}};
    zwp_text_input_v3_add_listener(text, &composition_events, &composition);
    zwp_text_input_v3_enable(text);
    zwp_text_input_v3_set_cursor_rectangle(text, 20, 30, 1, 20);
    zwp_text_input_v3_commit(text);
    pump(c, 5);
    auto run_method = [](const char *option, const char *value) {
      auto pid = fork();
      assert(pid >= 0);
      if (!pid) {
        execlp("fcitx5-remote", "fcitx5-remote", option, value, nullptr);
        _exit(127);
      }
      int status;
      assert(waitpid(pid, &status, 0) == pid && WIFEXITED(status) &&
             !WEXITSTATUS(status));
    };
    run_method("-s", "pinyin");
    run_method("-o", nullptr);
    pump(c, 30);
    auto key = [&](KeySym symbol) {
      auto code = XKeysymToKeycode(x, symbol);
      XTestFakeKeyEvent(x, code, True, CurrentTime);
      XTestFakeKeyEvent(x, code, False, CurrentTime);
      XSync(x, False);
      pump(c, 20);
    };
    key(XK_n);
    key(XK_i);
    wait_for(c, [&] { return composition.draws > 0; });
    assert(!composition.preedit.empty() && composition.commit.empty());
    key(XK_space);
    wait_for(c, [&] { return !composition.commit.empty(); });
    assert(composition.commit == u8"你" && composition.preedit.empty());
    physical_keys.clear();
    auto control = XKeysymToKeycode(x, XK_Control_L);
    auto a_key = XKeysymToKeycode(x, XK_a);
    XTestFakeKeyEvent(x, control, True, CurrentTime);
    XTestFakeKeyEvent(x, a_key, True, CurrentTime);
    XTestFakeKeyEvent(x, a_key, False, CurrentTime);
    XTestFakeKeyEvent(x, control, False, CurrentTime);
    XSync(x, False);
    wait_for(c, [&] {
      return std::find(physical_keys.begin(), physical_keys.end(),
                       std::make_pair(uint32_t(a_key - 8), 0u)) !=
             physical_keys.end();
    });
    const std::vector<std::pair<uint32_t, uint32_t>> shortcut = {
        {uint32_t(control - 8), 1}, {uint32_t(a_key - 8), 1},
        {uint32_t(a_key - 8), 0}, {uint32_t(control - 8), 0}};
    assert(physical_keys == shortcut && composition.commit == u8"你");
    puts("PASS Control shortcut remains physical while XIM composition is enabled");
    zwp_text_input_v3_disable(text);
    zwp_text_input_v3_commit(text);
    pump(c, 5);
    zwp_text_input_v3_destroy(text);
    run_method("-s", "keyboard-us");
    puts("PASS real Fcitx Pinyin XIM preedit/caret to CJK commit through "
         "text-input-v3");
  }

  payload = "Clipboard Tiếng Việt 😀\n";
  while (payload.size() < 1024 * 1024)
    payload += char('A' + payload.size() % 26);
  int input = memfd_create("clipboard-source", MFD_CLOEXEC);
  assert(input >= 0 && write(input, payload.data(), payload.size()) ==
                           ssize_t(payload.size()));
  lseek(input, 0, SEEK_SET);
  auto producer = xclip(input, true);
  close(input);
  wait_for(c, [] { return selection_offer && offers[selection_offer]; });
  int fds[2];
  assert(!pipe2(fds, O_CLOEXEC | O_NONBLOCK));
  wl_data_offer_receive(selection_offer, "text/plain;charset=utf-8", fds[1]);
  close(fds[1]);
  std::string received;
  bool eof = false;
  wait_for(c, [&] {
    char bytes[32768];
    auto n = read(fds[0], bytes, sizeof(bytes));
    if (n > 0)
      received.append(bytes, size_t(n));
    if (!n)
      eof = true;
    return eof;
  });
  close(fds[0]);
  assert(received == payload);
  auto *source = wl_data_device_manager_create_data_source(data_manager);
  static const wl_data_source_listener source_events = {
      [](void *, wl_data_source *, const char *) {},
      [](void *, wl_data_source *, const char *, int32_t fd) {
        fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
        writers.push_back({fd, 0});
      },
      [](void *, wl_data_source *) { ++cancelled; },
      [](void *, wl_data_source *) {},
      [](void *, wl_data_source *) {},
      [](void *, wl_data_source *, uint32_t) {}};
  wl_data_source_add_listener(source, &source_events, nullptr);
  wl_data_source_offer(source, "text/plain;charset=utf-8");
  // Deliver a real serial via the same synthetic core path used by CSD tests.
  XEvent press{};
  press.xbutton = {
      ButtonPress, 0,           True, x,  w,   DefaultRootWindow(x),
      None,        CurrentTime, 40,   40, 140, 140,
      0,           1,           True};
  XSendEvent(x, w, False, ButtonPressMask, &press);
  XSync(x, False);
  pump(c, 3);
  assert(button_serial);
  wl_data_device_set_selection(device, source, button_serial);
  pump(c, 5);
  kill(producer, SIGTERM);
  waitpid(producer, nullptr, 0);
  int output = memfd_create("clipboard-destination", MFD_CLOEXEC);
  assert(output >= 0);
  auto consumer = xclip(output, false);
  int status = 0;
  wait_for(c, [&] { return waitpid(consumer, &status, WNOHANG) == consumer; });
  assert(WIFEXITED(status) && !WEXITSTATUS(status));
  lseek(output, 0, SEEK_SET);
  received.clear();
  char bytes[16384];
  ssize_t n;
  while ((n = read(output, bytes, sizeof(bytes))) > 0)
    received.append(bytes, size_t(n));
  close(output);
  assert(received == payload);
  XSetInputFocus(x, other, RevertToParent, CurrentTime);
  XSync(x, False);
  pump(c, 5);
  assert(!selection_offer);
  wl_data_source_destroy(source);
  pump(c, 3);
  puts("PASS bidirectional 1 MiB UTF-8 clipboard with X11 INCR and focus "
       "gating");
  wl_data_device_release(device);
  wl_data_device_manager_destroy(data_manager);
  zwp_pointer_constraints_v1_destroy(constraints);
  zwp_relative_pointer_manager_v1_destroy(relative_manager);
  wl_pointer_release(pointer);
  wl_keyboard_release(keyboard);
  xdg_toplevel_destroy(a.top);
  xdg_surface_destroy(a.xdg);
  wl_surface_destroy(a.surface);
  wl_buffer_destroy(content);
  globals_destroy(c, registry_proxy);
  roundtrip(c);
  wl_display_disconnect(c.display);
  XDestroyWindow(x, other);
  XCloseDisplay(x);
}
