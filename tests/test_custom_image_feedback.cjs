const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js', 'utf8');
const nodes = new Map();
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {value: '', innerHTML: 'Downloading custom ZIP... 99%', setAttribute() {}});
  return nodes.get(id);
};
const ctx = vm.createContext({
  document: {getElementById: node},
  imageDownloadState: {flavor: 'CUSTOM', status: 'Failed', error: 'Custom ZIP must contain both system.img and vendor.img'},
  imageDownloadBusy: false, imageDownloadActive: () => false,
  selectedImageEntry: () => null, renderCustomUrlProgress() {}, escapeHtml: value => value,
});
vm.runInContext(source.slice(source.indexOf('function renderCustomImageStatus()'), source.indexOf("document.getElementById('controller-image').addEventListener")), ctx);
// An empty image library rejects the CUSTOM selection; errors must still replace download progress.
ctx.renderCreateImageHint();
assert.match(node('custom-image-status').innerHTML, /Image import failed:.*vendor.img/);
assert.equal(node('custom-image-status').className, 'custom-import-feedback error');
node('controller-image').value = 'installed';
ctx.reinstallTargetId = null;
ctx.renderCreateImageHint();
assert.match(node('custom-image-status').innerHTML, /Image import failed/);
console.log('PASS: custom import errors remain visible with empty or installed image selection');
