/* SPDX-License-Identifier: MIT — preserve guest input outside xdg geometry.
 * xdg geometry describes the visible frame. Transparent resize subsurfaces can
 * extend past it; keep those pixels in X11 without changing guest dimensions.
 */
#pragma once
#include "surface-tree.hpp"
#include <X11/Xutil.h>

namespace x11host {
struct WindowFrame {
  Rect bounds{0, 0, 640, 480};
  int left = 0, right = 0, top = 0, bottom = 0;
  int horizontal() const { return left + right; }
  int vertical() const { return top + bottom; }
  bool operator!=(const WindowFrame &other) const {
    return bounds.x != other.bounds.x || bounds.y != other.bounds.y ||
           bounds.w != other.bounds.w || bounds.h != other.bounds.h ||
           left != other.left || right != other.right || top != other.top ||
           bottom != other.bottom;
  }
  static WindowFrame from(Surface &root, Rect geometry, bool constrained) {
    double x0 = geometry.x, y0 = geometry.y, x1 = x0 + geometry.w,
           y1 = y0 + geometry.h;
    if (!constrained)
      root.walk(0, 0, [&](Surface &s, double x, double y) {
        auto [w, h] = s.size();
        Rect input{0, 0, w, h};
        if (!s.current.default_input) {
          // Region operations are surface-local. Clip before converting to
          // XRectangle's short coordinates, preserving subtraction and empty
          // input regions rather than expanding to Android's backing canvas.
          auto region = XCreateRegion();
          auto rectangle = XCreateRegion();
          for (auto &op : s.current.region) {
            int l = int(std::max(0.0, std::floor(op.second.x))),
                t = int(std::max(0.0, std::floor(op.second.y))),
                r = int(std::min(w, std::ceil(op.second.x + op.second.w))),
                b = int(std::min(h, std::ceil(op.second.y + op.second.h)));
            if (l >= r || t >= b)
              continue;
            XRectangle rect{short(l), short(t), (unsigned short)(r - l),
                            (unsigned short)(b - t)};
            if (op.first)
              XUnionRectWithRegion(&rect, region, region);
            else {
              XSubtractRegion(rectangle, rectangle, rectangle);
              XUnionRectWithRegion(&rect, rectangle, rectangle);
              XSubtractRegion(region, rectangle, region);
            }
          }
          XRectangle rect{};
          XClipBox(region, &rect);
          input = {double(rect.x), double(rect.y), double(rect.width),
                   double(rect.height)};
          XDestroyRegion(rectangle);
          XDestroyRegion(region);
        }
        if (input.w <= 0 || input.h <= 0)
          return;
        x0 = std::min(x0, std::floor(x + input.x));
        y0 = std::min(y0, std::floor(y + input.y));
        x1 = std::max(x1, std::ceil(x + input.x + input.w));
        y1 = std::max(y1, std::ceil(y + input.y + input.h));
      });
    return {{x0, y0, x1 - x0, y1 - y0},
            int(geometry.x - x0),
            int(x1 - geometry.x - geometry.w),
            int(geometry.y - y0),
            int(y1 - geometry.y - geometry.h)};
  }
};
} // namespace x11host
