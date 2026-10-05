#ifndef ANVILDROID_GAME_FIT_H
#define ANVILDROID_GAME_FIT_H
/* Integer contain transform. Never derive the source aspect from the host. */
struct game_fit_rect { int x, y, width, height; };
static struct game_fit_rect game_fit_rect(int sw, int sh, int w, int h) {
  struct game_fit_rect r={0};
  if(sw<1||sh<1||w<1||h<1||sw>8192||sh>8192||w>8192||h>8192)return r;
  if((int64_t)w*sh <= (int64_t)h*sw) {
    r.width=w; r.height=(int)((int64_t)w*sh/sw);
  } else {
    r.height=h; r.width=(int)((int64_t)h*sw/sh);
  }
  if(r.width<1)r.width=1;
  if(r.height<1)r.height=1;
  r.x=(w-r.width)/2;r.y=(h-r.height)/2;
  return r;
}
#endif
