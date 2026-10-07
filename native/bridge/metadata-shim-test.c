#define _GNU_SOURCE
#include <assert.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/xattr.h>
#include <sys/wait.h>
#include <unistd.h>

static void *fake_dlopen(const char *, int);
static void *fake_dlsym(void *, const char *);
#define dlopen fake_dlopen
#define dlsym fake_dlsym
#include "metadata-shim.c"
#undef dlopen
#undef dlsym

static struct { int version, numFds, numInts, fd; } handle = {12, 1, 0, -1};
static struct buffer_metadata received;
static int allocation_error;
int __android_log_print(int priority, const char *tag, const char *fmt, ...) {
  (void)priority; (void)tag; (void)fmt; return 0;
}
static int mock_alloc(struct alloc_device *dev, int w, int h, int format,
                      int usage, const struct native_handle **out, int *stride) {
  (void)dev; (void)format; (void)usage;
  if (allocation_error) return allocation_error;
  handle.fd = memfd_create("metadata-test", 0);
  *stride = (w + 15) & ~15;
  assert(!ftruncate(handle.fd, (long)*stride * h * 4));
  *out = (void *)&handle;
  return 0;
}
static struct alloc_device device = {.alloc = mock_alloc};
static int mock_open(const struct hw_module *m, const char *id, struct hw_device **out) {
  (void)m; (void)id; *out = &device.common; return 0;
}
static struct hw_methods methods = {.open = mock_open};
static struct hw_module module = {.methods = &methods};
static int mock_get(const char *id, const struct hw_module **out) {
  (void)id; *out = &module; return 0;
}
static void *mock_shm(void *out, void *display, const struct buffer_metadata *m,
                      const struct native_handle *h) {
  (void)display; (void)h; received = *m; *(void **)out = (void *)0x1234; return out;
}
static void *fake_dlopen(const char *name, int flags) {
  (void)name; (void)flags; return (void *)1;
}
static void *fake_dlsym(void *lib, const char *name) {
  (void)lib;
  if (!strcmp(name, "hw_get_module")) return (void *)mock_get;
  return (void *)mock_shm;
}
int main(void) {
  const struct hw_module *m;
  assert(!hw_get_module("gralloc", &m));
  assert(m->methods->open == mock_open);
  setenv("ANVILDROID_GRALLOC_METADATA", "1", 1);
  assert(!hw_get_module("gralloc", &m));
  assert(!hw_get_module("gralloc", &m)); /* repeat must not wrap itself */
  struct hw_device *d;
  assert(!m->methods->open(m, "gpu0", &d));
  const struct native_handle *h;
  int stride;
  assert(!device.alloc(&device, 19, 7, 1, 0, &h, &stride));
  assert(stride == 32);
  struct buffer_metadata empty = {7, 19, 0, 0};
  void *out = 0;
  assert(create_shm(&out, 0, &empty, h) == &out && out == (void *)0x1234);
  assert(received.width == 19 && received.height == 7 && received.pixel_stride == 32 && received.format == 1);
  /* Metadata follows the shared file across descriptor duplication/processes. */
  pid_t child = fork();
  assert(child >= 0);
  if (!child) {
    handle.fd = dup(handle.fd);
    create_shm(&out, 0, &empty, (void *)&handle);
    _exit(received.pixel_stride == 32 ? 0 : 1);
  }
  int status;
  assert(waitpid(child, &status, 0) == child && status == 0);
  struct buffer_metadata good = {7, 19, 32, 2};
  create_shm(&out, 0, &good, h);
  assert(received.format == 2); /* preserve complete native metadata */
  struct buffer_metadata corrupt = {16384, 19, 32, 1};
  assert(!fsetxattr(handle.fd, attr, &corrupt, sizeof(corrupt), 0));
  create_shm(&out, 0, &empty, h);
  assert(!received.format); /* reject metadata exceeding the backing file */
  corrupt = (struct buffer_metadata){7, 19, 32, 10};
  assert(!fsetxattr(handle.fd, attr, &corrupt, sizeof(corrupt), 0));
  create_shm(&out, 0, &empty, h);
  assert(!received.format); /* never guess unsupported layouts */
  assert(!fsetxattr(handle.fd, attr, &good, 3, 0));
  create_shm(&out, 0, &empty, h);
  assert(!received.format);
  allocation_error = -12;
  assert(device.alloc(&device, 19, 7, 1, 0, &h, &stride) == -12);
  close(handle.fd);
  return 0;
}
