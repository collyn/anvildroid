const RuntimeUsage = (() => {
  const samples = new Map();
  const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const positive = value => number(value) && value > 0;
  const gib = value => (value / 1024 ** 3).toFixed(1);
  const effectiveResources = record => !record.usageResourcesUnavailable && ['Running', 'Frozen'].includes(record.state) ? record.resources?.effective : null;
  function sample(record, now = performance.now()) {
    const data = record.resources, actual = effectiveResources(record);
    const previous = samples.get(record.id);
    record.cpuUsagePercent = null;
    if (!actual || !number(actual.cpu_usage_usec) || !actual.scope) { samples.delete(record.id); return; }
    const capacity = positive(actual.cpu_quota_count) ? actual.cpu_quota_count : data.host?.cpu_count;
    if (previous && previous.scope === actual.scope && previous.capacity === capacity && now > previous.time && actual.cpu_usage_usec >= previous.usage && positive(capacity)) {
      record.cpuUsagePercent = (actual.cpu_usage_usec - previous.usage) / ((now - previous.time) * 1000 * capacity) * 100;
    }
    samples.set(record.id, {time:now, usage:actual.cpu_usage_usec, scope:actual.scope, capacity});
  }
  function metrics(record) {
    const data = record.resources, actual = effectiveResources(record);
    const memoryMax = positive(actual?.memory_max_bytes) ? actual.memory_max_bytes : data?.host?.memory_total_mib * 1024 ** 2;
    const memory = number(actual?.memory_current_bytes) ? actual.memory_current_bytes : null;
    const cpu = number(record.cpuUsagePercent) && actual ? record.cpuUsagePercent : null;
    const disk = !record.usageDiskUnavailable && number(record.storage?.used_bytes) ? record.storage.used_bytes : null;
    const diskMax = record.storage?.total_bytes;
    const metric = (name, value, max, text, hint) => ({name, text, hint, percent:value != null && positive(max) ? Math.min(100, Math.max(0, value / max * 100)) : null});
    return [
      metric('Memory', memory, memoryMax, memory == null ? '—' : `${gib(memory)}${positive(memoryMax) ? ' / ' + gib(memoryMax) : ''} GiB`, actual?.memory_max_bytes != null ? 'Runtime memory / RAM limit' : 'Runtime memory / host RAM (no runtime limit)'),
      metric('CPU', cpu, 100, cpu == null ? '—' : `${cpu.toFixed(1)}%`, 'CPU usage / ' + (actual?.cpu_quota_count || data?.host?.cpu_count || 'available') + ' CPUs; sampled between refreshes'),
      metric('Disk', disk, diskMax, disk == null ? '—' : `${gib(disk)}${positive(diskMax) ? ' / ' + gib(diskMax) : ''} GiB`, 'Used space on the shared filesystem containing runtime data'),
    ];
  }
  function retain(ids) { for (const id of samples.keys()) if (!ids.has(id)) samples.delete(id); }
  return {sample, metrics, retain};
})();
if (typeof module !== 'undefined') module.exports = RuntimeUsage;
