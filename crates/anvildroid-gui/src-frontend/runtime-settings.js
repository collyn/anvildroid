// Keep boot-time settings behind the controller's stopped-worker checks.
// The UI owns the sequence and remains busy until Android has booted again.
const RuntimeSettings = (() => {
  const bootOptions = new Set(['arm_set', 'gpu_set', 'resources_set', 'display_set', 'backend_set']);
  async function apply(request, {send, transition, progress = () => {}}) {
    if (!bootOptions.has(request.op)) return {result: await send(request), restarted: false};
    const record = await send({op:'refresh', id:request.id});
    if (record.id !== request.id) throw new Error('Runtime identity changed; refresh and retry.');
    if (!['Running', 'Frozen'].includes(record.state)) return {result: await send(request), restarted: false};
    progress('stopping');
    await transition('stop', request.id);
    let result, applyError;
    progress('applying');
    try { result = await send(request); }
    catch (error) { applyError = error; }
    // Even a rejected setting should not leave a previously running runtime
    // stopped. Start uses the controller's current, committed configuration.
    progress('starting');
    try { await transition('start', request.id); }
    catch (error) {
      throw new Error(`${applyError ? `Setting failed: ${applyError}.` : 'Setting saved.'} Restart failed: ${error}. Check runtime status before retrying.`);
    }
    if (applyError) throw new Error(`Setting failed: ${applyError}. Runtime restarted with its current configuration.`);
    return {result, restarted:true};
  }
  async function prepareReinstall(request, {send, transition, progress = () => {}}) {
    const inspect = async () => {
      const record = await send({op:'refresh', id:request.id});
      if (record?.id !== request.id || record.name !== request.confirmed_name)
        throw new Error('Runtime identity or name changed; review reinstall again.');
      if (record.deletion_pending || record.unavailable || record.managed === false ||
          record.job?.status === 'Running' || record.appJob?.status === 'Running' || record.storage?.job?.status === 'Running')
        throw new Error('Runtime is busy or unavailable; refresh and retry.');
      return record;
    };
    let record = await inspect();
    if (['Running', 'Frozen'].includes(record.state)) {
      progress('stopping');
      await transition('stop', request.id);
      record = await inspect();
      if (record.state !== 'Stopped') throw new Error('Runtime did not stop; reinstall cancelled.');
    }
    const allowed = request.id === 'default' ? ['Stopped'] : ['Allocated', 'Prepared', 'Stopped', 'Error'];
    if (!allowed.includes(record.state)) throw new Error('Runtime is not ready for reinstall.');
    progress('applying');
  }
  async function wait(send, id, jobId, expected, {sleep = ms => new Promise(resolve => setTimeout(resolve, ms)), now = Date.now, timeout = 360000} = {}) {
    const deadline = now() + timeout;
    while (now() < deadline) {
      const record = await send({op:'refresh', id});
      if (record.id !== id || record.job?.id !== jobId) throw new Error('Runtime operation changed; automatic restart cancelled.');
      if (record.state === 'Error' || record.job.status === 'Failed') throw new Error(record.job.error || 'Runtime transition failed.');
      if (record.state === expected && record.job.status === 'Succeeded') return record;
      await sleep(500);
    }
    throw new Error(`Timed out waiting for runtime to become ${expected}.`);
  }
  return {apply, wait, prepareReinstall};
})();
if (typeof module !== 'undefined') module.exports = RuntimeSettings;
