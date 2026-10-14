// Keep tab navigation independent from backend latency. Existing content stays
// visible while stale data refreshes; explicit Refresh buttons bypass this cache.
(function(root) {
  function create({now = () => Date.now(), frame = callback => requestAnimationFrame(callback), ttl = 15000,
    onError = error => console.error('Page refresh failed', error)} = {}) {
    const completed = new Map(), pending = new Map();
    const mark = page => completed.set(page, now());
    function schedule(page, active, load) {
      if (pending.has(page)) return pending.get(page);
      if (completed.has(page) && now() - completed.get(page) < ttl) return Promise.resolve();
      // Two frames allow the selected page to paint before starting IPC work.
      const work = new Promise(resolve => frame(() => frame(resolve))).then(async () => {
        if (!active()) return;
        await load();
        mark(page);
      }).catch(onError).finally(() => pending.delete(page));
      pending.set(page, work);
      return work;
    }
    return {schedule, mark};
  }
  root.AnvilPageLoads = {create};
  if (typeof module !== 'undefined') module.exports = {create};
})(typeof window === 'undefined' ? globalThis : window);
