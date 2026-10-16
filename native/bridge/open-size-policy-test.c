#include "open-size-policy.h"
#include <assert.h>
int main(void) {
  struct open_size_history history={0};
  int ids[128],first[128];
  ids[0]=24;open_size_snapshot(&history,ids,1,first);assert(first[0]);
  /* First sight was small or restoring: becoming larger after a user resize
   * must not cause a delayed compact-open request. */
  open_size_snapshot(&history,ids,1,first);assert(!first[0]);
  open_size_defer(&history,24);
  open_size_snapshot(&history,ids,1,first);assert(first[0]);
  ids[1]=25;open_size_snapshot(&history,ids,2,first);assert(!first[0]&&first[1]);
  /* More than eight active apps and long sessions do not exhaust history. */
  for(int i=0;i<128;++i)ids[i]=100+i;
  open_size_snapshot(&history,ids,128,first);
  for(int i=0;i<128;++i)assert(first[i]);
  open_size_snapshot(&history,ids,128,first);
  for(int i=0;i<128;++i)assert(!first[i]);
  ids[0]=300;open_size_snapshot(&history,ids,1,first);assert(first[0]);
  open_size_snapshot(&history,ids,1,first);assert(!first[0]);
  open_size_snapshot(&history,ids,0,first);
  open_size_snapshot(&history,ids,1,first);assert(first[0]);
  return 0;
}
