/* Original runtime-scoped app settings. Dynamic text always uses textContent. */
(function(root) {
  const keys = ['fake_wifi', 'mouse_as_touch'];
  function create({request, document, toast = () => {}}) {
    const dialog = document.createElement('dialog');
    dialog.id = 'app-compatibility-dialog';
    dialog.setAttribute('aria-labelledby', 'app-compatibility-title');
    const form = document.createElement('form');
    const title = document.createElement('h2');
    title.id = 'app-compatibility-title';
    const subtitle = document.createElement('p');
    const notice = document.createElement('p');
    notice.setAttribute('role', 'status');
    notice.setAttribute('aria-live', 'polite');
    const note = document.createElement('p');
    note.textContent = 'These settings use Waydroid image properties. Image support varies; saved values do not verify app behavior. After a change, stop and reopen the app. Reset removes only this app’s entries; global settings still apply.';
    const fields = {};
    form.append(title, subtitle, note);
    for (const [key, labelText] of [['fake_wifi', 'Fake Wi-Fi'], ['mouse_as_touch', 'Mouse as touch']]) {
      const row = document.createElement('p');
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox'; input.id = `app-compatibility-${key}`;
      input.setAttribute('style', 'width:auto;min-width:0;margin-right:8px;padding:0');
      const text = document.createElement('span'); text.textContent = ` ${labelText} `;
      const description = document.createElement('small');
      label.append(input, text); row.append(label, description); form.append(row);
      fields[key] = {input, description};
    }
    const actions = document.createElement('div'); actions.className = 'dialog-actions';
    function button(text, type = 'button') {
      const element = document.createElement('button'); element.textContent = text;
      element.type = type; element.className = 'btn'; actions.append(element); return element;
    }
    const closeButton = button('Close'), refreshButton = button('Refresh');
    const resetButton = button('Reset app'), saveButton = button('Save', 'submit');
    saveButton.className = 'btn btn-primary';
    form.append(notice, actions); dialog.append(form); document.body.append(dialog);
    let token = 0, context = null, report = null, busy = false;
    function disabled(value) {
      busy = value;
      refreshButton.disabled = value;
      saveButton.disabled = value || !report;
      resetButton.disabled = value || !report;
      for (const key of keys) fields[key].input.disabled = value || !report || report.settings[key].inherited;
    }
    function validate(value, identity, previous = null) {
      if (!value || value.runtime_id !== identity.id || value.package !== identity.package ||
          !['string', 'number'].includes(typeof value.generation) ||
          (previous && value.generation !== previous.generation) ||
          typeof value.revision !== 'string' || !/^[0-9a-f]{64}$/.test(value.revision) ||
          !value.settings || keys.some(key => !value.settings[key] ||
            ['explicit','inherited','effective'].some(field => typeof value.settings[key][field] !== 'boolean') ||
            value.settings[key].effective !== (value.settings[key].explicit || value.settings[key].inherited)))
        throw new Error('Runtime or compatibility response changed; refresh and retry.');
      return value;
    }
    function render(value) {
      report = value;
      for (const key of keys) {
        const setting = report.settings[key]; fields[key].input.checked = setting.effective;
        fields[key].description.textContent = setting.inherited ? 'Enabled by global wildcard; per-app disable unavailable.' :
          setting.explicit ? 'Enabled for this app.' : 'No app override.';
      }
      notice.textContent = report.changed ? 'Saved. Stop and reopen the app to activate these settings.' : 'Current configuration loaded.';
    }
    async function refresh() {
      if (!context || busy) return;
      const current = token, identity = context;
      report = null; disabled(true); notice.textContent = 'Loading compatibility settings…';
      try {
        const result = await request({op:'compatibility_get', id:identity.id, package:identity.package});
        if (current !== token) return;
        render(validate(result, identity));
      } catch (error) {
        if (current === token) notice.textContent = String(error);
      } finally { if (current === token) disabled(false); }
    }
    async function mutate(action) {
      if (!context || !report || busy) return;
      const current = token, identity = context, previous = report;
      const payload = {op:`compatibility_${action}`, id:identity.id, package:identity.package, revision:report.revision};
      if (action === 'set') payload.settings = Object.fromEntries(keys.map(key => [key, fields[key].input.checked]));
      disabled(true); notice.textContent = action === 'reset' ? 'Resetting app entries…' : 'Saving…';
      try {
        const result = await request(payload);
        if (current !== token) return;
        render(validate(result, identity, previous));
        toast(result.changed ? 'Compatibility saved. Stop and reopen the app.' : 'Compatibility settings are unchanged.', 'success');
      } catch (error) {
        if (current === token) {
          report = null; notice.textContent = `${String(error)} Refresh before retrying.`;
          toast(String(error), 'error');
        }
      } finally { if (current === token) disabled(false); }
    }
    function closed() { token++; context = null; report = null; disabled(false); }
    function close() { closed(); if (dialog.open) dialog.close(); }
    async function open(runtime, packageName, label = packageName) {
      // Opening another app invalidates every pending read/write response.
      token++; report = null; busy = false;
      context = {id:typeof runtime === 'string' ? runtime : runtime.id, package:packageName};
      title.textContent = `Compatibility: ${label}`;
      subtitle.textContent = `${context.id} · ${packageName}`;
      for (const key of keys) { fields[key].input.checked = false; fields[key].description.textContent = ''; }
      if (!dialog.open) dialog.showModal();
      closeButton.focus();
      return refresh();
    }
    closeButton.addEventListener('click', close);
    refreshButton.addEventListener('click', refresh);
    resetButton.addEventListener('click', () => mutate('reset'));
    form.addEventListener('submit', event => { event.preventDefault(); void mutate('set'); });
    dialog.addEventListener('close', () => { if (!dialog.open) closed(); });
    disabled(false);
    return {open, close, refresh, save:() => mutate('set'), reset:() => mutate('reset')};
  }
  const api = {create};
  if (typeof module !== 'undefined') module.exports = api;
  else root.AnvilAppCompatibility = api;
})(typeof window !== 'undefined' ? window : globalThis);
