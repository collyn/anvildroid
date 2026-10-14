/* ADB is loaded only when its page opens. No startup polling. */
(function(root) {
  const authorizationHint = 'Open this runtime\'s Waydroid window, then tap Allow in the "Allow USB debugging?" dialog.';
  function connectionState(report, host) {
    if (!report?.enabled) return 'Disabled';
    const device = host?.devices?.find(item => item.endpoint === report.endpoint);
    return ({device:'Connected', unauthorized:'Unauthorized', authorizing:'Authorizing', offline:'Offline'})[device?.state] || 'Ready';
  }
  function create({request, invoke, escape, toast, copy, active, document, storage}) {
    let rows = [], host = null, loading = false, busy = null, timer = null, rendered = null;
    const body = document.getElementById('adb-runtime-rows');
    const refreshButton = document.getElementById('btn-refresh-adb');
    const pathInput = document.getElementById('adb-path');
    const notice = document.getElementById('adb-status');
    try { pathInput.value = storage?.getItem('anvildroid-adb-path') || ''; } catch (_) {}
    const sdkPath = () => pathInput.value.trim() || null;
    function render() {
      const markup = rows.map(row => {
        const report = row.adb;
        const state = row.error ? 'Unavailable' : connectionState(report, host);
        const available = ['Running', 'Frozen'].includes(row.state);
        return `<tr class="runtime-summary"><td><strong>${escape(row.name)}</strong><small>${escape(row.state || 'Unavailable')}</small></td><td><span class="status-badge ${state === 'Connected' ? 'connected' : ''}">${escape(state)}</span>${state === 'Unauthorized' ? `<small class="adb-auth-hint">${escape(authorizationHint)}</small>` : ''}${row.error || report?.error ? `<small class="adb-error">${escape(row.error || report.error)}</small>` : ''}</td><td>${report?.endpoint ? `<code>${escape(report.endpoint)}</code>` : '—'}</td><td><div class="runtime-actions"><button class="btn" data-adb-action="connect" data-id="${escape(row.id)}" ${busy || !available || !host?.available ? 'disabled' : ''}>${state === 'Connected' ? 'Reconnect' : 'Connect'}</button><button class="btn" data-adb-action="disconnect" data-id="${escape(row.id)}" ${busy || !report?.enabled ? 'disabled' : ''}>Disconnect</button><button class="btn btn-quiet" data-adb-action="copy" data-id="${escape(row.id)}" ${!report?.endpoint ? 'disabled' : ''} title="Copy adb connect command">Copy</button></div></td></tr>`;
      }).join('');
      if (markup !== rendered) { body.innerHTML = markup; rendered = markup; }
      pathInput.disabled = !!busy;
      refreshButton.disabled = loading || !!busy;
      notice.textContent = host?.available ? (host.executable || '') : host?.message || 'Loading…';
    }
    function stop() { clearTimeout(timer); timer = null; }
    function schedule() { stop(); if (active()) timer = setTimeout(refresh, 4000); }
    async function refresh() {
      if (loading || busy) return;
      loading = true; refreshButton.disabled = true;
      try {
        const results = await Promise.allSettled([request({op:'list'}), request({op:'refresh', id:'default'}), invoke('adb_host_status', {adbPath:sdkPath()})]);
        host = results[2].status === 'fulfilled' ? results[2].value : {available:false, message:String(results[2].reason)};
        const previous = new Map(rows.map(row => [row.id, row.adb]));
        const managed = results[0].status === 'fulfilled' ? results[0].value : [];
        const defaultRow = results[1].status === 'fulfilled' ? results[1].value : {id:'default', name:'Default runtime', state:'Unavailable', error:String(results[1].reason)};
        rows = [defaultRow, ...managed].map(row => ({...row, adb:previous.get(row.id)}));
        await Promise.all(rows.map(async row => {
          if (row.error) return;
          try { row.adb = await request({op:'adb_info', id:row.id}); }
          catch (error) { row.error = String(error); }
        }));
        render();
        if (results[0].status === 'rejected') notice.textContent += ` · ${results[0].reason}`;
      } catch (error) { notice.textContent = String(error); }
      finally { loading = false; refreshButton.disabled = false; schedule(); }
    }
    async function action(id, operation) {
      const row = rows.find(row => row.id === id);
      if (!row || busy) return;
      if (operation === 'copy') {
        try { await copy(`adb connect ${row.adb.endpoint}`); toast('ADB command copied', 'success'); }
        catch (error) { toast(String(error), 'error'); }
        return;
      }
      busy = id; stop(); render();
      try {
        if (operation === 'connect') {
          row.adb = await request({op:'adb_set', id, enabled:true});
          const message = await invoke('adb_host_action', {action:'connect', endpoint:row.adb.endpoint, adbPath:sdkPath()});
          host = await invoke('adb_host_status', {adbPath:sdkPath()});
          const state = connectionState(row.adb, host);
          toast(state === 'Unauthorized' ? authorizationHint : message, state === 'Connected' ? 'success' : 'info');
        } else {
          // Closing the relay also closes transports when adb is missing/broken.
          const endpoint = row.adb?.endpoint;
          row.adb = await request({op:'adb_set', id, enabled:false});
          if (endpoint && host?.available) {
            try { await invoke('adb_host_action', {action:'disconnect', endpoint, adbPath:sdkPath()}); }
            catch (error) { toast(String(error), 'error'); }
          }
          toast('ADB disconnected', 'success');
        }
      } catch (error) { toast(String(error), 'error'); }
      finally { busy = null; render(); await refresh(); }
    }
    refreshButton.addEventListener('click', refresh);
    pathInput.addEventListener('change', () => {
      try { storage?.setItem('anvildroid-adb-path', pathInput.value.trim()); } catch (_) {}
      refresh();
    });
    body.addEventListener('click', event => {
      const button = event.target.closest('[data-adb-action]');
      if (button && !button.disabled) action(button.dataset.id, button.dataset.adbAction);
    });
    return {refresh, stop, action};
  }
  root.AnvilAdb = {create, connectionState};
  if (typeof module !== 'undefined') module.exports = {create, connectionState};
})(typeof window === 'undefined' ? globalThis : window);
