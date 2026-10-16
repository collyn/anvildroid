/* Consumed DMA-BUF params need no bridge metadata. Some legacy HWC versions
 * retain the protocol proxy; its ownership must still remain with the caller. */
#include "bridge.c"
#include <assert.h>

int __android_log_print(int l,const char *t,const char *f,...) {
  (void)l;(void)t;(void)f;return 0;
}
static const struct message methods[]={{.name="create_immed",.signature="niiuu"},
  {.name="add"}, {.name="create"}};
static const struct interface params_i={.name="zwp_linux_buffer_params_v1",.methods=methods},
  buffer_i={.name="wl_buffer"};
static struct proxy params[256],buffer={&buffer_i};
static int destroyed,fail;
static struct proxy *marshal(struct proxy *p,uint32_t op,union argument *a,
                            const struct interface *i,uint32_t version) {
  (void)p;(void)op;(void)a;(void)version;
  return i==&buffer_i&&!fail?&buffer:0;
}
static struct proxy *flags(struct proxy *p,uint32_t op,const struct interface *i,
                          uint32_t v,uint32_t f,union argument *a) {
  assert(!f);return marshal(p,op,a,i,v);
}
static int listen(struct proxy *p,void (**f)(void),void *d) {
  (void)p;(void)f;(void)d;return 0;
}
static void destroy(struct proxy *p) {(void)p;++destroyed;}
static int count(void) {int n=0;for(struct object *o=objects;o;o=o->next)++n;return n;}
int main(void) {
  real_marshal=marshal;real_array_flags=flags;real_destroy=destroy;real_listen=listen;
  for(int i=0;i<256;++i) {
    params[i].interface=&params_i;
    wl_proxy_add_listener(&params[i],0,0);
    union argument add[6]={{0},{.u=0},{0},{.u=8192},{0},{0}};
    union argument create[5]={{.o=0},{.i=1984},{.i=1280},{.u=0x34325258},{0}};
    wl_proxy_marshal_array_constructor_versioned(&params[i],1,add,0,1);
    struct proxy *result=i%2?
      wl_proxy_marshal_flags(&params[i],0,&buffer_i,1,0,(void *)0,1984,1280,0x34325258u,0u):
      wl_proxy_marshal_array_constructor_versioned(&params[i],0,create,&buffer_i,1);
    assert(result==&buffer&&find(result)->buffer_transport==2);
    assert(find(result)->buffer_stride==8192);
    assert(!find(&params[i])&&count()==1);
    assert(destroyed==i); /* No hidden destruction of caller-owned params. */
    wl_proxy_destroy(result);
    assert(count()==0);
  }
  for(int i=0;i<256;++i)wl_proxy_destroy(&params[i]); /* A later destroy stays valid. */
  assert(destroyed==512);
  /* A failed constructor or asynchronous create is not a consumed immediate
   * constructor: retain metadata until its normal destruction. */
  wl_proxy_add_listener(&params[0],0,0);fail=1;
  union argument create[5]={{.o=0},{.i=1984},{.i=1280},{.u=0x34325258},{0}};
  assert(!wl_proxy_marshal_array_constructor_versioned(&params[0],0,create,&buffer_i,1));
  assert(find(&params[0]));fail=0;
  wl_proxy_marshal_array_constructor_versioned(&params[0],2,create,0,1);
  assert(find(&params[0]));wl_proxy_destroy(&params[0]);assert(!objects);
}
