#ifndef ANVILDROID_MENU_FONT_H
#define ANVILDROID_MENU_FONT_H
#include <stdint.h>
#include <stddef.h>
#include "menu-font-data.h"
static inline int menu_text_width(const char *text,int size) {
  const struct menu_glyph *glyphs=size==11?menu_glyphs_11:menu_glyphs_14;
  int width=0;
  for(;*text;++text) {
    unsigned code=(unsigned char)*text;if(code<32||code>126)code='?';
    width+=glyphs[code-32].advance;
  }
  return width;
}
/* Antialiased, proportional sans-serif. Width always describes the full
 * buffer stride; clipping the menu panel must never alter the row stride. */
static void menu_text(uint32_t *pixels,int w,int h,int clip_width,int x,int baseline,
                      const char *text,uint32_t color,int size) {
  const struct menu_glyph *glyphs=size==11?menu_glyphs_11:menu_glyphs_14;
  const unsigned char *alpha=size==11?menu_alpha_11:menu_alpha_14;
  for(;*text;++text) {
    unsigned code=(unsigned char)*text;
    if(code<32||code>126)code='?';
    const struct menu_glyph *g=&glyphs[code-32];
    for(int y=0;y<g->h;++y)for(int col=0;col<g->w;++col) {
      int px=x+g->x+col,py=baseline+g->y+y;
      if(px<0||px>=w||px>=clip_width||py<0||py>=h)continue;
      unsigned a=alpha[g->offset+y*g->w+col];
      if(!a)continue;
      uint32_t *at=&pixels[(size_t)py*w+px],bg=*at;
      unsigned opacity=((color>>24)*a+127)/255;
      unsigned r=(((color>>16)&255)*a+((bg>>16)&255)*(255-opacity)+127)/255;
      unsigned green=(((color>>8)&255)*a+((bg>>8)&255)*(255-opacity)+127)/255;
      unsigned b=((color&255)*a+(bg&255)*(255-opacity)+127)/255;
      unsigned out_alpha=opacity+((bg>>24)*(255-opacity)+127)/255;
      *at=(out_alpha<<24)|(r<<16)|(green<<8)|b;
    }
    x+=g->advance;
  }
}
#endif
