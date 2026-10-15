#ifndef ANVIL_CUSTOM_SIZE_H
#define ANVIL_CUSTOM_SIZE_H
/* Coalesce compositor configures; never relayout Android during a drag. */
struct custom_size { int enabled,w,h,dragging,ticks; };
static inline void custom_size_queue(struct custom_size *p,int w,int h,int dragging) {
  p->dragging=dragging;
  if(w<160||h<160||w>8192||h>8192)return;
  if(p->w!=w||p->h!=h){p->w=w;p->h=h;p->ticks=0;}
}
static inline int custom_size_due(struct custom_size *p,int pw,int ph,int *w,int *h) {
  if(!p->enabled||!p->w||p->dragging||pw<160||ph<160)return 0;
  if(++p->ticks<6)return 0;
  *w=p->w;*h=p->h;
  /* Stock HWC cannot change physical modes at runtime. Fit within capacity. */
  if(*w>pw){*h=(int)((long long)*h*pw / *w);*w=pw;}
  if(*h>ph){*w=(int)((long long)*w*ph / *h);*h=ph;}
  p->w=p->h=0;
  return *w>=160&&*h>=160;
}
#endif
