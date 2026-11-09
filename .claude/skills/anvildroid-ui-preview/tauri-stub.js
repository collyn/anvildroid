// Fake window.__TAURI__ for browser previews. Loaded before theme.js.
// Answers come from window.PREVIEW_FIXTURES (fixtures.js). Missing fixtures
// reject, so the UI shows its real error state and the console names the gap.
(() => {
  const params = new URLSearchParams(location.hash.slice(1));
  const theme = params.get('theme');
  if (theme) try { localStorage.setItem('anvildroid.theme', theme); } catch (_) {}
  const scenario = params.get('scenario') || 'default';
  const F = window.PREVIEW_FIXTURES;
  const data = { ...F.default, ...(F[scenario] || {}) };
  for (const key of Object.keys(data)) if (data[key] === undefined) delete data[key];
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  const missing = new Set();

  function answer(key, args) {
    if (!(key in data)) {
      if (!missing.has(key)) { missing.add(key); console.warn(`[preview] no fixture for ${key}`, args); }
      return Promise.reject(new Error(`Preview has no fixture for ${key}`));
    }
    const value = data[key];
    return Promise.resolve(clone(typeof value === 'function' ? value(args) : value));
  }

  async function invoke(command, args = {}) {
    if (command === 'runtime_controller') {
      const request = args.request || {};
      if (request.op === 'watch') {
        await sleep(Math.min(25, request.timeout || 25) * 1000);
        return { token: '0'.repeat(32), changed: false };
      }
      const byId = request.id && `controller:${request.op}:${request.id}`;
      return answer(byId && byId in data ? byId : `controller:${request.op}`, request);
    }
    return answer(command, args);
  }

  window.__TAURI__ = {
    core: { invoke },
    dialog: { open: async () => null, save: async () => null },
    clipboard: { writeText: async () => {} },
  };
  window.__PREVIEW__ = { scenario, missing };
})();
