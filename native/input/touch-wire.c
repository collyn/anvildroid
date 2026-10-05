#define _GNU_SOURCE
#include "touch-wire.h"
#include <sys/socket.h>
#include <string.h>

static void put32(unsigned char *p,uint32_t x) { for(int i=0;i<4;++i) p[i]=(unsigned char)(x>>(8*i)); }
static uint32_t get32(const unsigned char *p) { uint32_t x=0;for(int i=0;i<4;++i)x|=(uint32_t)p[i]<<(8*i);return x; }
static void put64(unsigned char *p,uint64_t x) { put32(p,(uint32_t)x);put32(p+4,(uint32_t)(x>>32)); }
static uint64_t get64(const unsigned char *p) { return get32(p)|((uint64_t)get32(p+4)<<32); }
static int route_ok(struct touch_route r) {
  return r.runtime && r.surface && r.x>=0 && r.y>=0 && r.x<=8192 && r.y<=8192 &&
    r.width>0 && r.height>0 && r.width<=8192-r.x && r.height<=8192-r.y;
}
static int frame_ok(const struct touch_frame *f) {
  if(!route_ok(f->route) || !f->epoch || !f->sequence || f->count<1 || f->count>ANVIL_TOUCH_CONTACTS ||
      f->down_time_ns>f->time_ns || f->action<TOUCH_PRESS || f->action>TOUCH_CANCEL) return 0;
  unsigned mask=0;int changed=0;
  for(int i=0;i<f->count;++i) {
    struct touch_point p=f->points[i];
    if(p.id<0 || p.id>=ANVIL_TOUCH_CONTACTS || (mask&(1u<<p.id)) ||
        p.x<f->route.x || p.y<f->route.y || p.x>=f->route.x+f->route.width || p.y>=f->route.y+f->route.height) return 0;
    for(int j=0;j<i;++j) if(f->points[j].binding==p.binding) return 0;
    mask|=1u<<p.id;changed|=p.id==f->changed_id;
  }
  return f->action==TOUCH_CANCEL ? f->changed_id==-1 : changed;
}
size_t touch_encode(const struct touch_frame *f,unsigned char *out,size_t capacity) {
  if(!frame_ok(f)) return 0;
  size_t length=88+16*(size_t)f->count;if(capacity<length) return 0;
  memset(out,0,length);put32(out,0x31545641);put32(out+4,(uint32_t)length);
  put32(out+8,(uint32_t)f->action);put32(out+12,(uint32_t)f->changed_id);put32(out+16,(uint32_t)f->count);
  put64(out+24,f->route.runtime);put64(out+32,f->route.surface);put64(out+40,f->epoch);
  put64(out+48,f->sequence);put64(out+56,f->time_ns);put64(out+64,f->down_time_ns);
  put32(out+72,(uint32_t)f->route.x);put32(out+76,(uint32_t)f->route.y);
  put32(out+80,(uint32_t)f->route.width);put32(out+84,(uint32_t)f->route.height);
  for(int i=0;i<f->count;++i) {
    unsigned char *p=out+88+16*i;put32(p,(uint32_t)f->points[i].id);
    put32(p+4,f->points[i].binding);put32(p+8,(uint32_t)f->points[i].x);put32(p+12,(uint32_t)f->points[i].y);
  }
  return length;
}
int touch_decode(const unsigned char *in,size_t length,struct touch_frame *out) {
  if(length<88 || length>TOUCH_WIRE_MAX || get32(in)!=0x31545641 || get32(in+4)!=length || get32(in+20)) return 0;
  uint32_t count=get32(in+16),action=get32(in+8),changed=get32(in+12);
  if(count<1 || count>ANVIL_TOUCH_CONTACTS || length!=88+16*count || action>TOUCH_CANCEL ||
      (changed!=UINT32_MAX && changed>=ANVIL_TOUCH_CONTACTS)) return 0;
  for(int i=72;i<88;i+=4) if(get32(in+i)>8192) return 0;
  struct touch_frame f={.route={get64(in+24),get64(in+32),(int)get32(in+72),(int)get32(in+76),(int)get32(in+80),(int)get32(in+84)},
    .epoch=get64(in+40),.sequence=get64(in+48),.time_ns=get64(in+56),.down_time_ns=get64(in+64),
    .action=(enum touch_action)action,.changed_id=changed==UINT32_MAX?-1:(int)changed,.count=(int)count};
  for(int i=0;i<f.count;++i) {
    const unsigned char *p=in+88+16*i;
    if(get32(p)>=ANVIL_TOUCH_CONTACTS || get32(p+8)>8192 || get32(p+12)>8192) return 0;
    f.points[i]=(struct touch_point){(int)get32(p),(int)get32(p+8),(int)get32(p+12),get32(p+4)};
  }
  if(!frame_ok(&f)) return 0;
  *out=f;return 1;
}
static int peer_ok(int fd,unsigned int uid) {
  struct ucred peer; socklen_t length=sizeof(peer);int type=0;socklen_t type_size=sizeof(type);
  return !getsockopt(fd,SOL_SOCKET,SO_TYPE,&type,&type_size) && type==SOCK_SEQPACKET &&
    !getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&peer,&length) && length==sizeof(peer) && peer.uid==uid;
}
int touch_packet_send(int fd,unsigned int uid,const struct touch_frame *f) {
  unsigned char packet[TOUCH_WIRE_MAX];size_t n=touch_encode(f,packet,sizeof(packet));
  return n && peer_ok(fd,uid) && send(fd,packet,n,MSG_DONTWAIT|MSG_NOSIGNAL)==(ssize_t)n;
}
int touch_packet_receive(int fd,unsigned int uid,struct touch_frame *f) {
  if(!peer_ok(fd,uid)) return 0;
  unsigned char packet[TOUCH_WIRE_MAX];
  ssize_t n=recv(fd,packet,sizeof(packet),MSG_DONTWAIT|MSG_TRUNC);
  return n>0 && n<=(ssize_t)sizeof(packet) && touch_decode(packet,(size_t)n,f);
}
static int same_route(struct touch_route a,struct touch_route b) {
  return a.runtime==b.runtime && a.surface==b.surface && a.x==b.x && a.y==b.y && a.width==b.width && a.height==b.height;
}
int touch_receiver_bind(struct touch_receiver *r,struct touch_route route,uint64_t epoch,uint64_t sequence) {
  if(!route_ok(route) || !epoch || r->count || r->fault || (r->epoch && epoch<=r->epoch)) return 0;
  r->route=route;r->epoch=epoch;r->sequence=sequence;r->focused=1;return 1;
}
void touch_receiver_blur(struct touch_receiver *r) { r->focused=0; }
int touch_receiver_apply(struct touch_receiver *r,const struct touch_frame *f,touch_sink sink,void *context) {
  int valid=!r->fault && frame_ok(f) && same_route(r->route,f->route) && f->epoch==r->epoch &&
    r->sequence!=UINT64_MAX && f->sequence==r->sequence+1 && f->time_ns>=r->time_ns &&
    (r->focused || f->action==TOUCH_CANCEL);
  if(valid) {
    int is_press=f->action==TOUCH_PRESS;
    valid=f->count==r->count+is_press && (r->count ? f->down_time_ns==r->down_time_ns :
                                        is_press && f->down_time_ns==f->time_ns);
    int new_points=0;
    for(int i=0;valid && i<f->count;++i) {
      int found=0;
      for(int j=0;j<r->count;++j)
        if(r->points[j].id==f->points[i].id && r->points[j].binding==f->points[i].binding) found=1;
      if(!found) {
        ++new_points;
        if(!is_press || f->points[i].id!=f->changed_id) valid=0;
      }
    }
    valid=valid && new_points==is_press;
  }
  if(!valid || !sink || !sink(context,f)) { r->fault=1;r->focused=0;return 0; }
  r->count=0;
  if(f->action!=TOUCH_CANCEL) for(int i=0;i<f->count;++i)
    if(f->action!=TOUCH_RELEASE || f->points[i].id!=f->changed_id) r->points[r->count++]=f->points[i];
  r->sequence=f->sequence;r->time_ns=f->time_ns;r->down_time_ns=f->down_time_ns;
  return 1;
}
int touch_receiver_disconnect(struct touch_receiver *r,uint64_t now,touch_sink sink,void *context) {
  r->focused=0;
  if(!r->count) return !r->fault;
  if(r->sequence==UINT64_MAX) { r->fault=1;return 0; }
  struct touch_frame cancel={.route=r->route,.epoch=r->epoch,.sequence=r->sequence+1,
    .time_ns=now<r->time_ns?r->time_ns:now,.down_time_ns=r->down_time_ns,
    .action=TOUCH_CANCEL,.changed_id=-1,.count=r->count};
  for(int i=0;i<r->count;++i) cancel.points[i]=r->points[i];
  if(!sink || !sink(context,&cancel)) { r->fault=1;return 0; }
  r->sequence=cancel.sequence;r->time_ns=cancel.time_ns;r->count=0;
  /* An earlier unknown delivery still needs a separate backend reset. */
  return !r->fault;
}
