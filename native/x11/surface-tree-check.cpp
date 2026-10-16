/* SPDX-License-Identifier: MIT — scene transactions without a display/GPU. */
#include "surface-tree.hpp"
#include "window-frame.hpp"
#include <cassert>
#include <cstdio>
using namespace x11host;
static void attach(Surface &s, int w, int h) {
  s.attach_pending = true;
  s.capture = [=]() {
    auto i = std::make_shared<Image>();
    i->width = w;
    i->height = h;
    return i;
  };
}
int main() {
  Surface app, header, menu, other;
  int released = 0, done = 0;
  attach(app, 640, 480);
  app.commit();
  attach(other, 400, 300);
  other.commit();
  header.reparent(&app);
  header.next_y = -33;
  attach(header, 642, 33);
  header.release = [&]() { ++released; };
  header.callbacks.push_back([&]() { ++done; });
  header.commit();
  assert(released == 1 && !header.current.image && done == 0);
  header.pending.dw = 200;
  header.pending.dh = 100; // Uncommitted state must not leak.
  app.commit();
  assert(header.current.image && header.y == -33 && header.size().first == 642);
  app.done();
  assert(done == 1);
  header.commit();
  app.commit();
  assert(header.size().first == 200);
  double x = 10, y = -20;
  assert(app.hit(x, y) == &header && y == 13);
  x = 100;
  y = 100;
  assert(app.hit(x, y) == &app);
  assert(other.size().first == 400); // Independent app state.
  header.pending.dh = 33;
  header.commit();
  app.commit();
  menu.reparent(&app);
  menu.next_x = 10;
  menu.next_y = 10;
  attach(menu, 100, 80);
  menu.commit();
  app.commit();
  x = 20;
  y = 20;
  assert(app.hit(x, y) == &menu);
  menu.stack(&app, false);
  x = 20;
  y = 20;
  assert(app.hit(x, y) == &menu);
  app.commit();
  x = 20;
  y = 20;
  assert(app.hit(x, y) == &app);
  menu.stack(&header, true);
  app.commit();
  menu.pending.default_input = false;
  menu.pending.region = {{true, {0, 0, 100, 80}}, {false, {0, 0, 30, 30}}};
  menu.commit();
  app.commit();
  x = 20;
  y = 20;
  assert(app.hit(x, y) == &app);
  x = 60;
  y = 60;
  assert(app.hit(x, y) == &menu);
  menu.sync = false;
  attach(menu, 120, 90);
  menu.commit();
  assert(menu.size().first == 120);
  app.attach_pending = true;
  app.commit();
  assert(!app.current.image && other.current.image);
  try {
    app.reparent(&menu);
    assert(false);
  } catch (const std::runtime_error &) {
  }
  menu.stack(&header, false);
  header.detach();
  app.commit(); // Queued sibling destruction.
  menu.detach();
  assert(app.children.size() == 1);
  {
    Surface canvas, grip;
    attach(canvas, 1024, 1024);
    canvas.pending.default_input = false;
    canvas.pending.region = {{true, {100, 200, 320, 200}}};
    canvas.commit();
    Rect geometry{99, 167, 322, 234};
    grip.reparent(&canvas);
    grip.next_x = 95;
    grip.next_y = 167;
    attach(grip, 5, 238);
    grip.commit();
    canvas.commit();
    auto frame = WindowFrame::from(canvas, geometry, false);
    assert(frame.left == 4 && frame.right == 0 && frame.bottom == 4);
    assert(frame.bounds.x == 95 && frame.bounds.y == 167 &&
           frame.bounds.w == 326);
    // Ordered subtraction must not remove an earlier area that was readded.
    grip.pending.default_input = false;
    grip.pending.region = {{true, {0, 0, 5, 238}},
                           {false, {0, 0, 2, 238}},
                           {true, {0, 0, 1, 238}},
                           {false, {4, 0, 1, 238}}};
    grip.commit();
    canvas.commit();
    frame = WindowFrame::from(canvas, geometry, false);
    assert(frame.left == 4);
    grip.pending.region.push_back({false, {0, 0, 5, 238}});
    grip.commit();
    canvas.commit();
    frame = WindowFrame::from(canvas, geometry, false);
    assert(frame.horizontal() == 0 && frame.vertical() == 0);
    // Fullscreen/maximized windows keep exact workarea geometry, even if a
    // client leaves its old resize subsurfaces attached during transition.
    frame = WindowFrame::from(canvas, geometry, true);
    assert(frame.bounds.x == geometry.x && frame.bounds.w == geometry.w);
    assert(frame.horizontal() == 0 && frame.vertical() == 0);
    grip.detach();
  }
  puts("PASS synchronized commits, deferred stacking, callback/release "
       "ownership, CSD input bounds/regions and app isolation");
}
