#include "touch-session.h"

static int route_valid(struct touch_route r) {
  return r.runtime && r.surface && r.x>=0 && r.y>=0 && r.x<=8192 && r.y<=8192 &&
    r.width>0 && r.height>0 && r.width<=8192-r.x && r.height<=8192-r.y;
}
static int find_binding(struct touch_session *s, uint32_t binding) {
  for (int i=0;i<s->count;++i) if (s->points[i].binding==binding) return i;
  return -1;
}
static void position(struct touch_session *s, struct touch_point *p,uint16_t x,uint16_t y) {
  p->x=s->route.x+(int)(((uint64_t)(s->route.width-1)*x+32767)/65535);
  p->y=s->route.y+(int)(((uint64_t)(s->route.height-1)*y+32767)/65535);
}
static int emit(struct touch_session *s,enum touch_action action,int id,uint64_t now,
                touch_sink sink,void *context) {
  if(s->sequence==UINT64_MAX) { s->fault=1;s->enabled=0;return 0; }
  struct touch_frame frame={.route=s->route,.epoch=s->epoch,.sequence=++s->sequence,
    .time_ns=now,.down_time_ns=s->down_ns,.action=action,.changed_id=id,.count=s->count};
  for(int i=0;i<s->count;++i) frame.points[i]=s->points[i];
  s->last_ns=now;
  if (!sink || !sink(context,&frame)) { s->fault=1;s->enabled=0;return 0; }
  return 1;
}
static int ready(struct touch_session *s,uint64_t epoch,uint64_t now) {
  return s->enabled && !s->fault && epoch==s->epoch && now>=s->last_ns;
}
int touch_bind(struct touch_session *s,struct touch_route route) {
  if (!route_valid(route) || s->fault || s->count || s->epoch==UINT64_MAX) return 0;
  s->route=route;s->epoch++;s->enabled=1;return 1;
}
int touch_press(struct touch_session *s,uint64_t epoch,uint32_t binding,uint16_t x,uint16_t y,
                 uint64_t now,touch_sink sink,void *context) {
  if(!ready(s,epoch,now) || find_binding(s,binding)>=0 || s->count==ANVIL_TOUCH_CONTACTS) return 0;
  int id=0;
  for(;id<ANVIL_TOUCH_CONTACTS;++id) {
    int used=0;for(int i=0;i<s->count;++i) if(s->points[i].id==id) used=1;
    if(!used) break;
  }
  struct touch_point *p=&s->points[s->count++];p->id=id;p->binding=binding;position(s,p,x,y);
  if(s->count==1) s->down_ns=now;
  return emit(s,TOUCH_PRESS,id,now,sink,context);
}
int touch_move(struct touch_session *s,uint64_t epoch,uint32_t binding,uint16_t x,uint16_t y,
                uint64_t now,touch_sink sink,void *context) {
  if(!ready(s,epoch,now)) return 0;
  int i=find_binding(s,binding);if(i<0) return 0;
  position(s,&s->points[i],x,y);
  return emit(s,TOUCH_MOVE,s->points[i].id,now,sink,context);
}
int touch_release(struct touch_session *s,uint64_t epoch,uint32_t binding,uint64_t now,
                   touch_sink sink,void *context) {
  if(!ready(s,epoch,now)) return 0;
  int i=find_binding(s,binding);if(i<0) return 0;
  if(!emit(s,TOUCH_RELEASE,s->points[i].id,now,sink,context)) return 0;
  for(int j=i+1;j<s->count;++j) s->points[j-1]=s->points[j];
  --s->count;return 1;
}
int touch_suspend(struct touch_session *s,uint64_t now,touch_sink sink,void *context) {
  s->enabled=0;
  if(!s->count) return !s->fault;
  if(now<s->last_ns) now=s->last_ns; /* Never discard a safety cancel for clock regression. */
  if(!emit(s,TOUCH_CANCEL,-1,now,sink,context)) return 0;
  s->count=0;
  return 1; /* fault, if already set, still requires explicit receiver reset. */
}
void touch_receiver_reset_confirmed(struct touch_session *s) {
  s->enabled=0;s->count=0;s->fault=0;
}
int touch_reconfigure(struct touch_session *s,struct touch_route route,int focused,
                      int stable,uint64_t now,touch_sink sink,void *context) {
  if(focused!=1 || stable!=1 || !route_valid(route)) {
    touch_suspend(s,now,sink,context);return 0;
  }
  if(s->enabled && !s->fault && s->route.runtime==route.runtime && s->route.surface==route.surface &&
      s->route.x==route.x && s->route.y==route.y && s->route.width==route.width && s->route.height==route.height) return 1;
  if(!touch_suspend(s,now,sink,context)) return 0;
  return touch_bind(s,route);
}
