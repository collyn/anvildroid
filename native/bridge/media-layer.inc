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
static int media_layer_symbols_checked;
static int media_layer_reported_tasks[128],media_layer_reported_next;
static int profile_name(const char *name,int package);
static int media_layer_first_report(int task) {
  for(unsigned i=0;i<sizeof(media_layer_reported_tasks)/sizeof(media_layer_reported_tasks[0]);++i)
    if(media_layer_reported_tasks[i]==task)return 0;
  /* Bound diagnostics to the task snapshot's maximum working set. A single
   * last-task marker would spam logs as different apps alternate frames. */
  media_layer_reported_tasks[media_layer_reported_next]=task;
  media_layer_reported_next=(media_layer_reported_next+1)%128;
  return 1;
}

static int media_layer_name(const char *name,unsigned long length,char *out,size_t capacity) {
  /* Raw package/component BLAST layers need a task identity regardless of
   * app. Bound the HIDL bytes before string operations; never guess between
   * multiple visible tasks or rewrite already tagged/system layers. */
  if(!name||!out||!capacity||!length||length>1024)return 0;
  char raw[1025],package[241];
  for(unsigned long i=0;i<length;++i) {
    if(!name[i])return 0;
    raw[i]=name[i];
  }
  raw[length]=0;
  char *slash=strchr(raw,'/');
  if(!slash||slash==raw||(size_t)(slash-raw)>=sizeof(package))return 0;
  const char *blast=strstr(slash+1,"(BLAST)#");
  if(!blast||blast==slash+1||blast[8]<'0'||blast[8]>'9')return 0;
  for(size_t i=0;i<(size_t)(slash-raw);++i)package[i]=raw[i];
  package[slash-raw]=0;
  if(!profile_name(package,1)||!strcmp(package,"android")||
     !strcmp(package,"com.android.systemui")||!strcmp(package,"com.android.launcher3"))return 0;
  int id=0;
  for(int i=0;i<task_count;++i)if(!strcmp(tasks[i].package,package)) {
    if(tasks[i].id<=0||id)return 0;
    id=tasks[i].id;
  }
  if(!id)return 0;
  int n=snprintf(out,capacity,"TID:%d#%s",id,raw);
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
  if(id&&media_layer_first_report(id)) {
    __android_log_print(4,"AnvilMediaLayer","BLAST layer assigned to task %d (metadata only)",id);
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
