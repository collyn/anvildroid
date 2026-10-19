/* Freestanding JNI entry: no libc, NDK runtime or host-global registration. */
#if !defined(__arm__) && !defined(__aarch64__)
#error "The acceptance probe must contain actual ARM machine code"
#endif
__attribute__((visibility("default")))
int Java_org_anvildroid_armprobe_MainActivity_run(void *env, void *receiver, int seed) {
    (void)env;
    (void)receiver;
    volatile int value = seed;
    for (int index = 0; index < 1000; ++index) value += seed;
    return value;
}
