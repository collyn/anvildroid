/* Explicit runtime directory grants; original MIT implementation. */
(function(root) {
  function create({request, document, toast = () => {}, chooseDirectory}) {
    const dialog = document.createElement('dialog');
    dialog.id = 'runtime-shares-dialog';
    const title = document.createElement('h2'); title.textContent = 'Shared folders';
    title.id = 'runtime-shares-title'; title.tabIndex = -1;
    dialog.setAttribute('aria-labelledby', title.id);
    const note = document.createElement('p');
    note.textContent = 'Stop this runtime before changing shares. Choose a non-sensitive folder inside your home. Android receives access to everything in that folder. Shares appear under /sdcard/AnvilDroid after the next start.';
    const list = document.createElement('div');
    const form = document.createElement('form');
    const name = document.createElement('input'); name.placeholder = 'Share name'; name.required = true; name.pattern = '[A-Za-z0-9][A-Za-z0-9_-]{0,47}'; name.setAttribute('aria-label', 'Share name');
    const source = document.createElement('input'); source.readOnly = true; source.placeholder = 'Choose a folder'; source.setAttribute('aria-label', 'Selected source folder');
    const pick = document.createElement('button'); pick.type = 'button'; pick.textContent = 'Choose folder';
    const label = document.createElement('label');
    const ro = document.createElement('input'); ro.type = 'checkbox'; ro.checked = true;
    label.append(ro, document.createTextNode(' Read only'));
    const save = document.createElement('button'); save.type = 'submit'; save.textContent = 'Save share';
    const notice = document.createElement('p'); notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite');
    const close = document.createElement('button'); close.type = 'button'; close.textContent = 'Close';
    const refresh = document.createElement('button'); refresh.type = 'button'; refresh.textContent = 'Refresh';
    form.append(name, source, pick, label, save); dialog.append(title, note, list, form, notice, refresh, close); document.body.append(dialog);
    let identity = null, epoch = 0, view = 0, busy = false, editable = false, removeButtons = [];
    const validName = /^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$/;
    for (const button of [pick,save,close,refresh]) button.className = 'btn';
    function lock(value) {
      busy = value; refresh.disabled = value;
      for (const item of [save,pick,name,source,ro,...removeButtons]) item.disabled = value || !editable;
    }
    function invalidate() {
      editable = false; view++; list.replaceChildren(); removeButtons = [];
    }
    function validate(value, runtime) {
      const bad = () => { throw new Error('Invalid shared-folder response or another runtime; refresh before retrying.'); };
      if (!value || value.runtime_id !== runtime.id || typeof value.editable !== 'boolean' ||
          !Array.isArray(value.shares) || value.shares.length > 16 ||
          (value.unsupported_reason !== undefined && typeof value.unsupported_reason !== 'string')) bad();
      const names = new Set();
      for (const share of value.shares) {
        if (!share || typeof share.name !== 'string' || !validName.test(share.name) || names.has(share.name) ||
            typeof share.source !== 'string' || !share.source.startsWith('/') || share.source.length > 4096 || share.source.includes('\0') ||
            share.target !== '/sdcard/AnvilDroid/' + share.name ||
            typeof share.read_only !== 'boolean' || typeof share.mounted !== 'boolean' ||
            (share.configured !== undefined && share.configured !== true) ||
            (share.error !== undefined && share.error !== null && typeof share.error !== 'string')) bad();
        names.add(share.name);
      }
      return value;
    }
    function render(value) {
      invalidate(); editable = value.editable;
      const rendered = view, current = epoch, runtimeId = identity.id;
      for (const share of value.shares) {
        const row = document.createElement('p');
        const text = document.createElement('span');
        text.textContent = `${share.name}: ${share.source} → ${share.target} (${share.read_only ? 'read only' : 'read/write'}; ${share.mounted ? 'mounted' : 'configured, not mounted'})${share.error ? ' — ' + share.error : ''} `;
        const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove';
        remove.className = 'btn'; remove.disabled = busy || !editable;
        remove.addEventListener('click', () => {
          if (current === epoch && rendered === view && identity?.id === runtimeId)
            return mutate('shares_remove', {name:share.name});
        });
        removeButtons.push(remove); row.append(text,remove); list.append(row);
      }
      if (!value.shares.length) list.textContent = 'No folders shared.';
      notice.textContent = value.unsupported_reason || (editable ?
        'Changes require a stopped runtime and take effect on its next start.' :
        'Changes are unavailable. Stop the runtime completely, then refresh.');
    }
    async function load(operation = 'shares_get', payload = {}) {
      if (!identity || busy || (operation !== 'shares_get' && !editable)) return;
      const runtime = identity, current = epoch;
      // A prior snapshot cannot authorize more mutations during or after failure.
      lock(true); invalidate(); notice.textContent = operation === 'shares_get' ? 'Loading shared folders…' : 'Saving shared folders…';
      try {
        const response = validate(await request({op:operation, ...payload, id:runtime.id}), runtime);
        if (current === epoch) { render(response); dialog.scrollTop = 0; }
      } catch (error) {
        if (current === epoch) { invalidate(); notice.textContent = `${String(error.message || error)} Refresh before retrying.`; }
      } finally { if (current === epoch) lock(false); }
    }
    async function mutate(operation, payload) {
      if (!identity || busy || !editable) return;
      return load(operation,payload);
    }
    pick.addEventListener('click', async () => {
      if (!identity || busy || !editable) return;
      const current = epoch;
      lock(true);
      try {
        const path = await chooseDirectory();
        if (current === epoch && typeof path === 'string' && path.startsWith('/') && path.length <= 4096 && !path.includes('\0')) source.value = path;
      } catch (error) {
        if (current === epoch) { notice.textContent = String(error.message || error); toast(String(error.message || error), 'error'); }
      } finally { if (current === epoch) lock(false); }
    });
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!identity || busy || !editable) return;
      if (!source.value) { notice.textContent = 'Choose a source folder first.'; return; }
      if (!validName.test(name.value)) { notice.textContent = 'Use a short alphanumeric share name.'; return; }
      return mutate('shares_set', {name:name.value,source:source.value,read_only:ro.checked});
    });
    function closed() { epoch++; identity = null; invalidate(); lock(false); }
    close.addEventListener('click', () => { closed(); dialog.close(); });
    refresh.addEventListener('click', () => load());
    dialog.addEventListener('close', () => { if (!dialog.open) closed(); });
    lock(false);
    return {open(runtime) {
      epoch++; identity = {id:runtime.id}; source.value = ''; name.value = ''; ro.checked = true;
      busy = false; invalidate(); lock(false);
      if (!dialog.open) dialog.showModal();
      title.focus();
      return load();
    }};
  }
  root.AnvilRuntimeShares = {create};
  if (typeof module !== 'undefined') module.exports = {create};
})(typeof window !== 'undefined' ? window : globalThis);
