/* SPDX-License-Identifier: MIT — per-monitor wl_output globals, hotplug,
 * wl_surface.enter/leave and the X11 desktop scale (wl_output.scale and
 * wp_fractional_scale_v1).
 *
 * Scale latching: Waydroid's composer reads the scale while calibrating its
 * first window, derives Android's display size and density from it, and
 * never recalibrates. The shared bridge mirrors that value. A later change
 * cannot be applied consistently to both, so the effective scale is fixed
 * once a client can observe it (first wl_output, fractional-scale or
 * toplevel); later Xft.dpi changes are logged and need a runtime restart.
 */
#pragma once
static void output_resource_destroy(wl_resource *r) {
  auto *o = get<Output>(r);
  if (!o)
    return;
  o->resources.erase(r);
  if (o->retired && !o->global && o->resources.empty()) {
    auto &list = o->server->outputs;
    for (auto it = list.begin(); it != list.end(); ++it)
      if (it->get() == o) {
        list.erase(it);
        break;
      }
  }
}
static void output_bind(wl_client *c, void *data, uint32_t v, uint32_t id) {
  auto *o = static_cast<Output *>(data);
  auto *s = o->server;
  s->latch_scale("wl_output");
  static const struct wl_output_interface ops = {destroy_request};
  auto *r = resource(c, &wl_output_interface, std::min(v, 4u), id, &ops, o,
                     output_resource_destroy);
  if (!r)
    return;
  o->resources.insert(r);
  s->send_output(o, r, true);
  for (auto *t : s->tops)
    if (t->node && t->entered.count(o) &&
        wl_resource_get_client(t->node->resource) == c)
      wl_surface_send_enter(t->node->resource, r);
}
void Server::send_output(Output *o, wl_resource *r, bool bind) {
  auto &m = o->monitor;
  int version = wl_resource_get_version(r);
  // Geometry is in the logical global space; the mode stays physical, as on
  // a scaling Wayland compositor.
  wl_output_send_geometry(r, int(std::lround(platform.logical(m.x))),
                          int(std::lround(platform.logical(m.y))), m.mm_width,
                          m.mm_height, WL_OUTPUT_SUBPIXEL_UNKNOWN, "AnvilDroid",
                          "X11", WL_OUTPUT_TRANSFORM_NORMAL);
  // The composer reads this once at startup to set Android's vsync; with
  // several outputs it keeps the fastest, as with any Wayland compositor.
  wl_output_send_mode(r, WL_OUTPUT_MODE_CURRENT | WL_OUTPUT_MODE_PREFERRED,
                      m.width, m.height, advertised_refresh_mhz(m.refresh_mhz));
  // The scale cannot change after latching, so it is sent once per bind.
  // Waydroid's composer replaces its (fractional) scale with any later
  // integer wl_output.scale, e.g. from a hotplugged output. A client that
  // already received a fractional preferred_scale therefore gets no integer
  // scale for fractional desktops (the protocol default applies; fractional
  // clients use preferred_scale).
  if (version >= 2 && bind &&
      !(platform.scale120 % scale_unit &&
        fractional_clients.count(wl_resource_get_client(r))))
    wl_output_send_scale(r, integer_scale(platform.scale120));
  if (version >= 4) {
    if (bind)
      wl_output_send_name(r, m.name.c_str());
    wl_output_send_description(r, ("AnvilDroid X11 " + m.name).c_str());
  }
  if (version >= 2)
    wl_output_send_done(r);
}
void Server::output_globals() { sync_outputs(true); }
void Server::sync_outputs(bool initial) {
  outputs_dirty = false;
  auto monitors = platform.monitors();
  std::vector<bool> matched(monitors.size());
  bool retired = false;
  for (auto &o : outputs) {
    if (o->retired)
      continue;
    size_t i = 0;
    while (i < monitors.size() &&
           (matched[i] || monitors[i].name != o->monitor.name))
      ++i;
    if (i == monitors.size()) {
      printf("x11_output_removed name=%s\n", o->monitor.name.c_str());
      o->retired = retired = true;
      for (auto *t : tops)
        if (t->entered.count(o.get())) {
          if (t->node)
            for (auto *r : o->resources)
              if (wl_resource_get_client(r) ==
                  wl_resource_get_client(t->node->resource))
                wl_surface_send_leave(t->node->resource, r);
          t->entered.erase(o.get());
        }
      wl_global_remove(o->global);
      continue;
    }
    matched[i] = true;
    if (monitors[i] == o->monitor)
      continue;
    o->monitor = monitors[i];
    auto &m = o->monitor;
    printf("x11_output_changed name=%s geometry=%dx%d+%d+%d mm=%dx%d "
           "refresh_mhz=%d\n",
           m.name.c_str(), m.width, m.height, m.x, m.y, m.mm_width,
           m.mm_height, m.refresh_mhz);
    for (auto *r : o->resources)
      send_output(o.get(), r, false);
  }
  for (size_t i = 0; i < monitors.size(); ++i) {
    if (matched[i])
      continue;
    auto o = std::make_unique<Output>();
    o->server = this;
    o->monitor = monitors[i];
    o->global = wl_global_create(display, &wl_output_interface, 4, o.get(),
                                 output_bind);
    ensure(o->global, "Cannot create wl_output");
    auto &m = o->monitor;
    printf("x11_output_added name=%s geometry=%dx%d+%d+%d mm=%dx%d "
           "refresh_mhz=%d advertised=%d primary=%d initial=%d\n",
           m.name.c_str(), m.width, m.height, m.x, m.y, m.mm_width,
           m.mm_height, m.refresh_mhz, advertised_refresh_mhz(m.refresh_mhz),
           int(m.primary), int(initial));
    outputs.push_back(std::move(o));
  }
  // Destroy withdrawn globals once clients had time to see global_remove.
  if (retired)
    wl_event_source_timer_update(retire_timer, 3000);
  for (auto *t : tops)
    sync_entered(t);
  for (auto *t : tops)
    if (t->popup && t->parent && !t->dismissed && t->resource &&
        t->positioner.reactive)
      popup_configure(t);
}
void Server::retire_outputs() {
  for (auto it = outputs.begin(); it != outputs.end();) {
    auto &o = *it;
    if (o->retired && o->global) {
      wl_global_destroy(o->global);
      o->global = nullptr;
    }
    if (o->retired && o->resources.empty())
      it = outputs.erase(it);
    else
      ++it;
  }
}
void Server::latch_scale(const char *by) {
  if (scale_latched)
    return;
  scale_latched = true;
  printf("x11_output_scale_latched=%d/120 by=%s\n", platform.scale120, by);
}
void Server::sync_scale() {
  scale_dirty = false;
  int now = platform.desktop_scale120();
  if (now == desktop_scale)
    return;
  desktop_scale = now;
  bool apply = !scale_latched;
  if (apply) {
    // No client has observed the old value: apply as if started now.
    platform.scale120 = now;
    ensure_fractional();
  }
  printf("x11_desktop_scale=%d/120 xft_dpi=%g effective=%d/120 "
         "restart_required=%d\n",
         now, platform.resource_dpi(), platform.scale120, int(!apply));
}
void Server::fractional_client(wl_client *c) {
  if (fractional_clients.count(c))
    return;
  struct Gone {
    wl_listener listener{};
    Server *server;
  };
  auto *gone = new Gone;
  gone->server = this;
  gone->listener.notify = [](wl_listener *l, void *data) {
    auto *g = reinterpret_cast<Gone *>(l);
    g->server->fractional_clients.erase(static_cast<wl_client *>(data));
    wl_list_remove(&l->link);
    delete g;
  };
  wl_client_add_destroy_listener(c, &gone->listener);
  fractional_clients.insert(c);
}
static void fractional_get(wl_client *c, wl_resource *r, uint32_t id,
                           wl_resource *surface) {
  auto *s = get<Server>(r);
  auto *n = Server::node(surface);
  if (n->fractional) {
    wl_resource_post_error(
        r, WP_FRACTIONAL_SCALE_MANAGER_V1_ERROR_FRACTIONAL_SCALE_EXISTS,
        "surface already has a fractional scale");
    return;
  }
  struct Scale {
    wl_listener listener{};
    Node *node;
    Scale(Node *n) : node(n) {
      listener.notify = [](wl_listener *l, void *) {
        reinterpret_cast<Scale *>(l)->node = nullptr;
      };
      wl_resource_add_destroy_listener(n->resource, &listener);
    }
    ~Scale() {
      if (node) {
        node->fractional = false;
        wl_list_remove(&listener.link);
      }
    }
  };
  static const struct wp_fractional_scale_v1_interface ops = {
      destroy_request};
  auto *f = resource(c, &wp_fractional_scale_v1_interface, 1, id, &ops,
                     new Scale(n),
                     [](wl_resource *r) { delete get<Scale>(r); });
  if (!f)
    return;
  n->fractional = true;
  // One global scale (Xft.dpi is screen-wide); every surface prefers it.
  wp_fractional_scale_v1_send_preferred_scale(f, uint32_t(s->platform.scale120));
  s->fractional_client(c);
}
void Server::ensure_fractional() {
  // Only a scaled desktop advertises it, keeping 1x registries unchanged.
  if (fractional_global || platform.scale120 == scale_unit)
    return;
  fractional_global = wl_global_create(
      display, &wp_fractional_scale_manager_v1_interface, 1, this,
      [](wl_client *c, void *data, uint32_t, uint32_t id) {
        auto *s = static_cast<Server *>(data);
        s->latch_scale("fractional-scale");
        static const struct wp_fractional_scale_manager_v1_interface ops = {
            destroy_request, fractional_get};
        resource(c, &wp_fractional_scale_manager_v1_interface, 1, id, &ops,
                 data);
      });
  ensure(fractional_global, "Cannot create fractional scale manager");
}
void Server::track_position(Top *t, const XConfigureEvent *event) {
  if (platform.testing() || !t->window.window)
    return;
  int x = 0, y = 0;
  if (event && event->send_event) {
    // ICCCM synthetic ConfigureNotify carries root coordinates.
    x = event->x;
    y = event->y;
  } else {
    Window child;
    if (!XTranslateCoordinates(platform.display, t->window.window,
                               DefaultRootWindow(platform.display), 0, 0, &x,
                               &y, &child))
      return;
  }
  bool changed = !t->placed || t->root_x != x || t->root_y != y;
  t->root_x = x;
  t->root_y = y;
  t->placed = true;
  if (!t->popup && !t->maximized && !t->fullscreen && t->window.mapped &&
      (!t->has_normal || t->normal_x != x || t->normal_y != y)) {
    t->previous_normal_x = t->has_normal ? t->normal_x : x;
    t->previous_normal_y = t->has_normal ? t->normal_y : y;
    t->normal_x = x;
    t->normal_y = y;
    t->has_normal = true;
  }
  if (changed)
    sync_entered(t);
}
void Server::sync_entered(Top *t) {
  std::set<Output *> now;
  bool visible = t->resource && t->node && t->window.window &&
                 t->window.mapped && !t->dismissed &&
                 (platform.testing() || t->placed);
  if (visible)
    for (auto &o : outputs)
      if (!o->retired && o->monitor.overlap(t->root_x, t->root_y,
                                            t->window.pw, t->window.ph) > 0)
        now.insert(o.get());
  if (now == t->entered)
    return;
  auto *client = t->node ? wl_resource_get_client(t->node->resource) : nullptr;
  for (auto *o : t->entered)
    if (!now.count(o) && t->node)
      for (auto *r : o->resources)
        if (wl_resource_get_client(r) == client)
          wl_surface_send_leave(t->node->resource, r);
  for (auto *o : now)
    if (!t->entered.count(o))
      for (auto *r : o->resources)
        if (wl_resource_get_client(r) == client)
          wl_surface_send_enter(t->node->resource, r);
  t->entered = now;
  std::string names;
  for (auto &o : outputs)
    if (now.count(o.get()))
      names += (names.empty() ? "" : ",") + o->monitor.name;
  printf("x11_surface_outputs=0x%lx outputs=%s\n", t->window.window,
         names.c_str());
}
Monitor Server::monitor_for(Top *t) {
  std::vector<Monitor> live;
  for (auto &o : outputs)
    if (!o->retired)
      live.push_back(o->monitor);
  if (live.empty())
    live = platform.monitors();
  if (t && t->placed)
    if (auto *m = monitor_for_rect(live, t->root_x, t->root_y, t->window.pw,
                                   t->window.ph))
      return *m;
  bool pointer = false;
  int x = 0, y = 0;
  if (!platform.testing()) {
    Window root, child;
    int wx, wy;
    unsigned mask;
    pointer = XQueryPointer(platform.display,
                            DefaultRootWindow(platform.display), &root, &child,
                            &x, &y, &wx, &wy, &mask);
  }
  return *monitor_for_point(live, pointer, x, y);
}
