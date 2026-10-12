// Bounded, session-local diagnostics. A completed Android job is not visibility.
const RuntimeWindow = (() => {
  const traces = [], pending = new Map();
  let next = 0;
  async function measure(record, work, {now = () => performance.now(), publish = () => {}} = {}) {
    if (pending.has(record.id)) return pending.get(record.id);
    const started = now();
    const trace = {id: ++next, runtime_id: record.id, started_at: new Date().toISOString(), stages: [], outcome:'pending', visibility:'unverified'};
    const stage = name => { trace.stages.push({name, elapsed_ms: Math.max(0, Math.round(now() - started))}); publish({...trace}); };
    // Start in a microtask so registration precedes callbacks/repeated clicks.
    const task = Promise.resolve().then(async () => {
      try {
        stage('request');
        const result = await work(stage);
        trace.outcome = 'succeeded';
        trace.visibility = result?.window?.state || 'unverified';
        if (result?.window?.reason) trace.window_reason = result.window.reason;
        if (result?.timings) trace.backend_timings = result.timings;
        return {...result, trace};
      } catch (error) {
        trace.outcome = 'failed'; trace.error = String(error); throw error;
      } finally {
        trace.elapsed_ms = Math.max(0, Math.round(now() - started));
        traces.push(trace); if (traces.length > 50) traces.shift();
        pending.delete(record.id); publish({...trace});
      }
    });
    pending.set(record.id, task);
    return task;
  }
  return {measure, isPending: id => pending.has(id), diagnostics: () => JSON.parse(JSON.stringify(traces))};
})();
if (typeof module !== 'undefined') module.exports = RuntimeWindow;
