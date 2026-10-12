#include <assert.h>
#include <pthread.h>
#include <string.h>

static unsigned long configured;
static int calls, instance, failure, missing;
static int configure(void *self, unsigned long count, int joins) {
  assert(self == &instance);
  (void)joins;
  assert(!calls || count >= configured);
  ++calls;
  if (failure) return failure;
  configured = count;
  return 0;
}
static void *lookup(void *handle, const char *symbol) {
  (void)handle;
  assert(!strcmp(symbol, "_ZN7android8hardware12ProcessState26setThreadPoolConfigurationEmb"));
  return missing ? 0 : (void *)configure;
}
#define dlsym lookup
#include "hidl-pool.inc"

static void *configure_many(void *arg) {
  for (int i = 0; i < 1000; ++i)
    assert(anvil_hidl_pool_configure(&instance, (unsigned long)arg, 0) == 0);
  return 0;
}
int main(void) {
  missing = 1;
  assert(anvil_hidl_pool_configure(&instance, 0, 0) == -38);
  assert(!hidl_pool_initialized && !calls);
  missing = 0;
  failure = -22;
  assert(anvil_hidl_pool_configure(&instance, 0, 0) == -22);
  assert(!hidl_pool_initialized);
  failure = 0;
  calls = 0;
  assert(anvil_hidl_pool_configure(&instance, 0, 0) == 0 && calls == 1);
  assert(anvil_hidl_pool_configure(&instance, 4, 1) == 0 && calls == 2);
  /* Polling must not shrink a running pool; skipped calls still return OK. */
  assert(anvil_hidl_pool_configure(&instance, 0, 0) == 0);
  assert(anvil_hidl_pool_configure(&instance, 1, 0) == 0);
  assert(calls == 2 && configured == 4);
  assert(anvil_hidl_pool_configure(&instance, 4, 1) == 0 && calls == 3);
  failure = -22;
  assert(anvil_hidl_pool_configure(&instance, 8, 1) == -22);
  assert(hidl_pool_size == 4 && configured == 4);
  failure = 0;
  pthread_t threads[8];
  for (unsigned long i = 0; i < 8; ++i)
    assert(!pthread_create(&threads[i], 0, configure_many, (void *)(i + 1)));
  for (int i = 0; i < 8; ++i) assert(!pthread_join(threads[i], 0));
  assert(configured == 8);
}
