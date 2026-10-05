/* Custom bionic path filters dereference the caller buffer before the kernel
 * can reject it. Preserve the image's filters for valid paths and return the
 * kernel's EFAULT/ENAMETOOLONG for invalid input. x86_64 Android only. */
#include <stddef.h>
#include <stdint.h>
#include <stdarg.h>

extern long syscall(long, ...);
#ifdef __ANDROID__
extern int *__errno(void);
#define ERRNO (*__errno())
#else
#include <errno.h>
#define ERRNO errno
#endif

static int pathname_valid_at(int directory, const char *path) {
    int saved = ERRNO;
    uint64_t buffer[32];
    long result = syscall(262L, directory, path, buffer, 0x100);
    if (result == -1 && (ERRNO == 14 || ERRNO == 36)) return 0;
    ERRNO = saved;
    return 1;
}

#define GUARDED_STAT(name) \
    extern int name(const char *, void *); \
    int anvildroid_##name(const char *path, void *buffer) { \
        if (!pathname_valid_at(-100, path)) return -1; \
        return name(path, buffer); \
    }
GUARDED_STAT(stat)
GUARDED_STAT(lstat)

extern int openat(int, const char *, int, ...);
extern int open(const char *, int, ...);
extern int __open_2(const char *, int);
static unsigned int open_mode(int flags, va_list args) {
    return (flags & 0x40) || (flags & 0x410000) == 0x410000
        ? va_arg(args, unsigned int) : 0;
}
int anvildroid_openat(int directory, const char *path, int flags, ...) {
    if (!pathname_valid_at(directory, path)) return -1;
    va_list args;
    va_start(args, flags);
    unsigned int mode = open_mode(flags, args);
    va_end(args);
    return openat(directory, path, flags, mode);
}
int anvildroid_open(const char *path, int flags, ...) {
    if (!pathname_valid_at(-100, path)) return -1;
    va_list args;
    va_start(args, flags);
    unsigned int mode = open_mode(flags, args);
    va_end(args);
    return open(path, flags, mode);
}
int anvildroid_open_2(const char *path, int flags) {
    if (!pathname_valid_at(-100, path)) return -1;
    return __open_2(path, flags);
}
