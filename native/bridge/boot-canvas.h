#ifndef ANVIL_BOOT_CANVAS_H
#define ANVIL_BOOT_CANVAS_H
#include <stdint.h>
/* Reserve capacity for effective scale up to 2 when boot calibrated at 1.
 * This is bounded headroom, NOT arbitrary hot-scaling support. Values returned
 * are logical HWC properties; validate the actual physical allocation. */
static int boot_canvas_size(int w, int h, int scale, int *width, int *height) {
  if (w < 64 || h < 64 || w > 8192 || h > 8192 || scale < 120 || scale > 1920)
    return 0;
  int reserve_scale = scale < 240 ? 240 : scale;
  int64_t pw = ((int64_t)w * reserve_scale + 119) / 120 + 64;
  int64_t ph = ((int64_t)h * reserve_scale + 119) / 120 + 128;
  int64_t lw = (pw * 120 + scale - 1) / scale;
  int64_t lh = (ph * 120 + scale - 1) / scale;
  pw = lw * scale / 120;
  ph = lh * scale / 120;
  if (pw > 8192 || ph > 8192 || pw * ph > 16777216)
    return 0;
  *width = (int)lw;
  *height = (int)lh;
  return 1;
}

/* Worker-probed workarea capacity in physical pixels. Margins and rounding
 * mirror boot_canvas_size; values returned are logical HWC properties. This
 * is bounded headroom, NOT arbitrary hot-scaling support. */
static int boot_canvas_capacity(int cap_w, int cap_h, int scale, int *width, int *height) {
  if (cap_w < 64 || cap_h < 64 || cap_w > 8192 || cap_h > 8192 || scale < 120 || scale > 1920)
    return 0;
  int64_t pw = (int64_t)cap_w + 64;
  int64_t ph = (int64_t)cap_h + 128;
  int64_t lw = (pw * 120 + scale - 1) / scale;
  int64_t lh = (ph * 120 + scale - 1) / scale;
  pw = lw * scale / 120;
  ph = lh * scale / 120;
  if (pw > 8192 || ph > 8192 || pw * ph > 16777216)
    return 0;
  *width = (int)lw;
  *height = (int)lh;
  return 1;
}
#endif
