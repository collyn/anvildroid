/* Android 13 / x86_64 HIDL name adapter. Only changes layer metadata; buffers,
 * fences, viewport geometry and rendering remain owned by the original HWC.
 * No HWC or libc++ object layouts are inspected. HIDL strings are accessed and
 * constructed through libhidlbase's exported methods, in overallocated storage.
 * Return<Error> is nontrivial: the x86_64 ABI passes its result storage first.
 * The original method constructs that result directly; we never inspect it.
 */
#define MEDIA_LAYER_METHOD "_ZN6vendor8waydroid7display4V1_214implementation15WaydroidDisplay12setLayerNameEjRKN7android8hardware11hidl_stringE"
static void *(*media_layer_original)(void *,void *,uint32_t,const void *);
static const char *(*media_string_data)(const void *);
static unsigned long (*media_string_size)(const void *);
static void (*media_string_create)(void *,const char *);
static void (*media_string_destroy)(void *);
static int media_layer_symbols_checked,media_layer_reported_task;

static int media_layer_name(const char *name,unsigned long length,char *out,size_t capacity) {
  const char *package="com.google.android.youtube";
  /* Only the observed SurfaceView/BLAST path. Never rewrite task-tagged UI,
   * system layers, arbitrary package prefixes or other apps. */
  if(!name||length<29||length>1024||strstr(name,package)!=name||name[26]!='/'||
     !strstr(name,"(BLAST)#"))return 0;
  int id=0;
  for(int i=0;i<task_count;++i)if(!strcmp(tasks[i].package,package)) {
    if(tasks[i].id<=0||id)return 0;
    id=tasks[i].id;
  }
  if(!id)return 0;
  int n=snprintf(out,capacity,"TID:%d#%s",id,name);
  return n>0&&(size_t)n<capacity?id:0;
}

void *anvil_media_set_layer_name(void *result,void *self,uint32_t layer,const void *name)
  __asm__(MEDIA_LAYER_METHOD);
void *anvil_media_set_layer_name(void *result,void *self,uint32_t layer,const void *name) {
  char rewritten[1100];int id=0;
  pthread_mutex_lock(mutex);
  if(!media_layer_symbols_checked) {
    void *hwc=dlopen("/vendor/lib64/hw/hwcomposer.waydroid.so",2|4);
    void *hidl=dlopen("libhidlbase.so",2);
    media_layer_original=hwc?(void *(*)(void *,void *,uint32_t,const void *))dlsym(hwc,MEDIA_LAYER_METHOD):0;
    if(hidl) {
      media_string_data=(const char *(*)(const void *))dlsym(hidl,"_ZNK7android8hardware11hidl_string5c_strEv");
      media_string_size=(unsigned long (*)(const void *))dlsym(hidl,"_ZNK7android8hardware11hidl_string4sizeEv");
      media_string_create=(void (*)(void *,const char *))dlsym(hidl,"_ZN7android8hardware11hidl_stringC1EPKc");
      media_string_destroy=(void (*)(void *))dlsym(hidl,"_ZN7android8hardware11hidl_stringD1Ev");
    }
    media_layer_symbols_checked=1;
  }
  if(media_string_data&&media_string_size&&media_string_create&&media_string_destroy&&name)
    id=media_layer_name(media_string_data(name),media_string_size(name),rewritten,sizeof(rewritten));
  if(id&&id!=media_layer_reported_task) {
    __android_log_print(4,"AnvilMediaLayer","YouTube BLAST layer assigned to task %d (metadata only)",id);
    media_layer_reported_task=id;
  }
  pthread_mutex_unlock(mutex);
  if(!media_layer_original||media_layer_original==anvil_media_set_layer_name)abort();
  if(!id)return media_layer_original(result,self,layer,name);
  _Alignas(16) unsigned char replacement[64];
  media_string_create(replacement,rewritten);
  void *returned=media_layer_original(result,self,layer,replacement);
  media_string_destroy(replacement);
  return returned;
}
