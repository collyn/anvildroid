/* SPDX-License-Identifier: MIT — native xdg popup role, placement and grabs. */
#pragma once
void Server::popup_place(Top *t) {
  if (platform.testing() || !t->parent || !t->parent->node || !t->window.window)
    return;
  auto *p = t->parent;
  Window child;
  int x, y;
  XTranslateCoordinates(platform.display, p->window.window,
                        DefaultRootWindow(platform.display), 0, 0, &x, &y,
                        &child);
  x += int(p->geometry.x - p->frame.bounds.x + t->popup_geometry.x -
           t->geometry.x + t->frame.bounds.x);
  y += int(p->geometry.y - p->frame.bounds.y + t->popup_geometry.y -
           t->geometry.y + t->frame.bounds.y);
  XMoveWindow(platform.display, t->window.window, x, y);
}
void Server::popup_configure(Top *t, uint32_t token, bool reposition) {
  if (!t->resource || !t->parent || t->dismissed)
    return;
  auto *p = t->parent;
  auto [w, h] = platform.output_size();
  double ox = p->geometry.x - p->frame.bounds.x,
         oy = p->geometry.y - p->frame.bounds.y;
  if (!platform.testing()) {
    Window child;
    int x, y;
    XTranslateCoordinates(platform.display, p->window.window,
                          DefaultRootWindow(platform.display), 0, 0, &x, &y,
                          &child);
    ox += x;
    oy += y;
  }
  Rect available{-ox, -oy, double(w), double(h)};
  if (!platform.testing()) {
    Atom type;
    int format;
    unsigned long count, left;
    unsigned char *bytes = nullptr;
    unsigned long desktop = 0;
    auto root_window = DefaultRootWindow(platform.display);
    if (XGetWindowProperty(platform.display, root_window,
                           platform.atom("_NET_CURRENT_DESKTOP"), 0, 1, False,
                           XA_CARDINAL, &type, &format, &count, &left,
                           &bytes) == Success &&
        bytes) {
      if (type == XA_CARDINAL && format == 32 && count)
        desktop = *reinterpret_cast<unsigned long *>(bytes);
      XFree(bytes);
      bytes = nullptr;
    }
    if (desktop < 1024 &&
        XGetWindowProperty(platform.display, root_window,
                           platform.atom("_NET_WORKAREA"), desktop * 4, 4,
                           False, XA_CARDINAL, &type, &format, &count, &left,
                           &bytes) == Success &&
        bytes) {
      if (type == XA_CARDINAL && format == 32 && count == 4) {
        auto *v = reinterpret_cast<unsigned long *>(bytes);
        if (v[2] && v[3] && v[2] <= 65535 && v[3] <= 65535)
          available = {double(v[0]) - ox, double(v[1]) - oy, double(v[2]),
                       double(v[3])};
      }
      XFree(bytes);
    }
  }
  t->popup_geometry = t->positioner.place(available);
  if (reposition && wl_resource_get_version(t->resource) >= 3)
    xdg_popup_send_repositioned(t->resource, token);
  auto r = t->popup_geometry;
  xdg_popup_send_configure(t->resource, int(r.x), int(r.y), int(r.w), int(r.h));
  auto value = serial();
  t->serials.push_back(value);
  if (t->serials.size() > 256)
    t->serials.pop_front();
  xdg_surface_send_configure(t->xdg, value);
  t->initial = true;
  popup_place(t);
}
void Server::popup_dismiss(Top *t) {
  if (!t->popup || t->dismissed)
    return;
  for (auto *child : tops)
    if (child->parent == t)
      popup_dismiss(child);
  t->dismissed = true;
  if (t->resource)
    xdg_popup_send_popup_done(t->resource);
  auto at = std::find(popup_grabs.begin(), popup_grabs.end(), t);
  if (at != popup_grabs.end()) {
    popup_grabs.erase(at, popup_grabs.end());
    if (!platform.testing())
      XUngrabPointer(platform.display, CurrentTime);
    if (!popup_grabs.empty() && !platform.testing()) {
      auto *last = popup_grabs.back();
      XGrabPointer(platform.display, last->window.window, True,
                   ButtonPressMask | ButtonReleaseMask | PointerMotionMask,
                   GrabModeAsync, GrabModeAsync, None, None, CurrentTime);
    }
    if (keyboard_focus == t)
      focus(popup_grabs.empty() ? t->parent : popup_grabs.back());
  }
  platform.map(t->window, false);
}
bool Server::popup_button(Top *target, int x, int y) {
  if (popup_grabs.empty())
    return false;
  for (auto *p : popup_grabs) {
    int px = x, py = y;
    if (!platform.testing() && target != p) {
      Window child;
      XTranslateCoordinates(platform.display, target->window.window,
                            p->window.window, x, y, &px, &py, &child);
    }
    if (px >= 0 && py >= 0 && px < p->window.width && py < p->window.height)
      return false;
  }
  popup_dismiss(popup_grabs.front());
  end_grab();
  pointer(nullptr, 0, 0);
  return true;
}
static void positioner_create(wl_client *c, wl_resource *r, uint32_t id) {
  static const struct xdg_positioner_interface ops = {
      destroy_request,
      [](wl_client *, wl_resource *r, int32_t w, int32_t h) {
        if (w <= 0 || h <= 0 || w > 8192 || h > 8192) {
          wl_resource_post_error(r, XDG_POSITIONER_ERROR_INVALID_INPUT,
                                 "invalid size");
          return;
        }
        auto *p = get<Positioner>(r);
        p->width = w;
        p->height = h;
      },
      [](wl_client *, wl_resource *r, int32_t x, int32_t y, int32_t w,
         int32_t h) {
        if (w <= 0 || h <= 0) {
          wl_resource_post_error(r, XDG_POSITIONER_ERROR_INVALID_INPUT,
                                 "invalid anchor rectangle");
          return;
        }
        get<Positioner>(r)->anchor_rect = {double(x), double(y), double(w),
                                           double(h)};
      },
      [](wl_client *, wl_resource *r, uint32_t v) {
        if (v > 8)
          wl_resource_post_error(r, XDG_POSITIONER_ERROR_INVALID_INPUT,
                                 "invalid anchor");
        else
          get<Positioner>(r)->anchor = v;
      },
      [](wl_client *, wl_resource *r, uint32_t v) {
        if (v > 8)
          wl_resource_post_error(r, XDG_POSITIONER_ERROR_INVALID_INPUT,
                                 "invalid gravity");
        else
          get<Positioner>(r)->gravity = v;
      },
      [](wl_client *, wl_resource *r, uint32_t v) {
        if (v > 63)
          wl_resource_post_error(r, XDG_POSITIONER_ERROR_INVALID_INPUT,
                                 "invalid constraint");
        else
          get<Positioner>(r)->constraint = v;
      },
      [](wl_client *, wl_resource *r, int32_t x, int32_t y) {
        auto *p = get<Positioner>(r);
        p->offset_x = x;
        p->offset_y = y;
      },
      [](wl_client *, wl_resource *r) { get<Positioner>(r)->reactive = true; },
      [](wl_client *, wl_resource *r, int32_t w, int32_t h) {
        if (w <= 0 || h <= 0)
          wl_resource_post_error(r, XDG_POSITIONER_ERROR_INVALID_INPUT,
                                 "invalid parent size");
        else {
          get<Positioner>(r)->parent_width = w;
          get<Positioner>(r)->parent_height = h;
        }
      },
      [](wl_client *, wl_resource *r, uint32_t v) {
        get<Positioner>(r)->parent_serial = v;
      }};
  resource(c, &xdg_positioner_interface, wl_resource_get_version(r), id, &ops,
           new Positioner, [](wl_resource *r) { delete get<Positioner>(r); });
}
static void popup_create(wl_client *c, wl_resource *r, uint32_t id,
                         wl_resource *parent, wl_resource *positioner) {
  auto *t = get<Top>(r);
  auto *s = t->server;
  if (t->resource) {
    wl_resource_post_error(r, XDG_SURFACE_ERROR_ALREADY_CONSTRUCTED,
                           "surface already constructed");
    return;
  }
  if (!parent || !get<Top>(parent)->resource || get<Top>(parent) == t) {
    wl_resource_post_error(r, XDG_WM_BASE_ERROR_INVALID_POPUP_PARENT,
                           "popup requires a live parent");
    return;
  }
  auto *p = get<Positioner>(positioner);
  if (!p->valid()) {
    wl_resource_post_error(r, XDG_WM_BASE_ERROR_INVALID_POSITIONER,
                           "incomplete positioner");
    return;
  }
  t->popup = true;
  t->parent = get<Top>(parent);
  t->positioner = *p;
  t->geometry = {0, 0, double(p->width), double(p->height)};
  t->resize_window = true;
  t->window = s->platform.create();
  if (!s->platform.testing()) {
    XSetWindowAttributes a{};
    a.override_redirect = True;
    XChangeWindowAttributes(s->platform.display, t->window.window,
                            CWOverrideRedirect, &a);
    XSetTransientForHint(s->platform.display, t->window.window,
                         t->parent->window.window);
    auto type = s->platform.atom("_NET_WM_WINDOW_TYPE_POPUP_MENU");
    XChangeProperty(s->platform.display, t->window.window,
                    s->platform.atom("_NET_WM_WINDOW_TYPE"), XA_ATOM, 32,
                    PropModeReplace, reinterpret_cast<unsigned char *>(&type),
                    1);
  }
  static const struct xdg_popup_interface ops = {
      [](wl_client *, wl_resource *r) {
        auto *t = get<Top>(r);
        auto &s = *t->server;
        if (!s.popup_grabs.empty() &&
            std::find(s.popup_grabs.begin(), s.popup_grabs.end(), t) !=
                s.popup_grabs.end() &&
            s.popup_grabs.back() != t) {
          wl_resource_post_error(r, XDG_WM_BASE_ERROR_NOT_THE_TOPMOST_POPUP,
                                 "destroy topmost popup first");
          return;
        }
        wl_resource_destroy(r);
      },
      [](wl_client *c, wl_resource *r, wl_resource *seat, uint32_t serial) {
        auto *t = get<Top>(r);
        auto &s = *t->server;
        if (get<Server>(seat) != &s || t->window.mapped ||
            !s.valid_input(c, serial) ||
            (!s.popup_grabs.empty() && s.popup_grabs.back() != t->parent)) {
          wl_resource_post_error(r, XDG_POPUP_ERROR_INVALID_GRAB,
                                 "invalid popup grab");
          return;
        }
        s.release_constraint();
        s.popup_grabs.push_back(t);
        // Grab the mapped parent until the popup's first committed buffer.
        if (!s.platform.testing())
          XGrabPointer(s.platform.display, t->parent->window.window, True,
                       ButtonPressMask | ButtonReleaseMask | PointerMotionMask,
                       GrabModeAsync, GrabModeAsync, None, None, CurrentTime);
      },
      [](wl_client *, wl_resource *r, wl_resource *p, uint32_t token) {
        auto *t = get<Top>(r);
        if (!get<Positioner>(p)->valid()) {
          wl_resource_post_error(r, XDG_WM_BASE_ERROR_INVALID_POSITIONER,
                                 "incomplete positioner");
          return;
        }
        t->positioner = *get<Positioner>(p);
        t->server->popup_configure(t, token, true);
      }};
  t->resource = resource(c, &xdg_popup_interface, wl_resource_get_version(r),
                         id, &ops, t, [](wl_resource *r) {
                           auto *t = get<Top>(r);
                           t->resource = nullptr;
                           t->server->close_top(t);
                         });
  s->create_ime(t);
  printf("x11_popup_created=0x%lx\n", t->window.window);
}
