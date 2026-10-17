#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "task-watch.h"

static void publish(const char *directory, const char *name) {
  char path[512];
  snprintf(path, sizeof(path), "%s/%s", directory, name);
  FILE *f = fopen(path, "w");
  assert(f);
  assert(fputs("task bounds\n", f) >= 0);
  assert(!fclose(f));
}

int main(void) {
  char directory[] = "/tmp/anvil-task-watch-XXXXXX", source[512], target[512];
  assert(mkdtemp(directory));
  struct task_watch watch = {.fd = -1};
  assert(task_watch_changed(&watch, directory));
  assert(watch.fd >= 0);
  /* Unchanged snapshots do not incur another parse on each 50 ms tick. */
  for (int i = 0; i < 80; ++i) assert(!task_watch_changed(&watch, directory));
  publish(directory, "resize");
  assert(!task_watch_changed(&watch, directory));
  publish(directory, "tasks.tmp");
  assert(!task_watch_changed(&watch, directory));
  snprintf(source, sizeof(source), "%s/tasks.tmp", directory);
  snprintf(target, sizeof(target), "%s/tasks", directory);
  assert(!rename(source, target));
  assert(task_watch_changed(&watch, directory));
  assert(!task_watch_changed(&watch, directory));
  publish(directory, "tasks");
  assert(task_watch_changed(&watch, directory));
  assert(!unlink(target));
  assert(task_watch_changed(&watch, directory));
  snprintf(source, sizeof(source), "%s/resize", directory);
  assert(!unlink(source));
  assert(!rmdir(directory));
  assert(task_watch_changed(&watch, directory));
  assert(watch.fd == -1);
  /* An unavailable watch falls back to the original 250 ms sample interval. */
  assert(task_watch_changed(&watch, directory));
  for (int i = 0; i < 4; ++i) assert(!task_watch_changed(&watch, directory));
  assert(task_watch_changed(&watch, directory));
  return 0;
}
