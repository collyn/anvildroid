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
  // Themes change the look only (skin.css is scoped to data-skin="hud");
  // navigation and layout are the same in every theme. HUD is the default.
  const SKIN_KEY = 'anvildroid.skin';
  const SKINS = ['classic', 'hud'];
  function savedSkin() {
    try { const value = localStorage.getItem(SKIN_KEY); return SKINS.includes(value) ? value : 'hud'; }
    catch (_) { return 'hud'; }
  }
  function applySkin(skin) {
    document.documentElement.dataset.skin = skin;
    document.querySelectorAll?.('[data-skin-choice]').forEach(button =>
      button.setAttribute('aria-pressed', String(button.dataset.skinChoice === skin)));
  }
  const AnvilTheme = {
    skins: SKINS,
    skin: savedSkin,
    setSkin(skin) {
      if (!SKINS.includes(skin)) return;
      try { localStorage.setItem(SKIN_KEY, skin); } catch (_) {}
      applySkin(skin);
    },
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
  applySkin(savedSkin());
  applyAccent(savedAccent());
  apply(saved());
  // The pickers live in Settings → Appearance; bind them once the page exists.
  document.addEventListener?.('DOMContentLoaded', () => {
    applySkin(savedSkin());
    applyAccent(savedAccent());
    document.addEventListener('click', event => {
      const accent = event.target.closest?.('[data-accent-choice]');
      if (accent) AnvilTheme.setAccent(accent.dataset.accentChoice);
      const skin = event.target.closest?.('[data-skin-choice]');
      if (skin) AnvilTheme.setSkin(skin.dataset.skinChoice);
    });
  });
})();
