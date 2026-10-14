#include "keymap-font.h"
#include <assert.h>
int main(void) {
  assert(keymap_text_width("WWW",2)>keymap_text_width("III",2));
  uint32_t pixels[100*40]={0};
  keymap_text_scaled_clipped(pixels,100,40,40,10,8,"Save changes",0xffffffff,2);
  int ink=0,smooth=0;
  for(int y=0;y<40;++y)for(int x=0;x<100;++x) {
    uint32_t p=pixels[y*100+x],alpha=p>>24;
    if(x>=40||y<8)assert(!p);
    assert((p&255)<=alpha&&((p>>8)&255)<=alpha&&((p>>16)&255)<=alpha);
    ink+=alpha>0;smooth+=alpha>0&&alpha<255;
  }
  assert(ink>0&&smooth>0); /* Lowercase glyphs and antialiased edges. */
  uint32_t small[40*24]={0};
  keymap_text_scaled(small,40,24,4,4,"Hold",0x90909090,1);
  ink=0;
  for(int i=0;i<40*24;++i){assert((small[i]>>24)<=0x90);ink+=small[i]!=0;}
  assert(ink>0); /* Passive markers keep their translucent glyph alpha. */
  uint32_t regular[40*24]={0};
  keymap_text(regular,40,24,4,4,"Add",0xffffffff);
  assert(keymap_text_size(2)==14&&keymap_text_size(1)==11);
  return 0;
}
