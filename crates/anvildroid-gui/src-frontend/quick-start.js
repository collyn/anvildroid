// First-run guide on the Apps page: get Android, create a device, install apps.
// Pure: main.js passes the observed state and handles the button actions.
const AnvilQuickStart = (() => {
  // state: {setupMissing, imageReady, imageBusy, imagePercent, imageError,
  //         hasDevice, deviceBusy, deviceRunning, catalogError}
  function steps(state) {
    const s = state || {};
    const image = s.imageReady || s.hasDevice ? 'done' : s.imageBusy ? 'busy' : 'todo';
    const device = s.deviceRunning ? 'done' : s.deviceBusy ? 'busy' : s.hasDevice ? 'ready' : image === 'done' ? 'todo' : 'locked';
    const apps = s.deviceRunning ? 'todo' : 'locked';
    return [
      {id:'image', title:'Get Android', status:image,
        detail: s.setupMissing ? 'Waydroid tools are missing on this computer. Open setup to install them first.'
          : image === 'done' ? 'Android is downloaded and verified.'
          : image === 'busy' ? `Downloading Android${Number.isFinite(s.imagePercent) ? ` · ${Math.floor(s.imagePercent)}%` : '…'} You can keep using the app.`
          : s.imageError || s.catalogError || 'Download the official Android 13 image (about 1 GB, no Google apps).',
        action: s.setupMissing ? {id:'setup', label:'Open setup'} : image === 'todo' ? {id:'download', label:'Download Android'} : null},
      {id:'device', title:'Create your Android device', status:device,
        detail: device === 'done' ? 'Your device is running.'
          : device === 'busy' ? 'Setting up and starting Android. The first start takes a few minutes.'
          : device === 'ready' ? 'Your device is created but turned off.'
          : device === 'todo' ? 'Creates “My Android” and starts it. Takes a few minutes the first time.'
          : 'Available after Android is downloaded.',
        action: device === 'todo' ? {id:'create', label:'Create and start'} : device === 'ready' ? {id:'start', label:'Start device'} : null},
      {id:'apps', title:'Install your apps', status:apps,
        detail: apps === 'todo' ? 'Install an APK file. Installed apps appear here and open in their own windows.' : 'Available when your device is running.',
        action: apps === 'todo' ? {id:'install', label:'Install APK'} : null},
    ];
  }

  function render(state, escape) {
    const list = steps(state);
    const label = {done:'Done', busy:'Working', ready:'Ready', todo:'Next', locked:'Later'};
    return `<section class="quick-start" aria-labelledby="quick-start-title"><h2 id="quick-start-title">Get started</h2><p class="hint">Three steps to your first Android app.</p><ol class="quick-start-steps">${list.map((step, index) =>
      `<li class="quick-start-step" data-status="${step.status}"><span class="quick-start-number" aria-hidden="true">${step.status === 'done' ? '✓' : index + 1}</span><div class="quick-start-body"><h3>${escape(step.title)} <span class="quick-start-tag">${label[step.status]}</span></h3><p${step.status === 'todo' && /fail|cannot|error/i.test(step.detail) ? ' role="alert"' : ''}>${escape(step.detail)}</p>${step.id === 'image' && step.status === 'busy' ? `<progress aria-label="Android download" max="100"${Number.isFinite(state?.imagePercent) ? ` value="${Math.max(0, Math.min(100, state.imagePercent)).toFixed(1)}"` : ''}></progress>` : ''}</div>${step.action ? `<button type="button" class="btn${step.action.id === 'setup' ? '' : ' btn-primary'}" data-quick-start="${step.action.id}">${escape(step.action.label)}</button>` : ''}</li>`).join('')}</ol></section>`;
  }

  return {steps, render};
})();
if (typeof module !== 'undefined') module.exports = AnvilQuickStart;
