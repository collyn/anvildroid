// Applies the saved appearance before first paint. "system" follows the
// desktop's light/dark preference and updates live when it changes.
(function () {
  const KEY = 'anvildroid.theme';
  const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
  function saved() {
    try { const value = localStorage.getItem(KEY); return ['light', 'dark', 'system'].includes(value) ? value : 'system'; }
    catch (_) { return 'system'; }
  }
  function resolve(choice) { return choice === 'system' ? (media && media.matches ? 'light' : 'dark') : choice; }
  function apply(choice) {
    const theme = resolve(choice);
    document.documentElement.dataset.theme = theme;
    // The native GTK titlebar is outside the webview; keep it in step.
    try { window.__TAURI__?.core?.invoke('set_window_theme', {light: theme === 'light'}).catch(() => {}); } catch (_) {}
    document.dispatchEvent?.(new CustomEvent('anvildroid-theme', {detail: {choice, theme}}));
    return theme;
  }
  // Accent presets live in theme.css (:root[data-accent=…]); blue is the default.
  const ACCENT_KEY = 'anvildroid.accent';
  const ACCENTS = ['blue', 'forge', 'toxic', 'crimson', 'synthwave', 'ice'];
  function savedAccent() {
    try { const value = localStorage.getItem(ACCENT_KEY); return ACCENTS.includes(value) ? value : 'blue'; }
    catch (_) { return 'blue'; }
  }
  function applyAccent(accent) {
    document.documentElement.dataset.accent = accent;
    document.querySelectorAll?.('[data-accent-choice]').forEach(button =>
      button.setAttribute('aria-pressed', String(button.dataset.accentChoice === accent)));
  }
  const AnvilTheme = {
    accents: ACCENTS,
    accent: savedAccent,
    setAccent(accent) {
      if (!ACCENTS.includes(accent)) return;
      try { localStorage.setItem(ACCENT_KEY, accent); } catch (_) {}
      applyAccent(accent);
    },
    choice: saved,
    set(choice) {
      if (!['light', 'dark', 'system'].includes(choice)) return;
      try { localStorage.setItem(KEY, choice); } catch (_) {}
      apply(choice);
    },
    apply: () => apply(saved()),
  };
  media?.addEventListener?.('change', () => { if (saved() === 'system') apply('system'); });
  window.AnvilTheme = AnvilTheme;
  applyAccent(savedAccent());
  apply(saved());
  // The picker lives in Settings → Appearance; bind it once the page exists.
  document.addEventListener?.('DOMContentLoaded', () => {
    applyAccent(savedAccent());
    document.addEventListener('click', event => {
      const button = event.target.closest?.('[data-accent-choice]');
      if (button) AnvilTheme.setAccent(button.dataset.accentChoice);
    });
  });
})();
