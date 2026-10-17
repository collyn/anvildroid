/* GPU-only presentation for image-native HWC. Uses public Wayland wire
 * metadata and the pinned Venus copy protocol; no private HWC C++ layouts.
 * Copy at commit, after HWC's producer fence wait, and never overwrite a
 * destination until wl_buffer.release. All state is under the bridge mutex. */
extern int dup(int);
extern int socket(int, int, int);
extern int connect(int, const void *, unsigned);
extern int close(int);
extern long send(int, const void *, size_t, int);
extern long recv(int, void *, size_t, int);
extern void *memcpy(void *, const void *, size_t);
struct nv_iov {void *base; size_t length;};
struct nv_msg {void *name; unsigned namelen; struct nv_iov *iov; size_t iovlen;
               void *control; size_t controllen; int flags;};
struct nv_cmsg {size_t length; int level, type;};
_Static_assert(sizeof(struct nv_msg)==56 && sizeof(struct nv_cmsg)==16,"LP64 socket ABI");
extern long sendmsg(int, const struct nv_msg *, int);
extern long recvmsg(int, struct nv_msg *, int);
struct nv_ring;
struct nv_slot {struct proxy *buffer; struct nv_ring *ring; int busy;};
struct nv_ring {
  struct nv_ring *next;
  struct object *owner;
  int socket, source_fd, logical_fd, notified;
  struct wl_display *display;
  uint64_t device, inode, modifier, used;
  uint32_t width, height, format, source_stride, offset;
  unsigned next_slot, dropped;
  uint32_t stride;
  struct nv_slot slots[3];
};
static struct nv_ring *nv_rings;
static int nv_enabled=-1;
static uint64_t nv_tick, nv_setups, nv_reuses, nv_frames, nv_copy_ns, nv_copy_max, nv_drops;
/* Kernel fstat layout is stable for the x86_64 Android bridge. Retain the
 * source FD so an inode cannot be recycled while an entry remains cached. */
static int nv_identity(int fd,uint64_t *device,uint64_t *inode) {
  _Alignas(8) unsigned char st[144]={0};long result;
  __asm__ volatile("syscall":"=a"(result):"a"(5),"D"((long)fd),"S"(st):"rcx","r11","memory");
  if(result)return 0;
  memcpy(device,st,8);memcpy(inode,st+8,8);return 1;
}
static uint64_t nv_now(void) {
  struct {long seconds,nanoseconds;} stamp;
  long status;
  __asm__ volatile("syscall":"=a"(status):"a"(228),"D"(1L),"S"(&stamp):"rcx","r11","memory");
  return status?0:(uint64_t)stamp.seconds*1000000000+(uint64_t)stamp.nanoseconds;
}
static int nv_copy_enabled(void) {
  if(nv_enabled<0) {
    int (*get)(const char *,char *)=dlsym((void *)0,"__system_property_get");
    char value[92]="";
    nv_enabled=get&&get("waydroid.anvil_gpu_copy_presentation",value)>0&&!strcmp(value,"true");
  }
  return nv_enabled;
}
static int nv_transfer(int fd,void *buffer,size_t size,int writing) {
  unsigned char *p=buffer;
  while(size) {
    long n=writing?send(fd,p,size,0x4000):recv(fd,p,size,0);
    if(n<=0)return 0;
    p+=n;size-=(size_t)n;
  }
  return 1;
}
static int nv_send_fd(int socket_fd,int fd) {
  char byte=0; _Alignas(8) unsigned char control[24]={0};
  struct nv_iov io={&byte,1};struct nv_msg msg={.iov=&io,.iovlen=1,.control=control,.controllen=24};
  struct nv_cmsg *c=(void *)control;c->length=20;c->level=1;c->type=1;
  memcpy(control+16,&fd,4);
  return sendmsg(socket_fd,&msg,0x4000)==1;
}
static int nv_recv_fd(int socket_fd) {
  char byte; _Alignas(8) unsigned char control[48]={0};
  struct nv_iov io={&byte,1};struct nv_msg msg={.iov=&io,.iovlen=1,.control=control,.controllen=48};
  if(recvmsg(socket_fd,&msg,0x40000000)!=1)return -1;
  int result=-1,count=0;
  for(size_t off=0;off+16<=msg.controllen;) {
    struct nv_cmsg *c=(void *)(control+off);
    if(c->length<16||c->length>msg.controllen-off)break;
    if(c->level==1&&c->type==1)for(size_t j=16;j+4<=c->length;j+=4) {
      int fd;memcpy(&fd,control+off+j,4);
      if(count++)close(fd);else result=fd;
    }
    off+=(c->length+7)&~(size_t)7;
  }
  if(count!=1||(msg.flags&8)){if(result>=0)close(result);return -1;}
  return result;
}
static int nv_connect(void) {
  int fd=socket(1,1|0x80000,0);if(fd<0)return -1;
  struct {unsigned short family;char path[108];} address={.family=1,.path="/dev/venus/venus.sock"};
  struct {long seconds,microseconds;} timeout={1,0};
  setsockopt(fd,1,20,&timeout,sizeof(timeout));setsockopt(fd,1,21,&timeout,sizeof(timeout));
  char name[]="hwc-present";uint32_t hello[]={sizeof(name),8};
  if(connect(fd,&address,sizeof(address))||!nv_transfer(fd,hello,sizeof(hello),1)||
      !nv_transfer(fd,name,sizeof(name),1)){close(fd);return -1;}
  return fd;
}
static int (*nv_open)(void)=nv_connect;
static void nv_trim(void);
static void nv_release(void *data,struct proxy *buffer) {
  (void)buffer;
  struct nv_slot *slot=data;
  pthread_mutex_lock(mutex);
  struct nv_ring *ring=slot->ring;slot->busy=0;
  int busy=0;for(int i=0;i<3;++i)busy|=ring->slots[i].busy;
  void (*notify)(void *,struct proxy *)=0;void *notify_data=0;struct proxy *logical=0;
  /* One logical HWC buffer owns all destinations. Forward one release only
   * after its last destination has retired, never once for every ring slot. */
  if(!busy&&!ring->notified&&ring->owner&&ring->owner->listener) {
    ring->notified=1;
    notify=(void (*)(void *,struct proxy *))ring->owner->listener[0];
    notify_data=ring->owner->listener_data;logical=ring->owner->p;
  }
  nv_trim();
  pthread_mutex_unlock(mutex);
  /* The HWC callback may destroy its logical proxy and reenter the bridge. */
  if(notify)notify(notify_data,logical);
}
static void (*nv_release_listener[])(void)={(void (*)(void))nv_release};
static void nv_destroy(struct nv_ring *ring) {
  struct nv_ring **at=&nv_rings;
  while(*at&&*at!=ring)at=&(*at)->next;
  if(*at)*at=ring->next;
  for(struct object *o=objects;o;o=o->next) {
    if(o->nv_pending==ring)o->nv_pending=0;
    for(int i=0;i<3;++i)if(o->attached_buffer==ring->slots[i].buffer)o->attached_buffer=0;
  }
  for(int i=0;i<3;++i)if(ring->slots[i].buffer) {
    real_marshal(ring->slots[i].buffer,0,0,0,1);real_destroy(ring->slots[i].buffer);
  }
  if(ring->logical_fd>=0)close(ring->logical_fd);
  if(ring->socket>=0)close(ring->socket);
  if(ring->source_fd>=0)close(ring->source_fd);
  free(ring);
}
static void nv_format(void *data,struct proxy *p,uint32_t format) {
  struct object *o=data;
  if(o->listener&&o->listener[0])((void (*)(void *,struct proxy *,uint32_t))o->listener[0])(o->listener_data,p,format);
}
static void nv_modifier(void *data,struct proxy *p,uint32_t format,uint32_t high,uint32_t low) {
  struct object *o=data;
  if((high>>24)==3)o->nv_nvidia=1;
  if(!high&&!low&&o->nv_format_count<32)o->nv_formats[o->nv_format_count++]=format;
  if(o->listener&&o->listener[1])((void (*)(void *,struct proxy *,uint32_t,uint32_t,uint32_t))o->listener[1])(o->listener_data,p,format,high,low);
}
static void (*nv_dmabuf_listener[])(void)={(void (*)(void))nv_format,(void (*)(void))nv_modifier};
static int nv_linear(struct object *manager,uint32_t format) {
  if(!manager||manager->nv_nvidia)return 0;
  for(unsigned i=0;i<manager->nv_format_count;++i)if(manager->nv_formats[i]==format)return 1;
  return 0;
}
static void nv_orphan(struct nv_ring *ring) {
  ring->owner=0;ring->used=++nv_tick;
  for(struct object *o=objects;o;o=o->next)if(o->nv_pending==ring)o->nv_pending=0;
}
static int nv_busy(struct nv_ring *ring) {
  return ring->slots[0].busy||ring->slots[1].busy||ring->slots[2].busy;
}
/* Bound idle retention to 32 allocations / 192 MiB. Busy buffers are never
 * evicted: destroying an HWC logical proxy does not release physical buffers. */
static void nv_trim(void) {
  for(;;) {
    uint64_t bytes=0;unsigned count=0;struct nv_ring *oldest=0;
    for(struct nv_ring *r=nv_rings;r;r=r->next) {
      ++count;bytes+=(uint64_t)r->stride*r->height*3;
      if(!r->owner&&!nv_busy(r)&&(!oldest||r->used<oldest->used))oldest=r;
    }
    if(!oldest||(count<=32&&bytes<=192ull*1024*1024))return;
    nv_destroy(oldest);
  }
}
static struct proxy *nv_import(struct object *params,const struct interface *interface,
                              int fd,uint32_t width,uint32_t height,uint32_t format,uint32_t stride) {
  struct proxy *manager=params->parent->p;union argument make={.o=0};
  struct proxy *p=real_marshal(manager,1,&make,params->protocol_interface,get_version(manager));
  if(!p)return 0;
  if(real_set_queue)real_set_queue(p,0);
  union argument plane[6]={{.i=fd},{.u=0},{.u=0},{.u=stride},{.u=0},{.u=0}};
  real_marshal(p,1,plane,0,get_version(p));
  union argument create[5]={{.o=0},{.u=width},{.u=height},{.u=format},{.u=0}};
  struct proxy *buffer=real_marshal(p,3,create,interface,1);
  real_marshal(p,0,0,0,get_version(p));real_destroy(p);
  if(buffer&&real_set_queue)real_set_queue(buffer,0);
  return buffer;
}
static struct proxy *nv_logical(struct nv_ring *ring,struct object *params,const struct interface *interface) {
  struct proxy *logical=nv_import(params,interface,ring->logical_fd,ring->width,ring->height,ring->format,ring->stride);
  if(!logical)return 0;
  ring->owner=track(logical);ring->owner->nv_ring=ring;
  ring->owner->buffer_stride=ring->stride;ring->notified=1;ring->used=++nv_tick;
  return logical;
}
static struct proxy *nv_create(struct object *params,union argument *args,
                               const struct interface *buffer_interface) {
  struct object *manager=params->parent;
  uint32_t width=args[1].u,height=args[2].u,format=args[3].u;
  uint64_t device=0,inode=0;
  if(!width||!height||width>8192||height>8192||(uint64_t)width*height>16777216||args[4].u||!nv_linear(manager,format)||
     !nv_identity(params->nv_plane_fd-1,&device,&inode))return 0;
  for(struct nv_ring *r=nv_rings;r;r=r->next) {
    if(!r->owner&&r->socket>=0&&r->display==params->display&&r->device==device&&r->inode==inode&&
       r->width==width&&r->height==height&&r->format==format&&r->modifier==params->nv_modifier&&
       r->source_stride==params->nv_stride&&r->offset==params->nv_offset) {
      struct proxy *logical=nv_logical(r,params,buffer_interface);
      if(logical)++nv_reuses;
      return logical;
    }
  }
  nv_trim();uint64_t before=nv_now();
  struct nv_ring *ring=calloc(1,sizeof(*ring));if(!ring)return 0;
  ring->socket=nv_open();ring->source_fd=dup(params->nv_plane_fd-1);ring->logical_fd=-1;
  ring->display=params->display;ring->device=device;ring->inode=inode;
  ring->width=width;ring->height=height;ring->format=format;ring->modifier=params->nv_modifier;
  ring->source_stride=params->nv_stride;ring->offset=params->nv_offset;
  int fds[3]={-1,-1,-1};struct proxy *result=0;
  do {
    if(ring->socket<0||ring->source_fd<0)break;
    uint32_t request[]={7,46,width,height,format,params->nv_stride,params->nv_offset,
                        (uint32_t)params->nv_modifier,(uint32_t)(params->nv_modifier>>32)},reply[4];
    if(!nv_transfer(ring->socket,request,sizeof(request),1)||!nv_send_fd(ring->socket,ring->source_fd)||
       !nv_transfer(ring->socket,reply,sizeof(reply),0)||reply[0]!=2||reply[1]!=46||reply[2]||
       reply[3]<width*4||reply[3]>131072||(reply[3]&255))break;
    ring->stride=reply[3];
    for(int i=0;i<3;++i){fds[i]=nv_recv_fd(ring->socket);if(fds[i]<0)break;}
    if(fds[0]<0||fds[1]<0||fds[2]<0)break;
    int installed=0;
    for(int i=0;i<3;++i) {
      struct proxy *buffer=nv_import(params,buffer_interface,fds[i],width,height,format,ring->stride);
      if(!buffer)break;
      ring->slots[i]=(struct nv_slot){.buffer=buffer,.ring=ring};
      if(real_listen(buffer,nv_release_listener,&ring->slots[i]))break;
      ++installed;
    }
    if(installed!=3)break;
    ring->logical_fd=dup(fds[0]);if(ring->logical_fd<0)break;
    result=nv_logical(ring,params,buffer_interface);if(!result)break;
    ring->next=nv_rings;nv_rings=ring;++nv_setups;
    if(nv_setups<=8)__android_log_print(4,"AnvilNvidiaPresent","GPU-copy setup %ux%u us=%llu inode=%llu",width,height,
        (unsigned long long)((nv_now()-before)/1000),(unsigned long long)inode);
  }while(0);
  for(int i=0;i<3;++i)if(fds[i]>=0)close(fds[i]);
  if(!result) {
    nv_destroy(ring);
    __android_log_print(6,"AnvilNvidiaPresent","GPU-copy setup failed; refusing tiled import");
  }
  return result;
}
/* Geometry-only restores reuse a copied destination, never the never-written
 * logical proxy. Pin its slot before allowing another frame to select it. */
static int nv_internal_attach(struct object *surface,struct proxy *buffer) {
  if(!surface||!buffer)return 0;
  struct object *o=find(buffer);
  if(o&&o->nv_ring){surface->nv_pending=o->nv_ring;return 1;}
  for(struct nv_ring *r=nv_rings;r;r=r->next)for(int i=0;i<3;++i)
    if(r->slots[i].buffer==buffer){r->slots[i].busy=1;return 0;}
  return 0;
}
/* Called only immediately before a real commit, after clipping/skip decisions.
 * The native HWC has already waited its acquire fence at this point. */
static void nv_commit(struct object *surface) {
  if(!surface)return;
  struct nv_ring *ring=surface->nv_pending;surface->nv_pending=0;
  if(!ring||surface->clip_hidden)return;
  unsigned slot=3;
  for(unsigned i=0;i<3;++i) {
    unsigned candidate=(ring->next_slot+i)%3;
    if(!ring->slots[candidate].busy){slot=candidate;break;}
  }
  if(slot==3){++ring->dropped;++nv_drops;return;} // Retain the last committed frame.
  uint32_t request[]={1,47,slot},reply[3];
  uint64_t before=nv_now();
  if(!nv_transfer(ring->socket,request,sizeof(request),1)||!nv_transfer(ring->socket,reply,sizeof(reply),0)||
     reply[0]!=1||reply[1]!=47||reply[2]) {
    if(ring->socket>=0)close(ring->socket);
    ring->socket=-1;
    __android_log_print(6,"AnvilNvidiaPresent","GPU copy failed; retaining last frame");return;
  }
  struct proxy *target=ring->slots[slot].buffer;
  uint64_t elapsed=nv_now()-before;
  ++nv_frames;nv_copy_ns+=elapsed;if(elapsed>nv_copy_max)nv_copy_max=elapsed;
  union argument attach[3]={{.o=target},{.i=surface->attach_x},{.i=surface->attach_y}};
  real_marshal(surface->p,1,attach,0,get_version(surface->p));
  surface->attached_buffer=target;
  ring->slots[slot].busy=1;ring->notified=0;ring->next_slot=(slot+1)%3;
  if((nv_frames%60)==0)
    __android_log_print(4,"AnvilNvidiaPresent","frames=%llu setups=%llu reuses=%llu busy_drops=%llu copy_us_avg=%llu max=%llu",
        (unsigned long long)nv_frames,(unsigned long long)nv_setups,(unsigned long long)nv_reuses,(unsigned long long)nv_drops,
        (unsigned long long)(nv_copy_ns/nv_frames/1000),(unsigned long long)(nv_copy_max/1000));
}
