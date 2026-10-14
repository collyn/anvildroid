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
/* Rounded CSD pixels use premultiplied alpha, never a painted desktop mask. */
static uint32_t csd_round_header(uint32_t color, int x, int y, int w, int state) {
  /* Header geometry includes the transparent resize border. Its visible
   * outline must align with Android content, not protrude by that border. */
  if (x < CSD_BORDER || x >= w-CSD_BORDER) return state & 1 ? 0xff212121 : 0;
  x -= CSD_BORDER;
  w -= 2*CSD_BORDER;
  if (state & 1) return color; /* Maximized windows keep square corners. */
  if (y >= CSD_RADIUS || (x >= CSD_RADIUS && x < w-CSD_RADIUS)) return color;
  int cx = x < CSD_RADIUS ? CSD_RADIUS*8 : (w-CSD_RADIUS)*8;
  int cy = CSD_RADIUS*8, count = 0;
  for (int sy=1; sy<8; sy+=2) for (int sx=1; sx<8; sx+=2) {
    int dx=x*8+sx-cx, dy=y*8+sy-cy;
    if (dx*dx+dy*dy <= CSD_RADIUS*CSD_RADIUS*64) ++count;
  }
  return ((color>>24)*count/16<<24) |
         (((color>>16)&255)*count/16<<16) |
         (((color>>8)&255)*count/16<<8) | ((color&255)*count/16);
}
/* Only the 1px band inside xdg geometry is painted when maximized.
 * The extra resize margin remains transparent, including corner extensions. */
static inline uint32_t csd_edge_pixel(int part,int x,int y,int w,int h,int state) {
  int inset=CSD_RESIZE-CSD_BORDER;
  if(part==8&&x>=CSD_RESIZE&&x<w-CSD_RESIZE&&y<h-CSD_RESIZE)return 0xff000000;
  if(!(state&1))return 0;
  if(part==1&&x>=w-CSD_BORDER&&y<h-inset)return 0xff212121;
  if(part==2&&x<CSD_BORDER&&y<h-inset)return 0xff212121;
  if(part==3&&x>=inset&&x<w-inset&&y<CSD_BORDER)return 0xff212121;
  if(part==8&&x>=inset&&x<w-inset&&y<h-inset)return 0xff212121;
  return 0;
}
/* Four-by-four coverage sampling in eighth-pixel coordinates keeps every
 * glyph centered on the same axis without adding a font or GPU dependency.
 * Button geometry matches the main app; neutral chrome blends with Android. */
static uint32_t csd_header_pixel(int x, int y, int w, int h, int state) {
  uint32_t base = y == h-1 ? 0xff333333 : 0xff212121;
  /* Paint the complete button cell, including the invisible top resize
   * grip. Pointer hit testing still reserves that strip for resizing. */
  int action = csd_hit(0, x, CSD_GRIP, w, h);
  if ((state & 2) && x >= CSD_BORDER && x < CSD_BORDER+CSD_BUTTON) action=CSD_MENU;
  if (action < CSD_MINIMIZE || action > CSD_MENU) return csd_round_header(base,x,y,w,state);
  int hover = (state >> 8) & 255, pressed = (state >> 16) & 255;
  uint32_t fill = base;
  if (hover == action && (!pressed || pressed == action))
    fill = action == CSD_CLOSE ? (pressed ? 0xffa02e3f : 0xffb83a4a) :
                                0xff383838;
  uint32_t ink = 0xffdedede;
  /* Glyphs sit dead center of the full header bar: equal clearance above
   * and below within the compact chrome. */
  int cx = (action==CSD_MENU ? CSD_BORDER+CSD_BUTTON/2 : csd_button_center(action, w)) * 8, cy = h * 4;
  unsigned red=0, green=0, blue=0;
  for (int sy=1; sy<8; sy+=2) for (int sx=1; sx<8; sx+=2) {
    int dx=x*8+sx-cx, dy=y*8+sy-cy;
    int ax=csd_abs(dx), ay=csd_abs(dy);
    /* Full 36px button cells use a flat neutral hover. */
    uint32_t color = y == h-1 ? base : fill;
    int glyph = 0;
    /* The minimize dash anchors the bottom of the glyph envelope; the
     * square and X stay centered. */
    if (action == CSD_MENU) glyph = ax<=7 && (ay<=7 || csd_abs(ay-32)<=7);
    if (action == CSD_MINIMIZE) glyph = ax<=40 && dy>=19 && dy<=29;
    if (action == CSD_CLOSE)
      glyph = ax<=40 && ay<=40 && (csd_abs(dx-dy)<=7 || csd_abs(dx+dy)<=7);
    if (action == CSD_MAXIMIZE) {
      if (state & 1) {
        /* Two offset outlined squares: back stroke shows through the front. */
        glyph = csd_square(dx,dy,-12,-12,28,10) ||
                csd_square(dx,dy,12,12,28,10);
      } else glyph = csd_square(dx,dy,0,0,40,10);
    }
    if (glyph) color=ink;
    red+=(color>>16)&255;green+=(color>>8)&255;blue+=color&255;
  }
  uint32_t color = 0xff000000 | ((red+8)/16<<16) | ((green+8)/16<<8) | (blue+8)/16;
  return csd_round_header(color,x,y,w,state);
}
#endif
