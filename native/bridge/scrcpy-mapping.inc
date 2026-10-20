static void map_advanced_reset(void);
static int map_advanced_motion(struct object *,int32_t,int32_t);
static int map_advanced_button(struct object *,uint32_t,uint32_t);
static int map_pointer_profile(struct object *);
static int map_pointer_xy(struct object *,int32_t,int32_t,int *,int *);
/* Experimental keymap sink. The runtime worker owns a control-only scrcpy
 * server at the fixed abstract name; network namespaces keep runtimes apart. */
extern int socket(int, int, int);
extern int connect(int, const void *, unsigned);
extern int close(int);
extern long send(int, const void *, size_t, int);
extern long recv(int, void *, size_t, int);

static int map_fd = -1;
static int map_broken;
static int map_points[10];
static int map_count;
static int map_dimension(const char *name) {
  char value[128]={0};int n=0;
  if(__system_property_get&&__system_property_get(name,value)>0)sscanf(value,"%d",&n);
  return n>0&&n<=65535?n:0;
}

static int absolute_pointer_enabled = -1;
static int map_connect(void);
static void map_disconnect(void);
static int map_task(struct object *,int *,int *,int *,int *);
static int absolute_pointer_for(struct object *o) {
  if(absolute_pointer_enabled<0) {
    char value[128]={0};
    absolute_pointer_enabled=__system_property_get&&
      __system_property_get("waydroid.anvil_absolute_pointer",value)>0&&!strcmp(value,"true");
  }
  return absolute_pointer_enabled&&game_fit_owner(find(o->pointer_surface))!=0;
}
static void map_put32(unsigned char *p,uint32_t v) {
  for(int j=0;j<4;++j)p[j]=(unsigned char)(v>>(24-j*8));
}
static int absolute_pointer_packet(struct object *o,int action,int changed) {
  int dw=map_dimension("waydroid.display_width"),dh=map_dimension("waydroid.display_height");
  if(!o->absolute_valid||!dw||!dh||!map_connect())return 0;
  unsigned char packet[32]={2,(unsigned char)action};
  memset(packet+2,255,8); /* scrcpy POINTER_ID_MOUSE */
  map_put32(packet+10,(uint32_t)o->absolute_x);map_put32(packet+14,(uint32_t)o->absolute_y);
  packet[18]=dw>>8;packet[19]=dw;packet[20]=dh>>8;packet[21]=dh;
  if(action!=1&&action!=7)packet[22]=packet[23]=255;
  map_put32(packet+24,(uint32_t)changed);map_put32(packet+28,(uint32_t)o->absolute_buttons);
  if(send(map_fd,packet,sizeof(packet),0x4000)!=(long)sizeof(packet)){map_disconnect();return 0;}
  return 1;
}
static int absolute_pointer_update(struct object *o,int32_t x,int32_t y,int motion) {
  /* Do not emit old-owner motion between keyboard leave and its matching
   * enter. The dispatch boundary resolves a genuine unmatched blur. */
  if(motion)for(struct object *keyboard=objects;keyboard;keyboard=keyboard->next)
    if(keyboard->display==o->display&&keyboard->keyboard_leave_pending)return 1;
  if((motion||map_pointer_profile(o))&&map_advanced_motion(o,x,y))return 1;
  o->absolute_valid=0;
  if(!absolute_pointer_for(o))return 0;
  struct object *surface=find(o->pointer_surface),*s=game_fit_owner(surface);
  int sx=0,sy=0;
  if(surface!=s)for(struct object *sub=objects;sub;sub=sub->next)
    if(sub->surface==surface&&sub->parent==s&&object_is(sub,"wl_subsurface")) {
      app_rotation_origin(sub,&sx,&sy);break;
    }
  /* Coordinates already passed through the shared inverse viewport transform.
   * HWC child positions remain in original logical canvas coordinates. */
  int px=(int)(((int64_t)x+256LL*sx)*hwc_scale_120/(256*120))+s->frame_origin_x;
  int py=(int)(((int64_t)y+256LL*sy)*hwc_scale_120/(256*120))+s->frame_origin_y;
  int tx,ty,w,h,dw=map_dimension("waydroid.display_width"),dh=map_dimension("waydroid.display_height");
  if(!map_task(s,&tx,&ty,&w,&h)||tx<0||ty<0||tx+w>dw||ty+h>dh)return 1;
  if(px<tx)px=tx;
  if(px>=tx+w)px=tx+w-1;
  if(py<ty)py=ty;
  if(py>=ty+h)py=ty+h-1;
  o->absolute_x=px;o->absolute_y=py;o->absolute_valid=1;
  if(motion&&(!map_count||o->absolute_buttons))absolute_pointer_packet(o,o->absolute_buttons?2:7,0);
  return 1;
}
static int absolute_pointer_button(struct object *o,uint32_t button,uint32_t state) {
  if(o->focus_blocked_button==button) {
    if(!state){o->focus_blocked_button=0;map_advanced_button(o,button,0);}
    return 1;
  }
  for(struct object *keyboard=objects;keyboard;keyboard=keyboard->next)
    if(keyboard->display==o->display&&keyboard->keyboard_leave_pending) {
      keymap_finish_keyboard_leaves(o->display);
      if(state)o->focus_blocked_button=button;else map_advanced_button(o,button,0);
      return 1;
    }
  if(map_advanced_button(o,button,state))return 1;
  if(!absolute_pointer_for(o))return 0;
  int bit=button==0x110?1:button==0x111?2:button==0x112?4:0;
  if(!bit)return 1;
  if(state)o->absolute_buttons|=bit;else o->absolute_buttons&=~bit;
  absolute_pointer_packet(o,state?0:1,bit);
  return 1;
}
static void keymap_finish_pointer_leaves(struct wl_display *display) {
  for(struct object *o=objects;o;o=o->next)if(o->display==display&&o->keymap_pointer_leave_pending){
    o->keymap_pointer_leave_pending=0;o->keymap_pointer_leave_surface=0;scrcpy_mapping_cancel();
  }
}
static void map_pointer_enter(struct object *o,struct proxy *surface) {
  if(!o->keymap_pointer_leave_pending)return;
  struct object *old=game_fit_owner(find(o->keymap_pointer_leave_surface));
  struct object *next=game_fit_owner(find(surface));
  int same=old&&old==next&&o->keymap_pointer_leave_surface!=surface;
  o->keymap_pointer_leave_pending=0;o->keymap_pointer_leave_surface=0;
  if(!same)scrcpy_mapping_cancel();
}
static void absolute_pointer_leave(struct object *o) {
  o->keymap_pointer_leave_pending=1;o->keymap_pointer_leave_surface=o->pointer_surface;
  int buttons=o->absolute_buttons;o->absolute_buttons=0;
  if(buttons)absolute_pointer_packet(o,1,buttons);
  o->absolute_valid=0;
}
static int absolute_pointer_axis(struct object *o,uint32_t axis,int32_t value) {
  if(map_pointer_profile(o))return 1;
  if(!absolute_pointer_for(o))return 0;
  if(!o->absolute_valid||axis>1||map_count||!map_connect())return 1;
  int dw=map_dimension("waydroid.display_width"),dh=map_dimension("waydroid.display_height");
  unsigned char packet[21]={3};
  map_put32(packet+1,(uint32_t)o->absolute_x);map_put32(packet+5,(uint32_t)o->absolute_y);
  packet[9]=dw>>8;packet[10]=dw;packet[11]=dh>>8;packet[12]=dh;
  int64_t fixed=-(int64_t)value*2048/(256*10);
  if(fixed>32767)fixed=32767;
  if(fixed< -32768)fixed=-32768;
  int at=axis==1?13:15;
  packet[at]=(unsigned char)((uint16_t)fixed>>8);packet[at+1]=(unsigned char)fixed;
  map_put32(packet+17,(uint32_t)o->absolute_buttons);
  if(send(map_fd,packet,sizeof(packet),0x4000)!=(long)sizeof(packet))map_disconnect();
  return 1;
}

static int map_connect(void) {
  if (map_fd >= 0) return 1;
  int fd = socket(1, 1, 0);
  if (fd < 0) return 0;
  struct { unsigned short family; char path[108]; } address = {.family=1};
  const char *name = "scrcpy_00000000";
  struct {long seconds,microseconds;} timeout={0,100000};
  setsockopt(fd,1,20,&timeout,sizeof(timeout));
  setsockopt(fd,1,21,&timeout,sizeof(timeout));
  address.path[0] = 0;
  memcpy(address.path + 1, name, strlen(name));
  /* Abstract addresses include the leading NUL in addrlen. */
  if (connect(fd, &address, (unsigned)(sizeof(address.family) + 1 + strlen(name)))) {
    __android_log_print(6, "AnvilKeymap", "scrcpy connect failed errno=%d", PROFILE_ERRNO);
    close(fd); return 0;
  }
  struct {int pid;unsigned uid,gid;} peer;
  unsigned peer_size=sizeof(peer);
  if(getsockopt(fd,1,17,&peer,&peer_size)||peer_size!=sizeof(peer)||peer.uid!=2000) {
    close(fd);return 0;
  }
  unsigned char hello;
  if (recv(fd, &hello, 1, 0) != 1 || hello != 0) { close(fd); return 0; }
  map_fd = fd; map_broken = 0; map_count = 0; return 1;
}
static void map_disconnect(void) {
  if (map_fd >= 0) close(map_fd);
  map_fd = -1; map_broken = 0; map_count = 0;
  map_advanced_reset();
  memset(map_points,0,sizeof(map_points));
}
static void scrcpy_mapping_cancel(void) {
  if(map_count&&map_fd>=0) {
    unsigned char packet[32]={2,3};
    if(send(map_fd,packet,sizeof(packet),0x4000)!=(long)sizeof(packet))map_disconnect();
  }
  map_count=0;memset(map_points,0,sizeof(map_points));map_advanced_reset();
}
static int map_task(struct object *s, int *x, int *y, int *w, int *h) {
  const char *package=keymap_package(s);
  if (!package) return 0;
  int found = 0;
  for (int i=0;i<task_count;++i) if (!strcmp(tasks[i].package,package)) {
    if (found++) return 0; /* never guess between duplicate package tasks */
    *x=tasks[i].x;*y=tasks[i].y;*w=tasks[i].width;*h=tasks[i].height;
  }
  return found==1 && *w>1 && *h>1 && *w<=8192 && *h<=8192;
}
static int map_send(struct object *s, int action, int pointer, int x, int y) {
  int tx,ty,w,h;if(!map_task(s,&tx,&ty,&w,&h))return 0;
  int dw=map_dimension("waydroid.display_width"),dh=map_dimension("waydroid.display_height");
  if(!dw||!dh||tx<0||ty<0||tx+w>dw||ty+h>dh)return 0;
  if (!map_connect()) return 0;
  if (x < 0) x = 0;
  if (x >= 65536) x = 65535;
  if (y < 0) y = 0;
  if (y >= 65536) y = 65535;
  int px=tx+(int)(((int64_t)(w-1)*x+32767)/65535);
  int py=ty+(int)(((int64_t)(h-1)*y+32767)/65535);
  unsigned char packet[32]={2,(unsigned char)action};
  uint64_t id=(uint64_t)pointer;
  packet[2]=id>>56;packet[3]=id>>48;packet[4]=id>>40;packet[5]=id>>32;
  packet[6]=id>>24;packet[7]=id>>16;packet[8]=id>>8;packet[9]=id;
  uint32_t values[]={px,py};
  for(int i=0;i<2;++i)for(int j=0;j<4;++j)packet[10+i*4+j]=(unsigned char)(values[i]>>(24-j*8));
  uint16_t shorts[]={(uint16_t)dw,(uint16_t)dh,(uint16_t)(action==1?0:65535)};
  for(int i=0;i<3;++i){packet[18+i*2]=shorts[i]>>8;packet[19+i*2]=shorts[i];}
  for(int i=0;i<2;++i)for(int j=0;j<4;++j)packet[24+i*4+j]=0;
  if(send(map_fd,packet,sizeof(packet),0x4000)!=(long)sizeof(packet)){map_disconnect();return 0;}
  __android_log_print(4,"AnvilKeymap","touch package=%s action=%d pointer=%d xy=%d,%d display=%dx%d",s->app_id,action,pointer,px,py,dw,dh);
  return 1;
}
/* Bounded state machine driven only under the bridge mutex. No worker threads,
 * queued commands or retained overlay pointers. Each control owns one contact. */
struct map_advanced {
  struct object *owner;
  struct keymap_point point;
  char package[241];
  int task, elapsed, total, down, phase, cycles, dx, dy, walking, suspended;
  int tx,ty,tw,th,lx,ly;
  int pointer_valid, pointer_x, pointer_y, ramp, ramp_x, ramp_y, target_x, target_y;
  long started;
  int64_t relative_x,relative_y;
};
static struct map_advanced map_advanced[10];
static int map_fire_slot=-1;
static int map_drag_slot=-1;
/* Fresh normalized cursor seed is separate from absolute mouse packets, whose
 * coordinates are display pixels. Clear it at every cancellation boundary. */
static struct object *map_cursor_pointer;
static int map_cursor_x,map_cursor_y;
static int map_fire_latched; /* Drain consumed mouse-up even after key release/cancel. */
/* Physical state survives cancellation and timed completion until key-up. Store
 * surface IDs rather than pointers: destroyed owners must never be dereferenced. */
static unsigned char map_held[768];
static int map_mouse_latched[5];
static int map_resetting;
static void scrcpy_mapping_release_key(int key) {if(key>=0&&key<768)map_held[key]=0;}
static void scrcpy_mapping_reconcile_keys(const uint32_t *keys,size_t count) {
  /* Enter is the compositor's authoritative keyboard snapshot. Releases while
   * another client held focus do not generate key-up events for this bridge. */
  memset(map_held,0,sizeof(map_held));
  for(size_t i=0;i<count;++i)if(keys[i]<768)map_held[keys[i]]=1;
}
static void map_advanced_reset(void) {
  memset(map_advanced,0,sizeof(map_advanced));map_fire_slot=-1;map_drag_slot=-1;map_cursor_pointer=0;
  for(struct object *o=objects;o;o=o->next){o->keymap_pointer_leave_pending=0;o->keymap_pointer_leave_surface=0;}
  if(!map_resetting){map_resetting=1;keymap_capture_stop();map_resetting=0;}
}
static int map_advanced_valid(struct map_advanced *a) {
  int found=0;
  for(struct object *s=objects;s;s=s->next)if(s==a->owner)found=1;
  int tx,ty,tw,th;
  if(!found || !map_task(a->owner,&tx,&ty,&tw,&th))return 0;
  const char *package=keymap_package(a->owner);
  if(!package||strlen(package)>240)return 0;
  if(!a->tw){memcpy(a->package,package,strlen(package)+1);a->tx=tx;a->ty=ty;a->tw=tw;a->th=th;}
  return !strcmp(package,a->package) && tx==a->tx && ty==a->ty && tw==a->tw && th==a->th &&
    !a->owner->resize.dragging && keymap_task_owner(a->owner)==a->task &&
    (!a->owner->overlay || a->owner->overlay->testing) && !a->owner->keymap_disabled;
}
static int map_advanced_emit(int slot,int action,int x,int y) {
  struct map_advanced *a=&map_advanced[slot];
  if(!map_advanced_valid(a)||!map_send(a->owner,action,slot,x,y)) {
    scrcpy_mapping_cancel();return 0;
  }
  a->down=action!=1;a->lx=x;a->ly=y;return 1;
}
static void map_advanced_end(int slot) {
  struct object *drag_owner=map_advanced[slot].owner;
  int restore_drag=map_drag_slot==slot;
  if(restore_drag)map_drag_slot=-1;
  if(map_advanced[slot].point.kind==KEYMAP_LOOK&&map_drag_slot>=0) {
    int drag=map_drag_slot;map_drag_slot=-1;map_advanced_end(drag);
  }
  if(map_advanced[slot].point.kind==KEYMAP_AIM && map_fire_slot>=0 && map_fire_slot!=slot) {
    int fire=map_fire_slot;map_fire_slot=-1;map_advanced_end(fire);
  }
  struct map_advanced *a=&map_advanced[slot];
  if(a->down&&!map_advanced_emit(slot,1,a->lx,a->ly))return;
  memset(a,0,sizeof(*a));map_points[slot]=0;
  if(map_count>0)--map_count;
  if(restore_drag)for(int i=0;i<10;++i)if(map_advanced[i].owner==drag_owner&&
      map_advanced[i].point.kind==KEYMAP_LOOK&&!map_advanced[i].suspended) {
    struct map_advanced *camera=&map_advanced[i];camera->relative_x=camera->relative_y=0;
    camera->pointer_valid=0;
    if(!camera->point.activation||keymap_capture_active(camera->owner))
      map_advanced_emit(i,0,camera->point.nx,camera->point.ny);
    break;
  }
}
/* Integer square root and projection keep radius physical on non-square tasks.
 * Work is bounded (32 iterations), independent of pointer distance. */
static uint64_t map_length(uint64_t n) {
  uint64_t root=0,bit=1ULL<<62;
  while(bit>n)bit>>=2;
  while(bit){if(n>=root+bit){n-=root+bit;root=(root>>1)+bit;}else root>>=1;bit>>=2;}
  return root;
}
static int map_direction_position(int slot,int nx,int ny) {
  struct map_advanced *a=&map_advanced[slot];struct keymap_point *p=&a->point;
  int x=nx,y=ny;
  if(p->radius) {
    int64_t dx=(int64_t)(nx-p->nx)*a->tw,dy=(int64_t)(ny-p->ny)*a->th;
    int64_t length=(int64_t)map_length((uint64_t)(dx*dx+dy*dy));
    int64_t radius=(int64_t)p->radius*(a->tw<a->th?a->tw:a->th);
    if(length&&(length>radius||p->kind==KEYMAP_WALK)) {
      x=p->nx+(int)(dx*radius/length/a->tw);y=p->ny+(int)(dy*radius/length/a->th);
    }
  }
  return map_advanced_emit(slot,2,x,y);
}
static void map_drag_begin(int slot) {
  struct map_advanced *fire=&map_advanced[slot];
  if(!fire->point.drag_shot)return;
  for(int i=0;i<10;++i)if(map_advanced[i].owner==fire->owner&&map_advanced[i].point.kind==KEYMAP_LOOK&&
      !map_advanced[i].suspended) {
    struct map_advanced *camera=&map_advanced[i];
    if(camera->point.activation&&!keymap_capture_active(camera->owner))return;
    if(camera->down&&!map_advanced_emit(i,1,camera->lx,camera->ly))return;
    fire->point.look_width=camera->point.look_width;fire->point.look_height=camera->point.look_height;
    fire->point.sensitivity_x=camera->point.sensitivity_x;fire->point.sensitivity_y=camera->point.sensitivity_y;
    fire->point.invert_y=camera->point.invert_y;
    fire->pointer_valid=camera->pointer_valid;fire->pointer_x=camera->pointer_x;fire->pointer_y=camera->pointer_y;
    camera->relative_x=camera->relative_y=0;map_drag_slot=slot;return;
  }
}
static long map_monotonic_ms(void) {
  struct { long sec,nsec; } stamp;
  if(syscall(228,1,&stamp))return 0; /* Linux/Android x86_64 CLOCK_MONOTONIC */
  return stamp.sec*1000+stamp.nsec/1000000;
}
static void map_advanced_advance(int milliseconds) {
  for(int i=0;i<10;++i) {
    struct map_advanced *a=&map_advanced[i];
    if(!a->owner)continue;
    if(!map_advanced_valid(a)){scrcpy_mapping_cancel();return;}
    struct keymap_point *p=&a->point;
    if(p->kind==KEYMAP_DPAD && p->ramp_ms && a->ramp<p->ramp_ms){
      a->ramp+=milliseconds;if(a->ramp>p->ramp_ms)a->ramp=p->ramp_ms;
      int x=a->ramp_x+(int)((int64_t)(a->target_x-a->ramp_x)*a->ramp/p->ramp_ms);
      int y=a->ramp_y+(int)((int64_t)(a->target_y-a->ramp_y)*a->ramp/p->ramp_ms);
      if(!map_advanced_emit(i,2,x,y))return;
    }
    if(p->kind==KEYMAP_LOOK) {
      if(a->suspended) {
        int focused=0;for(struct object *o=objects;o;o=o->next)
          if(o->keyboard_focus==a->owner&&!o->keyboard_leave_pending)focused=1;
        if(!focused){scrcpy_mapping_cancel();return;}
        continue;
      }
      if(map_drag_slot>=0)continue;
      if(p->activation&& !a->down && keymap_capture_active(a->owner))
        if(!map_advanced_emit(i,0,p->nx,p->ny))return;
      continue;
    }
    if(p->kind==KEYMAP_MOUSE_TOUCH||p->kind==KEYMAP_WALK||p->kind==KEYMAP_DPAD||p->kind==KEYMAP_AIM||p->kind==KEYMAP_SKILL ||
       (p->kind==KEYMAP_BASIC && p->end_mode!=1))continue;
    if(p->kind==KEYMAP_BASIC){a->elapsed+=milliseconds;if(a->elapsed>=p->duration)map_advanced_end(i);continue;}
    if(p->kind==KEYMAP_SWIPE&&p->end_mode==2&&a->elapsed>=p->duration)continue;
    a->elapsed+=milliseconds;
    if(p->kind==KEYMAP_MACRO)a->total+=milliseconds;
    if(p->kind==KEYMAP_MACRO && a->total>=p->duration*(p->repeats*2-1)) {map_advanced_end(i);continue;}
    if(p->kind==KEYMAP_SWIPE) {
      int t=a->elapsed>p->duration?p->duration:a->elapsed;
      int x=p->nx+(p->ex-p->nx)*t/p->duration;
      int y=p->ny+(p->ey-p->ny)*t/p->duration;
      if(!map_advanced_emit(i,2,x,y))return;
      if(t==p->duration&&p->end_mode!=2)map_advanced_end(i);
    } else if(a->elapsed>=p->duration) {
      a->elapsed=0;
      if(a->down) {
        if(!map_advanced_emit(i,1,a->lx,a->ly))return;
        if(p->kind==KEYMAP_MACRO && ++a->cycles>=p->repeats){map_advanced_end(i);continue;}
      } else {
        /* Macro alternates the two authored points; repeat stays at origin. */
        int end=p->kind==KEYMAP_MACRO&&(a->cycles&1);
        if(!map_advanced_emit(i,0,end?p->ex:p->nx,end?p->ey:p->ny))return;
      }
    }
  }
}
static void scrcpy_mapping_tick(void) {
  long now=map_monotonic_ms();
  for(int i=0;i<10;++i)if(map_advanced[i].owner) {
    if(!map_advanced[i].started)map_advanced[i].started=now;
    long delta=now-map_advanced[i].started;
    if(delta<0){scrcpy_mapping_cancel();return;}
    if(delta>10000)delta=10000;
    /* Advance all controls by the shared poll interval, once. */
    for(int j=0;j<10;++j)if(map_advanced[j].owner)map_advanced[j].started=now;
    map_advanced_advance((int)delta);return;
  }
}
static int map_advanced_key(struct object *s,struct keymap_overlay *h,int index,int key,int state) {
  struct keymap_point p=h->model.points[index];
  if((p.kind==KEYMAP_LOOK&&p.activation&& !state)||
     (p.kind==KEYMAP_SKILL&&p.skill_mode==KEYMAP_SKILL_TOGGLE&&!state))return 1;
  int slot=-1;
  for(int i=0;i<10;++i)if(map_advanced[i].owner==s&&map_advanced[i].point.key==p.key&&map_advanced[i].point.source==p.source)slot=i;
  if(p.kind==KEYMAP_DPAD) {
    if(!map_connect())return 1;
    if(slot<0&&(!state||key==p.walk_key))return 1;
    if(slot<0){for(int i=0;i<10;++i)if(!map_points[i]){slot=i;break;}}
    if(slot<0)return 1;
    struct map_advanced *a=&map_advanced[slot];
    if(!a->owner){*a=(struct map_advanced){.owner=s,.point=p,.task=h->task,.walking=p.walk_key?map_held[p.walk_key]:0};map_points[slot]=p.key?p.key:769;++map_count;
      if(!map_advanced_emit(slot,0,p.nx,p.ny))return 1;}
    int bit=0;for(int d=0;d<4;++d)if(key==keymap_dpad_key(&p,d))bit=1<<d;
    if(key==p.walk_key)a->walking=state;
    if(state)a->phase|=bit;else a->phase&=~bit;
    a->dx=!!(a->phase&8)-!!(a->phase&2);a->dy=!!(a->phase&4)-!!(a->phase&1);
    if(!a->phase){map_advanced_end(slot);return 1;}
    int radius=p.ex-p.nx,vertical=p.ey-p.ny;
    if(radius<0)radius=-radius;
    if(vertical<0)vertical=-vertical;
    if(vertical>radius)radius=vertical;
    if(radius<1024)radius=1024;
    if(radius>16384)radius=16384;
    if(a->walking)radius=radius*(p.walk_radius?p.walk_radius:40)/100;
    if(a->dx&&a->dy)radius=radius*46341/65536; /* normalized diagonal */
    int target_x,target_y;
    if(p.radius){
      int short_edge=a->tw<a->th?a->tw:a->th;
      int r=p.radius;if(a->walking)r=r*(p.walk_radius?p.walk_radius:40)/100;
      int rx=(int)((int64_t)r*short_edge/a->tw),ry=(int)((int64_t)r*short_edge/a->th);
      if(a->dx&&a->dy){rx=rx*46341/65536;ry=ry*46341/65536;}
      target_x=p.nx+a->dx*rx;target_y=p.ny+a->dy*ry;
    }else {target_x=p.nx+a->dx*radius;target_y=p.ny+a->dy*radius;}
    if(p.ramp_ms){a->ramp=0;a->ramp_x=a->lx;a->ramp_y=a->ly;a->target_x=target_x;a->target_y=target_y;}
    else map_advanced_emit(slot,2,target_x,target_y);
    return 1;
  }
  if(!state){if(slot>=0)map_advanced_end(slot);return 1;}
  if(slot>=0&&p.kind==KEYMAP_SKILL&&p.skill_mode==KEYMAP_SKILL_TOGGLE){map_advanced_end(slot);return 1;}
  if(slot>=0){if(p.kind==KEYMAP_LOOK&&p.activation){map_advanced_end(slot);keymap_capture_stop();}return 1;}
  for(int i=0;i<10;++i)if(!map_points[i]){slot=i;break;}
  if(slot<0)return 1;
  map_advanced[slot]=(struct map_advanced){.owner=s,.point=p,.task=h->task};
  /* Connect before registering state: a fresh connection clears contact slots. */
  if(!map_connect()){map_advanced_reset();return 1;}
  map_points[slot]=p.key?p.key:769;++map_count;
  map_advanced[slot].started=map_monotonic_ms();
  if(p.kind==KEYMAP_LOOK&&p.activation) {
    if(p.suspend_key&&map_held[p.suspend_key]){map_advanced[slot].suspended=1;return 1;}
    if(!keymap_capture_start(s)){memset(&map_advanced[slot],0,sizeof(map_advanced[slot]));map_points[slot]=0;--map_count;}
    return 1;
  }
  if(p.kind==KEYMAP_LOOK)for(struct object *pointer=objects;pointer;pointer=pointer->next)
    if(pointer->pointer_surface&&game_fit_owner(find(pointer->pointer_surface))==s){
      int32_t x=pointer->csd_x*256,y=pointer->csd_y*256;
      x=content_clip_input(pointer->pointer_surface,x,0);y=content_clip_input(pointer->pointer_surface,y,1);
      x=full_ui_input(pointer->pointer_surface,x,0);y=full_ui_input(pointer->pointer_surface,y,1);
      x=game_fit_input(pointer->pointer_surface,x,0);y=game_fit_input(pointer->pointer_surface,y,1);
      struct map_advanced *a=&map_advanced[slot];
      a->pointer_valid=map_pointer_xy(pointer,x,y,&a->pointer_x,&a->pointer_y);break;
    }
  map_drag_begin(slot);
  if(!map_advanced_emit(slot,0,p.nx,p.ny))return 1;
  if((p.kind==KEYMAP_SKILL&&p.skill_mode==KEYMAP_SKILL_IMMEDIATE)||p.kind==KEYMAP_WALK) {
    for(struct object *pointer=objects;pointer;pointer=pointer->next)
      if(pointer->pointer_surface&&game_fit_owner(find(pointer->pointer_surface))==s&&pointer==map_cursor_pointer) {
        if(!map_direction_position(slot,map_cursor_x,map_cursor_y))return 1;
        break;
      }
    if(p.kind==KEYMAP_SKILL&&p.skill_mode==KEYMAP_SKILL_IMMEDIATE)map_advanced_end(slot);
  }
  return 1;
}
/* Every pointer path uses the same content-to-Android conversion. */
static int map_pointer_xy(struct object *o,int32_t x,int32_t y,int *nx,int *ny) {
  struct object *surface=find(o->pointer_surface),*s=game_fit_owner(surface);
  int tx,ty,w,h,sx=0,sy=0;
  if(!s||!map_task(s,&tx,&ty,&w,&h))return 0;
  if(surface!=s)for(struct object *sub=objects;sub;sub=sub->next)
    if(sub->surface==surface&&sub->parent==s&&object_is(sub,"wl_subsurface")){app_rotation_origin(sub,&sx,&sy);break;}
  int px=(int)(((int64_t)x+256LL*sx)*hwc_scale_120/(256*120))+s->frame_origin_x;
  int py=(int)(((int64_t)y+256LL*sy)*hwc_scale_120/(256*120))+s->frame_origin_y;
  *nx=keymap_normalize(px-tx,w);*ny=keymap_normalize(py-ty,h);return 1;
}
static struct keymap_overlay *map_pointer_hints(struct object *o,struct object **owner) {
  struct object *s=game_fit_owner(find(o->pointer_surface));*owner=s;
  if(!s||s->keymap_disabled||s->resize.dragging||(s->overlay&&!s->overlay->testing))return 0;
  struct keymap_overlay *h=s->overlay&&s->overlay->testing?s->overlay:s->hints;
  if(!h||(!h->testing&&h->passive!=1))return 0;
  for(int i=0;i<10;++i)if(map_advanced[i].owner==s&&map_advanced[i].suspended)return 0;
  for(struct object *keyboard=objects;keyboard;keyboard=keyboard->next)
    if(keyboard->display==o->display&&keyboard->keyboard_focus&&
       (keyboard->keyboard_focus!=s||keyboard->keymap_panel_focus))return 0;
  const char *package=keymap_package(s);
  return package&&!strcmp(package,h->package)&&keymap_task_owner(s)==h->task?h:0;
}
static int map_pointer_profile(struct object *o) {
  struct object *s;struct keymap_overlay *h=map_pointer_hints(o,&s);
  if(h)for(int i=0;i<h->model.count;++i)if(h->model.points[i].kind==KEYMAP_MOUSE_TOUCH)return 1;
  for(int i=0;i<10;++i)if(map_advanced[i].owner==s&&s&&map_advanced[i].point.kind==KEYMAP_LOOK&&!map_advanced[i].suspended)return 1;
  return 0;
}
static int map_look_delta(int i,int64_t dx,int64_t dy) {
  struct map_advanced *a=&map_advanced[i];struct keymap_point *p=&a->point;
  if(!a->down)return 1;
  if(!dx&&!dy)return 1;
  dx=dx*(p->sensitivity_x?p->sensitivity_x:100)/100;
  dy=dy*(p->sensitivity_y?p->sensitivity_y:100)/100;
  if(p->invert_y)dy=-dy;
  int width=p->look_width?p->look_width:8192,height=p->look_height?p->look_height:8192;
  if(width>p->nx)width=p->nx;
  if(width>65535-p->nx)width=65535-p->nx;
  if(height>p->ny)height=p->ny;
  if(height>65535-p->ny)height=65535-p->ny;
  int64_t x=a->lx+dx,y=a->ly+dy;
  if(x<p->nx-width||x>p->nx+width||y<p->ny-height||y>p->ny+height){
    /* Bound one event to one region traversal, preserving the forward drag
     * before lifting. The next event continues from center, never reverses. */
    if(x<p->nx-width||x>p->nx+width){
      x=x<p->nx-width?p->nx-width:p->nx+width;
      y=a->ly+dy*(x-a->lx)/dx;
    }
    if(y<p->ny-height||y>p->ny+height){
      y=y<p->ny-height?p->ny-height:p->ny+height;
      x=a->lx+dx*(y-a->ly)/dy;
    }
    if((x!=a->lx||y!=a->ly)&&!map_advanced_emit(i,2,(int)x,(int)y))return 0;
    if(!map_advanced_emit(i,1,a->lx,a->ly))return 0;
    return map_advanced_emit(i,0,p->nx,p->ny);
  }
  return map_advanced_emit(i,2,x,y);
}
/* Relative events are host surface deltas. Apply the linear portions of the
 * same inverse viewport chain as absolute motion; translations cancel. Carry
 * eight extra fractional bits through every ratio, then retain the normalized
 * remainder across events so fine unaccelerated input is not discarded. */
static int64_t map_relative_ratio(int64_t delta,int64_t slope) {
  int64_t n=(delta/65536)*slope+(delta%65536)*slope/65536;
  int64_t limit=(int64_t)INT32_MAX*256;
  return n>limit?limit:n< -limit? -limit:n;
}
static int64_t map_relative_scale(struct object *owner,int32_t fixed,int axis) {
  int64_t delta=(int64_t)fixed*256;
  int64_t slope=(int64_t)full_ui_input(owner->p,65536,axis)-full_ui_input(owner->p,0,axis);
  delta=map_relative_ratio(delta,slope);
  slope=(int64_t)game_fit_input(owner->p,65536,axis)-game_fit_input(owner->p,0,axis);
  delta=map_relative_ratio(delta,slope);
  return map_relative_ratio(delta,(int64_t)65536*hwc_scale_120/120);
}
static void scrcpy_mapping_relative(struct object *owner,int32_t dx_fixed,int32_t dy_fixed) {
  for(int i=0;i<10;++i){struct map_advanced *a=&map_advanced[i];
    if(a->owner!=owner||a->point.kind!=KEYMAP_LOOK||!a->point.activation)continue;
    if(!keymap_capture_active(owner))return;
    if(a->suspended)return;
    if(map_drag_slot<0)for(int fire=0;fire<10;++fire)
      if(map_advanced[fire].owner==owner&&map_advanced[fire].point.drag_shot){map_drag_begin(fire);break;}
    if(map_drag_slot>=0){i=map_drag_slot;a=&map_advanced[i];}
    if(!a->down&&!map_advanced_emit(i,0,a->point.nx,a->point.ny))return;
    if(!map_advanced_valid(a)){scrcpy_mapping_cancel();return;}
    a->relative_x+=map_relative_scale(owner,dx_fixed,0)*65535;
    a->relative_y+=map_relative_scale(owner,dy_fixed,1)*65535;
    int64_t dx=a->relative_x/(65536LL*a->tw),dy=a->relative_y/(65536LL*a->th);
    a->relative_x%=65536LL*a->tw;a->relative_y%=65536LL*a->th;
    map_look_delta(i,dx,dy);return;
  }
}
static int scrcpy_mapping_escape(void) {
  int consumed=0;for(int i=0;i<10;++i)if(map_advanced[i].owner)consumed=1;
  scrcpy_mapping_cancel();return consumed;
}
static int map_advanced_motion(struct object *o,int32_t x,int32_t y) {
  struct object *s=game_fit_owner(find(o->pointer_surface));
  if(!s)return 0;
  int nx,ny;if(!map_pointer_xy(o,x,y,&nx,&ny))return 0;
  /* Seed free-touch coordinates even when absolute mouse forwarding is off. */
  o->absolute_x=nx;o->absolute_y=ny;o->absolute_valid=1;
  map_cursor_pointer=o;map_cursor_x=nx;map_cursor_y=ny;
  int consumed=0;
  for(int i=0;i<10;++i){struct map_advanced *a=&map_advanced[i];
    if(i==map_fire_slot||a->point.key==768||a->owner!=s)continue;
    if(i==map_drag_slot||a->point.kind==KEYMAP_LOOK){
      if(a->suspended)continue;
      if(a->point.kind==KEYMAP_LOOK&&map_drag_slot>=0){consumed=1;continue;}
      if(a->point.activation){consumed=1;continue;}
      if(!a->pointer_valid){a->pointer_valid=1;a->pointer_x=nx;a->pointer_y=ny;consumed=1;continue;}
      int dx=nx-a->pointer_x,dy=ny-a->pointer_y;a->pointer_x=nx;a->pointer_y=ny;
      if(!map_look_delta(i,dx,dy))return 1;
      consumed=1;continue;
    }
    if(a->point.kind!=KEYMAP_AIM&&a->point.kind!=KEYMAP_SKILL&&a->point.kind!=KEYMAP_WALK&&a->point.kind!=KEYMAP_MOUSE_TOUCH)continue;
    if(a->point.kind==KEYMAP_SKILL||a->point.kind==KEYMAP_WALK) {
      if(!map_direction_position(i,nx,ny))return 1;
    } else if(!map_advanced_emit(i,2,nx,ny))return 1;
    consumed=1;
  }
  return consumed||map_pointer_profile(o);
}

static int map_advanced_release(uint32_t button) {
  int bi=(int)button-272;
  if(bi>=0&&bi<5&&map_mouse_latched[bi]){
    map_mouse_latched[bi]=0;map_held[button]=0;
    for(int i=0;i<10;++i)if(map_advanced[i].owner&&map_advanced[i].point.key==(int)button&&map_advanced[i].point.source==KEYMAP_SOURCE_MOUSE&&
        !(map_advanced[i].point.kind==KEYMAP_LOOK&&map_advanced[i].point.activation)&&
        !(map_advanced[i].point.kind==KEYMAP_SKILL&&map_advanced[i].point.skill_mode==KEYMAP_SKILL_TOGGLE))map_advanced_end(i);
    /* Free touch uses pointer source key zero. */
    if(button==272)for(int i=0;i<10;++i)if(map_advanced[i].owner&&map_advanced[i].point.kind==KEYMAP_MOUSE_TOUCH)map_advanced_end(i);
    return 1;
  }
  if(button==272&&map_fire_latched){
    map_fire_latched=0;
    if(map_fire_slot>=0){int slot=map_fire_slot;map_fire_slot=-1;map_advanced_end(slot);}
    return 1;
  }
  return 0;
}
static int map_advanced_button(struct object *o,uint32_t button,uint32_t state) {
  if(!state&&map_advanced_release(button))return 1;
  int bi=(int)button-272;
  struct object *target;struct keymap_overlay *h=map_pointer_hints(o,&target);
  if(h&&bi>=0&&bi<5){
    int index=-1;
    for(int i=0;i<h->model.count;++i)if(h->model.points[i].source==1&&h->model.points[i].key==(int)button){index=i;break;}
    if(index<0&&button==272)for(int i=0;i<h->model.count;++i)if(h->model.points[i].kind==KEYMAP_MOUSE_TOUCH){index=i;break;}
    if(index>=0){
      if(state&&!map_mouse_latched[bi]){
        map_mouse_latched[bi]=1;
        if(h->model.points[index].kind==KEYMAP_MOUSE_TOUCH){
          if(!o->absolute_valid)return 1;
          struct keymap_point saved=h->model.points[index];
          h->model.points[index].nx=o->absolute_x;h->model.points[index].ny=o->absolute_y;
          map_advanced_key(target,h,index,(int)button,1);h->model.points[index]=saved;
        }else map_advanced_key(target,h,index,(int)button,1);
      }
      return 1;
    }
  }
  if(button!=0x110)return map_pointer_profile(o);
  if(map_fire_latched) {
    if(state)return 1;
    map_fire_latched=0;
    if(map_fire_slot>=0){int slot=map_fire_slot;map_fire_slot=-1;map_advanced_end(slot);}
    return 1;
  }
  struct object *s=game_fit_owner(find(o->pointer_surface));
  for(int i=0;i<10;++i)if(map_advanced[i].owner==s&&s&&map_advanced[i].point.kind==KEYMAP_AIM) {
    if(!state){if(map_fire_slot>=0){int slot=map_fire_slot;map_fire_slot=-1;map_advanced_end(slot);}return 1;}
    map_fire_latched=1;
    if(map_fire_slot>=0)return 1;
    for(int j=0;j<10;++j)if(!map_points[j]) {
      struct keymap_point p=map_advanced[i].point;
      p.kind=KEYMAP_SKILL;p.key=768; /* fire stays stationary at authored endpoint */
      map_advanced[j]=(struct map_advanced){.owner=s,.task=map_advanced[i].task,.point=p};
      map_points[j]=768;++map_count;map_fire_slot=j;
      map_advanced_emit(j,0,p.ex,p.ey);break;
    }
    return 1;
  }
  return map_pointer_profile(o);
}
static int scrcpy_mapping_key(struct object *s, struct keymap_overlay *h, int key, int state) {
  if(!state)scrcpy_mapping_release_key(key);
  if(!s||!h||(h->passive!=1&&!h->testing)||h->model.count<1||key<2||key>=768)return 0;
  int i=-1;for(int j=0;j<h->model.count;++j)if(h->model.points[j].source==KEYMAP_SOURCE_KEY&&h->model.points[j].key==key){i=j;break;}
  if(i<0)for(int j=0;j<h->model.count;++j)
    if(h->model.points[j].kind==KEYMAP_DPAD){
      const struct keymap_point *p=&h->model.points[j];
      for(int d=0;d<4;++d)if(key==keymap_dpad_key(p,d))i=j;
      if(key==p->walk_key)i=j;
      if(i>=0)break;
    }
  if(i<0)for(int j=0;j<h->model.count;++j)
    if(key==h->model.points[j].suspend_key||key==h->model.points[j].cancel_key){i=j;break;}
  if(i<0)return 0;
  if(!keymap_package(s)||strcmp(keymap_package(s),h->package)||keymap_task_owner(s)!=h->task) {
    scrcpy_mapping_cancel();return 1;
  }
  if(map_broken)return 1;
  if(h->model.points[i].source!=0&&key!=h->model.points[i].suspend_key&&key!=h->model.points[i].cancel_key)return 0;
  if(state){if(map_held[key])return 1;map_held[key]=1;}else map_held[key]=0;
  struct keymap_point *p=&h->model.points[i];
  if(key==p->suspend_key||key==p->cancel_key) {
    for(int slot=0;slot<10;++slot)if(map_advanced[slot].owner==s&&map_advanced[slot].point.key==p->key) {
      struct map_advanced *a=&map_advanced[slot];
      if(key==p->cancel_key) {
        if(state){if(a->down&&!map_advanced_emit(slot,2,p->cancel_x,p->cancel_y))return 1;map_advanced_end(slot);}
      } else if(state) {
        if(map_drag_slot>=0){int drag=map_drag_slot;map_drag_slot=-1;map_advanced_end(drag);}
        if(a->down&&!map_advanced_emit(slot,1,a->lx,a->ly))return 1;
        a->suspended=1;a->relative_x=a->relative_y=0;keymap_capture_stop();
      } else if(a->suspended) {
        a->suspended=0;
        if(!map_advanced_valid(a)||!keymap_capture_start(s))scrcpy_mapping_cancel();
      }
      return 1;
    }
    return 1;
  }
  return map_advanced_key(s,h,i,key,state);
}
