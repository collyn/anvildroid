/* Android 15 WayDroid HWC calls Binder RPC pool configuration twice. */
extern void *dlsym(void *, const char *);
static unsigned long pool_size;
static int pool_lock;
typedef void (*configure_fn)(unsigned long, int);
static void configure(unsigned long threads, int joins) {
  static configure_fn rpc;
  while (__atomic_exchange_n(&pool_lock, 1, __ATOMIC_ACQUIRE)) {}
  if (!rpc)
    rpc = (configure_fn)dlsym((void *)-1L,
                             "_ZN7android8hardware22configureRpcThreadpoolEmb");
  if (rpc && threads > pool_size) {
    rpc(threads, joins);
    pool_size = threads;
  }
  __atomic_store_n(&pool_lock, 0, __ATOMIC_RELEASE);
}
void rpc(unsigned long n, int j) __asm__("_ZN7android8hardware22configureRpcThreadpoolEmb");
/* Leave configureBinderRpcThreadpool alone: the real RPC function calls it
 * internally, and suppressing that nested call skips pool initialization. */
void rpc(unsigned long n, int j) { configure(n, j); }
