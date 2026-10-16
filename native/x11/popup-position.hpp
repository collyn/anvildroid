/* SPDX-License-Identifier: MIT — xdg-positioner geometry in parent window
 * coordinates. */
#pragma once
#include "surface-tree.hpp"
namespace x11host {
struct Positioner {
  int width = 0, height = 0;
  Rect anchor_rect;
  unsigned anchor = 0, gravity = 0, constraint = 0;
  int offset_x = 0, offset_y = 0;
  bool reactive = false;
  int parent_width = 0, parent_height = 0;
  uint32_t parent_serial = 0;
  bool valid() const {
    return width > 0 && height > 0 && anchor_rect.w > 0 && anchor_rect.h > 0;
  }
  static int direction(unsigned value, bool vertical) {
    // xdg enums:
    // none,top,bottom,left,right,top_left,bottom_left,top_right,bottom_right.
    if (vertical)
      return value == 1 || value == 5 || value == 7   ? -1
             : value == 2 || value == 6 || value == 8 ? 1
                                                      : 0;
    return value == 3 || value == 5 || value == 6   ? -1
           : value == 4 || value == 7 || value == 8 ? 1
                                                    : 0;
  }
  Rect place(Rect bounds) const {
    Rect out{0, 0, double(width), double(height)};
    auto axis = [&](bool y) {
      double start = y ? anchor_rect.y : anchor_rect.x,
             span = y ? anchor_rect.h : anchor_rect.w;
      double size = y ? height : width, lo = y ? bounds.y : bounds.x,
             hi = lo + (y ? bounds.h : bounds.w);
      int a = direction(anchor, y), g = direction(gravity, y),
          off = y ? offset_y : offset_x;
      auto position = [&](int ad, int gd) {
        return start +
               (ad < 0   ? 0
                : ad > 0 ? span
                         : span / 2) +
               (gd < 0   ? -size
                : gd > 0 ? 0
                         : -size / 2) +
               off;
      };
      double pos = position(a, g);
      unsigned slide = y ? 2 : 1, flip = y ? 8 : 4, resize = y ? 32 : 16;
      if ((constraint & flip) && (pos < lo || pos + size > hi)) {
        double other = position(-a, -g);
        if (other >= lo && other + size <= hi)
          pos = other;
      }
      if (constraint & slide)
        pos = std::clamp(pos, lo, std::max(lo, hi - size));
      if (constraint & resize) {
        double end = std::min(pos + size, hi);
        pos = std::max(pos, lo);
        size = std::max(1., end - pos);
      }
      if (y) {
        out.y = pos;
        out.h = size;
      } else {
        out.x = pos;
        out.w = size;
      }
    };
    axis(false);
    axis(true);
    return out;
  }
};
} // namespace x11host
