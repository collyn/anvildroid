#ifndef ANVILDROID_KEYMAP_FONT_H
#define ANVILDROID_KEYMAP_FONT_H
#include "menu-font.h"
/* Match the main app/header menu: Noto Sans Regular, with real glyph advances.
 * The editor API uses a top coordinate; the atlas renderer uses a baseline. */
static inline int keymap_text_size(int scale) { return scale==1?11:14; }
static inline int keymap_text_width(const char *text,int scale) {
  return menu_text_width(text,keymap_text_size(scale));
}
static void keymap_text_scaled_clipped(uint32_t *pixels,int w,int h,int clip_width,int x,int y,
                                      const char *text,uint32_t color,int scale) {
  int size=keymap_text_size(scale);
  menu_text(pixels,w,h,clip_width,x,y+(size==11?8:11),text,color,size);
}
static void keymap_text_scaled(uint32_t *pixels,int w,int h,int x,int y,
                               const char *text,uint32_t color,int scale) {
  keymap_text_scaled_clipped(pixels,w,h,w,x,y,text,color,scale);
}
static void keymap_text(uint32_t *pixels,int w,int h,int x,int y,
                        const char *text,uint32_t color) {
  keymap_text_scaled(pixels,w,h,x,y,text,color,2);
}
#endif
