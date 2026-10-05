/* Pure geometry shared by the editor/preview. Same normalized 0..65535
 * endpoint convention as native/input/touch-session.c. No capture/injection. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AnvilKeymapGeometry = api;
})(globalThis, function () {
  'use strict';
  const MAX = 65535;
  function validRect(r) {
    return r && [r.x, r.y, r.width, r.height].every(Number.isFinite)
      && r.width >= 1 && r.height >= 1 && r.width <= 32768 && r.height <= 32768;
  }
  function validPoint(p) {
    return p && Number.isInteger(p.nx) && Number.isInteger(p.ny)
      && p.nx >= 0 && p.nx <= MAX && p.ny >= 0 && p.ny <= MAX;
  }
  function gameRect(viewport, reference, mode) {
    if (!validRect(viewport)) throw new Error('Invalid content viewport');
    if (mode === 'stretch') return { ...viewport };
    if (mode !== 'contain' || !reference || !Number.isFinite(reference.width)
      || !Number.isFinite(reference.height) || reference.width < 1 || reference.height < 1)
      throw new Error('A verified reference aspect ratio is required');
    const scale = Math.min(viewport.width / reference.width, viewport.height / reference.height);
    const width = Math.min(viewport.width, reference.width * scale);
    const height = Math.min(viewport.height, reference.height * scale);
    if (width < 1 || height < 1) throw new Error('Game content is too small');
    return { x: viewport.x + (viewport.width - width) / 2,
      y: viewport.y + (viewport.height - height) / 2, width, height };
  }
  function project(point, rect) {
    if (!validPoint(point) || !validRect(rect)) throw new Error('Invalid mapping geometry');
    // Floating CSS coordinates; use androidPoint for integer Android pixels.
    return { x: rect.x + point.nx * (rect.width - 1) / MAX,
      y: rect.y + point.ny * (rect.height - 1) / MAX };
  }
  function androidPoint(point, rect) {
    if (!validRect(rect) || ![rect.x, rect.y, rect.width, rect.height].every(Number.isInteger)
      || rect.x < 0 || rect.y < 0 || rect.x + rect.width > 8192 || rect.y + rect.height > 8192)
      throw new Error('Android content bounds must be verified integer pixels');
    const p = project(point, rect);
    return { x: Math.round(p.x), y: Math.round(p.y) };
  }
  function normalize(point, rect) {
    if (!validRect(rect) || !point || !Number.isFinite(point.x) || !Number.isFinite(point.y))
      throw new Error('Invalid editor point');
    // Reject letterbox clicks, rather than silently binding a point on a bar.
    if (point.x < rect.x || point.y < rect.y || point.x > rect.x + rect.width - 1
      || point.y > rect.y + rect.height - 1) return null;
    return { nx: rect.width === 1 ? 0 : Math.round((point.x - rect.x) / (rect.width - 1) * MAX),
      ny: rect.height === 1 ? 0 : Math.round((point.y - rect.y) / (rect.height - 1) * MAX) };
  }
  return Object.freeze({ MAX, gameRect, project, androidPoint, normalize });
});
