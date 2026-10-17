#include "bridge.c"
#include <assert.h>
extern int pthread_join(unsigned long,void **);
extern int socketpair(int,int,int,int *);
static int sockets[2],source[2],copies,attached,releases,buffers,opens,fail_listener,listeners,expected_copies=4;
static struct proxy manager_proxy,params_proxy,physical[16],surface_proxy;
static const struct message surface_methods[2]={{.name="destroy"},{.name="attach",.signature="?oii"}};
static const struct interface params_i={.name="zwp_linux_buffer_params_v1"},buffer_i={.name="wl_buffer"},
 manager_i={.name="zwp_linux_dmabuf_v1"},surface_i={.name="wl_surface",.method_count=2,.methods=surface_methods};
int __android_log_print(int l,const char *t,const char *f,...) {(void)l;(void)t;(void)f;return 0;}
static int open_socket(void){++opens;return dup(sockets[0]);}
static uint32_t version(struct proxy *p){return p==&manager_proxy||p==&params_proxy?3:1;}
static void destroyed(struct proxy *p){(void)p;}
static int listening(struct proxy *p,void (**cb)(void),void *data){(void)p;(void)cb;(void)data;return ++listeners==fail_listener?-1:0;}
static struct proxy *marshal(struct proxy *p,uint32_t op,union argument *a,const struct interface *i,uint32_t v) {
 (void)v;
 if(p==&manager_proxy){assert(op==1&&i==&params_i);return &params_proxy;}
 if(p==&params_proxy){
  if(op==1){assert(a[3].u==4096&&!a[4].u&&!a[5].u);return 0;}
  if(op==3){assert(i==&buffer_i&&buffers<16);return &physical[buffers++];}
  assert(op==0);return 0;
 }
 if(p==&surface_proxy){assert(op==1&&a[0].o);++attached;return 0;}
 assert(op==0);return 0;
}
static void release(void *data,struct proxy *p){(void)data;assert(find(p)&&find(p)->nv_ring);++releases;}
static void *server(void *unused) {
 (void)unused;uint32_t init[9];assert(nv_transfer(sockets[1],init,sizeof(init),0));
 assert(init[0]==7&&init[1]==46&&init[2]==1000&&init[3]==700&&init[5]==4096);
 int fd=nv_recv_fd(sockets[1]);assert(fd>=0);close(fd);
 uint32_t reply[]={2,46,0,4096};assert(nv_transfer(sockets[1],reply,sizeof(reply),1));
 for(int i=0;i<3;++i)assert(nv_send_fd(sockets[1],source[0]));
 for(int i=0;i<expected_copies;++i){uint32_t copy[3];assert(nv_transfer(sockets[1],copy,sizeof(copy),0));
  assert(copy[0]==1&&copy[1]==47&&copy[2]==(unsigned)(i%3));
  uint32_t done[]={1,47,0};assert(nv_transfer(sockets[1],done,sizeof(done),1));++copies;}
 return 0;
}
int main(void) {
 assert(!socketpair(1,1,0,sockets));assert(!pipe2(source,0));
 manager_proxy.interface=&manager_i;params_proxy.interface=&params_i;surface_proxy.interface=&surface_i;
 for(int i=0;i<16;++i)physical[i].interface=&buffer_i;
 real_marshal=marshal;real_listen=listening;real_destroy=destroyed;get_version=version;nv_open=open_socket;
 struct object manager={.p=&manager_proxy,.nv_formats={0x34324241},.nv_format_count=1};
 struct object params={.p=&params_proxy,.parent=&manager,.protocol_interface=&params_i,
  .nv_plane_fd=source[0]+1,.nv_stride=4096,.nv_modifier=0x0300000000606014ull};
 union argument args[5]={{.o=0},{.u=1000},{.u=700},{.u=0x34324241},{.u=0}};
 uint64_t dev,ino;assert(nv_identity(source[0],&dev,&ino));
 unsigned long thread;assert(!pthread_create(&thread,0,server,0));
 struct proxy *p=nv_create(&params,args,&buffer_i);assert(p==&physical[3]);
 struct object *owner=find(p);struct nv_ring *ring=owner->nv_ring;
 void (*listener[])(void)={(void (*)(void))release};owner->listener=listener;
 struct object surface={.p=&surface_proxy,.nv_pending=ring};
 assert(!attached&&!copies); // Import/setup never reads producer pixels.
 for(int i=0;i<3;++i){surface.nv_pending=ring;nv_commit(&surface);assert(ring->slots[i].busy);}
 assert(attached==3);
 // HWC geometry resets destroy the logical proxy while compositor uses all
 // three physical slots. Reimport the same allocation through a different FD.
 wl_proxy_destroy(p);assert(!ring->owner&&nv_busy(ring));
 int duplicate=dup(source[0]);params.nv_plane_fd=duplicate+1;
 p=nv_create(&params,args,&buffer_i);close(duplicate);params.nv_plane_fd=source[0]+1;
 assert(p==&physical[4]&&opens==1&&nv_reuses==1&&find(p)->nv_ring==ring);
 owner=find(p);owner->listener=listener;
 struct object *transport=track(&surface_proxy);transport->attached_buffer=&physical[2];
 union argument attach[3]={{.o=p},{.i=0},{.i=0}};
 bridge_marshal(&surface_proxy,1,attach,0,1,0,0);
 assert(transport->nv_pending==ring&&transport->attached_buffer==&physical[2]&&attached==3);
 nv_commit(transport);assert(transport->attached_buffer==&physical[2]);
 assert(attached==3&&ring->dropped==1); // Never overwrite busy slots.
 nv_release(&ring->slots[0],0);assert(!releases);
 surface.nv_pending=ring;nv_commit(&surface);assert(attached==4&&ring->slots[0].busy);
 nv_release(&ring->slots[1],0);nv_release(&ring->slots[2],0);assert(!releases);
 nv_release(&ring->slots[0],0);assert(releases==1);
 surface.nv_pending=ring;surface.clip_hidden=1;nv_commit(&surface);assert(attached==4); // Hidden/skipped content is not copied.
 assert(!pthread_join(thread,0)&&copies==4);
 wl_proxy_destroy(p);assert(!ring->owner);
 // Equal layout is insufficient: a different allocation must get a new ring.
 int other[2];assert(!pipe2(other,0));params.nv_plane_fd=other[0]+1;expected_copies=0;
 assert(!pthread_create(&thread,0,server,0));
 struct proxy *different=nv_create(&params,args,&buffer_i);
 assert(different&&find(different)->nv_ring!=ring&&opens==2);
 assert(!pthread_join(thread,0));struct nv_ring *second=find(different)->nv_ring;
 wl_proxy_destroy(different);nv_destroy(second);close(other[0]);close(other[1]);
 params.nv_plane_fd=source[0]+1;
 // Bound idle retention; a busy orphan must survive until the real release.
 ring->height=100000;ring->slots[0].busy=1;nv_trim();assert(nv_rings==ring);
 nv_release(&ring->slots[0],0);nv_trim();assert(!nv_rings);
 // A failed listener on the last physical slot must fail the entire setup.
 expected_copies=0;fail_listener=listeners+3;
 assert(!pthread_create(&thread,0,server,0));
 assert(!nv_create(&params,args,&buffer_i));assert(!pthread_join(thread,0)&&!nv_rings);
 forget_proxy(&surface_proxy);
 for(int i=0;i<2;++i){close(sockets[i]);close(source[i]);}
}
