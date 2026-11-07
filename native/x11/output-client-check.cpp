/* SPDX-License-Identifier: MIT — per-monitor wl_output, hotplug,
 * enter/leave and desktop scale against the adapter on a private Xvfb.
 *
 *   output-client-check setup <dpi>     two RandR 1.5 monitors + Xft.dpi
 *   output-client-check reset           drop them again
 *   output-client-check <socket> <s120> client checks at scale s120/120
 */
#define main base_client_main
#include "host-client-check.cpp"
#undef main
#include "fractional-scale-client.h"
#include "output-scale.hpp"
#include <X11/extensions/Xrandr.h>
#include <fstream>
#include <functional>
#include <memory>
#include <set>
#include <sstream>

static const int monitor_w = 800, monitor_h = 1000;
static void set_dpi(Display *x, int dpi) {
  auto root = DefaultRootWindow(x);
  auto atom = XInternAtom(x, "RESOURCE_MANAGER", False);
  if (dpi <= 0)
    XDeleteProperty(x, root, atom);
  else {
    auto value = "Xft.dpi:\t" + std::to_string(dpi) + "\n";
    XChangeProperty(x, root, atom, XA_STRING, 8, PropModeReplace,
                    reinterpret_cast<const unsigned char *>(value.data()),
                    int(value.size()));
  }
  XSync(x, False);
}
static void set_monitor(Display *x, const char *name, int left, bool output) {
  auto root = DefaultRootWindow(x);
  auto *resources = XRRGetScreenResources(x, root);
  assert(resources && resources->noutput > 0);
  auto *m = XRRAllocateMonitor(x, output ? 1 : 0);
  m->name = XInternAtom(x, name, False);
  m->x = left;
  m->y = 0;
  m->width = monitor_w;
  m->height = monitor_h;
  m->mwidth = 211;
  m->mheight = 265;
  if (output)
    m->outputs[0] = resources->outputs[0];
  XRRSetMonitor(x, root, m);
  XRRFreeMonitors(m);
  XRRFreeScreenResources(resources);
  XSync(x, False);
}
static void delete_monitor(Display *x, const char *name) {
  XRRDeleteMonitor(x, DefaultRootWindow(x), XInternAtom(x, name, False));
  XSync(x, False);
}

struct Out {
  wl_output *output = nullptr;
  uint32_t global = 0;
  std::string name;
  int x = -1, y = -1, w = 0, h = 0, scale = 0, mm_w = 0, done = 0;
};
static std::vector<std::unique_ptr<Out>> outs;
static wp_fractional_scale_manager_v1 *fractional_manager;
static uint32_t preferred = 0;
static std::set<wl_output *> entered;
static const wl_output_listener out_events = {
    [](void *data, wl_output *, int32_t x, int32_t y, int32_t mm_w, int32_t,
       int32_t, const char *, const char *, int32_t) {
      auto *o = static_cast<Out *>(data);
      o->x = x;
      o->y = y;
      o->mm_w = mm_w;
    },
    [](void *data, wl_output *, uint32_t flags, int32_t w, int32_t h,
       int32_t) {
      if (flags & WL_OUTPUT_MODE_CURRENT) {
        static_cast<Out *>(data)->w = w;
        static_cast<Out *>(data)->h = h;
      }
    },
    [](void *data, wl_output *) { ++static_cast<Out *>(data)->done; },
    [](void *data, wl_output *, int32_t scale) {
      static_cast<Out *>(data)->scale = scale;
    },
    [](void *data, wl_output *, const char *name) {
      static_cast<Out *>(data)->name = name;
    },
    [](void *, wl_output *, const char *) {}};
static void output_registry(void *p, wl_registry *r, uint32_t name,
                            const char *iface, uint32_t version) {
  if (!strcmp(iface, "wl_output")) {
    assert(version == 4);
    auto o = std::make_unique<Out>();
    o->global = name;
    o->output = static_cast<wl_output *>(
        wl_registry_bind(r, name, &wl_output_interface, 4));
    wl_output_add_listener(o->output, &out_events, o.get());
    outs.push_back(std::move(o));
    return;
  }
  if (!strcmp(iface, "wp_fractional_scale_manager_v1"))
    fractional_manager = static_cast<wp_fractional_scale_manager_v1 *>(
        wl_registry_bind(r, name, &wp_fractional_scale_manager_v1_interface,
                         1));
  registry(p, r, name, iface, version);
}
static void output_remove(void *, wl_registry *, uint32_t name) {
  for (auto it = outs.begin(); it != outs.end(); ++it)
    if ((*it)->global == name) {
      entered.erase((*it)->output);
      wl_output_release((*it)->output);
      outs.erase(it);
      return;
    }
}
static Out *named(const std::string &name) {
  for (auto &o : outs)
    if (o->name == name)
      return o.get();
  return nullptr;
}
static std::set<std::string> entered_names() {
  std::set<std::string> names;
  for (auto &o : outs)
    if (entered.count(o->output))
      names.insert(o->name);
  return names;
}
static void settle(Client &c, const std::function<bool()> &done,
                   const char *what) {
  for (int i = 0; i < 1500; ++i) {
    roundtrip(c);
    if (done())
      return;
    usleep(2000);
  }
  fprintf(stderr, "timeout: %s\n", what);
  assert(false && "output event timeout");
}
static std::string host_log() {
  const char *path = getenv("ANVIL_TEST_HOST_LOG");
  assert(path);
  std::ifstream in(path);
  std::stringstream text;
  text << in.rdbuf();
  return text.str();
}

int main(int argc, char **argv) {
  setvbuf(stdout, nullptr, _IOLBF, 0);
  assert(getenv("ANVIL_TEST_X11"));
  auto *x = XOpenDisplay(nullptr);
  assert(x);
  if (argc == 3 && !strcmp(argv[1], "setup")) {
    // Replace any previous pair; deleting a missing monitor is harmless.
    auto previous = XSetErrorHandler([](Display *, XErrorEvent *) { return 0; });
    delete_monitor(x, "R");
    delete_monitor(x, "L");
    XSetErrorHandler(previous);
    set_monitor(x, "L", 0, true);
    set_monitor(x, "R", monitor_w, false);
    set_dpi(x, atoi(argv[2]));
    XCloseDisplay(x);
    return 0;
  }
  if (argc == 2 && !strcmp(argv[1], "reset")) {
    delete_monitor(x, "R");
    delete_monitor(x, "L");
    set_dpi(x, 0);
    XCloseDisplay(x);
    return 0;
  }
  assert(argc == 3);
  const int s120 = atoi(argv[2]);
  using x11host::to_logical_size;
  using x11host::to_physical_size;
  Client c{};
  c.display = wl_display_connect(argv[1]);
  assert(c.display);
  auto *registry_proxy = wl_display_get_registry(c.display);
  static const wl_registry_listener events = {output_registry, output_remove};
  wl_registry_add_listener(registry_proxy, &events, &c);
  roundtrip(c);
  roundtrip(c);
  // (A) one wl_output per RandR monitor, logical geometry, physical mode.
  assert(outs.size() == 2);
  auto *left = named("L"), *right = named("R");
  assert(left && right && left->done && right->done);
  assert(left->w == monitor_w && left->h == monitor_h && left->mm_w == 211);
  assert(right->w == monitor_w && right->h == monitor_h);
  assert(left->x == 0 && left->y == 0 &&
         right->x == int(std::lround(monitor_w * 120.0 / s120)));
  // (B) wl_output.scale = ceil(scale), fractional preferred = scale x 120.
  assert(left->scale == x11host::integer_scale(s120) &&
         right->scale == left->scale);
  assert(fractional_manager);
  puts("PASS two RandR monitors become two wl_outputs with names, logical "
       "geometry, physical modes and ceil(scale)");

  // First maximized configure: logical size of the monitor under the
  // pointer (where X11 WMs place a new window), not the whole screen.
  XWarpPointer(x, None, DefaultRootWindow(x), 0, 0, 0, 0, 1200, 500);
  XSync(x, False);
  auto max = app(c, "waydroid.output.maximized", true);
  roundtrip(c);
  roundtrip(c);
  auto size = sizes.at(max.top);
  assert(size.maximized && size.width == to_logical_size(monitor_w, s120) &&
         size.height == to_logical_size(monitor_h, s120));
  xdg_toplevel_destroy(max.top);
  xdg_surface_destroy(max.xdg);
  wl_surface_destroy(max.surface);
  puts("PASS first maximized configure carries the logical size of the "
       "pointer's monitor");

  auto *pointer = wl_seat_get_pointer(c.seat);
  wl_pointer_add_listener(pointer, &pointer_events, nullptr);
  auto a = app(c, "waydroid.output.check");
  static const wl_surface_listener surface_events = {
      [](void *, wl_surface *, wl_output *o) { entered.insert(o); },
      [](void *, wl_surface *, wl_output *o) { entered.erase(o); }, nullptr,
      nullptr};
  wl_surface_add_listener(a.surface, &surface_events, nullptr);
  auto *fractional = wp_fractional_scale_manager_v1_get_fractional_scale(
      fractional_manager, a.surface);
  static const wp_fractional_scale_v1_listener fractional_events = {
      [](void *, wp_fractional_scale_v1 *, uint32_t scale) {
        preferred = scale;
      }};
  wp_fractional_scale_v1_add_listener(fractional, &fractional_events, nullptr);
  roundtrip(c);
  roundtrip(c);
  assert(preferred == uint32_t(s120));
  // An Android-like viewported buffer: physical pixels, logical destination.
  const int lw = 300, lh = 200;
  auto *viewport = wp_viewporter_get_viewport(c.viewporter, a.surface);
  auto *content = buffer(c, to_physical_size(lw, s120),
                         to_physical_size(lh, s120), 0xff00ff00);
  wp_viewport_set_destination(viewport, lw, lh);
  xdg_surface_set_window_geometry(a.xdg, 0, 0, lw, lh);
  wl_surface_attach(a.surface, content, 0, 0);
  wl_surface_commit(a.surface);
  roundtrip(c);
  roundtrip(c);
  auto w = find_window(x, "waydroid.output.check");
  XWindowAttributes attributes{};
  settle(c, [&] {
    XGetWindowAttributes(x, w, &attributes);
    return attributes.map_state == IsViewable;
  }, "map");
  assert(attributes.width == to_physical_size(lw, s120) &&
         attributes.height == to_physical_size(lh, s120));
  XMoveWindow(x, w, 100, 100);
  XSync(x, False);
  settle(c, [] { return entered_names() == std::set<std::string>{"L"}; },
         "enter L");
  // Rendered at physical pixels: the far corner holds the content color.
  roundtrip(c);
  pixel(x, w, attributes.width - 2, attributes.height - 2, 0x00ff00);
  puts("PASS logical window and viewported buffer map to logical x scale "
       "X11 pixels on the output it entered");

  // Pointer pixels map back to logical surface coordinates.
  XWarpPointer(x, None, w, 0, 0, 0, 0, int(40 * s120 / 120.0),
               int(30 * s120 / 120.0));
  XSync(x, False);
  settle(c, [&] {
    return pointer_surface == a.surface && pointer_x == 40 && pointer_y == 30;
  }, "pointer");
  puts("PASS native pointer pixels reach the guest as logical coordinates");

  XMoveWindow(x, w, 1000, 100);
  XSync(x, False);
  settle(c, [] { return entered_names() == std::set<std::string>{"R"}; },
         "enter R, leave L");
  XMoveWindow(x, w, 700, 100);
  XSync(x, False);
  settle(c, [] {
    return entered_names() == std::set<std::string>{"L", "R"};
  }, "straddle");
  puts("PASS wl_surface.enter/leave follow the outputs a moved window "
       "intersects");

  // Hotplug: removal withdraws the global and leaves; re-adding binds anew.
  delete_monitor(x, "R");
  settle(c, [] { return outs.size() == 1 && named("L"); }, "remove R");
  assert(entered_names() == std::set<std::string>{"L"});
  set_monitor(x, "R", monitor_w, false);
  settle(c, [] {
    auto *r = named("R");
    return outs.size() == 2 && r && r->done;
  }, "add R");
  settle(c, [] {
    return entered_names() == std::set<std::string>{"L", "R"};
  }, "enter re-added R");
  // A fractional client is not sent a poisoning integer scale on hotplug.
  int expected = s120 % 120 ? 0 : x11host::integer_scale(s120);
  assert(named("R")->scale == expected && named("R")->w == monitor_w);
  puts("PASS monitor hotplug removes/adds wl_output globals with enter/leave");

  // A desktop scale change after latching is reported, not half-applied.
  set_dpi(x, 96);
  settle(c, [] {
    return host_log().find("restart_required=1") != std::string::npos;
  }, "latched dpi change");
  set_dpi(x, int(std::lround(s120 * 96 / 120.0)));
  puts("PASS desktop scale change after latch keeps the effective scale");

  wp_fractional_scale_v1_destroy(fractional);
  wp_viewport_destroy(viewport);
  xdg_toplevel_destroy(a.top);
  xdg_surface_destroy(a.xdg);
  wl_surface_destroy(a.surface);
  wl_buffer_destroy(content);
  wl_pointer_release(pointer);
  c.output = outs.front()->output; // Released by globals_destroy.
  for (size_t i = 1; i < outs.size(); ++i)
    wl_output_release(outs[i]->output);
  outs.clear();
  wp_fractional_scale_manager_v1_destroy(fractional_manager);
  globals_destroy(c, registry_proxy);
  roundtrip(c);
  wl_display_disconnect(c.display);
  XCloseDisplay(x);
  return 0;
}
