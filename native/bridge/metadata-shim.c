/* Compatibility adapter for the fingerprinted WayDroidATV software HWC.
 * HAL prefixes below follow LineageOS android_hardware_libhardware,
 * lineage-22.2/include_all/hardware/{hardware,gralloc}.h (Apache-2.0 ABI).
 * Buffer metadata travels with its memfd, including across Binder FD copies.
 * No pixel format is inferred, and there are no persistent sidecar files.
 */
typedef unsigned int u32;
typedef unsigned long u64;
struct native_handle { int version, numFds, numInts, data[]; };
struct hw_module;
struct hw_device {
  u32 tag, version;
  struct hw_module *module;
  u64 reserved[12];
  int (*close)(struct hw_device *);
};
struct hw_methods {
  int (*open)(const struct hw_module *, const char *, struct hw_device **);
};
struct hw_module {
  u32 tag;
  unsigned short module_api_version, hal_api_version;
  const char *id, *name, *author;
  struct hw_methods *methods;
  void *dso;
  u64 reserved[25];
};
struct alloc_device {
  struct hw_device common;
  int (*alloc)(struct alloc_device *, int, int, int, int,
               const struct native_handle **, int *);
};
struct buffer_metadata { u32 height, width, pixel_stride, format; };
_Static_assert(sizeof(struct hw_device) == 120, "LP64 HAL device ABI");
_Static_assert(sizeof(struct hw_module) == 248, "LP64 HAL module ABI");
_Static_assert(sizeof(struct buffer_metadata) == 16, "verified HWC metadata ABI");
extern void *dlsym(void *, const char *);
extern void *dlopen(const char *, int);
extern char *getenv(const char *);
extern int strcmp(const char *, const char *);
extern int fsetxattr(int, const char *, const void *, unsigned long, int);
extern long fgetxattr(int, const char *, void *, unsigned long);
extern long lseek(int, long, int);
extern int __android_log_print(int, const char *, const char *, ...);
static const char attr[] = "user.anvildroid.gralloc.v1";
static int (*real_open)(const struct hw_module *, const char *, struct hw_device **);
static int (*real_alloc)(struct alloc_device *, int, int, int, int,
                         const struct native_handle **, int *);
static int module_lock;
static int enabled(void) {
  const char *value = getenv("ANVILDROID_GRALLOC_METADATA");
  return value && !strcmp(value, "1");
}
static int valid(const struct buffer_metadata *m, const struct native_handle *h) {
  if (!h || h->version != 12 || h->numFds != 1 || h->data[0] < 0 ||
      !m->width || !m->height || m->width > 16384 || m->height > 16384 ||
      m->pixel_stride < m->width || m->pixel_stride > 32768 ||
      (m->format != 1 && m->format != 2)) return 0;
  long old = lseek(h->data[0], 0, 1);
  long size = lseek(h->data[0], 0, 2);
  if (old >= 0) lseek(h->data[0], old, 0);
  return size > 0 && (u64)m->pixel_stride * m->height * 4 <= (u64)size;
}
static int alloc_buffer(struct alloc_device *dev, int w, int h, int format,
                        int usage, const struct native_handle **handle, int *stride) {
  int ret = real_alloc(dev, w, h, format, usage, handle, stride);
  if (!ret && handle && stride) {
    struct buffer_metadata m = {(u32)h, (u32)w, (u32)*stride, (u32)format};
    if (valid(&m, *handle) && fsetxattr((*handle)->data[0], attr, &m, sizeof(m), 0))
      __android_log_print(6, "AnvilMetadata", "Cannot attach allocator metadata");
  }
  return ret;
}
static int open_device(const struct hw_module *module, const char *id,
                       struct hw_device **device) {
  int ret = real_open(module, id, device);
  if (!ret && !strcmp(id, "gpu0") && device && *device && (*device)->version == 0) {
    struct alloc_device *alloc = (struct alloc_device *)*device;
    if (alloc->alloc != alloc_buffer) {
      real_alloc = alloc->alloc;
      alloc->alloc = alloc_buffer;
      __android_log_print(4, "AnvilMetadata", "Allocator metadata enabled");
    }
  }
  return ret;
}
int hw_get_module(const char *id, const struct hw_module **module) {
  /* Passthrough HAL dependencies are RTLD_LOCAL on Android. RTLD_NEXT alone
   * cannot find libhardware when this preload is called from such a HAL. */
  void *library = dlopen("libhardware.so", 2);
  int (*real)(const char *, const struct hw_module **) =
      library ? (void *)dlsym(library, "hw_get_module") : (void *)0;
  if (!real) return -38;
  int ret = real(id, module);
  if (!ret && enabled() && !strcmp(id, "gralloc") && module && *module &&
      (*module)->module_api_version < 0x100 && (*module)->methods) {
    while (__atomic_exchange_n(&module_lock, 1, __ATOMIC_ACQUIRE)) {}
    static struct hw_methods methods;
    if ((*module)->methods->open != open_device) {
      real_open = (*module)->methods->open;
      methods.open = open_device;
      ((struct hw_module *)*module)->methods = &methods;
    }
    __atomic_store_n(&module_lock, 0, __ATOMIC_RELEASE);
  }
  return ret;
}
/* This one private C++ entry is gated by the exact HWC SHA256 in the worker.
 * x86_64 SysV: unique_ptr return uses an explicit sret pointer in RDI, then
 * display/metadata/handle in RSI/RDX/RCX. We forward return storage untouched;
 * no buffer, display, STL, or private native-handle layout is accessed.
 */
void *create_shm(void *, void *, const struct buffer_metadata *, const struct native_handle *)
  __asm__("_Z20create_shm_wl_bufferP7displayRK15buffer_metadataPK13native_handle");
void *create_shm(void *out, void *display, const struct buffer_metadata *metadata,
                 const struct native_handle *handle) {
  static void *library;
  if (!library) library = dlopen("/vendor/lib64/hw/hwcomposer.waydroid.so", 2);
  void *(*real)(void *, void *, const struct buffer_metadata *, const struct native_handle *) =
      library ? (void *)dlsym(library,
      "_Z20create_shm_wl_bufferP7displayRK15buffer_metadataPK13native_handle") : (void *)0;
  if (!real) { *(void **)out = (void *)0; return out; }
  struct buffer_metadata m;
  if (enabled() && (!metadata->format || !metadata->pixel_stride) &&
      handle && handle->numFds == 1 &&
      fgetxattr(handle->data[0], attr, &m, sizeof(m)) == sizeof(m) && valid(&m, handle)) {
    static int logged;
    if (!__atomic_exchange_n(&logged, 1, __ATOMIC_RELAXED))
      __android_log_print(4, "AnvilMetadata", "Recovered buffer %ux%u stride=%u format=%u",
                                      m.width, m.height, m.pixel_stride, m.format);
    return real(out, display, &m, handle);
  }
  return real(out, display, metadata, handle);
}
