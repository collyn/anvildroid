/* NVIDIA HWC starts the HIDL pool before setting up polling. libhidlbase
 * aborts on the later request to shrink it to zero. Preserve the running
 * pool, but forward the first configuration (including zero) and growth.
 * Serialize the real calls, not just publication of the requested size. */
static unsigned long hidl_pool_size;
static int hidl_pool_initialized, hidl_pool_lock;
void anvil_hidl_pool_configure(void *, unsigned long, int)
    __asm__("_ZN7android8hardware12ProcessState26setThreadPoolConfigurationEmb");
void anvil_hidl_pool_configure(void *self, unsigned long threads, int joins) {
  typedef void (*configure_fn)(void *, unsigned long, int);
  static configure_fn real;
  while (__atomic_exchange_n(&hidl_pool_lock, 1, __ATOMIC_ACQUIRE)) {}
  if (!real)
    real = (configure_fn)dlsym((void *)-1L,
        "_ZN7android8hardware12ProcessState26setThreadPoolConfigurationEmb");
  if (real && (!hidl_pool_initialized || threads >= hidl_pool_size)) {
    real(self, threads, joins);
    hidl_pool_size = threads;
    hidl_pool_initialized = 1;
  }
  __atomic_store_n(&hidl_pool_lock, 0, __ATOMIC_RELEASE);
}
