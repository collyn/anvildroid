#include "bridge.c"
#include <assert.h>
#include <string.h>
static int reports;
int __android_log_print(int a,const char*b,const char*c,...){(void)a;(void)c;if(!strcmp(b,"AnvilMediaLayer"))++reports;return 0;}
static const char *video="com.google.android.youtube/com.google.android.youtube.app.honeycomb.Shell$HomeActivity(BLAST)#190";
static char captured[1200];static int constructed,destroyed;
static const char *data(const void*p){return *(const char*const*)p;}
static unsigned long size(const void*p){return strlen(data(p));}
static void create(void*p,const char*s){*(char**)p=strdup(s);++constructed;}
static void destroy(void*p){free(*(char**)p);++destroyed;}
static void *original(void*r,void*self,uint32_t layer,const void*n){assert(self==(void*)123&&layer==7);snprintf(captured,sizeof(captured),"%s",data(n));return r;}
int main(void){
 char out[1100];
 assert(!media_layer_name(video,strlen(video),out,sizeof(out)));
 task_count=1;tasks[0].id=10;strcpy(tasks[0].package,"com.google.android.youtube");
 assert(media_layer_name(video,strlen(video),out,sizeof(out))==10);
 assert(strstr(out,"TID:10#com.google.android.youtube/")==out);
 assert(!media_layer_name(video,strlen(video),out,8));
 assert(!media_layer_name(out,strlen(out),out,sizeof(out)));
 assert(!media_layer_name("com.google.android.youtube.evil/x(BLAST)#1",40,out,sizeof(out)));
 const char *game="com.example.game/x(BLAST)#1";
 assert(!media_layer_name(game,strlen(game),out,sizeof(out)));
 tasks[0].id=12;strcpy(tasks[0].package,"com.example.game");
 assert(media_layer_name(game,strlen(game),out,sizeof(out))==12);
 assert(strstr(out,"TID:12#com.example.game/")==out);
 assert(!media_layer_name(game,strlen(game)-1,out,sizeof(out)));
 assert(!media_layer_name(game,strlen(game)+1,out,sizeof(out))); /* embedded NUL */
 char bounded[sizeof("com.example.game/x(BLAST)#1")-1];memcpy(bounded,game,sizeof(bounded));
 assert(media_layer_name(bounded,sizeof(bounded),out,sizeof(out))==12);
 assert(!media_layer_name(game,1025,out,sizeof(out)));
 assert(!media_layer_name(game,strlen(game),NULL,sizeof(out)));
 assert(!media_layer_name("com.example.game/x#1",20,out,sizeof(out)));
 strcpy(tasks[0].package,"com.android.systemui");
 const char *system="com.android.systemui/x(BLAST)#1";
 assert(!media_layer_name(system,strlen(system),out,sizeof(out)));
 tasks[0].id=10;strcpy(tasks[0].package,"com.google.android.youtube");
 tasks[1]=tasks[0];tasks[1].id=11;task_count=2;
 assert(!media_layer_name(video,strlen(video),out,sizeof(out)));
 media_layer_symbols_checked=1;media_layer_original=original;media_string_data=data;media_string_size=size;media_string_create=create;media_string_destroy=destroy;
 void *result=(void*)456;
 assert(anvil_media_set_layer_name(result,(void*)123,7,&video)==result);
 assert(!strcmp(captured,video)&&constructed==0);
 task_count=1;
 assert(anvil_media_set_layer_name(result,(void*)123,7,&video)==result);
 assert(strstr(captured,"TID:10#")==captured&&constructed==1&&destroyed==1);
 tasks[1].id=12;strcpy(tasks[1].package,"com.example.game");task_count=2;
 for(int i=0;i<300;++i) {
   assert(anvil_media_set_layer_name(result,(void*)123,7,&video)==result);
   assert(strstr(captured,"TID:10#")==captured);
   assert(anvil_media_set_layer_name(result,(void*)123,7,&game)==result);
   assert(strstr(captured,"TID:12#")==captured);
 }
 assert(reports==2&&constructed==601&&destroyed==601);
 return 0;
}
