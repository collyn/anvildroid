/* Linux/Android inotify ABI. The worker owns the descriptor. Input, resize and
 * profile timers still run at 50 ms; only task snapshot parsing is event driven.
 */
#ifndef ANVIL_TASK_WATCH_H
#define ANVIL_TASK_WATCH_H
extern int inotify_init1(int);
extern int inotify_add_watch(int, const char *, uint32_t);
extern long read(int, void *, size_t);
extern int close(int);

struct task_watch {
  int fd, retry;
  unsigned int ticks;
};
struct task_watch_event {
  int32_t wd;
  uint32_t mask, cookie, len;
  char name[];
};

static int task_watch_changed(struct task_watch *watch, const char *directory) {
  int changed = 0;
  if (watch->fd < 0) {
    /* Restricted/unsupported filesystems retain the previous 250 ms polling. */
    if (watch->retry > 0) { --watch->retry; return 0; }
    watch->retry = 4;
    watch->fd = inotify_init1(0x800 | 0x80000); /* NONBLOCK | CLOEXEC */
    if (watch->fd >= 0 && inotify_add_watch(watch->fd, directory,
        0x8 | 0x40 | 0x80 | 0x100 | 0x200 | 0x400 | 0x800) < 0) {
      close(watch->fd);
      watch->fd = -1;
    }
    changed = 1;
  }
  if (watch->fd >= 0) {
    union { uint64_t align; unsigned char bytes[4096]; } events;
    /* Bound work even if unrelated queue files are changing continuously. */
    for (int batch = 0; batch < 4; ++batch) {
      long length = read(watch->fd, events.bytes, sizeof(events.bytes));
      if (length <= 0) break;
      for (size_t offset = 0; offset + sizeof(struct task_watch_event) <= (size_t)length;) {
        struct task_watch_event *event = (void *)(events.bytes + offset);
        size_t size = sizeof(*event) + event->len;
        if (size > (size_t)length - offset) { changed = 1; break; }
        if (event->mask & (0x400 | 0x800 | 0x4000 | 0x8000)) {
          /* Directory moved/deleted, queue overflow, or watch invalidated. */
          close(watch->fd);
          watch->fd = -1;
          watch->retry = 0;
          return 1;
        }
        if (event->len >= 6 && !strcmp(event->name, "tasks")) changed = 1;
        offset += size;
      }
    }
  }
  /* Also reconcile after missed notifications (e.g. a mount replacement). */
  if (++watch->ticks >= 100) { watch->ticks = 0; changed = 1; }
  return changed;
}
#endif
