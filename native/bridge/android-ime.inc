/* Text-input-v3 on the native single-Android window, sharing the Android
 * receiver, generation checks and bounded socket transport with app windows. */
#include "../ime/text-state.h"
extern size_t strlen(const char *);
extern void *memcpy(void *, const void *, size_t);
extern char *strstr(const char *, const char *);
extern int snprintf(char *, size_t, const char *, ...);
extern int sscanf(const char *, const char *, ...);
#if defined(__ANDROID__)
extern void *fopen(const char *, const char *);
extern char *fgets(char *, int, void *);
extern int fclose(void *);
#else
#include <stdio.h>
#endif
extern int usleep(unsigned);
extern int pthread_mutex_lock(void *);
extern int pthread_mutex_unlock(void *);
extern int pthread_create(unsigned long *, const void *, void *(*)(void *), void *);
extern int pthread_detach(unsigned long);
extern int socket(int,int,int);
extern int connect(int,const void *,unsigned);
extern int getsockopt(int,int,int,void *,unsigned *);
extern int setsockopt(int,int,int,const void *,unsigned);
extern long recv(int,void *,size_t,int);
extern long ime_socket_send(int,const void *,size_t,int) __asm__("send");
extern int close(int);
struct ime_pollfd { int fd; short events,revents; };
extern int poll(struct ime_pollfd *,unsigned long,int);
static unsigned long ime_mutex_storage[8];
static void *mutex=ime_mutex_storage;
static struct wl_proxy *ime_manager,*ime_seat,*ime_text,*ime_surface;
static uint32_t ime_serial,ime_first_serial,ime_manager_name;
static uint64_t ime_generation,ime_enabled_generation;
static int ime_connected,ime_active,ime_type,ime_enabled,ime_broken,ime_started;
static char ime_package[256],ime_queue[16][32769];
static unsigned ime_head,ime_tail;
static struct anvil_ime_state ime_state;
static const struct wl_interface ime_text_interface;
static const struct wl_interface *ime_object_types[2],*ime_surface_types[1];
static const struct wl_message ime_manager_requests[]={
 {"destroy","",NULL},{"get_text_input","no",ime_object_types}};
static const struct wl_interface ime_manager_interface={"zwp_text_input_manager_v3",1,2,ime_manager_requests,0,NULL};
static const struct wl_message ime_requests[]={
 {"destroy","",NULL},{"enable","",NULL},{"disable","",NULL},
 {"set_surrounding_text","sii",NULL},{"set_text_change_cause","u",NULL},
 {"set_content_type","uu",NULL},{"set_cursor_rectangle","iiii",NULL},{"commit","",NULL}};
static const struct wl_message ime_events[]={
 {"enter","o",ime_surface_types},{"leave","o",ime_surface_types},
 {"preedit_string","?sii",NULL},{"commit_string","?s",NULL},
 {"delete_surrounding_text","uu",NULL},{"done","u",NULL}};
static const struct wl_interface ime_text_interface={"zwp_text_input_v3",1,8,ime_requests,6,ime_events};
static void ime_send(uint32_t op,union wl_argument *args){marshal(ime_text,op,NULL,1,0,args);}
static void ime_commit_state(void){ime_send(7,NULL);++ime_serial;}
static void ime_sync(void){
 if(!ime_text)return;
 int klass=ime_type&15,variation=ime_type&0xff0;
 int password=(klass==1&&(variation==0x80||variation==0x90||variation==0xe0))||(klass==2&&variation==0x10);
 int want=ime_connected&&!ime_broken&&ime_active&&!password&&owned(ime_surface);
 if(ime_enabled&&(!want||ime_enabled_generation!=ime_generation)){
  ime_send(2,NULL);ime_commit_state();ime_enabled=0;anvil_ime_focus(&ime_state,0);ime_head=ime_tail=0;
 }
 if(want&&!ime_enabled){
  ime_enabled=1;ime_enabled_generation=ime_generation;anvil_ime_focus(&ime_state,1);
  ime_send(1,NULL);union wl_argument content[2]={{.u=0},{.u=klass==2?3:0}};ime_send(5,content);
  ime_commit_state();ime_first_serial=ime_serial;
  __android_log_print(4,"AnvilDroidIme","single-window text-input enabled generation %llu",(unsigned long long)ime_generation);
 }
}
static void ime_enter(void *d,struct wl_proxy *p,struct wl_proxy *s){(void)d;(void)p;pthread_mutex_lock(mutex);ime_surface=s;ime_sync();pthread_mutex_unlock(mutex);}
static void ime_leave(void *d,struct wl_proxy *p,struct wl_proxy *s){(void)d;(void)p;pthread_mutex_lock(mutex);if(ime_surface==s)ime_surface=NULL;ime_sync();pthread_mutex_unlock(mutex);}
static void ime_preedit(void *d,struct wl_proxy *p,const char *v,int32_t a,int32_t b){(void)d;(void)p;pthread_mutex_lock(mutex);anvil_ime_preedit(&ime_state,v,a,b);pthread_mutex_unlock(mutex);}
static void ime_insert(void *d,struct wl_proxy *p,const char *v){(void)d;(void)p;pthread_mutex_lock(mutex);anvil_ime_commit(&ime_state,v);pthread_mutex_unlock(mutex);}
static void ime_delete(void *d,struct wl_proxy *p,uint32_t a,uint32_t b){(void)d;(void)p;pthread_mutex_lock(mutex);anvil_ime_delete(&ime_state,a,b);pthread_mutex_unlock(mutex);}
static char *ime_quote(char *out,const char *text){
 static const char hex[]="0123456789abcdef";*out++='"';
 for(const unsigned char *p=(const unsigned char *)text;*p;++p){
  if(*p=='"'||*p=='\\'){*out++='\\';*out++=(char)*p;}
  else if(*p<32){*out++='\\';*out++='u';*out++='0';*out++='0';*out++=hex[*p>>4];*out++=hex[*p&15];}
  else *out++=(char)*p;
 }
 *out++='"';return out;
}
static void ime_done(void *d,struct wl_proxy *p,uint32_t serial){
 (void)d;(void)p;pthread_mutex_lock(mutex);
 struct anvil_ime_batch b;
 if(anvil_ime_done(&ime_state,serial,ime_serial,&b)&&ime_enabled&&owned(ime_surface)&&ime_generation==ime_enabled_generation&&(int32_t)(serial-ime_first_serial)>=0){
  char json[50000];int n=snprintf(json,sizeof(json),"{\"v\":1,\"op\":\"batch\",\"generation\":%llu,\"deleteBefore\":%u,\"deleteAfter\":%u,\"cursorBegin\":%d,\"cursorEnd\":%d,\"commit\":",(unsigned long long)ime_enabled_generation,b.delete_before_bytes,b.delete_after_bytes,b.cursor_begin,b.cursor_end);
  char *end=ime_quote(json+n,b.commit);memcpy(end,",\"preedit\":",11);end=ime_quote(end+11,b.preedit);*end++='}';*end=0;
  unsigned next=(ime_tail+1)%16;
  size_t len=(size_t)(end-json);
  if(len>=sizeof(ime_queue[0])||next==ime_head)ime_broken=1;
  else if(ime_connected&&!ime_broken){memcpy(ime_queue[ime_tail],json,len+1);ime_tail=next;}
 }
 pthread_mutex_unlock(mutex);
}
static void (*ime_listener[])(void)={(void(*)(void))ime_enter,(void(*)(void))ime_leave,(void(*)(void))ime_preedit,(void(*)(void))ime_insert,(void(*)(void))ime_delete,(void(*)(void))ime_done};
#include "ime-transport.inc"
static void ime_create(void){
 if(!ime_manager||!ime_seat||ime_text)return;
 union wl_argument a[2]={{.o=NULL},{.o=ime_seat}};
 ime_text=marshal(ime_manager,1,&ime_text_interface,1,0,a);
 if(ime_text){listen(ime_text,ime_listener,NULL);__android_log_print(4,"AnvilDroidIme","single-window text-input-v3 bound");}
 if(!ime_started){unsigned long thread;if(!pthread_create(&thread,NULL,ime_worker,NULL)){pthread_detach(thread);ime_started=1;}}
}
static void ime_global(struct wl_proxy *registry,uint32_t name,const char *interface,uint32_t version){
 if(!ime_manager&&version>=1&&!strcmp(interface,ime_manager_interface.name)){
  void *lib=dlopen("/vendor/lib64/hw/hwcomposer.waydroid.so",2|4);
  ime_object_types[0]=&ime_text_interface;ime_object_types[1]=dlsym(lib,"wl_seat_interface");ime_surface_types[0]=dlsym(lib,"wl_surface_interface");
  union wl_argument a[4]={{.u=name},{.s=interface},{.u=1},{.o=NULL}};
  ime_manager=marshal(registry,0,&ime_manager_interface,1,0,a);ime_manager_name=name;ime_create();
 }
}
