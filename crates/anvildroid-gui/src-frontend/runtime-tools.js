/* Explicit local diagnostic export, embedded in Runtime Settings. Original MIT code. */
(function(root) {
  function create({invoke, document, toast = () => {}, chooseReport}) {
    function element(tag, text, parent) {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (parent) parent.append(node);
      return node;
    }
    const panel = element('section'); panel.className = 'runtime-inline-feature runtime-diagnostic-export';
    element('h4', 'Export report', panel);
    element('p', 'Save a local JSON report with host, image, GPU, runtime and health details. Nothing is uploaded.', panel).className = 'hint';
    const label = element('label', undefined, panel);
    const logs = element('input', undefined, label); logs.type = 'checkbox'; logs.checked = false;
    label.append(document.createTextNode(' Include recent runtime logs (redacted where recognized; review before sharing)'));
    const notice = element('p', '', panel); notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite');
    const save = element('button', 'Save report…', panel); save.type = 'button'; save.className = 'btn btn-primary';
    let runtime = null, epoch = 0, exporting = false;
    save.addEventListener('click', async () => {
      if (!runtime || exporting) return;
      const current = epoch, id = runtime.id, includeLogs = logs.checked;
      exporting = true; save.disabled = logs.disabled = true;
      try {
        const path = await chooseReport(`anvildroid-${id}-${Date.now()}.json`);
        if (!path || current !== epoch) return;
        notice.textContent = 'Collecting diagnostics…';
        const result = await invoke('export_runtime_diagnostics', {id, path, includeLogs});
        if (result?.runtime_id !== id || !Number.isSafeInteger(result.bytes) || result.bytes <= 0) throw new Error('Diagnostic export did not confirm this runtime');
        if (current === epoch) { notice.textContent = 'Report saved. Existing files are preserved.'; toast('Diagnostic report saved.', 'success'); }
      } catch (error) { if (current === epoch) notice.textContent = String(error.message || error); }
      finally { if (current === epoch) { exporting = false; save.disabled = logs.disabled = false; } }
    });
    save.disabled = logs.disabled = true;
    return {
      mount(host, value) {
        if (panel.parentNode !== host) host.append(panel);
        panel.dataset.runtimeId = value.id; panel.dataset.runtimeState = value.state;
        if (runtime?.id === value.id) return;
        epoch++; runtime = {id:value.id}; exporting = false;
        logs.checked = false; logs.disabled = save.disabled = false; notice.textContent = '';
      },
      deactivate() {
        if (runtime) epoch++;
        runtime = null; exporting = false; save.disabled = logs.disabled = true; panel.remove();
      },
    };
  }
  root.AnvilRuntimeTools = {create};
  if (typeof module !== 'undefined') module.exports = {create};
})(typeof window === 'undefined' ? globalThis : window);
