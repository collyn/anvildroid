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

static int map_connect(void) {
  if (map_fd >= 0) return 1;
  int fd = socket(1, 1, 0);
  if (fd < 0) return 0;
  struct { unsigned short family; char path[108]; } address = {.family=1};
  const char *name = "scrcpy_00000000";
  address.path[0] = 0;
  memcpy(address.path + 1, name, strlen(name));
  /* Abstract addresses include the leading NUL in addrlen. */
  if (connect(fd, &address, (unsigned)(sizeof(address.family) + 1 + strlen(name)))) {
    __android_log_print(6, "AnvilKeymap", "scrcpy connect failed errno=%d", PROFILE_ERRNO);
    close(fd); return 0;
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
  if (!s || !s->app_id) return 0;
  int found = 0;
  for (int i=0;i<task_count;++i) if (!strcmp(tasks[i].package,s->app_id)) {
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
  if(!s->app_id||strcmp(s->app_id,h->package)||keymap_task_owner(s)!=h->task) {
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
