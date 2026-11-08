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
  const AnvilTheme = {
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
  apply(saved());
})();
