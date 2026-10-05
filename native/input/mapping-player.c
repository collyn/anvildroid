#include "mapping-player.h"
static int index_of(struct mapping_player *p,uint32_t key) {
  for(int i=0;i<p->count;++i)if(p->bindings[i].key==key)return i;
  return -1;
}
static int contact(struct mapping_player *p,uint32_t key) {
  for(int i=0;i<p->session.count;++i)if(p->session.points[i].binding==key)return 1;
  return 0;
}
static void clear_deadlines(struct mapping_player *p) {
  for(int i=0;i<MAPPING_BINDINGS;++i)p->tap_deadline[i]=0;
}
int mapping_cancel(struct mapping_player *p,uint64_t now,touch_sink sink,void *ctx) {
  clear_deadlines(p);
  /* Keep physical down/captured state until actual release. A held key must
   * never replay into a new surface merely because focus or size changed. */
  return touch_suspend(&p->session,now,sink,ctx);
}
int mapping_configure(struct mapping_player *p,const struct mapping_binding *b,
                      int n,uint64_t now,touch_sink sink,void *ctx) {
  if(n<0||n>MAPPING_BINDINGS||(n&&!b))return 0;
  for(int i=0;i<n;++i) {
    if(b[i].key<2||b[i].key>=MAPPING_KEYS||(b[i].hold!=0&&b[i].hold!=1))return 0;
    for(int j=0;j<i;++j)if(b[i].key==b[j].key)return 0;
  }
  if(!mapping_cancel(p,now,sink,ctx))return 0;
  for(int i=0;i<n;++i)p->bindings[i]=b[i];
  p->count=n;return 1;
}
int mapping_route(struct mapping_player *p,struct touch_route route,int focused,
                  int stable,uint64_t now,touch_sink sink,void *ctx) {
  uint64_t epoch=p->session.epoch;
  int ok=touch_reconfigure(&p->session,route,focused,stable,now,sink,ctx);
  if(!ok||epoch!=p->session.epoch)clear_deadlines(p);
  return ok;
}
int mapping_key(struct mapping_player *p,uint32_t key,int pressed,uint64_t now,
                touch_sink sink,void *ctx) {
  if(key>=MAPPING_KEYS||(pressed!=0&&pressed!=1))return 0;
  uint32_t bit=1u<<(key%32);unsigned word=key/32;
  int held=(p->down[word]&bit)!=0,captured=(p->captured[word]&bit)!=0;
  if(!pressed) {
    p->down[word]&=~bit;p->captured[word]&=~bit;
    int i=index_of(p,key);
    if(captured&&i>=0&&p->bindings[i].hold&&contact(p,key)) {
      if(!touch_release(&p->session,p->session.epoch,key,now,sink,ctx))
        mapping_cancel(p,now,sink,ctx);
    }
    return captured;
  }
  if(held)return captured;
  p->down[word]|=bit;
  int i=index_of(p,key);
  if(i<0||!p->session.enabled||p->session.fault)return 0;
  p->captured[word]|=bit;
  if(contact(p,key))return 1; /* rapid re-press during the existing tap */
  const struct mapping_binding *b=&p->bindings[i];
  if(!b->hold&&now>UINT64_MAX-MAPPING_TAP_NS) {
    mapping_cancel(p,now,sink,ctx);return 1;
  }
  if(!touch_press(&p->session,p->session.epoch,key,b->nx,b->ny,now,sink,ctx)) {
    if(p->session.fault)mapping_cancel(p,now,sink,ctx);
    return 1;
  }
  if(!b->hold)p->tap_deadline[i]=now+MAPPING_TAP_NS;
  return 1;
}
int mapping_tick(struct mapping_player *p,uint64_t now,touch_sink sink,void *ctx) {
  if(p->session.fault)return 0;
  for(int i=0;i<p->count;++i)if(p->tap_deadline[i]&&now>=p->tap_deadline[i]) {
    p->tap_deadline[i]=0;
    if(!touch_release(&p->session,p->session.epoch,p->bindings[i].key,now,sink,ctx)) {
      mapping_cancel(p,now,sink,ctx);return 0;
    }
  }
  return 1;
}
