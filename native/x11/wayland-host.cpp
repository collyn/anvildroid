/* SPDX-License-Identifier: MIT
 * Waydroid's existing composer/bridge protocol, presented as native X11
 * windows. No nested desktop, Weston, guest HAL replacement, or host input
 * injection.
 */
#include "linux-dmabuf-server.h"
#include "pointer-constraints-server.h"
#include "popup-position.hpp"
#include "relative-pointer-server.h"
#include "text-input-server.h"
#include "viewporter-server.h"
#include "x11-platform.hpp"
#include "xdg-activation-server.h"
#include "xdg-shell-server.h"
#include "xim-text.hpp"
#include <array>
#include <csignal>
#include <deque>
#include <fcntl.h>
#include <linux/input-event-codes.h>
#include <locale.h>
#include <map>
#include <poll.h>
#include <set>
#include <sys/mman.h>
#include <sys/stat.h>
#include <wayland-server-protocol.h>
#include <wayland-server.h>
#include <wayland-version.h>
#include <xkbcommon/xkbcommon.h>

using namespace x11host;
struct Server;
struct Top;
struct Node : Surface {
  Server *server;
  wl_resource *resource;
  wl_resource *sub = nullptr;
  Top *top = nullptr;
  std::vector<uint32_t> cursor_pixels;
  bool viewport = false, cursor_role = false;
};
struct DmaBuffer {
  wl_resource *resource = nullptr;
  int fd = -1, width = 0, height = 0, stride = 0, offset = 0;
  uint32_t format = 0;
  uint64_t modifier = 0;
  ~DmaBuffer() {
    if (fd >= 0)
      close(fd);
  }
};
struct Top {
  Server *server;
  Node *node;
  wl_resource *xdg;
  wl_resource *resource = nullptr;
  NativeWindow window;
  Rect geometry{0, 0, 640, 480}, pending_geometry{0, 0, 0, 0};
  WindowFrame frame;
  bool frame_constrained = false;
  int normal_padding_w = 0, normal_padding_h = 0;
  bool geometry_pending = false, configured = false, configure_acked = false,
       initial = false, dirty = false, maximized = false, fullscreen = false,
       active = false, resize_window = false;
  std::string app = "Waydroid", title = "Android";
  std::deque<uint32_t> serials;
  XIC ic = nullptr;
  bool ic_focused = false;
  bool preedit_callbacks = false;
  Preedit preedit;
  XIMCallback ime_callbacks[4]{};
  bool identity_dirty = true;
  bool popup = false, dismissed = false;
  Top *parent = nullptr;
  Positioner positioner;
  Rect popup_geometry;
};
struct Text {
  bool enabled = false, pending = false;
  uint32_t serial = 0;
  Rect cursor, pending_cursor;
  std::string surrounding, pending_surrounding;
  int32_t selection = 0, anchor = 0, pending_selection = 0, pending_anchor = 0;
  uint32_t hint = 0, purpose = 0, pending_hint = 0, pending_purpose = 0;
};
struct TouchPoint {
  Node *node;
  int id;
};
struct Constraint {
  Server *server;
  Node *node;
  wl_resource *resource, *pointer;
  bool locked, persistent, active = false, spent = false;
  bool refresh = true;
  uint64_t input_revision = 0;
  double native_width = -1, native_height = -1, native_x = 0, native_y = 0;
  bool default_region = true, pending_default = true, region_pending = false;
  std::vector<std::pair<bool, Rect>> region, pending_region;
  bool region_cached = false, cached_default = true, hint_cached = false;
  std::vector<std::pair<bool, Rect>> cached_region;
  double cached_x = 0, cached_y = 0;
  bool hint_pending = false, hint_valid = false;
  double hint_x = 0, hint_y = 0, pending_x = 0, pending_y = 0;
};
static volatile sig_atomic_t stopping = 0;
static int stop(int, void *) {
  stopping = 1;
  return 0;
}
static void destroy_request(wl_client *, wl_resource *r) {
  wl_resource_destroy(r);
}
template <class T> static T *get(wl_resource *r) {
  return static_cast<T *>(wl_resource_get_user_data(r));
}
static uint32_t milliseconds() {
  timespec ts{};
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return uint32_t(uint64_t(ts.tv_sec) * 1000 + ts.tv_nsec / 1000000);
}
static wl_resource *resource(wl_client *c, const wl_interface *type,
                             int version, uint32_t id, const void *ops,
                             void *data,
                             wl_resource_destroy_func_t destroy = nullptr) {
  auto *r = wl_resource_create(c, type, version, id);
  if (!r) {
    wl_client_post_no_memory(c);
    return nullptr;
  }
  wl_resource_set_implementation(r, ops, data, destroy);
  return r;
}
#include "clipboard.hpp"
struct Server {
  Platform platform;
  wl_display *display;
  wl_event_loop *loop;
  wl_protocol_logger *protocol_trace = nullptr;
  wl_event_source *x11_source = nullptr;
  wl_event_source *term_source = nullptr, *int_source = nullptr;
  std::map<wl_resource *, std::unique_ptr<Node>> nodes;
  std::map<wl_resource *, std::shared_ptr<DmaBuffer>> buffers;
  std::set<Top *> tops;
  std::set<wl_resource *> pointers, keyboards;
  std::set<wl_resource *> touches;
  std::map<wl_resource *, wl_resource *> relatives;
  std::map<uint64_t, TouchPoint> touch_points;
  std::map<wl_resource *, std::unique_ptr<Constraint>> constraints;
  Constraint *active_constraint = nullptr;
  Window confine_window = 0;
  double pointer_x = 0, pointer_y = 0;
  void input_globals();
  bool xi_event(XEvent &e);
  void touch_event(int type, uint64_t contact, Top *t, double x, double y,
                   uint32_t time);
  void cancel_touches(Node *node = nullptr);
  void release_constraint(bool notify = true);
  void sync_constraints();
  bool constrain_native(Constraint *c);
  bool constraint_changed(Constraint *c);
  void pointer_destroy(wl_resource *r);
  void input_commit(Node *node);
  void input_cache(Node *node);
  void input_destroy(Node *node);
  std::pair<double, double> node_origin(Node *n);
  std::unique_ptr<Clipboard> clipboard;
  std::map<wl_resource *, Text> texts;
  Node *pointer_focus = nullptr;
  Node *cursor_node = nullptr;
  int cursor_x = 0, cursor_y = 0;
  Node *grab = nullptr;
  std::map<unsigned, uint32_t> pointer_buttons;
  Top *keyboard_focus = nullptr;
  std::set<uint32_t> pressed, blocked_until_release;
  std::string keymap;
  xkb_state *key_state = nullptr;
  int xkb_event = 0;
  int keymap_fd = -1;
  std::vector<Top *> popup_grabs;
  std::map<wl_client *, uint32_t> input_serials;
  std::map<wl_client *, std::deque<uint32_t>> input_history;
  uint32_t input_serial(wl_client *c) {
    auto value = serial();
    input_serials[c] = value;
    auto &history = input_history[c];
    history.push_back(value);
    if (history.size() > 64)
      history.pop_front();
    return value;
  }
  bool valid_input(wl_client *c, uint32_t value) const {
    auto at = input_history.find(c);
    return value && at != input_history.end() &&
           std::find(at->second.begin(), at->second.end(), value) !=
               at->second.end();
  }
  void popup_configure(Top *t, uint32_t token = 0, bool reposition = false);
  void popup_place(Top *t);
  void popup_dismiss(Top *t);
  bool popup_button(Top *target, int x, int y);
  explicit Server(bool testing) : platform(testing) {
    display = wl_display_create();
    ensure(display, "Cannot create private Waydroid protocol endpoint");
    // Keep libwayland's 4 KiB read batch. Raising the connection limit also
    // raises its input batch: many DMA-BUF snapshots in one dispatch can stall
    // XIM and overflow the legacy composer's outgoing request queue.
    if (getenv("ANVIL_TEST_PROTOCOL_TRACE"))
      protocol_trace = wl_display_add_protocol_logger(
          display,
          [](void *, wl_protocol_logger_type direction,
             const wl_protocol_logger_message *message) {
            pid_t pid;
            wl_client_get_credentials(wl_resource_get_client(message->resource),
                                      &pid, nullptr, nullptr);
            // Trace only names/IDs; never print clipboard or editor contents.
            fprintf(stderr, "protocol pid=%d %s %s@%u.%s\n", int(pid),
                    direction == WL_PROTOCOL_LOGGER_REQUEST ? "request" : "event",
                    wl_resource_get_class(message->resource),
                    wl_resource_get_id(message->resource), message->message->name);
          },
          nullptr);
    loop = wl_display_get_event_loop(display);
    term_source = wl_event_loop_add_signal(loop, SIGTERM, stop, nullptr);
    int_source = wl_event_loop_add_signal(loop, SIGINT, stop, nullptr);
    ensure(term_source && int_source, "Cannot watch adapter stop signals");
    if (!platform.testing()) {
      // X11 input must wake the same wait as guest commits. A periodic check
      // adds up to a full timeout to every click and wakes an idle host too.
      x11_source = wl_event_loop_add_fd(
          loop, ConnectionNumber(platform.display), WL_EVENT_READABLE,
          [](int, uint32_t, void *) { return 0; }, this);
      ensure(x11_source, "Cannot watch native X11 events");
    }
    if (const char *value = getenv("ANVILDROID_RUNTIME_ID")) {
      std::string runtime = value;
      ensure(runtime.size() == 34 && runtime.substr(0, 2) == "r-" &&
                 runtime.find_first_not_of("0123456789abcdef", 2) ==
                     std::string::npos,
             "Invalid runtime identity");
    }
    auto *ctx = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    ensure(ctx, "Cannot create keymap context");
    xkb_rule_names names{};
    unsigned char *rules = nullptr;
    if (!platform.testing()) {
      Atom type;
      int format;
      unsigned long count, left;
      if (XGetWindowProperty(
              platform.display, DefaultRootWindow(platform.display),
              platform.atom("_XKB_RULES_NAMES"), 0, 4096, False, XA_STRING,
              &type, &format, &count, &left, &rules) == Success &&
          rules && type == XA_STRING && format == 8) {
        const char **fields[] = {&names.rules, &names.model, &names.layout,
                                 &names.variant, &names.options};
        size_t offset = 0;
        for (auto *field : fields) {
          auto length =
              strnlen(reinterpret_cast<char *>(rules) + offset, count - offset);
          if (offset + length >= count)
            break;
          *field = reinterpret_cast<char *>(rules) + offset;
          offset += length + 1;
        }
      }
      int opcode, error, major = XkbMajorVersion, minor = XkbMinorVersion;
      if (XkbQueryExtension(platform.display, &opcode, &xkb_event, &error,
                            &major, &minor))
        XkbSelectEvents(platform.display, XkbUseCoreKbd, XkbStateNotifyMask,
                        XkbStateNotifyMask);
    }
    auto *map =
        xkb_keymap_new_from_names(ctx, &names, XKB_KEYMAP_COMPILE_NO_FLAGS);
    if (rules)
      XFree(rules);
    ensure(map, "Cannot create desktop keymap");
    key_state = xkb_state_new(map);
    ensure(key_state, "Cannot create desktop modifier state");
    char *text = xkb_keymap_get_as_string(map, XKB_KEYMAP_FORMAT_TEXT_V1);
    keymap = text;
    free(text);
    xkb_keymap_unref(map);
    xkb_context_unref(ctx);
    keymap_fd = memfd_create("anvildroid-x11-keymap", MFD_CLOEXEC);
    ensure(keymap_fd >= 0, "Cannot create keymap memfd");
    ensure(write(keymap_fd, keymap.c_str(), keymap.size() + 1) ==
               ssize_t(keymap.size() + 1),
           "Cannot write keymap");
    wl_display_init_shm(display);
    clipboard = std::make_unique<Clipboard>(
        display, platform.display,
        [this]() -> wl_client * {
          return keyboard_focus ? wl_resource_get_client(keyboard_focus->xdg)
                                : nullptr;
        },
        [this](wl_client *c, uint32_t value) { return valid_input(c, value); });
    globals();
  }
  ~Server() {
    wl_event_source_remove(term_source);
    wl_event_source_remove(int_source);
    if (x11_source)
      wl_event_source_remove(x11_source);
    wl_display_destroy_clients(display);
    nodes.clear();
    clipboard.reset();
    if (keymap_fd >= 0)
      close(keymap_fd);
    xkb_state_unref(key_state);
    if (protocol_trace)
      wl_protocol_logger_destroy(protocol_trace);
    wl_display_destroy(display);
  }
  uint32_t serial() { return wl_display_next_serial(display); }
  void end_grab() {
    // WM grabs/device recovery can consume the physical release. Deliver the
    // missing releases before dropping ownership, so guest fire/touch state
    // cannot survive a recovered grab. Normal release already erased its key.
    if (grab) for (auto button : pointer_buttons)
      for (auto *r : pointers)
        if (wl_resource_get_client(r) == wl_resource_get_client(grab->resource)) {
          wl_pointer_send_button(r, serial(), milliseconds(),
            button.first==1?BTN_LEFT:button.first==2?BTN_MIDDLE:BTN_RIGHT,
            WL_POINTER_BUTTON_STATE_RELEASED);
          if(wl_resource_get_version(r)>=5)wl_pointer_send_frame(r);
        }
    grab = nullptr;
    pointer_buttons.clear();
  }
  static Node *node(wl_resource *r) { return get<Node>(r); }
  void invalidate(Node *n) {
    while (n->parent)
      n = static_cast<Node *>(n->parent);
    if (n->top)
      n->top->dirty = true;
  }
  void configure(Top *t, int w = 0, int h = 0) {
    if (!t->resource)
      return;
    if (t->popup) {
      popup_configure(t);
      return;
    }
    // The composer calibrates Android's canvas from the first maximized
    // configure. An unspecified size defers that calibration until Full UI,
    // after the bridge has already assigned the window's role.
    if (!t->initial && t->maximized && w == 0 && h == 0) {
      auto size = platform.output_size();
      w = size.first;
      h = size.second;
    }
    wl_array states;
    wl_array_init(&states);
    auto state = [&](uint32_t value) {
      auto *p = static_cast<uint32_t *>(wl_array_add(&states, 4));
      if (p)
        *p = value;
    };
    if (t->active)
      state(XDG_TOPLEVEL_STATE_ACTIVATED);
    if (t->maximized)
      state(XDG_TOPLEVEL_STATE_MAXIMIZED);
    if (t->fullscreen)
      state(XDG_TOPLEVEL_STATE_FULLSCREEN);
    xdg_toplevel_send_configure(t->resource, w, h, &states);
    wl_array_release(&states);
    auto value = serial();
    t->serials.push_back(value);
    if (t->serials.size() > 256)
      t->serials.pop_front();
    xdg_surface_send_configure(t->xdg, value);
    if (getenv("ANVILDROID_RUNTIME_ID"))
      printf("x11_toplevel_configure=0x%lx size=%dx%d maximized=%d "
             "fullscreen=%d active=%d\n",
             t->window.window, w, h, int(t->maximized), int(t->fullscreen),
             int(t->active));
    t->initial = true;
  }
  void configure_native(Top *t) {
    // X11 measures the complete drawable, including transparent resize input.
    // xdg configure measures only the geometry accepted by the guest bridge.
    bool constrained = t->maximized || t->fullscreen;
    configure(
        t,
        std::max(1, t->window.width - (constrained ? 0 : t->normal_padding_w)),
        std::max(1,
                 t->window.height - (constrained ? 0 : t->normal_padding_h)));
  }
  void cancel_window_input(Top *t) {
    if(keyboard_focus==t)focus(nullptr);
    if(grab&&root(grab)==t)end_grab();
    if(active_constraint&&root(active_constraint->node)==t)release_constraint();
    if(pointer_focus&&root(pointer_focus)==t)pointer(nullptr,0,0);
    if(t->node)cancel_touches(t->node);
  }
  void close_top(Top *t) {
    if (t->node)
      cancel_touches(t->node);
    if (active_constraint && root(active_constraint->node) == t)
      release_constraint();
    if (t->popup)
      popup_dismiss(t);
    for (auto *child : tops)
      if (child->parent == t) {
        popup_dismiss(child);
        child->parent = nullptr;
      }
    if (keyboard_focus == t)
      focus(nullptr);
    if (pointer_focus && root(pointer_focus) == t)
      pointer(nullptr, 0, 0);
    if (grab && root(grab) == t)
      end_grab();
    if (t->ic) {
      XDestroyIC(t->ic);
      t->ic = nullptr;
    }
    platform.destroy(t->window);
    t->configured = false;
    t->configure_acked = false;
    t->initial = false;
  }
  static Top *root(Node *n) {
    if (!n)
      return nullptr;
    while (n->parent)
      n = static_cast<Node *>(n->parent);
    return n->top;
  }
  void move_resize(Top *t, wl_resource *seat, uint32_t serial, int direction) {
    // Only the button press delivered to this toplevel can start its WM grab.
    if (get<Server>(seat) != this || root(grab) != t ||
        pointer_buttons.find(1) == pointer_buttons.end() ||
        pointer_buttons.at(1) != serial || platform.testing())
      return;
    Window root_window, child;
    int x, y, wx, wy;
    unsigned mask;
    if (!XQueryPointer(platform.display, t->window.window, &root_window, &child,
                       &x, &y, &wx, &wy, &mask))
      return;
    // The WM consumes the release. End our implicit grab and send leave now;
    // its subsequent enter also tells the shared CSD bridge resize has ended.
    end_grab();
    pointer(nullptr, 0, 0);
    XUngrabPointer(platform.display, CurrentTime);
    platform.message(t->window, "_NET_WM_MOVERESIZE", x, y, direction, 1, 1);
  }
  void cursor() {
    auto *t = root(pointer_focus);
    if (!t)
      return;
    if (!cursor_node) {
      uint32_t transparent = 0;
      platform.cursor(t->window, &transparent, 1, 1, 0, 0);
      return;
    }
    auto *n = cursor_node;
    if (n->current.image && !n->cursor_pixels.empty())
      platform.cursor(t->window, n->cursor_pixels.data(),
                      n->current.image->width, n->current.image->height,
                      cursor_x, cursor_y);
  }
  void redraw() {
    for (auto *t : tops)
      if (t->dirty && t->resource && t->node) {
        t->dirty = false;
        auto *n = t->node;
        if (!t->initial) {
          configure(t);
          continue;
        }
        bool mapped = n->current.image && t->configured && !t->dismissed;
        if (!mapped) {
          if (t->window.mapped) {
            cancel_window_input(t);
            cancel_touches(n);
            if (active_constraint && root(active_constraint->node) == t)
              release_constraint();
          }
          platform.map(t->window, false);
          continue;
        }
        auto frame = WindowFrame::from(*n, t->geometry, t->frame_constrained);
        if (!t->frame_constrained) {
          t->normal_padding_w = frame.horizontal();
          t->normal_padding_h = frame.vertical();
        }
        if (frame != t->frame) {
          t->frame = frame;
          platform.frame_extents(t->window, frame);
          t->resize_window = true;
        }
        if (t->resize_window && t->geometry.w > 0) {
          platform.resize(t->window, int(frame.bounds.w), int(frame.bounds.h));
          t->resize_window = false;
        }
        platform.map(t->window, true);
        sync_constraints();
        if (t->popup) {
          popup_place(t);
          if (!popup_grabs.empty() && popup_grabs.back() == t &&
              !platform.testing()) {
            XGrabPointer(platform.display, t->window.window, True,
                         ButtonPressMask | ButtonReleaseMask |
                             PointerMotionMask,
                         GrabModeAsync, GrabModeAsync, None, None, CurrentTime);
            focus(t);
          }
        }
        platform.render(t->window, *n, frame.bounds);
      }
  }
  void pointer(Node *n, double x, double y) {
    if (active_constraint && active_constraint->locked && n)
      return;
    if (pointer_focus == n && pointer_x == x && pointer_y == y)
      return;
    pointer_x = x;
    pointer_y = y;
    if (pointer_focus != n) {
      for (auto *r : pointers)
        if (pointer_focus &&
            wl_resource_get_client(r) ==
                wl_resource_get_client(pointer_focus->resource))
          wl_pointer_send_leave(r, serial(), pointer_focus->resource);
      pointer_focus = n;
      for (auto *r : pointers)
        if (n &&
            wl_resource_get_client(r) == wl_resource_get_client(n->resource))
          wl_pointer_send_enter(r, input_serial(wl_resource_get_client(r)),
                                n->resource, wl_fixed_from_double(x),
                                wl_fixed_from_double(y));
    }
    if (n)
      for (auto *r : pointers)
        if (wl_resource_get_client(r) == wl_resource_get_client(n->resource)) {
          wl_pointer_send_motion(r, milliseconds(), wl_fixed_from_double(x),
                                 wl_fixed_from_double(y));
          if (wl_resource_get_version(r) >= 5)
            wl_pointer_send_frame(r);
        }
    sync_constraints();
  }
  bool text_active(Top *t) const {
    if (!t || keyboard_focus != t)
      return false;
    for (const auto &entry : texts)
      if (entry.second.enabled &&
          wl_resource_get_client(entry.first) == wl_resource_get_client(t->xdg))
        return true;
    return false;
  }
  void create_ime(Top *t);
  void ime_preedit(Top *t) {
    if (!text_active(t))
      return;
    const auto value = encode_utf8(t->preedit.text);
    for (auto &entry : texts)
      if (entry.second.enabled && wl_resource_get_client(entry.first) ==
                                      wl_resource_get_client(t->xdg)) {
        zwp_text_input_v3_send_preedit_string(entry.first, value.c_str(),
                                              t->preedit.byte_caret(),
                                              t->preedit.byte_caret());
        zwp_text_input_v3_send_done(entry.first, entry.second.serial);
      }
  }
  void ime_spot(Top *t) {
    if (!t || !t->ic || !text_active(t))
      return;
    for (auto &entry : texts)
      if (entry.second.enabled && wl_resource_get_client(entry.first) ==
                                      wl_resource_get_client(t->xdg)) {
        auto r = entry.second.cursor;
        XPoint point{
            short(std::clamp(r.x - t->frame.bounds.x, -32768., 32767.)),
            short(std::clamp(r.y + r.h - t->frame.bounds.y, -32768., 32767.))};
        auto list = XVaCreateNestedList(0, XNSpotLocation, &point, nullptr);
        XSetICValues(t->ic, XNPreeditAttributes, list, nullptr);
        XFree(list);
      }
  }
  void sync_ime() {
    for (auto *t : tops) {
      bool active = text_active(t);
      if (!t->ic || t->ic_focused == active)
        continue;
      if (active)
        XSetICFocus(t->ic);
      else {
        // Discard unfinished composition before returning to physical keys.
        char *pending = Xutf8ResetIC(t->ic);
        if (pending)
          XFree(pending);
        XUnsetICFocus(t->ic);
        t->preedit = {};
      }
      t->ic_focused = active;
      if (active)
        ime_spot(t);
    }
  }
  void focus(Top *t) {
    if (keyboard_focus == t)
      return;
    if (keyboard_focus) {
      auto *old = keyboard_focus;
      blocked_until_release.insert(pressed.begin(),pressed.end());
      if(grab&&root(grab)==old)end_grab();
      if(old->node)cancel_touches(old->node);
      old->active = false;
      for (auto *r : keyboards)
        if (wl_resource_get_client(r) == wl_resource_get_client(old->xdg)) {
          for (auto key : pressed)
            wl_keyboard_send_key(r, serial(), milliseconds(), key,
                                 WL_KEYBOARD_KEY_STATE_RELEASED);
          if (old->node)
            wl_keyboard_send_leave(r, serial(), old->node->resource);
        }
      for (auto &entry : texts)
        if (old->node && wl_resource_get_client(entry.first) ==
                             wl_resource_get_client(old->xdg)) {
          zwp_text_input_v3_send_leave(entry.first, old->node->resource);
          entry.second.enabled = entry.second.pending = false;
        }
      configure_native(old);
    }
    pressed.clear();
    keyboard_focus = t;
    if (t) {
      t->active = true;
      wl_array keys;
      wl_array_init(&keys);
      for (auto *r : keyboards)
        if (wl_resource_get_client(r) == wl_resource_get_client(t->xdg))
          wl_keyboard_send_enter(r, input_serial(wl_resource_get_client(r)),
                                 t->node->resource, &keys);
      wl_array_release(&keys);
      if (!platform.testing()) {
        // Focus can return after releases went to another X client. Reconcile
        // physical state rather than swallowing the first fresh press, while
        // preventing repeat of any key already held before this app focused.
        char physical[32]{};XQueryKeymap(platform.display,physical);
        for(unsigned code=8;code<256;++code)
          if(physical[code/8]&(1u<<(code%8)))blocked_until_release.insert(code-8);
          else blocked_until_release.erase(code-8);
        XkbStateRec state{};
        if (XkbGetState(platform.display, XkbUseCoreKbd, &state) == Success)
          modifiers(state.base_mods, state.latched_mods, state.locked_mods,
                    state.base_group, state.latched_group, state.locked_group);
      }
      for (auto &entry : texts)
        if (wl_resource_get_client(entry.first) ==
            wl_resource_get_client(t->xdg))
          zwp_text_input_v3_send_enter(entry.first, t->node->resource);
      configure_native(t);
    }
    sync_ime();
    sync_constraints();
    clipboard->focus();
  }
  void modifiers(unsigned depressed, unsigned latched, unsigned locked,
                 unsigned group, unsigned latched_group,
                 unsigned locked_group) {
    xkb_state_update_mask(key_state, depressed, latched, locked, group,
                          latched_group, locked_group);
    if (!keyboard_focus)
      return;
    for (auto *r : keyboards)
      if (wl_resource_get_client(r) ==
          wl_resource_get_client(keyboard_focus->xdg))
        wl_keyboard_send_modifiers(
            r, serial(),
            xkb_state_serialize_mods(key_state, XKB_STATE_MODS_DEPRESSED),
            xkb_state_serialize_mods(key_state, XKB_STATE_MODS_LATCHED),
            xkb_state_serialize_mods(key_state, XKB_STATE_MODS_LOCKED),
            xkb_state_serialize_layout(key_state, XKB_STATE_LAYOUT_EFFECTIVE));
  }
  void native_event(XEvent e) {
    if (clipboard->event(e))
      return;
    // Remote XIMs also use ClientMessage events addressed to their own
    // protocol windows. Filter those before looking up an Android window.
    if (e.type != KeyPress && e.type != KeyRelease && XFilterEvent(&e, None))
      return;
    if (e.type == MappingNotify) {
      XRefreshKeyboardMapping(&e.xmapping);
      return;
    }
    if (xkb_event && e.type == xkb_event) {
      auto *event = reinterpret_cast<XkbEvent *>(&e);
      if (event->any.xkb_type == XkbStateNotify) {
        auto &state = event->state;
        modifiers(state.base_mods, state.latched_mods, state.locked_mods,
                  state.base_group, state.latched_group, state.locked_group);
      }
      return;
    }
    Top *t = nullptr;
    for (auto *item : tops)
      if (item->window.window == e.xany.window) {
        t = item;
        break;
      }
    if (!t || !t->node)
      return;
    if (e.type == ClientMessage &&
        e.xclient.message_type == platform.protocols &&
        Atom(e.xclient.data.l[0]) == platform.wm_delete) {
      if (t->popup)
        popup_dismiss(t);
      else
        xdg_toplevel_send_close(t->resource);
      return;
    }
    if(e.type==UnmapNotify) {
      // Do not rely on a later FocusOut: guest unmap and WM minimization must
      // revoke contacts/timers even when no further native event arrives.
      cancel_window_input(t);return;
    }
    if (e.type == Expose)
      t->dirty = true;
    if (e.type == PropertyNotify &&
        e.xproperty.atom == platform.atom("_NET_WM_STATE")) {
      Atom type;
      int format;
      unsigned long count, left;
      unsigned char *bytes = nullptr;
      if (XGetWindowProperty(platform.display, t->window.window,
                             e.xproperty.atom, 0, 32, False, XA_ATOM, &type,
                             &format, &count, &left, &bytes) == Success) {
        bool maxh = false, maxv = false, full = false;
        auto *values = reinterpret_cast<Atom *>(bytes);
        if (bytes && type == XA_ATOM && format == 32)
          for (unsigned long i = 0; i < count; ++i) {
            maxh |=
                values[i] == platform.atom("_NET_WM_STATE_MAXIMIZED_HORZ");
            maxv |=
                values[i] == platform.atom("_NET_WM_STATE_MAXIMIZED_VERT");
            full |= values[i] == platform.atom("_NET_WM_STATE_FULLSCREEN");
          }
        bool changed =
            t->maximized != (maxh && maxv) || t->fullscreen != full;
        t->maximized = maxh && maxv;
        t->fullscreen = full;
        if (bytes)
          XFree(bytes);
        if (changed)
          configure_native(t);
      }
      return;
    }
    if (e.type == ConfigureNotify) {
      for (auto *p : tops)
        if (p->popup && p->parent == t && !p->dismissed) {
          if (p->positioner.reactive)
            popup_configure(p);
          else
            popup_place(p);
        }
      if (t->window.width != e.xconfigure.width ||
          t->window.height != e.xconfigure.height) {
        t->window.width = e.xconfigure.width;
        t->window.height = e.xconfigure.height;
        if (!t->popup)
          configure_native(t);
        t->dirty = true;
      }
      return;
    }
    if (e.type == FocusIn) {
      focus(t);
      return;
    }
    if (e.type == FocusOut) {
      if (e.xfocus.mode == NotifyGrab || e.xfocus.mode == NotifyUngrab)
        return;
      if (keyboard_focus == t)
        focus(nullptr);
      return;
    }
    if (e.type == LeaveNotify && !grab) {
      if (active_constraint)
        return;
      pointer(nullptr, 0, 0);
      return;
    }
    if (e.type == EnterNotify || e.type == MotionNotify ||
        e.type == ButtonPress || e.type == ButtonRelease) {
      if (e.type == EnterNotify && e.xcrossing.mode == NotifyGrab)
        return;
      // A WM may ignore our accepted-size request during its resize grab.
      // Ask the client to commit its final geometry once that grab ends.
      if (e.type == EnterNotify && e.xcrossing.mode == NotifyUngrab &&
          (t->window.width != t->frame.bounds.w ||
           t->window.height != t->frame.bounds.h))
        configure_native(t);
      unsigned state = e.type == EnterNotify    ? e.xcrossing.state
                       : e.type == MotionNotify ? e.xmotion.state
                                                : e.xbutton.state;
      if (grab && e.type != ButtonRelease &&
          !(state & (Button1Mask | Button2Mask | Button3Mask)))
        end_grab(); // Recover when another X11 grab consumed the release.
      if (e.type == ButtonRelease && e.xbutton.button < 4 &&
          !pointer_buttons.count(e.xbutton.button))
        return;
      int local_x = e.type == EnterNotify    ? e.xcrossing.x
                    : e.type == MotionNotify ? e.xmotion.x
                                             : e.xbutton.x;
      int local_y = e.type == EnterNotify    ? e.xcrossing.y
                    : e.type == MotionNotify ? e.xmotion.y
                                             : e.xbutton.y;
      auto *target = active_constraint ? root(active_constraint->node)
                     : grab            ? root(grab)
                                       : t;
      if (!target) {
        end_grab(); // The grabbed subsurface may have detached meanwhile.
        target = t;
      }
      if (target != t) {
        Window child;
        XTranslateCoordinates(platform.display, t->window.window,
                              target->window.window, local_x, local_y,
                              &local_x, &local_y, &child);
      }
      if (e.type == ButtonPress && popup_button(t, local_x, local_y))
        return;
      double x = local_x + target->frame.bounds.x,
             y = local_y + target->frame.bounds.y;
      Node *hit = nullptr;
      if (active_constraint || grab) {
        hit = active_constraint ? active_constraint->node : grab;
        for (auto *at = hit; at->parent;
             at = static_cast<Node *>(at->parent)) {
          x -= at->x;
          y -= at->y;
        }
      } else
        hit = static_cast<Node *>(t->node->hit(x, y));
      pointer(hit, x, y);
      if ((e.type == ButtonPress || e.type == ButtonRelease) && hit) {
        auto button = e.xbutton.button;
        auto button_serial =
            input_serial(wl_resource_get_client(hit->resource));
        if (button < 4 && e.type == ButtonPress) {
          if (popup_button(t, local_x, local_y))
            return;
          if (!grab)
            grab = hit;
          pointer_buttons[button] = button_serial;
          input_serials[wl_resource_get_client(hit->resource)] =
              button_serial;
          focus(root(hit));
        }
        for (auto *r : pointers)
          if (wl_resource_get_client(r) ==
              wl_resource_get_client(hit->resource)) {
            if (button >= 4 && button <= 7) {
              if (e.type == ButtonPress)
                wl_pointer_send_axis(
                    r, e.xbutton.time,
                    button <= 5 ? WL_POINTER_AXIS_VERTICAL_SCROLL
                                : WL_POINTER_AXIS_HORIZONTAL_SCROLL,
                    wl_fixed_from_int((button == 4 || button == 6) ? -10
                                                                   : 10));
            } else
              wl_pointer_send_button(r, button_serial, e.xbutton.time,
                                     button == 1   ? BTN_LEFT
                                     : button == 2 ? BTN_MIDDLE
                                                   : BTN_RIGHT,
                                     e.type == ButtonPress
                                         ? WL_POINTER_BUTTON_STATE_PRESSED
                                         : WL_POINTER_BUTTON_STATE_RELEASED);
            if (wl_resource_get_version(r) >= 5)
              wl_pointer_send_frame(r);
          }
        if (button < 4 && e.type == ButtonRelease) {
          pointer_buttons.erase(button);
          if (pointer_buttons.empty())
            end_grab();
        }
      }
      return;
    }
    if (e.type == KeyPress || e.type == KeyRelease) {
      // XIM commits may arrive as synthetic KeyPress events with keycode 0.
      // Only physical keys participate in held-key suppression.
      uint32_t physical_key=e.xkey.keycode>=8?e.xkey.keycode-8:0;
      if(e.xkey.keycode>=8&&e.xkey.keycode<=255&&blocked_until_release.count(physical_key)) {
        if(e.type==KeyRelease)blocked_until_release.erase(physical_key);
        return;
      }
      // Queued events for an old native window must not become input to the
      // guest's newly focused app. Held XKB repeat is client-side on Wayland.
      if(keyboard_focus!=t)return;
      if (e.type == KeyPress &&
          e.xkey.keycode == XKeysymToKeycode(platform.display, XK_Escape) &&
          !popup_grabs.empty()) {
        popup_dismiss(popup_grabs.back());
        return;
      }
      bool input = text_active(t);
      // Editors, shortcuts and games need physical keys even when a host
      // input method is active. XIM must only consume opted-in text input.
      if (input && XFilterEvent(&e, t->window.window)) {
        if (getenv("ANVIL_TEST_XIM_TRACE"))
          fprintf(stderr, "xim key filtered type=%d window=0x%lx\n", e.type,
                  t->window.window);
        return;
      }
      bool handled = false;
      if (input && e.type == KeyPress && t->ic) {
        // Leave room for Wayland's message header and trailing NUL within
        // its 4096-byte wire buffer, just as for surrounding/preedit text.
        std::vector<char> text(4001);
        KeySym symbol;
        Status status;
        int n = Xutf8LookupString(t->ic, &e.xkey, text.data(),
                                  text.size() - 1, &symbol, &status);
        if (getenv("ANVIL_TEST_XIM_TRACE"))
          fprintf(stderr, "xim lookup status=%d bytes=%d key=%u\n", status, n,
                  e.xkey.keycode);
        handled = status == XBufferOverflow;
        if (n > 0 && n <= 4000 &&
            (status == XLookupChars || status == XLookupBoth) &&
            static_cast<unsigned char>(text[0]) >= 32 &&
            !(e.xkey.state & (ControlMask | Mod1Mask | Mod4Mask))) {
          for (auto &entry : texts)
            if (entry.second.enabled && wl_resource_get_client(entry.first) ==
                                            wl_resource_get_client(t->xdg)) {
              text[n] = 0;
              zwp_text_input_v3_send_commit_string(entry.first, text.data());
              zwp_text_input_v3_send_done(entry.first, entry.second.serial);
              handled = true;
            }
        }
      }
      if (handled || e.xkey.keycode < 8 || e.xkey.keycode > 255)
        return;
      uint32_t key = e.xkey.keycode - 8;
      if(e.type==KeyPress&&pressed.count(key))return;
      if (e.type == KeyRelease && !pressed.count(key))
        return;
      if (e.type == KeyPress)
        pressed.insert(key);
      else
        pressed.erase(key);
      for (auto *r : keyboards)
        if (wl_resource_get_client(r) == wl_resource_get_client(t->xdg))
          wl_keyboard_send_key(
              r, input_serial(wl_resource_get_client(r)), e.xkey.time, key,
              e.type == KeyPress ? WL_KEYBOARD_KEY_STATE_PRESSED
                                 : WL_KEYBOARD_KEY_STATE_RELEASED);
    }
  }
  void events() {
    if (platform.testing())
      return;
    XEvent motion{};
    bool pending_motion = false;
    auto flush_motion = [&]() {
      if (pending_motion) {
        native_event(motion);
        pending_motion = false;
      }
    };
    // Bound each turn and coalesce absolute samples without moving clicks,
    // keys, focus transitions or touch events ahead of the preceding motion.
    // Bound motion traffic to Android's input injector while retaining every
    // relative sample and every button/key transition.
    for (unsigned budget = 64; budget && XPending(platform.display); --budget) {
      XEvent e;
      XNextEvent(platform.display, &e);
      if (getenv("ANVIL_TEST_XIM_TRACE") &&
          (e.type == KeyPress || e.type == KeyRelease))
        fprintf(stderr, "xim native event type=%d window=0x%lx\n", e.type,
                e.xany.window);
      if (e.type == GenericEvent) {
        if (e.xcookie.evtype != XI_RawMotion && e.xcookie.evtype != XI_Motion)
          flush_motion();
        if (xi_event(e))
          continue;
      }
      if (platform.xi_opcode && !e.xany.send_event &&
          (e.type == MotionNotify || e.type == ButtonPress ||
           e.type == ButtonRelease))
        continue;
      if (e.type == MotionNotify) {
        if (pending_motion && (motion.xmotion.window != e.xmotion.window ||
                               motion.xmotion.state != e.xmotion.state))
          flush_motion();
        motion = e;
        pending_motion = true;
      } else {
        flush_motion();
        native_event(e);
      }
    }
    flush_motion();
  }
  void globals();
};

#include "input.hpp"
#include "popup.hpp"
#include "xim-input.hpp"

static void node_destroy(wl_resource *r) {
  auto *n = get<Node>(r);
  auto *s = n->server;
  s->input_destroy(n);
  if (s->cursor_node == n)
    s->cursor_node = nullptr;
  if (s->pointer_focus == n)
    s->pointer(nullptr, 0, 0);
  if (s->grab == n)
    s->end_grab();
  if (n->top) {
    s->close_top(n->top);
    n->top->node = nullptr;
  }
  auto *parent = static_cast<Node *>(n->parent);
  n->detach();
  for (auto *child : n->children)
    if (child)
      child->parent = nullptr;
  if (n->sub)
    wl_resource_set_user_data(n->sub, nullptr);
  if (parent)
    s->invalidate(parent);
  auto *client = wl_resource_get_client(r);
  s->nodes.erase(r);
  bool other = false;
  for (auto &entry : s->nodes)
    if (wl_resource_get_client(entry.first) == client)
      other = true;
  if (!other) {
    s->input_serials.erase(client);
    s->input_history.erase(client);
  }
}
static void surface_create(wl_client *c, wl_resource *r, uint32_t id) {
  auto *s = get<Server>(r);
  auto n = std::make_unique<Node>();
  n->server = s;
  static const struct wl_surface_interface ops = {
      destroy_request,
      [](wl_client *, wl_resource *r, wl_resource *b, int32_t x, int32_t y) {
        auto *n = Server::node(r);
        if (x || y) {
          wl_resource_post_error(r, WL_SURFACE_ERROR_INVALID_OFFSET,
                                 "nonzero attach offset");
          return;
        }
        n->attach_pending = true;
        n->capture = {};
        n->release = {};
        if (b) {
          auto *s = n->server;
          auto found = s->buffers.find(b);
          if (found != s->buffers.end()) {
            auto buffer = found->second;
            n->capture = [s, buffer]() {
              return s->platform.dmabuf(
                  buffer->fd, buffer->width, buffer->height, buffer->stride,
                  buffer->offset, buffer->format, buffer->modifier);
            };
            n->release = [buffer]() {
              if (buffer->resource)
                wl_buffer_send_release(buffer->resource);
            };
          } else if (auto *shm = wl_shm_buffer_get(b)) {
            // Hold the wl_buffer resource with a destroy listener through
            // commit.
            struct Ref {
              wl_listener listener{};
              wl_resource *resource;
              Ref(wl_resource *r) : resource(r) {
                listener.notify = [](wl_listener *l, void *) {
                  reinterpret_cast<Ref *>(l)->resource = nullptr;
                };
                wl_resource_add_destroy_listener(r, &listener);
              }
              ~Ref() {
                if (resource)
                  wl_list_remove(&listener.link);
              }
            };
            auto ref = std::make_shared<Ref>(b);
            n->capture = [s, ref, n]() {
              if (!ref->resource)
                return std::shared_ptr<Image>{};
              auto *shm = wl_shm_buffer_get(ref->resource);
              wl_shm_buffer_begin_access(shm);
              {
                int w = wl_shm_buffer_get_width(shm),
                    h = wl_shm_buffer_get_height(shm);
                n->cursor_pixels.clear();
                if (w > 0 && h > 0 && w <= 256 && h <= 256) {
                  n->cursor_pixels.resize(size_t(w) * h);
                  for (int y = 0; y < h; ++y)
                    memcpy(n->cursor_pixels.data() + size_t(y) * w,
                           static_cast<unsigned char *>(
                               wl_shm_buffer_get_data(shm)) +
                               size_t(y) * wl_shm_buffer_get_stride(shm),
                           w * 4);
                }
              }
              std::shared_ptr<Image> out;
              try {
                out = s->platform.shm(
                    wl_shm_buffer_get_width(shm), wl_shm_buffer_get_height(shm),
                    wl_shm_buffer_get_stride(shm),
                    static_cast<unsigned char *>(wl_shm_buffer_get_data(shm)),
                    wl_shm_buffer_get_format(shm) == WL_SHM_FORMAT_ARGB8888);
              } catch (...) {
                wl_shm_buffer_end_access(shm);
                throw;
              }
              wl_shm_buffer_end_access(shm);
              return out;
            };
            n->release = [ref]() {
              if (ref->resource)
                wl_buffer_send_release(ref->resource);
            };
            (void)shm;
          } else
            wl_resource_post_error(r, WL_DISPLAY_ERROR_INVALID_OBJECT,
                                   "unsupported surface buffer");
        }
      },
      [](wl_client *, wl_resource *, int32_t, int32_t, int32_t, int32_t) {},
      [](wl_client *c, wl_resource *r, uint32_t id) {
        auto *cb = wl_resource_create(c, &wl_callback_interface, 1, id);
        struct Ref {
          wl_listener listener{};
          wl_resource *resource;
          Ref(wl_resource *r) : resource(r) {
            listener.notify = [](wl_listener *l, void *) {
              reinterpret_cast<Ref *>(l)->resource = nullptr;
            };
            wl_resource_add_destroy_listener(r, &listener);
          }
          ~Ref() {
            if (resource)
              wl_list_remove(&listener.link);
          }
        };
        auto ref = std::make_shared<Ref>(cb);
        Server::node(r)->callbacks.push_back([ref]() {
          if (ref->resource) {
            wl_callback_send_done(ref->resource, milliseconds());
            wl_resource_destroy(ref->resource);
          }
        });
      },
      [](wl_client *, wl_resource *, wl_resource *) {},
      [](wl_client *, wl_resource *r, wl_resource *region) {
        auto &state = Server::node(r)->pending;
        ++state.input_revision;
        state.default_input = !region;
        state.region.clear();
        if (region)
          state.region = *get<std::vector<std::pair<bool, Rect>>>(region);
      },
      [](wl_client *, wl_resource *r) {
        auto *n = Server::node(r);
        if (n->top) {
          auto *t = n->top;
          // WM state is a suggestion until the guest commits its accepted
          // frame. Keep drawing the previous transaction in the meantime.
          t->frame_constrained = t->maximized || t->fullscreen;
          if (t->geometry_pending)
            t->geometry = t->pending_geometry;
          // A configure suggests a size; the bridge may accept the same
          // aligned frame as before. Compare with the native window, not the
          // previous geometry, including state-only commits after an ack.
          if (t->geometry_pending || t->configure_acked)
            t->resize_window = true;
          t->geometry_pending = false;
          t->configure_acked = false;
        }
        n->server->input_cache(n);
        n->commit();
        if (n->server->cursor_node == n) {
          n->server->cursor();
          n->done();
        }
        if (n->top && !n->top->initial)
          n->server->configure(n->top);
      },
      [](wl_client *, wl_resource *r, int32_t transform) {
        if (transform < 0 || transform > 7)
          wl_resource_post_error(r, WL_SURFACE_ERROR_INVALID_TRANSFORM,
                                 "invalid transform");
        else
          Server::node(r)->pending.transform = transform;
      },
      [](wl_client *, wl_resource *r, int32_t scale) {
        if (scale <= 0 || scale > 16)
          wl_resource_post_error(r, WL_SURFACE_ERROR_INVALID_SCALE,
                                 "invalid scale");
        else
          Server::node(r)->pending.scale = scale;
      },
      [](wl_client *, wl_resource *, int32_t, int32_t, int32_t, int32_t) {},
      nullptr};
  n->resource = resource(c, &wl_surface_interface, wl_resource_get_version(r),
                         id, &ops, n.get(), node_destroy);
  auto *raw = n.get();
  n->changed = [s, raw]() {
    s->invalidate(raw);
    s->input_commit(raw);
  };
  s->nodes[n->resource] = std::move(n);
}
static void region_create(wl_client *c, wl_resource *, uint32_t id) {
  static const struct wl_region_interface ops = {
      destroy_request,
      [](wl_client *, wl_resource *r, int32_t x, int32_t y, int32_t w,
         int32_t h) {
        if (w > 0 && h > 0)
          get<std::vector<std::pair<bool, Rect>>>(r)->push_back(
              {true, {double(x), double(y), double(w), double(h)}});
      },
      [](wl_client *, wl_resource *r, int32_t x, int32_t y, int32_t w,
         int32_t h) {
        if (w > 0 && h > 0)
          get<std::vector<std::pair<bool, Rect>>>(r)->push_back(
              {false, {double(x), double(y), double(w), double(h)}});
      }};
  resource(c, &wl_region_interface, 1, id, &ops,
           new std::vector<std::pair<bool, Rect>>, [](wl_resource *r) {
             delete get<std::vector<std::pair<bool, Rect>>>(r);
           });
}
static void subsurface_create(wl_client *c, wl_resource *, uint32_t id,
                              wl_resource *surface, wl_resource *parent) {
  auto *n = Server::node(surface);
  auto *p = Server::node(parent);
  try {
    n->reparent(p);
  } catch (const std::exception &e) {
    wl_resource_post_error(surface, WL_SUBCOMPOSITOR_ERROR_BAD_SURFACE, "%s",
                           e.what());
    return;
  }
  static const struct wl_subsurface_interface ops = {
      destroy_request,
      [](wl_client *, wl_resource *r, int32_t x, int32_t y) {
        if (auto *n = get<Node>(r)) {
          n->next_x = x;
          n->next_y = y;
        }
      },
      [](wl_client *, wl_resource *r, wl_resource *sibling) {
        if (auto *n = get<Node>(r))
          try {
            n->stack(Server::node(sibling), true);
          } catch (const std::exception &e) {
            wl_resource_post_error(r, WL_SUBSURFACE_ERROR_BAD_SURFACE, "%s",
                                   e.what());
          }
      },
      [](wl_client *, wl_resource *r, wl_resource *sibling) {
        if (auto *n = get<Node>(r))
          try {
            n->stack(Server::node(sibling), false);
          } catch (const std::exception &e) {
            wl_resource_post_error(r, WL_SUBSURFACE_ERROR_BAD_SURFACE, "%s",
                                   e.what());
          }
      },
      [](wl_client *, wl_resource *r) {
        if (auto *n = get<Node>(r))
          n->sync = true;
      },
      [](wl_client *, wl_resource *r) {
        if (auto *n = get<Node>(r)) {
          n->sync = false;
          if (!n->synchronized())
            n->apply();
        }
      }};
  n->sub =
      resource(c, &wl_subsurface_interface, 1, id, &ops, n, [](wl_resource *r) {
        if (auto *n = get<Node>(r)) {
          auto *p = static_cast<Node *>(n->parent);
          n->detach();
          n->sub = nullptr;
          if (p)
            n->server->invalidate(p);
        }
      });
}

static void toplevel_create(wl_client *c, wl_resource *r, uint32_t id) {
  auto *t = get<Top>(r);
  auto *s = t->node->server;
  if (t->resource) {
    wl_resource_post_error(r, XDG_SURFACE_ERROR_ALREADY_CONSTRUCTED,
                           "toplevel already exists");
    return;
  }
  static const struct xdg_toplevel_interface ops = {
      destroy_request,
      [](wl_client *, wl_resource *, wl_resource *) {},
      [](wl_client *, wl_resource *r, const char *value) {
        auto *t = get<Top>(r);
        t->title = std::string(value).substr(0, 1024);
        t->identity_dirty = true;
      },
      [](wl_client *, wl_resource *r, const char *value) {
        get<Top>(r)->app = std::string(value).substr(0, 512);
        get<Top>(r)->identity_dirty = true;
      },
      [](wl_client *, wl_resource *, wl_resource *, uint32_t, int32_t,
         int32_t) {},
      [](wl_client *, wl_resource *r, wl_resource *seat, uint32_t serial) {
        auto *t = get<Top>(r);
        t->node->server->move_resize(t, seat, serial, 8);
      },
      [](wl_client *, wl_resource *r, wl_resource *seat, uint32_t serial,
         uint32_t edge) {
        auto *t = get<Top>(r);
        static const std::map<unsigned, int> directions = {
            {1, 1}, {2, 5}, {4, 7}, {5, 0}, {6, 6}, {8, 3}, {9, 2}, {10, 4}};
        auto found = directions.find(edge);
        if (found == directions.end())
          return;
        t->node->server->move_resize(t, seat, serial, found->second);
      },
      [](wl_client *, wl_resource *, int32_t, int32_t) {},
      [](wl_client *, wl_resource *, int32_t, int32_t) {},
      [](wl_client *, wl_resource *r) {
        auto *t = get<Top>(r);
        auto &s = t->node->server->platform;
        if (!t->window.mapped) {
          t->maximized = true;
          s.initial_state(t->window, t->maximized, t->fullscreen);
        } else if (!s.testing())
          s.message(t->window, "_NET_WM_STATE", 1,
                    s.atom("_NET_WM_STATE_MAXIMIZED_VERT"),
                    s.atom("_NET_WM_STATE_MAXIMIZED_HORZ"), 1);
      },
      [](wl_client *, wl_resource *r) {
        auto *t = get<Top>(r);
        auto &s = t->node->server->platform;
        if (!t->window.mapped) {
          t->maximized = false;
          s.initial_state(t->window, t->maximized, t->fullscreen);
        } else if (!s.testing())
          s.message(t->window, "_NET_WM_STATE", 0,
                    s.atom("_NET_WM_STATE_MAXIMIZED_VERT"),
                    s.atom("_NET_WM_STATE_MAXIMIZED_HORZ"), 1);
      },
      [](wl_client *, wl_resource *r, wl_resource *) {
        auto *t = get<Top>(r);
        auto &s = t->node->server->platform;
        if (!t->window.mapped) {
          t->fullscreen = true;
          s.initial_state(t->window, t->maximized, t->fullscreen);
        } else if (!s.testing())
          s.message(t->window, "_NET_WM_STATE", 1,
                    s.atom("_NET_WM_STATE_FULLSCREEN"), 0, 1);
      },
      [](wl_client *, wl_resource *r) {
        auto *t = get<Top>(r);
        auto &s = t->node->server->platform;
        if (!t->window.mapped) {
          t->fullscreen = false;
          s.initial_state(t->window, t->maximized, t->fullscreen);
        } else if (!s.testing())
          s.message(t->window, "_NET_WM_STATE", 0,
                    s.atom("_NET_WM_STATE_FULLSCREEN"), 0, 1);
      },
      [](wl_client *, wl_resource *r) {
        auto *t = get<Top>(r);
        auto &p = t->node->server->platform;
        if (!p.testing())
          XIconifyWindow(p.display, t->window.window, DefaultScreen(p.display));
      }};
  t->window = s->platform.create();
  t->resource = resource(c, &xdg_toplevel_interface, wl_resource_get_version(r),
                         id, &ops, t, [](wl_resource *r) {
                           auto *t = get<Top>(r);
                           if (t->node)
                             t->node->server->close_top(t);
                           t->resource = nullptr;
                         });
  if (wl_resource_get_version(t->resource) >= 5) {
    wl_array capabilities;
    wl_array_init(&capabilities);
    for (uint32_t value : {XDG_TOPLEVEL_WM_CAPABILITIES_WINDOW_MENU,
                           XDG_TOPLEVEL_WM_CAPABILITIES_MAXIMIZE,
                           XDG_TOPLEVEL_WM_CAPABILITIES_FULLSCREEN,
                           XDG_TOPLEVEL_WM_CAPABILITIES_MINIMIZE}) {
      auto *p = static_cast<uint32_t *>(wl_array_add(&capabilities, 4));
      if (p)
        *p = value;
    }
    xdg_toplevel_send_wm_capabilities(t->resource, &capabilities);
    wl_array_release(&capabilities);
  }
  s->create_ime(t);
  printf("x11_toplevel_created=0x%lx\n", t->window.window);
}
static void xdg_surface_create(wl_client *c, wl_resource *r, uint32_t id,
                               wl_resource *surface) {
  auto *n = Server::node(surface);
  if (n->has_role) {
    wl_resource_post_error(surface, XDG_WM_BASE_ERROR_ROLE,
                           "surface already has a role");
    return;
  }
  n->has_role = true;
  auto *t = new Top;
  t->server = n->server;
  t->node = n;
  n->top = t;
  n->server->tops.insert(t);
  static const struct xdg_surface_interface ops = {
      destroy_request, toplevel_create, popup_create,
      [](wl_client *, wl_resource *r, int32_t x, int32_t y, int32_t w,
         int32_t h) {
        auto *t = get<Top>(r);
        if (w <= 0 || h <= 0 || w > 8192 || h > 8192 ||
            uint64_t(w) * h > 16777216) {
          wl_resource_post_error(r, XDG_SURFACE_ERROR_INVALID_SIZE,
                                 "window geometry out of bounds");
          return;
        }
        t->pending_geometry = {double(x), double(y), double(w), double(h)};
        t->geometry_pending = true;
      },
      [](wl_client *, wl_resource *r, uint32_t serial) {
        auto *t = get<Top>(r);
        auto it = std::find(t->serials.begin(), t->serials.end(), serial);
        if (it == t->serials.end()) {
          wl_resource_post_error(r, XDG_SURFACE_ERROR_INVALID_SERIAL,
                                 "unknown configure serial");
          return;
        }
        t->serials.erase(t->serials.begin(), ++it);
        t->configured = true;
        t->configure_acked = true;
      }};
  t->xdg = resource(c, &xdg_surface_interface, wl_resource_get_version(r), id,
                    &ops, t, [](wl_resource *r) {
                      auto *t = get<Top>(r);
                      if (t->resource)
                        wl_resource_destroy(t->resource);
                      if (t->node) {
                        auto *s = t->node->server;
                        s->close_top(t);
                        t->node->top = nullptr;
                      }
                      t->server->tops.erase(t);
                      delete t;
                    });
}

static void viewport_create(wl_client *c, wl_resource *, uint32_t id,
                            wl_resource *surface) {
  if (Server::node(surface)->viewport) {
    wl_resource_post_error(surface, WP_VIEWPORTER_ERROR_VIEWPORT_EXISTS,
                           "surface already has a viewport");
    return;
  }
  Server::node(surface)->viewport = true;
  struct View {
    wl_listener listener{};
    Node *node;
    View(Node *n) : node(n) {
      listener.notify = [](wl_listener *l, void *) {
        reinterpret_cast<View *>(l)->node = nullptr;
      };
      wl_resource_add_destroy_listener(n->resource, &listener);
    }
    ~View() {
      if (node)
        wl_list_remove(&listener.link);
    }
  };
  static const struct wp_viewport_interface ops = {
      destroy_request,
      [](wl_client *, wl_resource *r, wl_fixed_t x, wl_fixed_t y, wl_fixed_t w,
         wl_fixed_t h) {
        auto *n = get<View>(r)->node;
        if (!n)
          return;
        auto dx = wl_fixed_to_double(x), dy = wl_fixed_to_double(y),
             dw = wl_fixed_to_double(w), dh = wl_fixed_to_double(h);
        if (!((x == -256 && y == -256 && w == -256 && h == -256) ||
              (dx >= 0 && dy >= 0 && dw > 0 && dh > 0))) {
          wl_resource_post_error(r, WP_VIEWPORT_ERROR_BAD_VALUE,
                                 "invalid source");
          return;
        }
        n->pending.source = {dx, dy, dw, dh};
      },
      [](wl_client *, wl_resource *r, int32_t w, int32_t h) {
        auto *n = get<View>(r)->node;
        if (!n)
          return;
        if (!((w == -1 && h == -1) ||
              (w > 0 && h > 0 && w <= 8192 && h <= 8192))) {
          wl_resource_post_error(r, WP_VIEWPORT_ERROR_BAD_VALUE,
                                 "invalid destination");
          return;
        }
        n->pending.dw = w;
        n->pending.dh = h;
      }};
  resource(c, &wp_viewport_interface, 1, id, &ops,
           new View(Server::node(surface)), [](wl_resource *r) {
             auto *v = get<View>(r);
             if (v->node) {
               v->node->viewport = false;
               v->node->pending.source = {-1, -1, -1, -1};
               v->node->pending.dw = v->node->pending.dh = -1;
             }
             delete v;
           });
}

struct Params {
  Server *server;
  std::shared_ptr<DmaBuffer> buffer = std::make_shared<DmaBuffer>();
  bool used = false;
};
static void params_create_buffer(wl_client *c, wl_resource *r, uint32_t id,
                                 int32_t w, int32_t h, uint32_t format,
                                 uint32_t flags) {
  auto *p = get<Params>(r);
  auto &b = p->buffer;
  bool supported = format == 0x34325241 || format == 0x34325258 ||
                   format == 0x34324241 || format == 0x34324258;
  if (p->used || b->fd < 0 || w < 1 || h < 1 || w > 8192 || h > 8192 ||
      uint64_t(w) * h > 16777216 || b->stride < w * 4 || b->stride > 131072 ||
      flags || !supported || b->modifier) {
    wl_resource_post_error(r, ZWP_LINUX_BUFFER_PARAMS_V1_ERROR_INVALID_FORMAT,
                           "unsupported/bad LINEAR RGB buffer");
    return;
  }
  auto size = lseek(b->fd, 0, SEEK_END);
  if (size < 0 ||
      uint64_t(b->offset) + uint64_t(h - 1) * b->stride + uint64_t(w) * 4 >
          uint64_t(size)) {
    wl_resource_post_error(r, ZWP_LINUX_BUFFER_PARAMS_V1_ERROR_OUT_OF_BOUNDS,
                           "DMA-BUF layout exceeds allocation");
    return;
  }
  p->used = true;
  b->width = w;
  b->height = h;
  b->format = format;
  static const struct wl_buffer_interface ops = {destroy_request};
  b->resource = resource(c, &wl_buffer_interface, 1, id, &ops, p->server,
                         [](wl_resource *r) {
                           auto *s = get<Server>(r);
                           auto it = s->buffers.find(r);
                           if (it != s->buffers.end()) {
                             it->second->resource = nullptr;
                             s->buffers.erase(it);
                           }
                         });
  p->server->buffers[b->resource] = b;
  if (!id)
    zwp_linux_buffer_params_v1_send_created(r, b->resource);
  // The single-use params object no longer owns the imported allocation.
  // Some HWC versions retain consumed params; keeping this reference pins
  // every old DMA-BUF and its fd even after wl_buffer destruction.
  p->buffer.reset();
}
static void params_create(wl_client *c, wl_resource *r, uint32_t id) {
  static const struct zwp_linux_buffer_params_v1_interface ops = {
      destroy_request,
      [](wl_client *, wl_resource *r, int32_t fd, uint32_t plane,
         uint32_t offset, uint32_t stride, uint32_t hi, uint32_t lo) {
        auto *p = get<Params>(r);
        if (p->used || plane || p->buffer->fd >= 0 || offset > INT32_MAX ||
            stride > INT32_MAX || hi || lo) {
          close(fd);
          wl_resource_post_error(r, ZWP_LINUX_BUFFER_PARAMS_V1_ERROR_PLANE_IDX,
                                 "invalid LINEAR plane");
          return;
        }
        p->buffer->fd = fd;
        p->buffer->offset = offset;
        p->buffer->stride = stride;
        p->buffer->modifier = (uint64_t(hi) << 32) | lo;
      },
      [](wl_client *c, wl_resource *r, int32_t w, int32_t h, uint32_t format,
         uint32_t flags) {
        params_create_buffer(c, r, 0, w, h, format, flags);
      },
      params_create_buffer};
  resource(c, &zwp_linux_buffer_params_v1_interface, wl_resource_get_version(r),
           id, &ops, new Params{get<Server>(r)},
           [](wl_resource *r) { delete get<Params>(r); });
}

static void seat_pointer(wl_client *c, wl_resource *r, uint32_t id) {
  auto *s = get<Server>(r);
  static const struct wl_pointer_interface ops = {
      [](wl_client *, wl_resource *r, uint32_t, wl_resource *surface, int32_t x,
         int32_t y) {
        auto *s = get<Server>(r);
        auto *n = surface ? Server::node(surface) : nullptr;
        if (n) {
          if (n->has_role && !n->cursor_role) {
            wl_resource_post_error(r, WL_POINTER_ERROR_ROLE,
                                   "cursor surface already has a role");
            return;
          }
          n->has_role = true;
          n->cursor_role = true;
        }
        s->cursor_node = n;
        s->cursor_x = x;
        s->cursor_y = y;
        s->cursor();
      },
      destroy_request};
  auto *p =
      resource(c, &wl_pointer_interface, wl_resource_get_version(r), id, &ops,
               s, [](wl_resource *r) { get<Server>(r)->pointer_destroy(r); });
  s->pointers.insert(p);
}
static void seat_keyboard(wl_client *c, wl_resource *r, uint32_t id) {
  auto *s = get<Server>(r);
  static const struct wl_keyboard_interface ops = {destroy_request};
  auto *k =
      resource(c, &wl_keyboard_interface, wl_resource_get_version(r), id, &ops,
               s, [](wl_resource *r) { get<Server>(r)->keyboards.erase(r); });
  s->keyboards.insert(k);
  wl_keyboard_send_keymap(k, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, s->keymap_fd,
                          s->keymap.size() + 1);
  if (wl_resource_get_version(k) >= 4)
    wl_keyboard_send_repeat_info(k, 25, 600);
}
static void text_create(wl_client *c, wl_resource *r, uint32_t id,
                        wl_resource *) {
  auto *s = get<Server>(r);
  static const struct zwp_text_input_v3_interface ops = {
      destroy_request,
      [](wl_client *, wl_resource *r) {
        get<Server>(r)->texts.at(r).pending = true;
      },
      [](wl_client *, wl_resource *r) {
        get<Server>(r)->texts.at(r).pending = false;
      },
      [](wl_client *, wl_resource *r, const char *value, int32_t cursor,
         int32_t anchor) {
        auto &t = get<Server>(r)->texts.at(r);
        std::string text(value);
        try {
          decode_utf8(text);
          if (text.size() > 4000 || cursor < 0 || anchor < 0 ||
              size_t(cursor) > text.size() || size_t(anchor) > text.size())
            return;
          decode_utf8(text.substr(0, cursor));
          decode_utf8(text.substr(0, anchor));
          t.pending_surrounding = text;
          t.pending_selection = cursor;
          t.pending_anchor = anchor;
        } catch (const std::exception &) {
        }
      },
      [](wl_client *, wl_resource *, uint32_t) {},
      [](wl_client *, wl_resource *r, uint32_t hint, uint32_t purpose) {
        auto &t = get<Server>(r)->texts.at(r);
        t.pending_hint = hint;
        t.pending_purpose = purpose;
      },
      [](wl_client *, wl_resource *r, int32_t x, int32_t y, int32_t w,
         int32_t h) {
        if (w < 0 || h < 0)
          return;
        get<Server>(r)->texts.at(r).pending_cursor = {double(x), double(y),
                                                      double(w), double(h)};
      },
      [](wl_client *, wl_resource *r) {
        auto &t = get<Server>(r)->texts.at(r);
        auto *s = get<Server>(r);
        t.enabled = t.pending && s->keyboard_focus &&
                    wl_resource_get_client(r) ==
                        wl_resource_get_client(s->keyboard_focus->xdg);
        t.cursor = t.pending_cursor;
        t.surrounding = t.pending_surrounding;
        t.selection = t.pending_selection;
        t.anchor = t.pending_anchor;
        t.hint = t.pending_hint;
        t.purpose = t.pending_purpose;
        ++t.serial;
        get<Server>(r)->sync_ime();
        get<Server>(r)->ime_spot(get<Server>(r)->keyboard_focus);
      }};
  auto *t = resource(c, &zwp_text_input_v3_interface, 1, id, &ops, s,
                     [](wl_resource *r) {
                       auto *s = get<Server>(r);
                       s->texts.erase(r);
                       s->sync_ime();
                     });
  s->texts.emplace(t, Text{});
  if (s->keyboard_focus && wl_resource_get_client(t) ==
                               wl_resource_get_client(s->keyboard_focus->xdg))
    zwp_text_input_v3_send_enter(t, s->keyboard_focus->node->resource);
}
void Server::globals() {
  input_globals();
  wl_global_create(display, &wl_compositor_interface, 4, this,
                   [](wl_client *c, void *data, uint32_t v, uint32_t id) {
                     static const struct wl_compositor_interface ops = {
                         surface_create, region_create};
                     resource(c, &wl_compositor_interface, std::min(v, 4u), id,
                              &ops, data);
                   });
  wl_global_create(display, &wl_subcompositor_interface, 1, this,
                   [](wl_client *c, void *data, uint32_t, uint32_t id) {
                     static const struct wl_subcompositor_interface ops = {
                         destroy_request, subsurface_create};
                     resource(c, &wl_subcompositor_interface, 1, id, &ops,
                              data);
                   });
  wl_global_create(display, &xdg_wm_base_interface, 6, this,
                   [](wl_client *c, void *data, uint32_t v, uint32_t id) {
                     static const struct xdg_wm_base_interface ops = {
                         destroy_request, positioner_create, xdg_surface_create,
                         [](wl_client *, wl_resource *, uint32_t) {}};
                     resource(c, &xdg_wm_base_interface, std::min(v, 6u), id,
                              &ops, data);
                   });
  wl_global_create(display, &wp_viewporter_interface, 1, this,
                   [](wl_client *c, void *data, uint32_t, uint32_t id) {
                     static const struct wp_viewporter_interface ops = {
                         destroy_request, viewport_create};
                     resource(c, &wp_viewporter_interface, 1, id, &ops, data);
                   });
  wl_global_create(display, &zwp_linux_dmabuf_v1_interface, 3, this,
                   [](wl_client *c, void *data, uint32_t v, uint32_t id) {
                     static const struct zwp_linux_dmabuf_v1_interface ops = {
                         destroy_request, params_create, nullptr, nullptr};
                     auto *r = resource(c, &zwp_linux_dmabuf_v1_interface,
                                        std::min(v, 3u), id, &ops, data);
                     for (uint32_t f : {0x34325241u, 0x34325258u, 0x34324241u,
                                        0x34324258u}) {
                       zwp_linux_dmabuf_v1_send_format(r, f);
                       if (v >= 3)
                         zwp_linux_dmabuf_v1_send_modifier(r, f, 0, 0);
                     }
                   });
  wl_global_create(
      display, &wl_output_interface, 3, this,
      [](wl_client *c, void *data, uint32_t v, uint32_t id) {
        static const struct wl_output_interface ops = {destroy_request};
        auto *r = resource(c, &wl_output_interface, std::min(v, 3u), id, &ops,
                           nullptr);
        wl_output_send_geometry(r, 0, 0, 508, 285, WL_OUTPUT_SUBPIXEL_UNKNOWN,
                                "AnvilDroid", "X11",
                                WL_OUTPUT_TRANSFORM_NORMAL);
        auto [width, height] =
            static_cast<Server *>(data)->platform.output_size();
        wl_output_send_mode(r,
                            WL_OUTPUT_MODE_CURRENT | WL_OUTPUT_MODE_PREFERRED,
                            width, height, 60000);
        if (v >= 2) {
          wl_output_send_scale(r, 1);
          wl_output_send_done(r);
        }
      });
  wl_global_create(
      display, &wl_seat_interface, 5, this,
      [](wl_client *c, void *data, uint32_t v, uint32_t id) {
        static const struct wl_seat_interface ops = {
            seat_pointer, seat_keyboard, seat_touch, destroy_request};
        auto *r =
            resource(c, &wl_seat_interface, std::min(v, 5u), id, &ops, data);
        wl_seat_send_capabilities(
            r, WL_SEAT_CAPABILITY_POINTER | WL_SEAT_CAPABILITY_KEYBOARD |
                   ((static_cast<Server *>(data)->platform.testing() ||
                     static_cast<Server *>(data)->platform.touch_available)
                        ? WL_SEAT_CAPABILITY_TOUCH
                        : 0));
        if (v >= 2)
          wl_seat_send_name(r, "anvildroid-x11");
      });
  wl_global_create(
      display, &zwp_text_input_manager_v3_interface, 1, this,
      [](wl_client *c, void *data, uint32_t, uint32_t id) {
        static const struct zwp_text_input_manager_v3_interface ops = {
            destroy_request, text_create};
        resource(c, &zwp_text_input_manager_v3_interface, 1, id, &ops, data);
      });
  wl_global_create(
      display, &xdg_activation_v1_interface, 1, this,
      [](wl_client *c, void *data, uint32_t, uint32_t id) {
        static const struct xdg_activation_v1_interface ops = {
            destroy_request,
            [](wl_client *c, wl_resource *r, uint32_t id) {
              static const struct xdg_activation_token_v1_interface token_ops =
                  {[](wl_client *, wl_resource *, uint32_t, wl_resource *) {},
                   [](wl_client *, wl_resource *, const char *) {},
                   [](wl_client *, wl_resource *, wl_resource *) {},
                   [](wl_client *, wl_resource *r) {
                     auto value = std::to_string(get<Server>(r)->serial());
                     xdg_activation_token_v1_send_done(r, value.c_str());
                   },
                   destroy_request};
              resource(c, &xdg_activation_token_v1_interface, 1, id, &token_ops,
                       get<Server>(r));
            },
            [](wl_client *, wl_resource *r, const char *token,
               wl_resource *surface) {
              // Only owned guest surfaces can be targeted. The X11 WM decides
              // focus.
              if (strlen(token) > 512)
                return;
              auto *t = Server::root(Server::node(surface));
              if (t && t->resource && t->configured)
                get<Server>(r)->platform.activate(t->window);
            }};
        resource(c, &xdg_activation_v1_interface, 1, id, &ops, data);
      });
}

int main(int argc, char **argv) {
  setvbuf(stdout, nullptr, _IOLBF, 0);
  setlocale(LC_CTYPE, "");
  try {
    // Block before EGL starts worker threads, so the event-loop signal sources
    // always wake the indefinite wait, including an endpoint with no clients.
    signal(SIGPIPE, SIG_IGN);
    sigset_t signals;
    sigemptyset(&signals);
    sigaddset(&signals, SIGTERM);
    sigaddset(&signals, SIGINT);
    ensure(!sigprocmask(SIG_BLOCK, &signals, nullptr),
           "Cannot prepare adapter stop signals");
    bool testing = false;
    std::string socket;
    for (int i = 1; i < argc; ++i) {
      std::string arg = argv[i];
      if (arg == "--test-headless")
        testing = true;
      else if (arg == "--socket" && i + 1 < argc)
        socket = argv[++i];
      else
        throw std::runtime_error("Unknown X11 adapter argument");
    }
    ensure(!socket.empty() && socket[0] == '/' && socket.size() < 100,
           "Private absolute socket path required");
    Server server(testing);
    ensure(!wl_display_add_socket(server.display, socket.c_str()),
           "Cannot bind private protocol socket");
    printf("x11_adapter_ready protocol=1 socket=%s headless=%d\n",
           socket.c_str(), int(testing));
    while (!stopping) {
      server.events();
      // Deliver input/configures before potentially blocking GPU work.
      wl_display_flush_clients(server.display);
      for (auto *t : server.tops)
        if (t->resource && t->node && t->identity_dirty) {
          server.platform.identity(t->window, t->app, t->title);
          t->identity_dirty = false;
        }
      server.redraw();
      wl_display_flush_clients(server.display);
      // EGL/Xlib round trips can read events into Xlib's queue, leaving its
      // socket empty. Drain those before entering an indefinite fd wait.
      int timeout = -1;
      if (!server.platform.testing()) {
        XFlush(server.platform.display);
        if (XEventsQueued(server.platform.display, QueuedAlready))
          timeout = 0;
      }
      auto status = wl_event_loop_dispatch(server.loop, timeout);
      if (status < 0 && errno == EINTR)
        continue;
      ensure(status >= 0, "Waydroid endpoint dispatch failed");
    }
    return 0;
  } catch (const std::exception &e) {
    fprintf(stderr, "FAIL X11 adapter: %s\n", e.what());
    return 1;
  }
}
