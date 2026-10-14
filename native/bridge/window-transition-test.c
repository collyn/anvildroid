#include "bridge.c"
#include <assert.h>
extern int pthread_mutex_trylock(void *);
int __android_log_print(int l,const char *t,const char *f,...) {
  (void)l;(void)t;(void)f;return 0;
}
static int calls;
static int transition(int operation,int task,const char *package) {
  /* HWC may send Wayland requests while processing the call. */
  assert(!pthread_mutex_trylock(mutex));pthread_mutex_unlock(mutex);
  ++calls;
  if (operation==3) {assert(!task&&!package);return 1;}
  assert(task==84&&!strcmp(package,"com.test"));
  return operation==1||operation==2||operation==4||operation==5;
}
int main(void) {
  hwc_transition_probed=1;
  assert(!hwc_transition_call(1,84,"com.test")); // Stock HWC remains supported.
  hwc_transition_api=transition;
  assert(hwc_transition_call(1,84,"com.test"));
  assert(hwc_transition_call(5,84,"com.test"));
  assert(hwc_transition_call(4,84,"com.test"));
  assert(hwc_transition_call(2,84,"com.test"));
  assert(hwc_transition_call(3,0,0));
  assert(calls==5);
}
