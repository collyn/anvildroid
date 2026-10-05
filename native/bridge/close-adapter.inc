/* A fresh Android shell transaction, scoped and revalidated by the companion,
 * avoids relying on HWC's cached Task HAL/Binder proxy after framework restart.
 */
struct close_request {
  int task;
  char package[241], epoch[40];
};
extern int *__errno(void);
extern int *__errno_location(void);
static struct close_request close_queue[16];
static unsigned close_head, close_tail;
static char close_epoch[40];
static int close_epoch_valid(const char *text) {
  if (strlen(text) != 36)
    return 0;
  for (int i = 0; i < 36; ++i)
    if (!((text[i] >= '0' && text[i] <= '9') ||
          (text[i] >= 'a' && text[i] <= 'f') || text[i] == '-'))
      return 0;
  return 1;
}
static int close_request_app(struct object *s) {
  if (!s || !s->app || !profile_name(s->app_id, 1) ||
      !close_epoch_valid(close_epoch))
    return 0;
  int id = 0, matches = 0, surfaces = 0;
  for (int i = 0; i < task_count; ++i)
    if (!strcmp(s->app_id, tasks[i].package)) {
      id = tasks[i].id;
      ++matches;
    }
  for (struct object *o = objects; o; o = o->next)
    if (o->app && o->app_id && !strcmp(o->app_id, s->app_id))
      ++surfaces;
  unsigned next = (close_tail + 1) % 16;
  if (matches != 1 || surfaces != 1 || id <= 0 || next == close_head)
    return 0;
  struct close_request *r = &close_queue[close_tail];
  r->task = id;
  snprintf(r->package, sizeof(r->package), "%s", s->app_id);
  snprintf(r->epoch, sizeof(r->epoch), "%s", close_epoch);
  close_tail = next;
  __android_log_print(4, "AnvilDroid", "close task queued id=%d package=%s", id,
                      s->app_id);
  return 1;
}
static void close_poll(void) {
  int dir = open("/data/waydroid_tmp/anvildroid",
                 PROFILE_DIRECTORY | PROFILE_NOFOLLOW | PROFILE_CLOEXEC);
  if (dir < 0)
    return;
  char epoch[40] = "";
  int fd = openat(dir, "close-epoch",
                  PROFILE_NOFOLLOW | PROFILE_CLOEXEC | PROFILE_NONBLOCK);
  if (fd >= 0) {
    char data[64];
    long n = read(fd, data, sizeof(data) - 1);
    close(fd);
    if (n > 0) {
      data[n] = 0;
      sscanf(data, "%39s", epoch);
    }
  }
  pthread_mutex_lock(mutex);
  snprintf(close_epoch, sizeof(close_epoch), "%s",
           close_epoch_valid(epoch) ? epoch : "");
  struct close_request request = {0};
  unsigned head = close_head;
  if (head != close_tail)
    request = close_queue[head];
  pthread_mutex_unlock(mutex);
  if (request.task) {
    fd = openat(dir, "close",
                PROFILE_NOFOLLOW | PROFILE_CLOEXEC | PROFILE_NONBLOCK);
    if (fd >= 0)
      close(fd);
    else if (PROFILE_ERRNO == 2) {
      int out = openat(
          dir, "close.tmp",
          1 | PROFILE_CREAT | 01000 | PROFILE_NOFOLLOW | PROFILE_CLOEXEC, 0660);
      if (out >= 0) {
        char data[320];
        int n = snprintf(data, sizeof(data), "%s %d %s\n", request.epoch,
                         request.task, request.package);
        int ok = write(out, data, (unsigned long)n) == n;
        close(out);
        if (ok)
          ok = !renameat(dir, "close.tmp", dir, "close");
        if (ok) {
          pthread_mutex_lock(mutex);
          if (close_head == head)
            close_head = (close_head + 1) % 16;
          pthread_mutex_unlock(mutex);
        }
      }
    }
  }
  close(dir);
}
