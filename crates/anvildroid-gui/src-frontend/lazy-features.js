// Load optional features once, in dependency order. A failed download can retry.
(function(root) {
  function create(load = src => new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = resolve;
    script.onerror = () => {
      script.remove();
      reject(new Error(`Cannot load ${src}. Please try again.`));
    };
    document.head.appendChild(script);
  })) {
    const pending = new Map();
    const features = {controls:['keymap-drafts.js', 'keymap-library.js'], runtime:['runtime-settings.js']};
    function script(src) {
      if (!pending.has(src)) {
        const work = Promise.resolve().then(() => load(src)).catch(error => {
          pending.delete(src);
          throw error;
        });
        pending.set(src, work);
      }
      return pending.get(src);
    }
    async function ensure(feature) {
      if (!features[feature]) throw new Error(`Unknown feature: ${feature}`);
      for (const src of features[feature]) await script(src);
    }
    return {ensure};
  }
  root.AnvilLazyFeatures = {create};
  if (typeof module !== 'undefined') module.exports = {create};
})(typeof window === 'undefined' ? globalThis : window);
