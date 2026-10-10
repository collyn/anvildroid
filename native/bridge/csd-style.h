#ifndef ANVILDROID_CSD_STYLE_H
#define ANVILDROID_CSD_STYLE_H
#include <stdint.h>
#include "csd-layout.h"

static int csd_style_state(int maximized, int hover, int pressed) {
  return (maximized ? 1 : 0) | (hover << 8) | (pressed << 16);
}
static int csd_abs(int n) { return n < 0 ? -n : n; }
static int csd_square(int dx, int dy, int cx, int cy, int half, int stroke) {
  int ax = csd_abs(dx - cx), ay = csd_abs(dy - cy);
  return ax <= half && ay <= half && (ax >= half - stroke || ay >= half - stroke);
}
/* Four-by-four coverage sampling in eighth-pixel coordinates keeps every
 * glyph centered on the same axis without adding a font or GPU dependency.
 * The flat header palette is the original AnvilDroid chrome; only the
 * button cells below are decorated. */
static uint32_t csd_header_pixel(int x, int y, int w, int h, int state) {
  uint32_t base = y == 0 ? 0xff41464e : y == h-1 ? 0xff25292f : 0xff30343b;
  int action = csd_hit(0, x, y, w, h);
  if (action < CSD_MINIMIZE || action > CSD_CLOSE) return base;
  int hover = (state >> 8) & 255, pressed = (state >> 16) & 255;
  uint32_t fill = base;
  if (hover == action && (!pressed || pressed == action))
    fill = action == CSD_CLOSE ? (pressed ? 0xffa63842 : 0xffc84c56) :
                                (pressed ? 0xff555d68 : 0xff444b55);
  uint32_t ink = hover == action ? 0xfff9fafb : 0xffdde2e8;
  /* Glyphs sit dead center of the full header bar: equal clearance above
   * and below within the 38px chrome. */
  int cx = csd_button_center(action, w) * 8, cy = h * 4;
  unsigned red=0, green=0, blue=0;
  for (int sy=1; sy<8; sy+=2) for (int sx=1; sx<8; sx+=2) {
    int dx=x*8+sx-cx, dy=y*8+sy-cy;
    int ax=csd_abs(dx), ay=csd_abs(dy);
    /* Compact 22px rounded-square highlight, 4px corner radius, centered
     * with the glyphs: the hover no longer floods the whole button cell. */
    int rx=ax>56?ax-56:0, ry=ay>56?ay-56:0;
    uint32_t color = ax<=88 && ay<=88 && rx*rx+ry*ry<=32*32 ? fill : base;
    int glyph = 0;
    /* The minimize dash anchors the bottom of the glyph envelope; the
     * square and X stay centered. */
    if (action == CSD_MINIMIZE) glyph = ax<=48 && dy>=40 && dy<=56;
    if (action == CSD_CLOSE)
      glyph = ax<=48 && ay<=48 && (csd_abs(dx-dy)<=14 || csd_abs(dx+dy)<=14);
    if (action == CSD_MAXIMIZE) {
      if (state & 1) {
        /* Two offset outlined squares: back stroke shows through the front. */
        glyph = csd_square(dx,dy,-16,-16,44,16) ||
                csd_square(dx,dy,16,16,44,16);
      } else glyph = csd_square(dx,dy,0,0,48,16);
    }
    if (glyph) color=ink;
    red+=(color>>16)&255;green+=(color>>8)&255;blue+=color&255;
  }
  return 0xff000000 | ((red+8)/16<<16) | ((green+8)/16<<8) | (blue+8)/16;
}
#endif
