/* SPDX-License-Identifier: MIT — XI2 touch/relative input and pointer
 * constraints. */
#pragma once
std::pair<double, double> Server::node_origin(Node *n) {
  double x = 0, y = 0;
  for (auto *at = n; at && at->parent; at = static_cast<Node *>(at->parent)) {
    x += at->x;
    y += at->y;
  }
  return {x, y};
}
void Server::cancel_touches(Node *node) {
  std::set<wl_client *> clients;
  for (auto &entry : touch_points)
    if (!node || entry.second.node == node ||
        (root(node) && root(entry.second.node) == root(node)))
      clients.insert(wl_resource_get_client(entry.second.node->resource));
  for (auto *r : touches)
    if (clients.count(wl_resource_get_client(r)))
      wl_touch_send_cancel(r);
  for (auto it = touch_points.begin(); it != touch_points.end();)
    if (clients.count(wl_resource_get_client(it->second.node->resource)))
      it = touch_points.erase(it);
    else
      ++it;
}
void Server::touch_event(int type, uint64_t contact, Top *t, double x, double y,
                         uint32_t time) {
  if (type == XI_TouchBegin) {
    if (touch_points.count(contact) || touch_points.size() >= 32 || !t ||
        !t->node)
      return;
    double sx = x + t->frame.bounds.x, sy = y + t->frame.bounds.y;
    auto *n = static_cast<Node *>(t->node->hit(sx, sy));
    if (!n)
      return;
    bool receiver = false;
    for (auto *r : touches)
      receiver |=
          wl_resource_get_client(r) == wl_resource_get_client(n->resource);
    if (!receiver)
      return;
    int id = 0;
    for (;; ++id) {
      bool used = false;
      for (auto &p : touch_points)
        used |= p.second.id == id;
      if (!used)
        break;
    }
    touch_points.emplace(contact, TouchPoint{n, id});
    focus(root(n));
    auto serial_value = input_serial(wl_resource_get_client(n->resource));
    for (auto *r : touches)
      if (wl_resource_get_client(r) == wl_resource_get_client(n->resource)) {
        wl_touch_send_down(r, serial_value, time, n->resource, id,
                           wl_fixed_from_double(sx), wl_fixed_from_double(sy));
        wl_touch_send_frame(r);
      }
    return;
  }
  auto it = touch_points.find(contact);
  if (it == touch_points.end())
    return;
  auto point = it->second;
  auto *owner = root(point.node);
  if (!owner) {
    cancel_touches(point.node);
    return;
  }
  if (t && t != owner && !platform.testing()) {
    Window child;
    int ox, oy;
    XTranslateCoordinates(platform.display, t->window.window,
                          owner->window.window, 0, 0, &ox, &oy, &child);
    x += ox;
    y += oy;
  }
  auto [ox, oy] = node_origin(point.node);
  x += owner->frame.bounds.x - ox;
  y += owner->frame.bounds.y - oy;
  for (auto *r : touches)
    if (wl_resource_get_client(r) ==
        wl_resource_get_client(point.node->resource)) {
      if (type == XI_TouchEnd)
        wl_touch_send_up(r, serial(), time, point.id);
      else
        wl_touch_send_motion(r, time, point.id, wl_fixed_from_double(x),
                             wl_fixed_from_double(y));
      wl_touch_send_frame(r);
    }
  if (type == XI_TouchEnd)
    touch_points.erase(it);
}
void Server::release_constraint(bool notify) {
  auto *c = active_constraint;
  if (!c)
    return;
  active_constraint = nullptr;
  c->active = false;
  if (!c->persistent)
    c->spent = true;
  if (!platform.testing()) {
    XUngrabPointer(platform.display, CurrentTime);
    if (confine_window) {
      XDestroyWindow(platform.display, confine_window);
      confine_window = 0;
    }
    if (c->locked && c->hint_valid && c->node && root(c->node) &&
        keyboard_focus == root(c->node)) {
      auto [ox, oy] = node_origin(c->node);
      auto *t = root(c->node);
      if (c->node->accepts(c->hint_x, c->hint_y))
        XWarpPointer(platform.display, None, t->window.window, 0, 0, 0, 0,
                     int(ox + c->hint_x - t->frame.bounds.x),
                     int(oy + c->hint_y - t->frame.bounds.y));
    }
    XFlush(platform.display);
  }
  if (notify && c->resource) {
    if (c->locked)
      zwp_locked_pointer_v1_send_unlocked(c->resource);
    else
      zwp_confined_pointer_v1_send_unconfined(c->resource);
  }
}
bool Server::constraint_changed(Constraint *c) {
  if (!c->node || !root(c->node))
    return true;
  auto [w, h] = c->node->size();
  auto [x, y] = node_origin(c->node);
  auto *t = root(c->node);
  return c->refresh || c->input_revision != c->node->current.input_revision ||
         c->native_width != w || c->native_height != h ||
         c->native_x != x - t->frame.bounds.x ||
         c->native_y != y - t->frame.bounds.y;
}
bool Server::constrain_native(Constraint *c) {
  if (!c->default_region && c->region.empty())
    return false;
  auto *n = c->node;
  auto *t = root(n);
  auto [w, h] = n->size();
  auto [ox, oy] = node_origin(n);
  c->refresh = false;
  c->input_revision = n->current.input_revision;
  c->native_width = w;
  c->native_height = h;
  c->native_x = ox - t->frame.bounds.x;
  c->native_y = oy - t->frame.bounds.y;
  if (platform.testing())
    return true;
  auto region_for = [&](bool full,
                        const std::vector<std::pair<bool, Rect>> &ops) {
    XserverRegion region = XFixesCreateRegion(platform.display, nullptr, 0);
    if (full) {
      XRectangle rect{0, 0, (unsigned short)w, (unsigned short)h};
      XFixesSetRegion(platform.display, region, &rect, 1);
    }
    for (auto &op : ops) {
      auto r = op.second;
      XRectangle rect{short(std::clamp(r.x, -32768., 32767.)),
                      short(std::clamp(r.y, -32768., 32767.)),
                      (unsigned short)std::clamp(r.w, 0., 65535.),
                      (unsigned short)std::clamp(r.h, 0., 65535.)};
      auto part = XFixesCreateRegion(platform.display, &rect, 1);
      if (op.first)
        XFixesUnionRegion(platform.display, region, region, part);
      else
        XFixesSubtractRegion(platform.display, region, region, part);
      XFixesDestroyRegion(platform.display, part);
    }
    return region;
  };
  auto input = region_for(n->current.default_input, n->current.region);
  auto requested = region_for(c->default_region, c->region);
  XFixesIntersectRegion(platform.display, input, input, requested);
  XFixesDestroyRegion(platform.display, requested);
  XRectangle extent{0, 0, (unsigned short)w, (unsigned short)h};
  auto bounds = XFixesCreateRegion(platform.display, &extent, 1);
  XFixesIntersectRegion(platform.display, input, input, bounds);
  XFixesDestroyRegion(platform.display, bounds);
  if (c->locked) {
    XRectangle at{short(pointer_x), short(pointer_y), 1, 1};
    auto dot = XFixesCreateRegion(platform.display, &at, 1);
    XFixesIntersectRegion(platform.display, input, input, dot);
    XFixesDestroyRegion(platform.display, dot);
  }
  int count = 0;
  auto *rects = XFixesFetchRegion(platform.display, input, &count);
  if (rects)
    XFree(rects);
  if (!count) {
    XFixesDestroyRegion(platform.display, input);
    return false;
  }
  auto previous = confine_window;
  confine_window = XCreateWindow(
      platform.display, t->window.window, int(ox - t->frame.bounds.x),
      int(oy - t->frame.bounds.y), unsigned(w), unsigned(h), 0, 0, InputOnly,
      CopyFromParent, 0, nullptr);
  XFixesSetWindowShapeRegion(platform.display, confine_window, ShapeBounding, 0,
                             0, input);
  XFixesDestroyRegion(platform.display, input);
  auto empty = XFixesCreateRegion(platform.display, nullptr, 0);
  XFixesSetWindowShapeRegion(platform.display, confine_window, ShapeInput, 0, 0,
                             empty);
  XFixesDestroyRegion(platform.display, empty);
  XMapWindow(platform.display, confine_window);
  int status = XGrabPointer(
      platform.display, t->window.window, True,
      PointerMotionMask | ButtonPressMask | ButtonReleaseMask, GrabModeAsync,
      GrabModeAsync, confine_window, None, CurrentTime);
  if (status != GrabSuccess) {
    XDestroyWindow(platform.display, confine_window);
    confine_window = previous;
    return false;
  }

  if (previous)
    XDestroyWindow(platform.display, previous);
  return true;
}
void Server::sync_constraints() {
  if (active_constraint) {
    if (!active_constraint->node || !root(active_constraint->node) ||
        !root(active_constraint->node)->node->current.image ||
        pointer_focus != active_constraint->node ||
        keyboard_focus != root(active_constraint->node) ||
        !root(active_constraint->node)->window.mapped)
      release_constraint();
    else {
      if (constraint_changed(active_constraint) &&
          !constrain_native(active_constraint))
        release_constraint();
      if (active_constraint)
        return;
    }
  }
  if (!popup_grabs.empty())
    return;
  for (auto &entry : constraints) {
    auto *c = entry.second.get();
    auto *n = c->node;
    auto *t = n ? root(n) : nullptr;
    if (!n || !c->pointer || c->spent || pointer_focus != n ||
        keyboard_focus != t || !t || !t->window.mapped ||
        !n->accepts(pointer_x, pointer_y))
      continue;
    bool inside = c->default_region;
    for (auto &op : c->region)
      if (op.second.contains(pointer_x, pointer_y))
        inside = op.first;
    if (!inside)
      continue;
    if (!constrain_native(c))
      continue;
    c->active = true;
    active_constraint = c;
    if (c->locked)
      zwp_locked_pointer_v1_send_locked(c->resource);
    else
      zwp_confined_pointer_v1_send_confined(c->resource);
    break;
  }
}
void Server::input_cache(Node *n) {
  for (auto &entry : constraints) {
    auto *c = entry.second.get();
    if (c->node != n)
      continue;
    if (c->region_pending) {
      c->cached_default = c->pending_default;
      c->cached_region = c->pending_region;
      c->region_cached = true;
      c->region_pending = false;
    }
    if (c->hint_pending) {
      c->cached_x = c->pending_x;
      c->cached_y = c->pending_y;
      c->hint_cached = true;
      c->hint_pending = false;
    }
  }
}
void Server::input_commit(Node *n) {
  for (auto &entry : constraints) {
    auto *c = entry.second.get();
    if (c->node != n)
      continue;
    if (c->region_cached) {
      c->refresh = true;
      c->default_region = c->cached_default;
      c->region = c->cached_region;
      c->region_cached = false;
    }
    if (c->hint_cached) {
      c->hint_x = c->cached_x;
      c->hint_y = c->cached_y;
      c->hint_valid = true;
      c->hint_cached = false;
    }
  }
  sync_constraints();
}
void Server::input_destroy(Node *n) {
  cancel_touches(n);
  for (auto &entry : constraints)
    if (entry.second->node == n) {
      if (entry.second->active)
        release_constraint();
      entry.second->node = nullptr;
    }
}
bool Server::xi_event(XEvent &e) {
  if (!platform.xi_opcode || e.xcookie.extension != platform.xi_opcode)
    return false;
  if (!XGetEventData(platform.display, &e.xcookie))
    return true;
  auto cookie = e.xcookie;
  if (cookie.evtype == XI_RawMotion) {
    auto *raw = static_cast<XIRawEvent *>(cookie.data);
    double accelerated[2]{}, unaccelerated[2]{};
    int index = 0;
    for (int i = 0; i < raw->valuators.mask_len * 8; ++i)
      if (XIMaskIsSet(raw->valuators.mask, i)) {
        if (i < 2) {
          accelerated[i] = raw->valuators.values[index];
          unaccelerated[i] = raw->raw_values[index];
        }
        ++index;
      }
    timespec now{};
    clock_gettime(CLOCK_MONOTONIC, &now);
    uint64_t us = uint64_t(now.tv_sec) * 1000000 + now.tv_nsec / 1000;
    if (pointer_focus)
      for (auto &entry : relatives)
        if (entry.second && wl_resource_get_client(entry.first) ==
                                wl_resource_get_client(pointer_focus->resource))
          zwp_relative_pointer_v1_send_relative_motion(
              entry.first, uint32_t(us >> 32), uint32_t(us),
              wl_fixed_from_double(accelerated[0]),
              wl_fixed_from_double(accelerated[1]),
              wl_fixed_from_double(unaccelerated[0]),
              wl_fixed_from_double(unaccelerated[1]));
    XFreeEventData(platform.display, &cookie);
    return true;
  }
  if (cookie.evtype == XI_HierarchyChanged) {
    cancel_touches();
    XFreeEventData(platform.display, &cookie);
    return true;
  }
  if (cookie.evtype == XI_TouchBegin || cookie.evtype == XI_TouchUpdate ||
      cookie.evtype == XI_TouchEnd) {
    auto *event = static_cast<XIDeviceEvent *>(cookie.data);
    Top *t = nullptr;
    for (auto *item : tops)
      if (item->window.window == event->event)
        t = item;
    if (t)
      touch_event(cookie.evtype,
                  (uint64_t(uint32_t(event->sourceid)) << 32) |
                      uint32_t(event->detail),
                  t, event->event_x, event->event_y, event->time);
    XFreeEventData(platform.display, &cookie);
    return true;
  }
  if (cookie.evtype == XI_Motion || cookie.evtype == XI_ButtonPress ||
      cookie.evtype == XI_ButtonRelease) {
    auto event = *static_cast<XIDeviceEvent *>(cookie.data);
    unsigned buttons = 0;
    for (int b = 1; b <= 3; ++b)
      if (event.buttons.mask_len * 8 > b && XIMaskIsSet(event.buttons.mask, b))
        buttons |= Button1Mask << (b - 1);
    XFreeEventData(platform.display, &cookie);
    if (event.flags & XIPointerEmulated)
      return true;
    e = {};
    e.xany.display = platform.display;
    e.xany.window = event.event;
    e.xany.send_event = True;
    if (cookie.evtype == XI_Motion) {
      e.type = MotionNotify;
      e.xmotion.x = int(event.event_x);
      e.xmotion.y = int(event.event_y);
      e.xmotion.time = event.time;
      e.xmotion.state = event.mods.effective;
      e.xmotion.state |= buttons;
    } else {
      e.type = cookie.evtype == XI_ButtonPress ? ButtonPress : ButtonRelease;
      e.xbutton.x = int(event.event_x);
      e.xbutton.y = int(event.event_y);
      e.xbutton.time = event.time;
      e.xbutton.button = event.detail;
      e.xbutton.state = event.mods.effective;
      e.xbutton.state |= buttons;
    }
    return false;
  }
  XFreeEventData(platform.display, &cookie);
  return true;
}
static void seat_touch(wl_client *c, wl_resource *r, uint32_t id) {
  auto *s = get<Server>(r);
  static const struct wl_touch_interface ops = {destroy_request};
  auto *t = resource(c, &wl_touch_interface, wl_resource_get_version(r), id,
                     &ops, s, [](wl_resource *r) {
                       auto *s = get<Server>(r);
                       s->touches.erase(r);
                     });
  s->touches.insert(t);
}
static void constraint_create(wl_client *c, wl_resource *r, uint32_t id,
                              wl_resource *surface, wl_resource *pointer,
                              wl_resource *region, uint32_t lifetime,
                              bool locked) {
  auto *s = get<Server>(r);
  auto *n = Server::node(surface);
  if (get<Server>(pointer) != s || lifetime < 1 || lifetime > 2) {
    wl_resource_post_error(r, WL_DISPLAY_ERROR_INVALID_METHOD,
                           "invalid pointer constraint");
    return;
  }
  for (auto &entry : s->constraints)
    if (entry.second->node == n) {
      wl_resource_post_error(
          r, ZWP_POINTER_CONSTRAINTS_V1_ERROR_ALREADY_CONSTRAINED,
          "surface already constrained");
      return;
    }
  auto state = std::make_unique<Constraint>();
  auto *con = state.get();
  con->server = s;
  con->node = n;
  con->pointer = pointer;
  con->locked = locked;
  con->persistent = lifetime == 2;
  con->default_region = !region;
  if (region)
    con->region = *get<std::vector<std::pair<bool, Rect>>>(region);
  auto set_region = +[](wl_client *, wl_resource *r, wl_resource *region) {
    auto *c = get<Constraint>(r);
    c->pending_default = !region;
    c->pending_region = region
                            ? *get<std::vector<std::pair<bool, Rect>>>(region)
                            : std::vector<std::pair<bool, Rect>>{};
    c->region_pending = true;
  };
  static const struct zwp_locked_pointer_v1_interface lock_ops = {
      destroy_request,
      [](wl_client *, wl_resource *r, wl_fixed_t x, wl_fixed_t y) {
        auto *c = get<Constraint>(r);
        c->pending_x = wl_fixed_to_double(x);
        c->pending_y = wl_fixed_to_double(y);
        c->hint_pending = true;
      },
      set_region};
  static const struct zwp_confined_pointer_v1_interface confine_ops = {
      destroy_request, set_region};
  con->resource = resource(c,
                           locked ? &zwp_locked_pointer_v1_interface
                                  : &zwp_confined_pointer_v1_interface,
                           1, id,
                           locked ? static_cast<const void *>(&lock_ops)
                                  : static_cast<const void *>(&confine_ops),
                           con, [](wl_resource *r) {
                             auto *c = get<Constraint>(r);
                             auto *s = c->server;
                             if (c->active)
                               s->release_constraint(false);
                             s->constraints.erase(r);
                           });
  s->constraints[con->resource] = std::move(state);
  s->sync_constraints();
}
void Server::pointer_destroy(wl_resource *r) {
  for (auto &entry : constraints)
    if (entry.second->pointer == r) {
      if (entry.second->active)
        release_constraint();
      entry.second->pointer = nullptr;
    }
  for (auto &entry : relatives)
    if (entry.second == r)
      entry.second = nullptr;
  pointers.erase(r);
}
void Server::input_globals() {
  if (!platform.testing() && !platform.constraints_available)
    return;
  wl_global_create(
      display, &zwp_pointer_constraints_v1_interface, 1, this,
      [](wl_client *c, void *data, uint32_t, uint32_t id) {
        static const struct zwp_pointer_constraints_v1_interface ops = {
            destroy_request,
            [](wl_client *c, wl_resource *r, uint32_t id, wl_resource *s,
               wl_resource *p, wl_resource *region, uint32_t lifetime) {
              constraint_create(c, r, id, s, p, region, lifetime, true);
            },
            [](wl_client *c, wl_resource *r, uint32_t id, wl_resource *s,
               wl_resource *p, wl_resource *region, uint32_t lifetime) {
              constraint_create(c, r, id, s, p, region, lifetime, false);
            }};
        resource(c, &zwp_pointer_constraints_v1_interface, 1, id, &ops, data);
      });
  wl_global_create(
      display, &zwp_relative_pointer_manager_v1_interface, 1, this,
      [](wl_client *c, void *data, uint32_t, uint32_t id) {
        static const struct zwp_relative_pointer_manager_v1_interface ops = {
            destroy_request, [](wl_client *c, wl_resource *r, uint32_t id,
                                wl_resource *pointer) {
              auto *s = get<Server>(r);
              if (get<Server>(pointer) != s)
                return;
              static const struct zwp_relative_pointer_v1_interface ops = {
                  destroy_request};
              auto *relative = resource(
                  c, &zwp_relative_pointer_v1_interface, 1, id, &ops, s,
                  [](wl_resource *r) { get<Server>(r)->relatives.erase(r); });
              s->relatives[relative] = pointer;
            }};
        resource(c, &zwp_relative_pointer_manager_v1_interface, 1, id, &ops,
                 data);
      });
}
