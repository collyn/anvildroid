/* Experimental keymap sink. The runtime worker owns a control-only scrcpy
 * server at the fixed abstract name; network namespaces keep runtimes apart. */
extern int socket(int, int, int);
extern int connect(int, const void *, unsigned);
extern int close(int);
extern long send(int, const void *, size_t, int);
extern long recv(int, void *, size_t, int);
extern int __system_property_get(const char *, char *) __attribute__((weak));

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
  o->absolute_valid=0;
  if(!absolute_pointer_for(o))return 0;
  struct object *surface=find(o->pointer_surface),*s=game_fit_owner(surface);
  int sx=0,sy=0;
  if(surface!=s)for(struct object *sub=objects;sub;sub=sub->next)
    if(sub->surface==surface&&sub->parent==s&&object_is(sub,"wl_subsurface")) {
      sx=sub->x;sy=sub->y;break;
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
  if(!absolute_pointer_for(o))return 0;
  int bit=button==0x110?1:button==0x111?2:button==0x112?4:0;
  if(!bit)return 1;
  if(state)o->absolute_buttons|=bit;else o->absolute_buttons&=~bit;
  absolute_pointer_packet(o,state?0:1,bit);
  return 1;
}
static void absolute_pointer_leave(struct object *o) {
  int buttons=o->absolute_buttons;o->absolute_buttons=0;
  if(buttons)absolute_pointer_packet(o,1,buttons);
  o->absolute_valid=0;
}
static int absolute_pointer_axis(struct object *o,uint32_t axis,int32_t value) {
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
  memset(map_points,0,sizeof(map_points));
}
static void scrcpy_mapping_cancel(void) {
  if(map_count&&map_fd>=0) {
    unsigned char packet[32]={2,3};
    if(send(map_fd,packet,sizeof(packet),0x4000)!=(long)sizeof(packet))map_disconnect();
  }
  map_count=0;memset(map_points,0,sizeof(map_points));
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
static int scrcpy_mapping_key(struct object *s, struct keymap_overlay *h, int key, int state) {
  if(!s||!h||h->passive!=1||h->model.count<1||key<2||key>=768)return 0;
  int i=-1;for(int j=0;j<h->model.count;++j)if(h->model.points[j].key==key){i=j;break;}
  if(i<0)return 0;
  if(!keymap_package(s)||strcmp(keymap_package(s),h->package)||keymap_task_owner(s)!=h->task) {
    scrcpy_mapping_cancel();return 1;
  }
  if(map_broken)return 1;
  if(state){ if(map_count>=10)return 1; for(int j=0;j<10;++j)if(map_points[j]==key)return 1;
    int slot=0;while(slot<10&&map_points[slot])++slot;
    if(map_send(s,0,slot,h->model.points[i].nx,h->model.points[i].ny)) {
      map_points[slot]=key;++map_count;
    }
    return 1;
  }
  int slot=-1;for(int j=0;j<10;++j)if(map_points[j]==key){slot=j;break;}
  if (slot < 0) return 1;
  if(map_send(s,1,slot,h->model.points[i].nx,h->model.points[i].ny)) {
    map_points[slot]=0;--map_count;
  }
  return 1;
}
