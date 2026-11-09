// Device settings as a game-style menu: categories on the left, one category
// on the right. It only rearranges what main.js already rendered, so the
// render code, its events and its tests are untouched.
const AnvilSettingsTabs = (() => {
  const SECTION = 'details.runtime-settings-group, details.runtime-setting-row, details.runtime-details, details.runtime-image-details';
  // Most-used first; anything unknown keeps its place after these.
  const ORDER = ['graphics', 'display', 'performance', 'notifications', 'camera', 'device identity',
    'google play', 'arm translation', 'android image', 'storage', 'managed copy', 'diagnostics', 'runtime details', 'session details'];
  const STORE = 'anvildroid.settingsTab';

  const keyOf = title => String(title || '').trim().toLowerCase().replace(/\s+/g, ' ');
  const rank = key => { const index = ORDER.findIndex(name => key.startsWith(name)); return index < 0 ? ORDER.length : index; };

  // Stable sort by the preferred order; ties keep DOM order.
  function order(keys) {
    return keys.map((key, index) => ({key, index})).sort((a, b) => rank(a.key) - rank(b.key) || a.index - b.index).map(item => item.key);
  }
  // The saved choice wins when it is still available, else the first one.
  function pick(available, saved) {
    return available.includes(saved) ? saved : available[0] || null;
  }

  function load() { try { return localStorage.getItem(STORE) || ''; } catch (_) { return ''; } }
  function save(key) { try { localStorage.setItem(STORE, key); } catch (_) {} }

  // Titles come from summaries; "Device identitySpoof…" style summaries keep only the first label.
  function titleOf(section) {
    const summary = section.querySelector(':scope > summary');
    if (!summary) return '';
    const label = summary.querySelector(':scope > span, :scope > strong');
    const text = (label?.firstChild?.textContent || label?.textContent || summary.firstChild?.textContent || summary.textContent || '').trim();
    // "Image: Waydroid VANILLA · 1.40 GiB" is a long summary; the menu needs a short name.
    return /^image\b/i.test(text) ? 'Android image' : text;
  }

  function enhance(root, page) {
    const sections = [...root.querySelectorAll(SECTION)].filter(section => !section.parentElement.closest(SECTION) && !section.closest('[hidden]'));
    if (sections.length < 2) return;
    const advanced = section => Boolean(section.closest('[data-level="advanced"]'));
    const simple = page.classList.contains('settings-simple');
    const entries = sections.map(section => ({section, title: titleOf(section), key: keyOf(titleOf(section)), advanced: advanced(section)}))
      .filter(entry => entry.key);
    const keys = order(entries.map(entry => entry.key));
    const available = keys.filter(key => !simple || !entries.find(entry => entry.key === key).advanced);
    const active = pick(available, root._settingsTab || load());
    root._settingsTab = active;

    root.classList.add('tabbed-settings');
    // Wrappers between the root and a section step out of the layout.
    for (const entry of entries) {
      for (let node = entry.section.parentElement; node && node !== root; node = node.parentElement) node.classList.add('tabs-passthrough');
    }
    // Everything else at that level (actions, image, notes) forms the top bar.
    root.querySelectorAll(':scope > *, .tabs-passthrough > *').forEach(node => {
      const isPart = node.matches(SECTION) || node.classList.contains('tabs-passthrough') || node.classList.contains('settings-tabs-nav');
      node.classList.toggle('tabs-bar-item', !isPart);
    });

    let nav = root.querySelector(':scope > .settings-tabs-nav');
    if (!nav) {
      nav = document.createElement('nav');
      nav.className = 'settings-tabs-nav';
      nav.setAttribute('aria-label', 'Settings categories');
      nav.addEventListener('click', event => {
        const button = event.target.closest('button[data-settings-tab]');
        if (!button) return;
        root._settingsTab = button.dataset.settingsTab;
        save(root._settingsTab);
        enhance(root, page);
        root.querySelector('.settings-tab-active > summary')?.scrollIntoView?.({block: 'nearest'});
      });
      root.prepend(nav);
    }
    // Built with textContent: summaries can include user-chosen device names.
    const signature = JSON.stringify(keys.map(key => [key, entries.find(item => item.key === key).advanced]));
    if (nav._signature !== signature) {
      nav._signature = signature;
      nav.replaceChildren(...keys.map(key => {
        const entry = entries.find(item => item.key === key);
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.settingsTab = key;
        if (entry.advanced) button.dataset.level = 'advanced';
        button.textContent = entry.title;
        return button;
      }));
    }
    nav.querySelectorAll('button').forEach(button => button.setAttribute('aria-current', String(button.dataset.settingsTab === active)));

    for (const entry of entries) {
      const on = entry.key === active;
      entry.section.classList.toggle('settings-tab-active', on);
      entry.section.classList.toggle('settings-tab-hidden', !on);
      if (on && !entry.section.open) entry.section.open = true;
    }
  }

  function attach(page) {
    if (!page || typeof MutationObserver === 'undefined') return;
    const roots = () => [page.querySelector('#runtime-settings-content > .runtime-card'), page.querySelector('#legacy-runtime-detail:not([hidden])')].filter(Boolean);
    let queued = false, applying = false;
    const run = () => {
      queued = false;
      applying = true;
      try { roots().forEach(root => enhance(root, page)); }
      finally { observer.takeRecords(); applying = false; }
    };
    const observer = new MutationObserver(() => {
      if (applying || queued) return;
      queued = true;
      requestAnimationFrame(run);
    });
    observer.observe(page, {subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'hidden']});
    run();
  }

  if (typeof document !== 'undefined' && document.getElementById) {
    const start = () => attach(document.getElementById('page-runtime-settings'));
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
  }
  return {order, pick, keyOf};
})();
if (typeof module !== 'undefined') module.exports = AnvilSettingsTabs;
