/* Package-scoped resize policy. All cache access holds the Wayland mutex;
 * durable IO runs only on task_worker, never in an input callback. */

struct resize_mode_entry {
  char package[241];
  int fixed, loaded;
  unsigned int dirty;
  struct resize_mode_entry *next;
};
static struct resize_mode_entry *resize_modes;
static int resize_mode_default(const char *package) {
  return package && (!strcmp(package,"com.shg.sg432") || !strcmp(package,"com.shg.sg451"));
}
static struct resize_mode_entry *resize_mode_find(const char *package,int create) {
  if(!profile_name(package,1))return 0;
  for(struct resize_mode_entry *n=resize_modes;n;n=n->next)
    if(!strcmp(n->package,package))return n;
  if(!create)return 0;
  struct resize_mode_entry *n=calloc(1,sizeof(*n));
  if(!n)return 0;
  snprintf(n->package,sizeof(n->package),"%s",package);
  n->fixed=resize_mode_default(package);n->next=resize_modes;resize_modes=n;
  return n;
}
static int resize_mode_fixed(const char *package) {
  struct resize_mode_entry *n=resize_mode_find(package,0);
  return n?n->fixed:resize_mode_default(package);
}
static int resize_mode_worker_fixed(const char *package) {
  pthread_mutex_lock(mutex);
  int fixed=resize_mode_fixed(package);
  pthread_mutex_unlock(mutex);
  return fixed;
}
static void resize_mode_set(const char *package,int fixed) {
  struct resize_mode_entry *n=resize_mode_find(package,1);
  if(!n)return;
  n->fixed=!!fixed;n->loaded=1;++n->dirty;
  for(struct object *s=objects;s;s=s->next) {
    if(!s->app||!s->app_id||strcmp(s->app_id,package))continue;
    s->resize.width=s->resize.height=0;
    s->resize.dirty=0;s->resize.quiet_ticks=0;
    s->preview_width=s->preview_height=0;
    /* Changing to layout mode applies the current host frame once; fixed
     * mode keeps the existing source and cancels all queued task relayout. */
    if(!fixed&&s->fit_w>=64&&s->fit_h>=64) {
      s->resize.width=s->fit_w;s->resize.height=s->fit_h;s->resize.dirty=1;
    }
  }
}
static int resize_mode_dir(void) {
  char path[512];
  snprintf(path,sizeof(path),"%s/resize-modes",PROFILE_ROOT);
  mkdir(PROFILE_ROOT,0700);mkdir(path,0700);
  return open(path,PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
}
static void resize_mode_load(const char *package) {
  pthread_mutex_lock(mutex);
  struct resize_mode_entry *n=resize_mode_find(package,1);
  int loading=n&&!n->loaded;
  pthread_mutex_unlock(mutex);
  if(!loading)return;
  int fixed=resize_mode_default(package),dir=resize_mode_dir();
  if(dir>=0) {
    int fd=openat(dir,package,PROFILE_NOFOLLOW|PROFILE_CLOEXEC|PROFILE_NONBLOCK);
    if(fd>=0) {
      char bytes[3];long length=read(fd,bytes,sizeof(bytes));
      if(length==2&&bytes[1]=='\n'&&(bytes[0]=='0'||bytes[0]=='1'))fixed=bytes[0]=='1';
      close(fd);
    }
    close(dir);
  }
  pthread_mutex_lock(mutex);
  if(!n->loaded){n->fixed=fixed;n->loaded=1;}
  pthread_mutex_unlock(mutex);
}
static void resize_mode_poll(void) {
  char package[241];int fixed=0;unsigned int dirty=0;
  pthread_mutex_lock(mutex);
  for(struct resize_mode_entry *n=resize_modes;n;n=n->next)if(n->dirty) {
    snprintf(package,sizeof(package),"%s",n->package);fixed=n->fixed;dirty=n->dirty;break;
  }
  pthread_mutex_unlock(mutex);
  if(!dirty)return;
  int dir=resize_mode_dir(),ok=0;
  if(dir>=0) {
    char tmp[96],bytes[2]={(char)('0'+fixed),'\n'};
    static unsigned int serial;
    snprintf(tmp,sizeof(tmp),".pending-%d-%u",getpid(),++serial);
    int fd=openat(dir,tmp,1|PROFILE_CREAT|PROFILE_EXCL|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600);
    if(fd>=0) {
      ok=write(fd,bytes,2)==2&&!fsync(fd);close(fd);
      if(ok)ok=!renameat(dir,tmp,dir,package);
      if(ok)ok=!fsync(dir);
      if(!ok)unlinkat(dir,tmp,0);
    }
    close(dir);
  }
  if(ok) {
    pthread_mutex_lock(mutex);
    struct resize_mode_entry *n=resize_mode_find(package,0);
    if(n&&n->dirty==dirty)n->dirty=0;
    pthread_mutex_unlock(mutex);
  }
}
