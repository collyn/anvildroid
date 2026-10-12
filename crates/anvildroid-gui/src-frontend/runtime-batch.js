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
  async function run(records, op, {refresh, execute, requirements, progress = () => {}}) {
    const results = [];
    for (const snapshot of records) {
      progress(snapshot, results.length, records.length);
      try {
        const record = await refresh(snapshot.id);
        if (record.id !== snapshot.id) throw new Error('Runtime identity changed.');
        if (['delete', 'reinstall'].includes(op) && record.name !== snapshot.name) throw new Error('Runtime name changed; review confirmation again.');
        if (!eligible(record, op, requirements)) { results.push({id: snapshot.id, name: snapshot.name, status: 'skipped', detail: 'State changed or action unavailable.'}); continue; }
        if (op === 'restart') {
          await execute(record, 'stop');
          const stopped = await refresh(record.id);
          if (stopped.id !== record.id || stopped.state !== 'Stopped') throw new Error('Stop did not complete; restart cancelled.');
          await execute(stopped, 'start');
        } else await execute(record, op);
        results.push({id: snapshot.id, name: snapshot.name, status: 'succeeded'});
      } catch (error) { results.push({id: snapshot.id, name: snapshot.name, status: 'failed', detail: String(error)}); }
    }
    return results;
  }
  return {operations, eligible, run};
})();
if (typeof module !== 'undefined') module.exports = RuntimeBatch;
