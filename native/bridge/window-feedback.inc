/* Runtime-local acknowledgements. Configuration/content is not proof of focus
 * or presentation: the compositor remains responsible for those decisions. */
#ifndef WINDOW_FEEDBACK_ROOT
#define WINDOW_FEEDBACK_ROOT "/data/waydroid_tmp/anvildroid"
#endif
static int window_nonce(const char *text) {
  for (int i=0;i<32;++i)
    if (!((text[i]>='0'&&text[i]<='9')||(text[i]>='a'&&text[i]<='f'))) return 0;
  return text[32]==0;
}
static int window_is_full(struct object *s) {
  return s && s->app_id && !strcmp(s->app_id,"Waydroid");
}
/* Called under the Wayland mutex. A detached buffer invalidates readiness. */
static void window_feedback_event(struct object *o,const char *cls,const char *method) {
  if (!o) return;
  if (!strcmp(cls,"xdg_surface") && !strcmp(method,"ack_configure") && o->surface)
    o->surface->window_configured=1;
  if (!strcmp(cls,"wl_surface") && !strcmp(method,"commit"))
    o->window_content=o->attached_buffer!=0;
  if (!strcmp(cls,"xdg_toplevel") && !strcmp(method,"destroy") && o->surface) {
    o->surface->window_configured=0;
    o->surface->window_activated=0;
  }
}
static void window_feedback_snapshot(int *ready,int *activated) {
  *ready=0; *activated=0;
  for (struct object *top=objects;top;top=top->next) {
    if (!object_is(top,"xdg_toplevel") ||
        !window_is_full(top->surface) || !top->surface->window_configured) continue;
    /* A protocol/import error can leave HWC alive with all cached surface
     * flags intact, although the compositor has removed every window. */
    if (top->display && real_display_get_error && real_display_get_error(top->display)) continue;
    struct object *s=top->surface;
    int content=s->window_content;
    for (struct object *sub=objects;sub;sub=sub->next)
      if (sub->parent==s && sub->surface && sub->surface->window_content) content=1;
    if (content) { *ready=1; if(s->window_activated)*activated=1; }
  }
}
/* Only the task IO worker touches paths; never block the Wayland event mutex
 * on disk. Atomic exclusive temporary files and NOFOLLOW avoid path attacks. */
static void window_feedback_poll(void) {
  int dir=open(WINDOW_FEEDBACK_ROOT,PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  if(dir<0)return;
  static int announced;
  if(!announced){
    char temporary[80],message[80];
    snprintf(temporary,sizeof(temporary),"window-support-%d.tmp",getpid());
    unlinkat(dir,temporary,0);
    int support=openat(dir,temporary,1|PROFILE_CREAT|PROFILE_EXCL|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600);
    if(support>=0){
      int length=snprintf(message,sizeof(message),"ANVIL_WINDOW 1 %d\n",getpid());
      long written=write(support,message,(unsigned long)length);close(support);
      if(written==length&&!renameat(dir,temporary,dir,"window-support"))announced=1;
      else unlinkat(dir,temporary,0);
    }
  }
  char line[640]={0},nonce[33]={0},token[513]={0},extra;
  int fd=openat(dir,"window-request",PROFILE_NOFOLLOW|PROFILE_CLOEXEC|PROFILE_NONBLOCK);
  long n=fd<0?-1:read(fd,line,sizeof(line)-1);
  if(fd>=0)close(fd);
  int fields=n>0?sscanf(line,"ANVIL_WINDOW 1 %32s %512s %c",nonce,token,&extra):0;
  int valid=n>0&&n<(long)sizeof(line)-1&&(fields==1||fields==2)&&window_nonce(nonce);
  for(unsigned char *p=(unsigned char *)token;*p;++p)if(*p<=32||*p>=127)valid=0;
  if(!valid){close(dir);return;}
  int ready,activated;
  static char activated_nonce[33];
  pthread_mutex_lock(mutex);
  window_feedback_snapshot(&ready,&activated);
  if(ready&&token[0]&&strcmp(activated_nonce,nonce)&&window_activate(token))
    snprintf(activated_nonce,sizeof(activated_nonce),"%s",nonce);
  pthread_mutex_unlock(mutex);
  static char last_nonce[33];static int last_ready=-1,last_activated=-1;
  if(!strcmp(last_nonce,nonce)&&last_ready==ready&&last_activated==activated){close(dir);return;}
  char temporary[80],response[128];
  snprintf(temporary,sizeof(temporary),"window-reply-%d.tmp",getpid());
  unlinkat(dir,temporary,0);
  fd=openat(dir,temporary,1|PROFILE_CREAT|PROFILE_EXCL|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600);
  if(fd>=0){
    int length=snprintf(response,sizeof(response),"ANVIL_WINDOW 1 %s %d %d %d\n",nonce,ready,activated,getpid());
    long written=write(fd,response,(unsigned long)length);close(fd);
    if(written==length && !renameat(dir,temporary,dir,"window-reply")){
      snprintf(last_nonce,sizeof(last_nonce),"%s",nonce);last_ready=ready;last_activated=activated;
    }else unlinkat(dir,temporary,0);
  }
  close(dir);
}
