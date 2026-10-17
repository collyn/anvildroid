/* Runtime components and explicit local diagnostic export. Original MIT code. */
(function(root) {
  function create({request, invoke, document, toast = () => {}, chooseReport, confirm = async () => true, openImages, refreshRuntime = () => {}}) {
    function element(tag, text, parent) {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (parent) parent.append(node);
      return node;
    }
    const dialog = element('dialog'); dialog.id = 'runtime-components-dialog';
    const title = element('h2', 'Components', dialog);
    title.id = 'runtime-components-title'; title.tabIndex = -1;
    dialog.setAttribute('aria-labelledby', title.id);
    element('p', 'Installed ARM components below describe saved configuration. Stop the runtime before changes; start it afterward to activate them.', dialog);
    const list = element('div', undefined, dialog);
    const notice = element('p', '', dialog); notice.setAttribute('role', 'status');
    const refresh = element('button', 'Refresh', dialog); refresh.type = 'button'; refresh.className = 'btn';
    const close = element('button', 'Close', dialog); close.type = 'button'; close.className = 'btn';
    document.body.append(dialog);
    let runtime = null, epoch = 0, busy = false, report = null;
    function valid(value, id) {
      const bad = () => { throw new Error('Component response does not match this runtime'); };
      if (!value || value.schema_version !== 1 || value.runtime_id !== id || !Array.isArray(value.components) || value.components.length > 32 || !/^[a-f0-9]{64}$/.test(value.revision || '')) bad();
      for (const row of value.components) {
        if (!row || !['libndk','libhoudini','gapps','microg','smartdock','magisk'].includes(row.id) || typeof row.name !== 'string' || typeof row.status !== 'string' || !['arm_translation','image','extension'].includes(row.kind) || typeof row.can_enable !== 'boolean' || typeof row.can_disable !== 'boolean' || (row.packages !== undefined && !Array.isArray(row.packages))) bad();
        if (row.conflicts !== undefined && (!Array.isArray(row.conflicts) || row.conflicts.some(item => typeof item !== 'string'))) bad();
        if (row.architectures !== undefined && (!Array.isArray(row.architectures) || row.architectures.some(item => typeof item !== 'string'))) bad();
        if (row.android_sdks !== undefined && (!Array.isArray(row.android_sdks) || row.android_sdks.some(item => !Number.isSafeInteger(item)))) bad();
        for (const item of row.packages || []) {
          if (!item || typeof item.label !== 'string' || typeof item.cached !== 'boolean' || (item.sha256 !== null && !/^[a-f0-9]{64}$/.test(item.sha256 || ''))) bad();
        }
      }
      return value;
    }
    function render() {
      list.replaceChildren();
      refresh.disabled = busy;
      if (!report) return;
      for (const row of report.components) {
        const box = element('section', undefined, list); box.className = 'runtime-card';
        element('h3', row.name, box);
        element('p', row.status + (row.installed_version ? ' · ' + row.installed_version : ''), box);
        if (row.reason) element('p', row.reason, box);
        if (row.conflicts?.length) element('small', 'Conflicts: ' + row.conflicts.join(', '), box);
        if (row.kind === 'arm_translation') {
          element('p', `Architecture: ${(row.architectures || [report.architecture || 'unknown']).join(', ')} · Android SDK: ${(row.android_sdks || []).join(', ') || 'unknown'}`, box);
          const select = element('select', undefined, box); select.setAttribute('aria-label', row.name + ' version');
          let selectedPackage = null;
          for (const item of row.packages || []) {
            const verification = item.verification === 'existing_libraries' ? 'existing system libraries' : item.cached ? 'cached; verified on installation' : 'download required';
            const option = element('option', `${item.label} · ${verification}`, select);
            option.value = item.sha256 || ''; option.disabled = !item.cached;
            if (item.cached && selectedPackage === null) { option.selected = true; selectedPackage = item; }
          }
          select.value = selectedPackage?.sha256 || '';
          const packageNote = element('p', '', box);
          function describePackage() {
            const selected = (row.packages || []).find(item => (item.sha256 || '') === select.value && item.cached);
            packageNote.textContent = selected ? `Version: ${selected.version || 'unknown'} · SHA-256: ${selected.sha256 || 'unavailable for existing libraries'}` : 'Download a catalog package before enabling it.';
          }
          select.addEventListener('change', describePackage); describePackage();
          const stopped = ['Stopped', 'Prepared', 'STOPPED'].includes(runtime.state);
          select.disabled = busy || !stopped;
          const enable = element('button', 'Enable selected version', box); enable.type = 'button'; enable.className = 'btn btn-primary';
          enable.disabled = busy || !stopped || !row.can_enable;
          enable.addEventListener('click', () => change(row.id, true, select.value || null));
          const disable = element('button', 'Disable', box); disable.type = 'button'; disable.className = 'btn';
          disable.disabled = busy || !stopped || !row.can_disable;
          disable.addEventListener('click', () => change(row.id, false, null));
          if ((row.packages || []).some(item => !item.cached)) {
            const download = element('button', 'Download a catalog version…', box); download.type = 'button'; download.className = 'btn'; download.disabled = busy;
            download.addEventListener('click', () => { dialog.close(); openImages('arm'); });
          }
        } else if (row.action === 'create_gapps_runtime') {
          const image = element('button', 'Open image library', box); image.type = 'button'; image.className = 'btn';
          image.addEventListener('click', () => { dialog.close(); openImages('gapps'); });
        }
      }
      refresh.disabled = busy;
    }
    async function load() {
      if (!runtime || busy) return;
      const current = epoch, id = runtime.id; busy = true; report = null; render(); refresh.disabled = true;
      try {
        const value = valid(await request({op:'components_get', id}), id);
        if (current !== epoch) return;
        report = value; notice.textContent = 'Cached downloads and configured components are shown separately. Native behavior depends on the image.';
      } catch (error) { if (current === epoch) { report = null; notice.textContent = String(error.message || error); } }
      finally { if (current === epoch) { busy = false; render(); dialog.scrollTop = 0; } }
    }
    async function change(componentId, enabled, digest) {
      if (!runtime || busy || !report) return;
      const current = epoch, id = runtime.id, revision = report.revision;
      busy = true; render();
      try {
        if (!await confirm(`${enabled ? 'Enable' : 'Disable'} ${componentId} in ${runtime.name || id}?\nChanging the ARM provider replaces the active provider for this runtime. Start the runtime after saving to activate the change.`, {title:'Change component'})) return;
        if (current !== epoch) return;
        const value = valid(await request({op:'components_set', id, component_id:componentId, enabled, archive_sha256:digest, revision}), id);
        if (current === epoch) { report = value; notice.textContent = 'Configuration saved. Start this runtime to activate it.'; toast('Component configuration saved.', 'success'); await refreshRuntime(id); }
      } catch (error) { if (current === epoch) { report = null; notice.textContent = `${String(error.message || error)} Refresh before retrying.`; } }
      finally { if (current === epoch) { busy = false; render(); } }
    }
    close.addEventListener('click', () => dialog.close()); refresh.addEventListener('click', load);
    dialog.addEventListener('close', () => { epoch++; runtime = null; report = null; busy = false; });

    const diagnosticDialog = element('dialog'); diagnosticDialog.id = 'runtime-diagnostics-dialog';
    const diagnosticTitle = element('h2', 'Export diagnostics', diagnosticDialog);
    diagnosticTitle.id = 'runtime-diagnostics-title'; diagnosticTitle.tabIndex = -1;
    diagnosticDialog.setAttribute('aria-labelledby', diagnosticTitle.id);
    element('p', 'Save a local JSON report with host, image, GPU, runtime and health details. Nothing is uploaded.', diagnosticDialog);
    const label = element('label', undefined, diagnosticDialog);
    const logs = element('input', undefined, label); logs.type = 'checkbox'; logs.checked = false;
    label.append(document.createTextNode(' Include recent runtime logs (redacted where recognized; review before sharing)'));
    const diagnosticNotice = element('p', '', diagnosticDialog); diagnosticNotice.setAttribute('role', 'status');
    const save = element('button', 'Save report…', diagnosticDialog); save.type = 'button'; save.className = 'btn btn-primary';
    const diagnosticClose = element('button', 'Close', diagnosticDialog); diagnosticClose.type = 'button'; diagnosticClose.className = 'btn';
    document.body.append(diagnosticDialog);
    let diagnosticRuntime = null, diagnosticEpoch = 0, exporting = false;
    save.addEventListener('click', async () => {
      if (!diagnosticRuntime || exporting) return;
      const current = diagnosticEpoch, id = diagnosticRuntime.id, includeLogs = logs.checked;
      exporting = true; save.disabled = logs.disabled = true;
      try {
        const path = await chooseReport(`anvildroid-${id}-${Date.now()}.json`);
        if (!path || current !== diagnosticEpoch) return;
        diagnosticNotice.textContent = 'Collecting diagnostics…';
        const result = await invoke('export_runtime_diagnostics', {id, path, includeLogs});
        if (result?.runtime_id !== id || !Number.isSafeInteger(result.bytes) || result.bytes <= 0) throw new Error('Diagnostic export did not confirm this runtime');
        if (current === diagnosticEpoch) { diagnosticNotice.textContent = 'Report saved. Existing files are preserved.'; toast('Diagnostic report saved.', 'success'); }
      } catch (error) { if (current === diagnosticEpoch) diagnosticNotice.textContent = String(error.message || error); }
      finally { if (current === diagnosticEpoch) { exporting = false; save.disabled = logs.disabled = false; } }
    });
    diagnosticClose.addEventListener('click', () => diagnosticDialog.close());
    diagnosticDialog.addEventListener('close', () => { diagnosticEpoch++; diagnosticRuntime = null; exporting = false; });
    return {
      components(value) { epoch++; runtime = {...value}; report = null; busy = false; list.replaceChildren(); notice.textContent = 'Loading…'; title.textContent = 'Components · ' + (value.name || value.id); dialog.showModal(); title.focus(); return load(); },
      diagnostics(value) { diagnosticEpoch++; diagnosticRuntime = {...value}; exporting = false; logs.checked = false; logs.disabled = save.disabled = false; diagnosticNotice.textContent = ''; diagnosticDialog.showModal(); diagnosticTitle.focus(); },
    };
  }
  root.AnvilRuntimeTools = {create};
  if (typeof module !== 'undefined') module.exports = {create};
})(typeof window === 'undefined' ? globalThis : window);
