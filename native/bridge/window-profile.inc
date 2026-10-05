/* Runtime-local durable profiles, owned by the compositor's Android UID.
 * IO runs on task_worker, outside the Wayland mutex. Per-runtime /data isolates
 * identical packages even while the host GUI is closed. Version1 JSON uses the
 * same logical content-size fields as the host core schema. */
#ifndef PROFILE_ROOT
#define PROFILE_ROOT "/data/waydroid_tmp/anvildroid/window-profiles"
#endif
extern int mkdir(const char *, unsigned int);
extern int open(const char *, int, ...);
extern int openat(int, const char *, int, ...);
extern long read(int, void *, unsigned long);
extern long write(int, const void *, unsigned long);
extern int close(int);
extern int fsync(int);
extern int renameat(int, const char *, int, const char *);
extern int unlinkat(int, const char *, int);
extern int getpid(void);
enum { PROFILE_DIRECTORY=0200000, PROFILE_NOFOLLOW=0400000,
       PROFILE_CLOEXEC=02000000, PROFILE_NONBLOCK=04000,
       PROFILE_CREAT=0100, PROFILE_EXCL=0200 };
static unsigned long profile_next_token;
static int profile_name(const char *name, int package);
/* A window resize may make fragile apps (Cocos/ARM games) recreate their
 * window surface (sometimes into a new task). Restoring the saved profile
 * right after that re-fights the user's resize and hammers the recreated
 * window. A surface that replaced another surface of the same package
 * WITHOUT a gap in the object list is treated as a recreation, not a
 * launch: the restore is skipped, the saved file stays. A gap of a few
 * polls (force-stop/close followed by a relaunch) is a genuine launch and
 * restores normally. */
#define PROFILE_RECREATE_TICKS 60 /* ~3 s at the 50 ms poll */
#define PROFILE_GAP_TICKS 2       /* absent polls before a new surface is a launch */
struct profile_task_seen {
  char package[256];
  struct object *surface;
  int tick, present, gap_ticks;
  int resized_task, resize_tick;
  struct profile_task_seen *next;
};
static struct profile_task_seen *profile_seen;
static int profile_tick;
static void profile_note_resize(const char *package, int task) {
  for (struct profile_task_seen *n=profile_seen;n;n=n->next)
    if (!strcmp(n->package,package)) {
      n->resized_task=task; n->resize_tick=profile_tick; return;
    }
}
static int profile_recreated(const char *package) {
  if (!package) return 0;
  for (struct profile_task_seen *n = profile_seen; n; n = n->next) {
    if (strcmp(n->package, package)) continue;
    if (n->tick && profile_tick - n->tick <= PROFILE_RECREATE_TICKS) return 1;
    /* Surface recreation can take seconds, not two polls. A recently resized
     * task must not restore an old profile just because its surface had a gap.
     * A real relaunch creates a new task and remains eligible for restoration. */
    if (n->resize_tick && profile_tick-n->resize_tick < 200)
      for(int i=0;i<task_count;++i)
        if(tasks[i].id==n->resized_task && !strcmp(tasks[i].package,package)) return 1;
  }
  return 0;
}
static void profile_observe_surfaces(void) {
  ++profile_tick;
  for (struct profile_task_seen *n = profile_seen; n; n = n->next) n->present = 0;
  for (struct object *o = objects; o; o = o->next) {
    if (!o->app || !o->app_id || !profile_name(o->app_id, 1)) continue;
    struct profile_task_seen *n = profile_seen;
    for (; n; n = n->next) if (!strcmp(n->package, o->app_id)) break;
    if (n) {
      if (!n->present) {
        if (n->surface && n->surface != o && n->gap_ticks < PROFILE_GAP_TICKS)
          n->tick = profile_tick; /* replaced without a launch gap: recreation */
        else
          n->tick = 0; /* fresh surface after a gap (or first): launch */
        n->surface = o;
        n->gap_ticks = 0;
      }
      n->present = 1;
    } else {
      n = calloc(1, sizeof(*n));
      if (!n) continue;
      snprintf(n->package, sizeof(n->package), "%s", o->app_id);
      n->surface = o;
      n->present = 1;
      n->next = profile_seen;
      profile_seen = n;
    }
  }
  for (struct profile_task_seen *n = profile_seen; n; n = n->next)
    if (!n->present && n->gap_ticks < 100) ++n->gap_ticks;
}
static int profile_name(const char *name, int package) {
  int n = 0, segment = 0;
  if (!name) return 0;
  for (; name[n]; ++n) {
    unsigned char c = name[n];
    if (n >= (package ? 240 : 128)) return 0;
    if (package && c == '.') { if (!segment) return 0; segment=0; continue; }
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
          (c >= '0' && c <= '9') || c == '_' || (!package && c == '-'))) return 0;
    ++segment;
  }
  return n && segment;
}
static int profile_size(int w, int h) { return w >= 64 && h >= 64 && w <= 8192 && h <= 8192; }
/* 1=found, 0=missing, -1=invalid/error. O_NOFOLLOW prevents redirected files.
 * Files are small, canonical JSON written here; unknown formats fail closed. */
static int profile_read(int dir, const char *package, const char *runtime, int *w, int *h) {
  extern int *__errno(void);
#ifndef __ANDROID__
  extern int *__errno_location(void);
#define PROFILE_ERRNO (*__errno_location())
#else
#define PROFILE_ERRNO (*__errno())
#endif
  int fd = openat(dir, package, PROFILE_NOFOLLOW|PROFILE_CLOEXEC|PROFILE_NONBLOCK);
  if (fd < 0) return PROFILE_ERRNO == 2 ? 0 : -1;
  char bytes[1024], r[129], p[241];
  long count = read(fd, bytes, sizeof(bytes)-1);
  char extra;
  long tail = count >= 0 ? read(fd, &extra, 1) : -1;
  close(fd);
  if (count <= 0 || tail != 0) return -1;
  bytes[count] = 0;
  int version=0, used=0;
  if (sscanf(bytes, "{\"version\":%d,\"runtime\":\"%128[^\"]\",\"package\":\"%240[^\"]\",\"normal_content\":{\"width\":%d,\"height\":%d}}%n",
      &version,r,p,w,h,&used) != 5 || !used || version != 1 || strcmp(r,runtime) || strcmp(p,package) || !profile_size(*w,*h)) return -1;
  for (int i=used; i<count; ++i)
    if (bytes[i]!='\n' && bytes[i]!='\r' && bytes[i]!=' ' && bytes[i]!='\t') return -1;
  return 1;
}
static int profile_write(int dir, const char *package, const char *runtime, int w, int h) {
  int old_w,old_h;
  if (profile_read(dir,package,runtime,&old_w,&old_h) < 0) return 0;
  char data[640], tmp[96];
  int length=snprintf(data,sizeof(data),"{\"version\":1,\"runtime\":\"%s\",\"package\":\"%s\",\"normal_content\":{\"width\":%d,\"height\":%d}}\n",runtime,package,w,h);
  static unsigned int serial;
  snprintf(tmp,sizeof(tmp),".pending-%d-%u",getpid(),++serial);
  int fd=openat(dir,tmp,1|PROFILE_CREAT|PROFILE_EXCL|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600);
  if (fd<0) return 0;
  int ok=write(fd,data,(unsigned long)length)==length && !fsync(fd);
  close(fd);
  if (ok) ok=!renameat(dir,tmp,dir,package);
  if (ok) ok=!fsync(dir);
  if (!ok) unlinkat(dir,tmp,0);
  return ok;
}
static int profile_unique(struct object *s) {
  int matches=0, surfaces=0;
  for (int i=0;i<task_count;++i) if (!strcmp(s->app_id,tasks[i].package)) ++matches;
  for (struct object *o=objects;o;o=o->next)
    if (o->app && o->app_id && !strcmp(o->app_id,s->app_id)) ++surfaces;
  return matches==1 && surfaces==1;
}
static void profile_poll(void) {
  char package[241], runtime[129];
  const char *id=getenv("ANVILDROID_RUNTIME_ID");
  if (!id || !*id) id="existing";
  if (!profile_name(id,0)) return;
  snprintf(runtime,sizeof(runtime),"%s",id);
  unsigned long token=0;
  int loading=0,w=0,h=0;
  pthread_mutex_lock(mutex);
  profile_observe_surfaces();
  for (struct object *s=objects;s;s=s->next) {
    if(game_fit_enabled(s))continue; /* Old task-size profiles must not relayout games. */
    if (!s->app || !profile_name(s->app_id,1) || !s->has_bounds || !s->resize.ready) continue;
    if (!profile_unique(s) || s->resize.dragging || s->resize.maximized ||
        s->resize.width || s->preview_width || !profile_size(s->content_width,s->content_height)) {
      s->profile_ticks=0; continue;
    }
    if (s->profile_restoring) { s->profile_restoring=0; s->profile_dirty=0; }
    if (s->profile_loaded && !s->profile_dirty) continue;
    if (!s->profile_loaded && profile_recreated(s->app_id)) {
      /* Recreated task right after a resize: keep the user's current size
       * instead of restoring the saved profile into the fragile window. Do
       * not let the recreated window's own size overwrite the saved profile
       * until the user drags the window themselves. */
      s->profile_loaded=1;
      s->profile_dirty=0;
      s->profile_recreated=1;
      continue;
    }
    /* 500ms of confirmed normal content, not a transient configure/drag. */
    if (s->profile_width!=s->content_width || s->profile_height!=s->content_height) {
      s->profile_width=s->content_width; s->profile_height=s->content_height; s->profile_ticks=0;
    }
    if (++s->profile_ticks<10) continue;
    if (!s->profile_token) s->profile_token=++profile_next_token;
    token=s->profile_token; loading=!s->profile_loaded;
    w=s->content_width; h=s->content_height;
    snprintf(package,sizeof(package),"%s",s->app_id);
    break;
  }
  pthread_mutex_unlock(mutex);
  if (!token) return;
  mkdir(PROFILE_ROOT,0700);
  int dir=open(PROFILE_ROOT,PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  int result=-1, saved_w=w, saved_h=h;
  if (dir>=0) {
    result=loading ? profile_read(dir,package,runtime,&saved_w,&saved_h) :
                   (profile_write(dir,package,runtime,w,h) ? 1 : -1);
    close(dir);
  }
  pthread_mutex_lock(mutex);
  for (struct object *s=objects;s;s=s->next) {
    if (s->profile_token!=token) continue; /* Never act on a destroyed/reused surface. */
    if (!profile_unique(s) || s->resize.dragging || s->resize.maximized || s->resize.width ||
        s->preview_width || s->content_width!=w || s->content_height!=h) { s->profile_ticks=0; break; }
    s->profile_loaded=1;
    s->profile_dirty=0;
    if (loading && result==1 && (saved_w!=w || saved_h!=h)) {
      /* Bound by known HWC canvas; the compositor may further constrain the
       * window to its workarea. Never rewrite saved size merely due to clamp. */
      if (s->viewport) {
        int available_w=s->viewport->width-(s->bx>0?s->bx:0);
        int available_h=s->viewport->height-(s->by>0?s->by:0);
        if (available_w>=64 && saved_w>available_w) saved_w=available_w;
        if (available_h>=64 && saved_h>available_h) saved_h=available_h;
      }
      s->resize.width=saved_w; s->resize.height=saved_h; s->resize.dirty=1; s->resize.quiet_ticks=0;
      s->preview_width=saved_w; s->preview_height=saved_h; s->profile_restoring=1;
      __android_log_print(4,"AnvilDroid","restore profile %s logical=%dx%d",package,saved_w,saved_h);
    } else if (!loading && result==1) {
      __android_log_print(4,"AnvilDroid","saved profile %s logical=%dx%d",package,w,h);
    } else if (result<0) {
      __android_log_print(5,"AnvilDroid","profile IO rejected for %s; preserving existing file",package);
    }
    break;
  }
  pthread_mutex_unlock(mutex);
}
