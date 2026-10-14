#ifndef ANVIL_OPEN_SIZE_POLICY_H
#define ANVIL_OPEN_SIZE_POLICY_H
/* Observe every task on first sight, even if it is already small, restoring a
 * profile, or in Full UI. A later user resize must never become its first open.
 * Keep only the active snapshot so long sessions do not fill a fixed history. */
struct open_size_history { int ids[128], count; };
static void open_size_snapshot(struct open_size_history *history,
                               const int *ids, int count, int *first) {
  if (count > 128) count = 128;
  for (int i=0;i<count;++i) {
    first[i]=1;
    for (int j=0;j<history->count;++j)
      if (history->ids[j]==ids[i]) { first[i]=0; break; }
  }
  history->count=count;
  for (int i=0;i<count;++i) history->ids[i]=ids[i];
}
#endif
