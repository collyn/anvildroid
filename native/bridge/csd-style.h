#ifndef ANVILDROID_CSD_STYLE_H
#define ANVILDROID_CSD_STYLE_H
#include <stdint.h>
#include "csd-layout.h"

static int csd_style_state(int maximized, int hover, int pressed) {
  return (maximized ? 1 : 0) | (hover << 8) | (pressed << 16);
}
static int csd_abs(int n) { return n < 0 ? -n : n; }
static int csd_outline(int x, int y, int left, int top, int right, int bottom) {
  return x >= left-6 && x <= right+6 && y >= top-6 && y <= bottom+6 &&
    (csd_abs(x-left)<=6 || csd_abs(x-right)<=6 ||
     csd_abs(y-top)<=6 || csd_abs(y-bottom)<=6);
}
/* Four-by-four coverage sampling in eighth-pixel coordinates keeps every
 * glyph centered on the same axis without adding a font or GPU dependency. */
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
  int cx = csd_button_center(action, w) * 8, cy = h * 4;
  unsigned red=0, green=0, blue=0;
  for (int sy=1; sy<8; sy+=2) for (int sx=1; sx<8; sx+=2) {
    int dx=x*8+sx-cx, dy=y*8+sy-cy;
    int ax=csd_abs(dx), ay=csd_abs(dy);
    int rx=ax>72?ax-72:0, ry=ay>72?ay-72:0;
    uint32_t color = ax<=104 && ay<=104 && rx*rx+ry*ry<=32*32 ? fill : base;
    int glyph = 0;
    if (action == CSD_MINIMIZE) glyph = ax<=44 && ay<=6;
    if (action == CSD_CLOSE)
      glyph = ax<=44 && ay<=44 && (csd_abs(dx-dy)<=8 || csd_abs(dx+dy)<=8);
    if (action == CSD_MAXIMIZE) {
      if (state & 1) {
        glyph = csd_outline(dx,dy,-16,-40,40,16) && !(dx<22 && dy>-22);
        glyph |= csd_outline(dx,dy,-40,-16,16,40);
      } else glyph = csd_outline(dx,dy,-40,-40,40,40);
    }
    if (glyph) color=ink;
    red+=(color>>16)&255;green+=(color>>8)&255;blue+=color&255;
  }
  return 0xff000000 | ((red+8)/16<<16) | ((green+8)/16<<8) | (blue+8)/16;
}
#endif
