/* SPDX-License-Identifier: MIT — wl_output refresh from X11 RandR modes,
 * without a display. */
#pragma once
#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>
namespace x11host {
// Advertised when RandR is absent or reports no usable timing (Xvfb,
// headless tests).
constexpr int32_t default_refresh_mhz = 60000;
constexpr int32_t min_refresh_mhz = 23000, max_refresh_mhz = 500000;
// RandR mode flag values (randr.h RR_Interlace/RR_DoubleScan).
constexpr uint64_t mode_interlace = 0x10, mode_doublescan = 0x20;
// Vertical refresh in mHz, rounded to nearest, matching xrandr: interlace
// halves vTotal, doublescan doubles it. 0 means unknown timing.
inline int32_t mode_refresh_mhz(uint64_t dot_clock, uint32_t h_total,
                                uint32_t v_total, uint64_t flags) {
  if (!dot_clock || !h_total || !v_total)
    return 0;
  uint64_t numerator = dot_clock * (flags & mode_interlace ? 2000 : 1000);
  uint64_t denominator =
      uint64_t(h_total) * v_total * (flags & mode_doublescan ? 2 : 1);
  uint64_t mhz = (numerator + denominator / 2) / denominator;
  return int32_t(std::clamp<uint64_t>(mhz, min_refresh_mhz, max_refresh_mhz));
}
// One active CRTC with its current mode timing.
struct OutputMode {
  int32_t x = 0, y = 0, width = 0, height = 0, refresh_mhz = 0;
  bool primary = false;
  unsigned long crtc = 0;
  std::string name; // First connected output driven by this CRTC.
};
// Whole-screen refresh (startup log and virtual monitors without outputs)
// follows the primary monitor. Without a primary (or
// when it is disabled/has unknown timing), the fastest active CRTC is used so
// Android never paces slower than any monitor its windows can appear on.
inline int32_t select_refresh_mhz(const std::vector<OutputMode> &modes) {
  int32_t fastest = 0;
  for (const auto &m : modes) {
    if (m.primary && m.refresh_mhz > 0)
      return m.refresh_mhz;
    fastest = std::max(fastest, m.refresh_mhz);
  }
  return fastest > 0 ? fastest : default_refresh_mhz;
}
// The Waydroid HWC truncates refresh to whole Hz (refresh / 1000) before
// deriving its vsync period, so 59997 mHz would run Android at 59 Hz. Round
// to the nearest whole Hz instead; Android paces itself from this period.
inline int32_t advertised_refresh_mhz(int32_t mhz) {
  return (mhz + 500) / 1000 * 1000;
}
// One wl_output: a RandR 1.5 monitor (including `xrandr --setmonitor`
// virtual monitors), else one active CRTC, else the whole X screen. Geometry
// is in X11 (physical) pixels in root coordinates.
struct Monitor {
  std::string name;
  int32_t x = 0, y = 0, width = 0, height = 0;
  int32_t mm_width = 0, mm_height = 0, refresh_mhz = default_refresh_mhz;
  bool primary = false;
  bool operator==(const Monitor &o) const {
    return name == o.name && x == o.x && y == o.y && width == o.width &&
           height == o.height && mm_width == o.mm_width &&
           mm_height == o.mm_height && refresh_mhz == o.refresh_mhz &&
           primary == o.primary;
  }
  bool operator!=(const Monitor &o) const { return !(*this == o); }
  int64_t overlap(int64_t rx, int64_t ry, int64_t rw, int64_t rh) const {
    int64_t w = std::min<int64_t>(x + int64_t(width), rx + rw) -
                std::max<int64_t>(x, rx),
            h = std::min<int64_t>(y + int64_t(height), ry + rh) -
                std::max<int64_t>(y, ry);
    return w > 0 && h > 0 ? w * h : 0;
  }
  bool contains(int64_t px, int64_t py) const {
    return px >= x && py >= y && px < x + int64_t(width) &&
           py < y + int64_t(height);
  }
};
// Missing EDID/virtual sizes keep the historical 96 DPI estimate
// (1920x1080 -> 508x285 mm).
inline int32_t estimated_mm(int32_t pixels) {
  return int32_t(int64_t(std::max(0, pixels)) * 254 / 960);
}
// Unique, stable wl_output names; the v4 name event is sent once per bind.
inline void unique_monitor_names(std::vector<Monitor> &monitors) {
  for (size_t i = 0; i < monitors.size(); ++i) {
    auto &m = monitors[i];
    if (m.name.empty())
      m.name = "X11-" + std::to_string(i);
    std::string base = m.name;
    for (int n = 2;; ++n) {
      bool duplicate = false;
      for (size_t j = 0; j < i; ++j)
        duplicate |= monitors[j].name == m.name;
      if (!duplicate)
        break;
      m.name = base + "-" + std::to_string(n);
    }
  }
}
// The monitor holding most of a window, as Mutter/Muffin and KWin assign a
// window's output; nullptr when it lies on none.
inline const Monitor *monitor_for_rect(const std::vector<Monitor> &monitors,
                                       int64_t x, int64_t y, int64_t w,
                                       int64_t h) {
  const Monitor *best = nullptr;
  int64_t area = 0;
  for (auto &m : monitors) {
    auto a = m.overlap(x, y, std::max<int64_t>(w, 1), std::max<int64_t>(h, 1));
    if (a > area) {
      area = a;
      best = &m;
    }
  }
  return best;
}
// Unplaced windows: the monitor under the pointer (where X11 WMs place new
// windows), else the primary, else the first.
inline const Monitor *monitor_for_point(const std::vector<Monitor> &monitors,
                                        bool pointer, int64_t px, int64_t py) {
  if (pointer)
    for (auto &m : monitors)
      if (m.contains(px, py))
        return &m;
  for (auto &m : monitors)
    if (m.primary)
      return &m;
  return monitors.empty() ? nullptr : &monitors.front();
}
} // namespace x11host
