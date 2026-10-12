// Batch operations are serialized through completion, not just request acceptance.
const RuntimeBatch = (() => {
  const operations = ['open', 'start', 'stop', 'restart', 'reinstall', 'delete'];
  function eligible(record, op, requirements = {}) {
    if (!record || !operations.includes(op) || record.unavailable || record.refreshError ||
        record.job?.status === 'Running' || record.appJob?.status === 'Running' ||
        record.storage?.job?.status === 'Running' || ['Provisioning', 'Starting', 'Stopping'].includes(record.state)) return false;
    if (record.deletion_pending) return op === 'delete' && record.id !== 'default';
    if (op === 'open') return record.state === 'Running' && (record.id === 'default' || (record.display?.effective_mode || record.display?.mode) === 'desktop');
    if (op === 'delete') return record.id !== 'default' && (['Allocated', 'Prepared', 'Stopped'].includes(record.state) || (record.state === 'Error' && record.job?.operation === 'prepare'));
    if (op === 'reinstall') return record.id === 'default' ? record.managed && record.state === 'Stopped' : ['Allocated', 'Prepared', 'Stopped', 'Error'].includes(record.state);
    if (op === 'stop') return ['Running', 'Frozen', 'Error'].includes(record.state);
    if (record.storage?.error || requirements.can_start !== true || record.import_status === 'pending') return false;
    if (op === 'restart') return ['Running', 'Frozen'].includes(record.state);
    return ['Prepared', 'Stopped', 'Error'].includes(record.state) && !(record.state === 'Error' && record.job?.operation === 'prepare');
  }
  async function run(records, op, {refresh, execute, requirements, progress = () => {}, settled = () => {}}) {
    const results = [];
    async function perform(snapshot, index) {
      progress(snapshot, index, records.length);
      try {
        const record = await refresh(snapshot.id);
        if (record.id !== snapshot.id) throw new Error('Runtime identity changed.');
        if (['delete', 'reinstall'].includes(op) && record.name !== snapshot.name) throw new Error('Runtime name changed; review confirmation again.');
        if (!eligible(record, op, requirements)) { return {id: snapshot.id, name: snapshot.name, status: 'skipped', detail: 'State changed or action unavailable.'}; }
        let result;
        if (op === 'restart') {
          const stopResult = await execute(record, 'stop');
          const stopped = await refresh(record.id);
          if (stopped.id !== record.id || stopped.state !== 'Stopped') throw new Error('Stop did not complete; restart cancelled.');
          await execute(stopped, 'start');
          const warning = stopResult?.shutdown_warning || stopped.shutdown_warning;
          if (warning) result = {shutdown_warning: warning};
        } else result = await execute(record, op);
        return {id: snapshot.id, name: snapshot.name, status: 'succeeded', ...(result ? {result} : {}),
          ...(result?.shutdown_warning ? {detail:result.shutdown_warning} : {})};
      } catch (error) { return {id: snapshot.id, name: snapshot.name, status: 'failed', detail: String(error)}; }
      finally { settled(snapshot); }
    }
    if (op === 'open') {
      // Opening independent windows may overlap. Lifecycle/data mutations stay
      // serialized, and two workers leave headroom in the controller's pool.
      let next = 0;
      async function worker() {
        while (next < records.length) {
          const index = next++;
          results[index] = await perform(records[index], index);
        }
      }
      await Promise.all(Array.from({length: Math.min(2, records.length)}, () => worker()));
    } else {
      for (let index = 0; index < records.length; index++) results.push(await perform(records[index], index));
    }
    return results;
  }
  return {operations, eligible, run};
})();
if (typeof module !== 'undefined') module.exports = RuntimeBatch;
