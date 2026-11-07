/* SPDX-License-Identifier: MIT — RandR mode timing to wl_output mHz without a
 * display. */
#include "output-mode.hpp"
#include "output-scale.hpp"
#include <cassert>
#include <cstdio>
using namespace x11host;
static OutputMode crtc(int32_t refresh, bool primary = false) {
  OutputMode m;
  m.width = 1920;
  m.height = 1080;
  m.refresh_mhz = refresh;
  m.primary = primary;
  return m;
}
int main() {
  // CEA 1080p60 and its NTSC 1000/1001 variant.
  assert(mode_refresh_mhz(148500000, 2200, 1125, 0) == 60000);
  assert(mode_refresh_mhz(148351648, 2200, 1125, 0) == 59940);
  assert(mode_refresh_mhz(297000000, 2200, 1125, 0) == 120000);
  assert(mode_refresh_mhz(74175824, 2750, 1125, 0) == 23976);
  // High-refresh panels report fractional rates; round to nearest mHz.
  assert(mode_refresh_mhz(580000000, 2720, 1481, 0) == 143981);
  assert(mode_refresh_mhz(241500000, 2720, 1481, 0) == 59951);
  // 1080i: vTotal counts both fields, so the field rate is doubled.
  assert(mode_refresh_mhz(74250000, 2200, 1125, mode_interlace) == 60000);
  assert(mode_refresh_mhz(74250000, 2200, 1125, 0) == 30000);
  // Doublescan scans each line twice, halving the rate.
  assert(mode_refresh_mhz(12587500, 400, 262, mode_doublescan) == 60055);
  assert(mode_refresh_mhz(12587500, 400, 262, 0) == 120110);
  assert(mode_refresh_mhz(148500000, 2200, 1125,
                          mode_interlace | mode_doublescan) == 60000);
  // Xvfb reports zero timing; implausible timings are clamped.
  assert(mode_refresh_mhz(0, 0, 0, 0) == 0);
  assert(mode_refresh_mhz(148500000, 0, 1125, 0) == 0);
  assert(mode_refresh_mhz(148500000, 2200, 0, 0) == 0);
  assert(mode_refresh_mhz(1000000000, 1000, 1, 0) == max_refresh_mhz);
  assert(mode_refresh_mhz(1000, 1000, 1000, 0) == min_refresh_mhz);
  // Selection: primary first, else the fastest active CRTC, else 60 Hz.
  assert(select_refresh_mhz({}) == default_refresh_mhz);
  assert(advertised_refresh_mhz(59997) == 60000 && advertised_refresh_mhz(59940) == 60000);
  assert(advertised_refresh_mhz(143981) == 144000 && advertised_refresh_mhz(119959) == 120000);
  assert(advertised_refresh_mhz(60000) == 60000 && advertised_refresh_mhz(23976) == 24000);
  assert(select_refresh_mhz({crtc(0), crtc(0, true)}) == default_refresh_mhz);
  assert(select_refresh_mhz({crtc(143981), crtc(59940, true)}) == 59940);
  assert(select_refresh_mhz({crtc(59940), crtc(143981), crtc(120000)}) ==
         143981);
  assert(select_refresh_mhz({crtc(0, true), crtc(120000)}) == 120000);
  // Xft.dpi parsing (xrdb formats) and its 1/120 scale.
  assert(resource_dpi("Xft.dpi:\t192\n") == 192);
  assert(resource_dpi("Xcursor.size: 48\n*Xft.dpi:  144 \nXft.antialias: 1\n") == 144);
  assert(resource_dpi("Xft.dpix: 192\nXft.dpi: abc\n") == 0 && resource_dpi("") == 0);
  assert(resource_dpi("Xft.dpi: 96\nXft.dpi: 120\n") == 120);
  assert(scale120_from_dpi(0) == 120 && scale120_from_dpi(72) == 120);
  assert(scale120_from_dpi(96) == 120 && scale120_from_dpi(120) == 150);
  assert(scale120_from_dpi(144) == 180 && scale120_from_dpi(192) == 240);
  assert(scale120_from_dpi(9600) == max_scale120);
  assert(integer_scale(120) == 1 && integer_scale(150) == 2 &&
         integer_scale(240) == 2 && integer_scale(250) == 3);
  // Sizes survive logical -> X11 -> logical at every supported scale.
  for (int scale : {120, 126, 150, 180, 210, 240, 300, 480})
    for (int n = 1; n < 4000; ++n) {
      assert(to_logical_size(to_physical_size(n, scale), scale) == n);
      int px = to_physical_size(to_logical_size(n, scale), scale);
      assert((px <= n || to_logical_size(n, scale) == 1) &&
             (scale != 120 || px == n));
    }
  assert(to_physical(10.3, 120) == 10.3 && to_logical(10.3, 120) == 10.3);
  assert(to_physical(40, 180) == 60 && to_logical(60, 180) == 40);
  // Monitor assignment: most overlap, then pointer, primary, first.
  Monitor l, r;
  l.name = "L";
  l.width = r.width = 800;
  l.height = r.height = 1000;
  r.name = "R";
  r.x = 800;
  r.primary = true;
  std::vector<Monitor> pair{l, r};
  assert(monitor_for_rect(pair, 700, 0, 300, 100)->name == "R");
  assert(monitor_for_rect(pair, 500, 0, 300, 100)->name == "L");
  assert(!monitor_for_rect(pair, 1700, 0, 30, 30));
  assert(monitor_for_point(pair, true, 10, 10)->name == "L");
  assert(monitor_for_point(pair, false, 10, 10)->name == "R");
  assert(monitor_for_point(pair, true, 5000, 10)->name == "R");
  pair[1].primary = false;
  assert(monitor_for_point(pair, true, 5000, 10)->name == "L");
  std::vector<Monitor> names(3);
  names[0].name = names[1].name = "eDP-1";
  unique_monitor_names(names);
  assert(names[0].name == "eDP-1" && names[1].name == "eDP-1-2" &&
         names[2].name == "X11-2");
  assert(estimated_mm(1920) == 508 && estimated_mm(1080) == 285);
  puts("PASS RandR mode refresh and output selection");
  puts("PASS Xft.dpi scale, logical/physical size round trips and monitor "
       "assignment");
}
