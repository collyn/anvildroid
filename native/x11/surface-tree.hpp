/* SPDX-License-Identifier: MIT — protocol-neutral committed surface state. */
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <functional>
#include <memory>
#include <stdexcept>
#include <vector>

namespace x11host {
struct Rect {
  double x = 0, y = 0, w = 0, h = 0;
  bool contains(double px, double py) const {
    return px >= x && py >= y && px < x + w && py < y + h;
  }
};
struct Image {
  unsigned texture = 0;
  int width = 0, height = 0;
  virtual ~Image() = default;
};
struct Surface {
  struct State {
    std::shared_ptr<Image> image;
    int scale = 1, transform = 0, dw = -1, dh = -1;
    Rect source{-1, -1, -1, -1};
    bool default_input = true;
    uint64_t input_revision = 0;
    // Region subtraction is preserved as ordered operations.
    std::vector<std::pair<bool, Rect>> region;
  } current, pending, cached;
  bool attach_pending = false, has_cached = false, sync = true;
  bool has_role = false;
  Surface *parent = nullptr;
  std::vector<Surface *>
      children; // Bottom to top; nullptr denotes the parent plane.
  int x = 0, y = 0, next_x = 0, next_y = 0;
  std::vector<std::function<void()>> callbacks, cached_callbacks,
      ready_callbacks;
  struct Stack {
    Surface *child, *sibling;
    bool above;
  };
  std::vector<Stack> pending_stack;
  std::function<std::shared_ptr<Image>()> capture;
  std::function<void()> release;
  std::function<void()> changed;
  explicit Surface() { children.push_back(nullptr); }
  bool synchronized() const {
    return parent && (sync || parent->synchronized());
  }
  std::pair<double, double> size() const {
    auto &i = current;
    if (!i.image)
      return {0, 0};
    double w = i.image->width, h = i.image->height;
    if (i.transform & 1)
      std::swap(w, h);
    w /= i.scale;
    h /= i.scale;
    if (i.source.w >= 0) {
      w = i.source.w;
      h = i.source.h;
    }
    if (i.dw > 0) {
      w = i.dw;
      h = i.dh;
    }
    return {w, h};
  }
  bool accepts(double px, double py) const {
    auto [w, h] = size();
    if (px < 0 || py < 0 || px >= w || py >= h)
      return false;
    if (current.default_input)
      return true;
    bool inside = false;
    for (auto &op : current.region)
      if (op.second.contains(px, py))
        inside = op.first;
    return inside;
  }
  void commit() {
    // Capturing before release makes client buffer reuse independent of
    // visibility. Synchronized child commits remain invisible until the parent
    // transaction.
    if (attach_pending) {
      pending.image = capture ? capture() : nullptr;
      attach_pending = false;
      if (release)
        release();
      capture = {};
      release = {};
    }
    has_cached = true;
    cached = pending;
    cached_callbacks.insert(cached_callbacks.end(),
                            std::make_move_iterator(callbacks.begin()),
                            std::make_move_iterator(callbacks.end()));
    callbacks.clear();
    if (!synchronized())
      apply();
  }
  void apply() {
    if (has_cached) {
      current = cached;
      has_cached = false;
      ready_callbacks.insert(ready_callbacks.end(),
                             std::make_move_iterator(cached_callbacks.begin()),
                             std::make_move_iterator(cached_callbacks.end()));
      cached_callbacks.clear();
    }
    for (auto op : pending_stack) {
      auto &v = children;
      v.erase(std::remove(v.begin(), v.end(), op.child), v.end());
      auto at = std::find(v.begin(), v.end(),
                          op.sibling == this ? nullptr : op.sibling);
      if (op.above)
        ++at;
      v.insert(at, op.child);
    }
    pending_stack.clear();
    for (auto *child : children)
      if (child) {
        child->x = child->next_x;
        child->y = child->next_y;
        if (child->sync || synchronized())
          child->apply();
      }
    if (changed)
      changed();
  }
  void done() {
    auto done = std::move(ready_callbacks);
    ready_callbacks.clear();
    for (auto &f : done)
      f();
    for (auto *c : children)
      if (c)
        c->done();
  }
  void detach() {
    if (parent) {
      auto &v = parent->children;
      v.erase(std::remove(v.begin(), v.end(), this), v.end());
      auto &ops = parent->pending_stack;
      ops.erase(std::remove_if(ops.begin(), ops.end(),
                               [this](auto op) {
                                 return op.child == this || op.sibling == this;
                               }),
                ops.end());
      parent = nullptr;
    }
  }
  void reparent(Surface *p) {
    if (!p || p == this || parent || has_role)
      throw std::runtime_error("Surface already has a role");
    for (auto *at = p; at; at = at->parent)
      if (at == this)
        throw std::runtime_error("Subsurface cycle");
    parent = p;
    has_role = true;
    p->children.push_back(this);
  }
  void stack(Surface *sibling, bool above) {
    if (!parent || sibling == this ||
        (sibling != parent && sibling->parent != parent))
      throw std::runtime_error("Invalid subsurface sibling");
    parent->pending_stack.push_back({this, sibling, above});
  }
  template <class F> void walk(double ox, double oy, F &&visit) {
    for (auto *child : children)
      if (child)
        child->walk(ox + child->x, oy + child->y, visit);
      else if (current.image)
        visit(*this, ox, oy);
  }
  Surface *hit(double &px, double &py) {
    for (auto it = children.rbegin(); it != children.rend(); ++it) {
      if (*it) {
        double cx = px - (*it)->x, cy = py - (*it)->y;
        if (auto *s = (*it)->hit(cx, cy)) {
          px = cx;
          py = cy;
          return s;
        }
      } else if (current.image && accepts(px, py))
        return this;
    }
    return nullptr;
  }
};
} // namespace x11host
