/* SPDX-License-Identifier: MIT — X11 desktop scale and logical/physical
 * coordinate mapping, without a display.
 *
 * X11 has no compositor-side scaling. Toolkit scaling is announced through
 * Xft.dpi in RESOURCE_MANAGER (GNOME/Cinnamon/MATE/KDE/XFCE write
 * 96 x scale there). The adapter exposes that scale exactly as a fractional
 * Wayland compositor does (wl_output.scale = ceil, wp_fractional_scale_v1
 * preferred_scale = scale x 120) and keeps every Wayland coordinate logical:
 * logical x scale = X11 pixels. Scale is in 1/120 units throughout.
 */
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <string>

namespace x11host {
constexpr int scale_unit = 120, min_scale120 = 120, max_scale120 = 480;
// Last Xft.dpi value in an Xrm resource string, 0 when absent or invalid.
// Accepts "Xft.dpi:\t192", "*Xft.dpi: 144" and surrounding whitespace.
inline double resource_dpi(const std::string &resources) {
  double dpi = 0;
  size_t at = 0;
  while (at < resources.size()) {
    size_t end = resources.find('\n', at);
    if (end == std::string::npos)
      end = resources.size();
    std::string line = resources.substr(at, end - at);
    at = end + 1;
    size_t i = line.find_first_not_of(" \t");
    if (i == std::string::npos)
      continue;
    if (line[i] == '*')
      ++i;
    static const std::string key = "Xft.dpi";
    if (line.compare(i, key.size(), key))
      continue;
    i = line.find_first_not_of(" \t", i + key.size());
    if (i == std::string::npos || line[i] != ':')
      continue;
    std::string value = line.substr(i + 1);
    char *tail = nullptr;
    double parsed = strtod(value.c_str(), &tail);
    if (tail == value.c_str() || !std::isfinite(parsed))
      continue;
    while (*tail == ' ' || *tail == '\t' || *tail == '\r')
      ++tail;
    if (*tail)
      continue;
    dpi = parsed > 0 && parsed <= 9600 ? parsed : 0;
  }
  return dpi;
}
// 96 DPI is 1x. Values below 1x (or missing) keep 1x; above 4x are clamped.
inline int scale120_from_dpi(double dpi) {
  if (!(dpi > 0))
    return scale_unit;
  return std::clamp(int(std::lround(dpi * scale_unit / 96.0)), min_scale120,
                    max_scale120);
}
// wl_output.scale: KWin/Mutter advertise ceil(fractional scale).
inline int integer_scale(int scale120) {
  return (std::max(scale120, 1) + scale_unit - 1) / scale_unit;
}
// Sizes: logical -> pixels rounds up and pixels -> logical rounds down, so
// to_logical_size(to_physical_size(n)) == n and a requested logical window
// size survives the X11 round trip without resize oscillation.
inline int to_physical_size(int logical, int scale120) {
  if (logical <= 0)
    return logical;
  return int((int64_t(logical) * scale120 + scale_unit - 1) / scale_unit);
}
inline int to_logical_size(int physical, int scale120) {
  if (physical <= 0)
    return physical;
  return std::max(1, int(int64_t(physical) * scale_unit / scale120));
}
// Positions/extents. 1x returns its argument unchanged (bit-exact).
inline double to_physical(double logical, int scale120) {
  return scale120 == scale_unit ? logical : logical * scale120 / scale_unit;
}
inline double to_logical(double physical, int scale120) {
  return scale120 == scale_unit ? physical : physical * scale_unit / scale120;
}
} // namespace x11host
