/* SPDX-License-Identifier: MIT — deterministic input/protocol state checks. */
#define main adapter_program_main
#include "wayland-host.cpp"
#undef main
#include <cassert>
#include <sys/socket.h>

static std::vector<uint32_t> wire(Server &s, int fd) {
  wl_display_flush_clients(s.display);
  uint32_t words[512];
  auto n = recv(fd, words, sizeof(words), MSG_DONTWAIT);
  assert(n >= 0 && n % 4 == 0);
  return {words, words + n / 4};
}
static void geometry_unicode() {
  Preedit p;
  p.replace(0, 0, U"Toi😀", 4);
  p.replace(1, 1, U"ô", 2);
  assert(encode_utf8(p.text) == "Tôi😀" && p.byte_caret() == 3);
  p.replace(3, 1, U"e\u0301", 5);
  assert(p.byte_caret() == 7);
  bool invalid = false;
  try {
    p.replace(99, 0, U"x", 0);
  } catch (...) {
    invalid = true;
  }
  assert(invalid && encode_utf8(p.text) == "Tôié");
  for (auto value : {"\xc0\xaf", "\xed\xa0\x80", "\xf4\x90\x80\x80", "\xe1"}) {
    invalid = false;
    try {
      decode_utf8(value);
    } catch (...) {
      invalid = true;
    }
    assert(invalid);
  }
  invalid = false;
  try {
    p.replace(0, int(p.text.size()), std::u32string(4001, U'a'), 0);
  } catch (...) {
    invalid = true;
  }
  assert(invalid);
  Positioner position;
  position.width = 80;
  position.height = 60;
  position.anchor_rect = {90, 90, 10, 10};
  position.anchor = 8;
  position.gravity = 8;
  position.constraint = 4 | 8;
  auto r = position.place({0, 0, 120, 120});
  assert(r.x == 10 && r.y == 30 && r.w == 80 && r.h == 60);
  position.constraint = 1 | 2;
  r = position.place({0, 0, 120, 120});
  assert(r.x == 40 && r.y == 60);
  position.width = 200;
  position.constraint = 1 | 16;
  r = position.place({0, 0, 120, 120});
  assert(r.x == 0 && r.w == 120);
  puts("PASS Unicode replacement/UTF-8 caret, malformed text and popup "
       "flip/slide/resize");
}
static void input_backpressure() {
#if WAYLAND_VERSION_MAJOR > 1 || WAYLAND_VERSION_MINOR >= 23
  Server s(true);
  int fds[2];
  assert(!socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, fds));
  int size = 1024;
  assert(!setsockopt(fds[0], SOL_SOCKET, SO_SNDBUF, &size, sizeof(size)));
  fcntl(fds[1], F_SETFL, O_NONBLOCK);
  char prefix[512]{};
  size_t prefilling = 0;
  for (;;) {
    auto n = send(fds[0], prefix, sizeof(prefix), MSG_DONTWAIT | MSG_NOSIGNAL);
    if (n < 0) {
      assert(errno == EAGAIN);
      break;
    }
    prefilling += size_t(n);
  }
  auto *client = wl_client_create(s.display, fds[0]);
  assert(client);
  auto *pointer = wl_resource_create(client, &wl_pointer_interface, 5, 2);
  for (int i = 0; i < 64; ++i) {
    wl_pointer_send_motion(pointer, i, wl_fixed_from_int(i % 300), 0);
    wl_pointer_send_frame(pointer);
  }
  wl_display_flush_clients(s.display); // the full socket must return EAGAIN
  while (prefilling) {
    auto n = recv(fds[1], prefix, std::min(prefilling, sizeof(prefix)), 0);
    assert(n > 0);
    prefilling -= size_t(n);
  }
  size_t received = 0;
  for (int i = 0; i < 1000 && received < 1792; ++i) {
    wl_display_flush_clients(s.display);
    char bytes[4096];
    auto n = recv(fds[1], bytes, sizeof(bytes), MSG_DONTWAIT);
    if (n > 0)
      received += size_t(n);
    else
      assert(n < 0 && (errno == EAGAIN || errno == EINTR));
  }
  assert(received == 1792);
  close(fds[1]);
  puts("PASS a full native input turn survives a paused reader and socket EAGAIN");
#endif
}
static void native_event_budget() {
  assert(getenv("ANVIL_TEST_X11"));
  Server s(false);
  XEvent event{};
  event.type = ClientMessage;
  event.xany.display = s.platform.display;
  for (int i = 0; i < 512; ++i)
    XPutBackEvent(s.platform.display, &event);
  s.events();
  assert(XEventsQueued(s.platform.display, QueuedAlready) >= 448);
  int fds[2];
  assert(!socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, fds));
  assert(wl_client_create(s.display, fds[0]));
  uint32_t sync[] = {1, 12u << 16, 2}; // wl_display.sync (opcode 0)
  assert(write(fds[1], sync, sizeof(sync)) == ssize_t(sizeof(sync)));
  assert(wl_event_loop_dispatch(s.loop, 0) >= 0);
  auto result = wire(s, fds[1]);
  assert(result.size() >= 3 && result[0] == 2 &&
         (result[1] & 65535) == WL_CALLBACK_DONE);
  close(fds[1]);
  puts("PASS guest sync is dispatched while native X events remain queued");
}
static void motion_order() {
  Server s(false);
  XSync(s.platform.display, True);
  int fds[2];
  assert(!socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, fds));
  auto *client = wl_client_create(s.display, fds[0]);
  assert(client);
  Node n;
  Top t{};
  n.server = &s;
  n.top = &t;
  n.resource = wl_resource_create(client, &wl_surface_interface, 4, 2);
  n.current.image = std::make_shared<Image>();
  n.current.image->width = n.current.image->height = 300;
  t.server = &s;
  t.node = &n;
  t.xdg = wl_resource_create(client, &xdg_surface_interface, 3, 3);
  t.window.window = 0x123456;
  s.tops.insert(&t);
  s.keyboard_focus = &t;
  s.pointer_focus = &n;
  auto *pointer = wl_resource_create(client, &wl_pointer_interface, 5, 4);
  s.pointers.insert(pointer);
  std::vector<XEvent> events;
  auto add = [&](int type, int x, unsigned state) {
    XEvent e{};
    e.type = type;
    e.xany.display = s.platform.display;
    e.xany.window = t.window.window;
    e.xany.send_event = True;
    if (type == MotionNotify) {
      e.xmotion.x = x;
      e.xmotion.y = 10;
      e.xmotion.state = state;
    } else {
      e.xbutton.x = x;
      e.xbutton.y = 10;
      e.xbutton.state = state;
      e.xbutton.button = 1;
    }
    events.push_back(e);
  };
  add(MotionNotify, 1, 0);
  add(MotionNotify, 2, 0);
  add(MotionNotify, 3, 0);
  add(ButtonPress, 3, 0);
  add(MotionNotify, 4, Button1Mask);
  add(MotionNotify, 5, Button1Mask);
  add(ButtonRelease, 5, Button1Mask);
  add(MotionNotify, 6, 0);
  for (auto i = events.rbegin(); i != events.rend(); ++i)
    XPutBackEvent(s.platform.display, &*i);
  s.events();
  auto messages = wire(s, fds[1]);
  std::vector<std::pair<unsigned, int>> delivered;
  for (size_t i = 0; i < messages.size();) {
    unsigned opcode = messages[i + 1] & 65535;
    if (opcode == WL_POINTER_MOTION)
      delivered.emplace_back(opcode, int32_t(messages[i + 3]) / 256);
    if (opcode == WL_POINTER_BUTTON)
      delivered.emplace_back(opcode, messages[i + 5]);
    i += (messages[i + 1] >> 16) / 4;
  }
  const std::vector<std::pair<unsigned, int>> expected = {
      {WL_POINTER_MOTION, 3}, {WL_POINTER_BUTTON, 1},
      {WL_POINTER_MOTION, 5}, {WL_POINTER_BUTTON, 0},
      {WL_POINTER_MOTION, 6}};
  assert(delivered == expected && !s.grab && s.pointer_buttons.empty());
  s.tops.clear();
  s.pointers.clear();
  s.keyboard_focus = nullptr;
  s.pointer_focus = nullptr;
  close(fds[1]);
  puts("PASS absolute motion coalesces while preserving drag/click/release order");
}
int main(int argc, char **argv) {
  if (argc == 2 && !strcmp(argv[1], "--x11-input-burst")) {
    native_event_budget();
    motion_order();
    return 0;
  }
  assert(argc == 1);
  input_backpressure();
  geometry_unicode();
  Server s(true);
  int fds[2];
  assert(!socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, fds));
  auto *client = wl_client_create(s.display, fds[0]);
  assert(client);
  Node n, child;
  Top t{};
  n.server = child.server = &s;
  t.server = &s;
  t.node = &n;
  n.top = &t;
  n.resource = wl_resource_create(client, &wl_surface_interface, 4, 2);
  child.resource = wl_resource_create(client, &wl_surface_interface, 4, 3);
  auto *touch = wl_resource_create(client, &wl_touch_interface, 5, 4);
  s.touches.insert(touch);
  t.xdg = wl_resource_create(client, &xdg_surface_interface, 3, 5);
  t.window.mapped = true;
  n.current.image = std::make_shared<Image>();
  n.current.image->width = 300;
  n.current.image->height = 200;
  child.reparent(&n);
  child.x = 40;
  child.y = 30;
  child.current.image = std::make_shared<Image>();
  child.current.image->width = 100;
  child.current.image->height = 100;
  s.touch_event(XI_TouchBegin, 100, &t, 50, 50, 1);
  s.touch_event(XI_TouchBegin, 101, &t, 200, 100, 1);
  auto messages = wire(s, fds[1]);
  assert(messages[0] == 4 && (messages[1] & 65535) == WL_TOUCH_DOWN);
  assert(messages[4] == 3 && messages[5] == 0 &&
         int32_t(messages[6]) == 10 * 256 && int32_t(messages[7]) == 20 * 256);
  assert(s.touch_points.at(100).node == &child &&
         s.touch_points.at(101).id == 1);
  s.touch_event(XI_TouchUpdate, 100, &t, 250, 150, 2);
  messages = wire(s, fds[1]);
  assert((messages[1] & 65535) == WL_TOUCH_MOTION && messages[3] == 0 &&
         int32_t(messages[4]) == 210 * 256);
  s.touch_event(XI_TouchEnd, 101, &t, 200, 100, 3);
  wire(s, fds[1]);
  s.cancel_touches(&child);
  messages = wire(s, fds[1]);
  assert((messages[1] & 65535) == WL_TOUCH_CANCEL && s.touch_points.empty());
  s.touch_event(XI_TouchBegin, 102, &t, 60, 60, 4);
  wire(s, fds[1]);
  assert(s.touch_points.at(102).id == 0);
  s.cancel_touches();
  wire(s, fds[1]);
  auto *pointer = wl_resource_create(client, &wl_pointer_interface, 5, 6);
  auto *locked =
      wl_resource_create(client, &zwp_locked_pointer_v1_interface, 1, 7);
  auto state = std::make_unique<Constraint>();
  auto *con = state.get();
  con->server = &s;
  con->node = &n;
  con->pointer = pointer;
  con->resource = locked;
  con->locked = true;
  con->persistent = true;
  s.constraints[locked] = std::move(state);
  s.pointer_focus = &n;
  s.pointer_x = 10;
  s.pointer_y = 10;
  s.sync_constraints();
  assert(con->active);
  wire(s, fds[1]);
  s.focus(nullptr);
  assert(!con->active && !con->spent);
  wire(s, fds[1]);
  s.focus(&t);
  assert(con->active);
  wire(s, fds[1]);
  s.pointer_destroy(pointer);
  assert(!con->active && !con->pointer);
  wire(s, fds[1]);
  con->persistent = false;
  con->pointer = pointer;
  s.sync_constraints();
  wire(s, fds[1]);
  assert(con->active);
  s.focus(nullptr);
  wire(s, fds[1]);
  assert(con->spent && !con->active);
  s.focus(&t);
  assert(!con->active);
  con->pending_default = false;
  con->pending_region = {{true, {10, 10, 20, 20}}};
  con->region_pending = true;
  s.input_cache(&n);
  con->pending_region = {{true, {50, 50, 20, 20}}};
  con->region_pending = true;
  s.input_commit(&n);
  assert(con->region[0].second.x == 10 && con->region_pending);
  s.input_cache(&n);
  s.input_commit(&n);
  assert(con->region[0].second.x == 50 && !con->region_pending);
  s.input_destroy(&n);
  assert(!con->node);
  s.focus(nullptr);
  s.pointer_focus = nullptr;
  s.touches.clear();
  s.constraints.clear();
  child.detach();
  close(fds[1]);
  puts("PASS multitouch stable IDs/local coordinates/cancel and "
       "persistent/oneshot lock/focus/pointer teardown");
}
