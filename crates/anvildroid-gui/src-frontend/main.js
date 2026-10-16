// AnvilDroid GUI — P5.2 frontend
const { invoke: tauriInvoke } = window.__TAURI__.core;
tauriInvoke('get_app_version').then(version => {
  document.getElementById('app-version').textContent = `v${version}`;
  document.title = `AnvilDroid ${version}`;
}).catch(() => {
  document.getElementById('app-version').textContent = 'Version unavailable';
});
// Keep each request pinned to its target. Runtime selection is enabled only once
// an isolated backend exists; catalog inspection itself is a global query.
const activeRuntimeId = 'default';
const openSettingsOnLaunch = window.location?.search === '?view=settings';
const openControlsOnLaunch = window.location?.search === '?view=controls';
let libraryPage = !openSettingsOnLaunch && !openControlsOnLaunch;
let appStatesTimer = null, appStatesLoading = false;
function invoke(command, args = {}) {
  return tauriInvoke(command, command === 'get_runtime_catalog'
    ? args : { ...args, runtimeId: activeRuntimeId });
}

// --- Navigation ---
const navItems = document.querySelectorAll('.nav-item:not(.disabled)');
const pages = document.querySelectorAll('.page');
const pageLoads = AnvilPageLoads.create();
const lazyFeatures = AnvilLazyFeatures.create();
let adbPage = null;
let currentPage = openSettingsOnLaunch || openControlsOnLaunch ? null : 'apps';

function navigatePage(target, refresh = true) {
  if (!['apps', 'runtime', 'runtime-settings', 'images', 'settings', 'controls', 'adb'].includes(target)) return;
  if (currentPage === target && (target !== 'controls' || window.AnvilKeymapLibrary) && (target !== 'adb' || adbPage)) return;
  if (target !== 'adb') adbPage?.stop();
  currentPage = target;
  libraryPage = target === 'apps';
  clearTimeout(appStatesTimer);
  navItems.forEach(n => {
    const selected = n.dataset.page === (target === 'runtime-settings' ? 'runtime' : target);
    n.classList.toggle('active', selected);
    if (selected) n.setAttribute('aria-current', 'page');
    else n.removeAttribute('aria-current');
  });
  pages.forEach(p => p.classList.remove('active'));
  document.getElementById(`page-${target}`).classList.add('active');
  if (refresh) pageLoads.schedule(target, () => currentPage === target, async () => {
    if (target === 'apps') {
      if (!appLibraryStarted) await initializeAppLibrary();
      else if (!refreshingApps) await refreshAppStates();
    }
    if (target === 'runtime' || target === 'runtime-settings') await lazyFeatures.ensure('runtime');
    if (target === 'runtime') await refreshRuntimePage({light:true});
    if (target === 'images') await refreshImagesPage({online:false});
    if (target === 'settings') await refreshSettings();
    if (target === 'adb') {
      try { await lazyFeatures.ensure('adb'); }
      catch (error) { document.getElementById('adb-status').textContent = String(error); showAppToast(String(error), 'error'); throw error; }
      if (currentPage !== target) return;
      if (!adbPage) adbPage = window.AnvilAdb.create({request:controllerRequest, invoke:tauriInvoke,
        escape:escapeHtml, toast:showAppToast, copy:copyTextToClipboard,
        active:() => currentPage === 'adb', document, storage:window.localStorage});
      await adbPage.refresh();
    }
    if (target === 'controls') {
      try {
        await lazyFeatures.ensure('controls');
        if (currentPage === target) await window.AnvilKeymapLibrary.refresh();
      } catch (error) {
        showAppToast(String(error), 'error');
        throw error;
      }
    }
  });
  if (target === 'apps' && appLibraryStarted) appStatesTimer = setTimeout(refreshAppStates, 5000);
  scheduleRuntimeUsage();
}
navItems.forEach(item => {
  item.setAttribute('role', 'button');
  item.setAttribute('tabindex', '0');
  if (item.classList.contains('active')) item.setAttribute('aria-current', 'page');
  item.addEventListener('click', () => navigatePage(item.dataset.page));
  item.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      navigatePage(item.dataset.page);
    }
  });
});
document.getElementById('btn-refresh-runtimes').addEventListener('click', refreshRuntimePage);
document.getElementById('btn-refresh-images').addEventListener('click', refreshImagesPage);
document.getElementById('btn-refresh-adb').addEventListener('click', () => { if (!adbPage) navigatePage('adb'); });
document.getElementById('btn-start-controller').addEventListener('click', startRuntimeController);
async function startRuntimeController() {
  const button = document.getElementById('btn-start-controller');
  const status = document.getElementById('controller-start-message');
  if (controllerLoading || button.disabled) return;
  button.disabled = true;
  status.textContent = 'Requesting administrator access…';
  try {
    // Direct invoke: the invoke() wrapper pins runtimeId, which Tauri would
    // reject as an unexpected argument for this command.
    const result = await tauriInvoke('start_runtime_controller');
    status.textContent = '';
    showAppToast(result.message, result.ok ? 'success' : 'error');
    if (result.ok) await refreshController();
  } catch (error) {
    status.textContent = '';
    showAppToast(`Cannot start runtime controller: ${error}`, 'error');
  } finally {
    button.disabled = false;
  }
}
document.getElementById('btn-restart-controller').addEventListener('click', restartRuntimeController);
function controllerRestartBlocked() {
  return runtimeOperations.size > 0 || controllerLoading || controllerMutation || runtimeBatchBusy || runtimeTransition ||
    pendingLaunch || pendingAppAction || probeInstallBusy || imageDownloadBusy ||
    imageDownloadActive() || controllerHasActiveWork();
}
async function restartRuntimeController() {
  const button = document.getElementById('btn-restart-controller');
  if (button.disabled || controllerRestartBlocked()) return;
  // Reserve the operation before opening the dialog to prevent double clicks.
  button.disabled = true;
  let locked = false;
  try {
    const confirmed = await AppDialog.confirm(
      'Restart the controller service? Running managed runtimes and their windows may stop. You can start them again after reconnecting.',
      {title:'Restart controller', kind:'warning', okLabel:'Restart', cancelLabel:'Cancel'});
    if (!confirmed || controllerRestartBlocked()) return;
    controllerMutation = true;
    locked = true;
    clearTimeout(controllerTimer);
    button.textContent = 'Restarting…';
    renderController();
    const connection = document.getElementById('controller-connection');
    connection.textContent = 'Restarting controller…';
    connection.className = 'status-badge';
    document.getElementById('controller-message').textContent = 'Requesting administrator access…';
    const result = await tauriInvoke('restart_runtime_controller');
    if (!result?.ok) throw new Error(result?.message || 'Controller restart failed');
    // systemctl waits for the job, but Type=simple may not have bound its socket yet.
    let reachable = false;
    let lastError;
    for (let attempt = 0; attempt < 30; attempt++) {
      try {
        const records = await controllerRequest({op:'list'});
        if (!Array.isArray(records)) throw new Error('Invalid runtime list');
        reachable = true;
        break;
      } catch (error) {
        lastError = error;
        if (attempt < 29) await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }
    if (!reachable) throw new Error(`Service restart finished, but reconnect failed: ${lastError}`);
    showAppToast('Controller restarted.', 'success');
  } catch (error) {
    showAppToast(`Cannot restart controller: ${error}`, 'error');
  } finally {
    if (locked) {
      controllerMutation = false;
      document.getElementById('controller-message').textContent = '';
      await refreshController();
    }
    button.textContent = 'Restart controller';
    button.disabled = controllerRestartBlocked();
  }
}
async function refreshRuntimePage({light = false} = {}) {
  const button = document.getElementById('btn-refresh-runtimes');
  button.disabled = true;
  try {
    await Promise.all([refreshController({light}), refreshImageDownload(), ...(light ? [] : [refreshStatus()])]);
    pageLoads.mark('runtime');
  }
  finally {
    button.disabled = false;
    resumeWaydroidInitCheck();
  }
}

async function refreshImagesPage({online = true} = {}) {
  const button = document.getElementById('btn-refresh-images');
  button.disabled = true;
  try {
    await Promise.all([refreshImageDownload(), checkOfficialImages(online)]);
    pageLoads.mark('images');
  } finally { button.disabled = false; }
}

// --- Runtime status ---
const stateEl = document.getElementById('runtime-state');
const sessionEl = document.getElementById('session-service');
const containerEl = document.getElementById('container-state');
const gpuEl = document.getElementById('gpu-mode');
const healthMsgRow = document.getElementById('health-message-row');
const healthMsgEl = document.getElementById('health-message');
const btnRefresh = document.getElementById('btn-refresh-runtimes');
const btnStart = document.getElementById('btn-start');
const btnStop = document.getElementById('btn-stop');
const btnRecover = document.getElementById('btn-recover');

function stateLabel(state) {
  if (!state) return '—';
  const type_ = typeof state === 'string' ? state : state.type;
  switch (type_) {
    case 'Running': return 'Running';
    case 'Frozen': return 'Frozen';
    case 'Starting': return 'Starting';
    case 'Stopped': return 'Stopped';
    case 'NotInstalled': return 'Not installed';
    case 'SessionLost': return 'Session lost';
    case 'Error': return 'Error';
    default: return type_;
  }
}

function runtimeBadgeKind(state) {
  const type = typeof state === 'string' ? state : state?.type;
  return ['Error', 'SessionLost', 'NotInstalled', 'Unavailable'].includes(type) ? 'error'
    : ['Running', 'Prepared'].includes(type) ? 'success' : 'info';
}

function updateDefaultRuntimeBadge(state, error = '') {
  const pending = controllerPending?.id === 'default' ? controllerPending : null;
  const restarting = runtimeOperations.get('default') === 'restart' || runtimeBatchRestarting.has('default') ||
    (pending && pending.op !== 'reinstall' && ['stopping', 'starting'].includes(pending.phase));
  const label = restarting ? 'Restarting' : pending?.op === 'reinstall' && pending.phase === 'applying' ? 'Reinstalling'
    : defaultRuntimeOperation === 'stop_runtime' || pending?.phase === 'stopping' ? 'Stopping'
    : defaultRuntimeOperation ? 'Starting' : stateLabel(state);
  stateEl.textContent = label;
  stateEl.className = `runtime-status ${restarting || pending || defaultRuntimeOperation ? 'info' : runtimeBadgeKind(state)}`;
  stateEl.title = error;
  stateEl.setAttribute('aria-busy', String(Boolean(restarting || pending || defaultRuntimeOperation)));
}

function updateRuntimeButtons(state) {
  const type_ = typeof state === 'string' ? state : (state ? state.type : null);
  btnStart.style.display = (type_ === 'Stopped' || type_ === 'NotInstalled') ? '' : 'none';
  btnStop.style.display = (type_ === 'Running' || type_ === 'Frozen' || type_ === 'Starting') ? '' : 'none';
  const detail = typeof state === 'object' ? String(state?.detail || '') : '';
  const staleSession = type_ === 'Error' && detail.includes('session=RUNNING, container=STOPPED');
  btnRecover.style.display = (type_ === 'SessionLost' || staleSession) ? '' : 'none';
  btnRecover.title = staleSession ? 'Recover stale session' : 'Recover';
  btnRecover.setAttribute('aria-label', btnRecover.title + ' Default runtime');
}

async function refreshStatus() {
  btnRefresh.disabled = true;
  btnRefresh.textContent = 'Loading…';
  try {
    const report = await invoke('get_health_report');
    libraryDefaultState = typeof report?.status?.state === 'string' ? report.status.state : report?.status?.state?.type || 'Unknown';
    updateDefaultRuntimeBadge(report.status.state);

    sessionEl.textContent = report.status.session_service ? 'Present' : 'Missing';
    sessionEl.className = `status-value ${report.status.session_service ? 'running' : 'error'}`;

    containerEl.textContent = report.status.container_state || '—';
    containerEl.className = `status-value ${report.status.container_state === 'RUNNING' ? 'running' : report.status.container_state === 'FROZEN' ? 'frozen' : 'unknown'}`;

    gpuEl.textContent = report.status.gpu_mode
      ? `${report.status.gpu_mode} (configured)`
      : '—';

    if (report.message) {
      healthMsgRow.style.display = 'flex';
      healthMsgEl.textContent = report.message;
    } else {
      healthMsgRow.style.display = 'none';
    }
    updateRuntimeButtons(report.status.state);
    renderApps(allApps);
  } catch (err) {
    updateDefaultRuntimeBadge('Unavailable', String(err));
    updateRuntimeButtons(null);
    libraryDefaultState = 'Unknown';
    renderApps(allApps);
  } finally {
    btnRefresh.disabled = false;
    btnRefresh.textContent = 'Refresh';
  }
}


// --- Runtime actions ---
async function runtimeAction(action, btn) {
  if (controllerRecords.some(record => record.import_status === 'pending' && record.state === 'Provisioning')) {
    showAppToast('Keep Default runtime stopped until its managed copy is verified.', 'info');
    return;
  }
  if (runtimeOperationBusy('default')) {
    showAppToast('Wait for the current operation to complete.', 'info');
    return;
  }
  btn.disabled = true;
  const iconButton = btn.classList.contains('runtime-action-icon');
  const label = btn.textContent;
  btn.setAttribute('aria-busy', 'true');
  if (iconButton) btn.classList.add('is-working');
  else btn.textContent = 'Working…';
  defaultRuntimeOperation = action;
  updateDefaultRuntimeBadge(existingRuntime?.state || libraryDefaultState);
  try {
    const result = await trackRuntimeTransition(() => invoke(action));
    if (result.success && action !== 'stop_runtime') refreshApps();
    if (!pendingLaunch && !pendingAppAction) showAppToast(result.success ? result.message : `${result.message}${result.recovery_hint ? ' ' + result.recovery_hint : ''}`,
      result.success ? 'success' : 'error');
    await refreshExistingRuntime();
    renderController();
    setTimeout(refreshStatus, 2000);
  } catch (err) {
    showAppToast(`Error: ${err}`, 'error');
  } finally {
    defaultRuntimeOperation = null;
    updateDefaultRuntimeBadge(existingRuntime?.state || libraryDefaultState);
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    if (iconButton) btn.classList.remove('is-working');
    else btn.textContent = label;
    renderController();
  }
}

btnStart.addEventListener('click', () => runtimeAction('start_runtime', btnStart));
btnStop.addEventListener('click', () => runtimeAction('stop_runtime', btnStop));
btnRecover.addEventListener('click', () => runtimeAction('recover_runtime', btnRecover));

// --- Toast notifications ---
let appToastTimer;
function dismissAppToast() {
  clearTimeout(appToastTimer);
  document.getElementById('app-toast').hidden = true;
}
function showAppToast(message, type = 'info') {
  message = String(message ?? '');
  if (!message) return;
  clearTimeout(appToastTimer);
  const toast = document.getElementById('app-toast');
  document.getElementById('app-toast-message').textContent = message;
  toast.className = `app-toast ${type}`;
  toast.hidden = false;
  const minimumDuration = type === 'error' ? 10000 : type === 'success' ? 3500 : 6000;
  appToastTimer = setTimeout(dismissAppToast, Math.min(12000, Math.max(minimumDuration, message.length * 50)));
}
document.getElementById('app-toast-close').addEventListener('click', dismissAppToast);

// --- Apps ---
const appsGrid = document.getElementById('apps-grid');
const btnRefreshApps = document.getElementById('btn-refresh-apps');
const btnInstallApk = document.getElementById('btn-install-apk');
const sortSelect = document.getElementById('sort-apps');
const librarySearch = document.getElementById('library-search');
const tagFilter = document.getElementById('filter-tag');
let currentCategory = 'all';

let allApps = [];
let currentSort = 'name-asc';
let managedLibrary = [];
let managedLibraryLoading = false;
let managedLibraryError = '';
let libraryDefaultState = 'Unknown';
const libraryStarts = new Map();
const libraryStartErrors = new Map();
const managedSnapshots = new Map();
const managedInventoryChecked = new Map();
const managedPreferences = new Map();
const managedPreferenceErrors = new Map();
const managedPreferenceVersions = new Map();
const runtimeFilter = document.getElementById('filter-runtime');
// null selects all runtimes, including runtimes discovered by later refreshes.
// An empty set intentionally selects none; removing a runtime must not broaden it.
let libraryRuntimeSelection = null;
function libraryRuntimeSelected(id) {
  return libraryRuntimeSelection === null || libraryRuntimeSelection.has(id);
}
function setLibraryRuntimeFilter(ids) {
  libraryRuntimeSelection = ids === null ? null : new Set(ids);
  renderRuntimeFilter();
  removeContextMenu();
  renderManagedLibraryStatus();
  renderApps(allApps);
}
function renderRuntimeFilter() {
  const records = [{id:'default', name:'Default runtime'}, ...managedLibrary];
  const label = document.getElementById('runtime-filter-label');
  const selected = records.filter(record => libraryRuntimeSelected(record.id));
  label.textContent = libraryRuntimeSelection === null ? 'All runtimes'
    : selected.length === 1 ? selected[0].name : `${selected.length} runtimes`;
  label.title = libraryRuntimeSelection === null ? 'All runtimes' : selected.map(record => record.name).join(', ') || 'No runtimes selected';
  const panel = document.getElementById('runtime-filter-options');
  const structure = JSON.stringify(records.map(record => [record.id, record.name]));
  if (panel._runtimeStructure !== structure) {
    panel.innerHTML = `<label class="runtime-filter-option runtime-filter-all"><input type="checkbox" data-library-runtime="all">All runtimes</label>` + records.map(record => `<label class="runtime-filter-option"><input type="checkbox" data-library-runtime="${escapeHtml(record.id)}"><span title="${escapeHtml(record.name)}">${escapeHtml(record.name)}</span></label>`).join('');
    panel._runtimeStructure = structure;
  }
  panel.querySelectorAll('input[data-library-runtime]').forEach(input => {
    const all = input.dataset.libraryRuntime === 'all';
    input.checked = all ? libraryRuntimeSelection === null : libraryRuntimeSelected(input.dataset.libraryRuntime);
    input.indeterminate = all && libraryRuntimeSelection !== null && selected.length > 0;
  });
}
runtimeFilter.addEventListener('change', event => {
  const input = event.target;
  const id = input.dataset.libraryRuntime;
  if (!id) return;
  if (id === 'all') setLibraryRuntimeFilter(input.checked ? null : []);
  else {
    const ids = libraryRuntimeSelection === null ? new Set(['default', ...managedLibrary.map(record => record.id)]) : new Set(libraryRuntimeSelection);
    if (input.checked) ids.add(id); else ids.delete(id);
    setLibraryRuntimeFilter([...ids]);
  }
});
document.addEventListener('click', event => {
  if (runtimeFilter.open && !runtimeFilter.contains(event.target)) runtimeFilter.open = false;
});
runtimeFilter.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    runtimeFilter.open = false;
    document.getElementById('runtime-filter-label').focus();
  }
});
// The library view is a presentation preference only. Keep it local to this
// window so a corrupt/blocked storage implementation can never prevent the
// app catalogue from rendering.
const LIBRARY_VIEW_KEY = 'anvildroid.library-view';
const libraryViewButtons = [...document.querySelectorAll('.view-button[data-view]')];
let libraryView = 'grid';
function restoreLibraryPreferences() {
  try {
    const savedView = localStorage.getItem(LIBRARY_VIEW_KEY);
    if (savedView === 'grid' || savedView === 'list') applyLibraryView(savedView, false);
  } catch (_) { /* private mode or disabled storage: use the default */ }
  loadAppTags();
  renderTagFilters();
}

function applyLibraryView(view, persist = true) {
  if (view !== 'grid' && view !== 'list') return;
  libraryView = view;
  if (appsGrid) {
    if (appsGrid.dataset) appsGrid.dataset.view = view;
    if (appsGrid.classList?.toggle) appsGrid.classList.toggle('list-view', view === 'list');
  }
  libraryViewButtons.forEach(button => {
    const selected = button.dataset.view === view;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  if (persist) {
    try { localStorage.setItem(LIBRARY_VIEW_KEY, view); } catch (_) { /* ignore */ }
  }
}

libraryViewButtons.forEach(button => button.addEventListener('click', () => {
  applyLibraryView(button.dataset.view);
}));
applyLibraryView(libraryView, false);

function appPreferences(app) { return app.runtime_id && app.runtime_id !== 'default' ? managedPreferences.get(app.runtime_id) : library; }
function isFavorite(app) { return appPreferences(app)?.favorites?.includes(app.package) || false; }
function lastOpened(app) { return appPreferences(app)?.recent?.[app.package] || 0; }

let library = { favorites: [], recent: {}, sort: 'name-asc' };
let libraryReady = false;
const TAGS_KEY = 'anvildroid.app-tags.v1';
let appTags = { tags: [], assignments: {} };
function loadAppTags() { try { const raw = JSON.parse(localStorage.getItem(TAGS_KEY) || '{}'); if (Array.isArray(raw.tags)) appTags.tags = raw.tags.filter(t => typeof t === 'string').slice(0, 32); if (raw.assignments && typeof raw.assignments === 'object') appTags.assignments = raw.assignments; } catch (_) {} }
function saveAppTags() { try { localStorage.setItem(TAGS_KEY, JSON.stringify(appTags)); } catch (_) {} }
function tagsForApp(pkg) { return Array.isArray(appTags.assignments[pkg]) ? appTags.assignments[pkg] : []; }
function renderTagFilters() {
  if (!tagFilter) return;
  const groups = [['all','All tags'], ['system','System'], ['user','User'], ['games','Games'], ['productivity','Productivity'], ['media','Media']];
  const choices = [...groups, ...appTags.tags.map(tag => [`tag:${tag}`, tag])];
  if (!choices.some(([value]) => value === currentCategory)) currentCategory = 'all';
  tagFilter.innerHTML = groups.map(([value,label]) => `<option value="${value}">${label}</option>`).join('') +
    (appTags.tags.length ? `<optgroup label="My tags">${appTags.tags.map(tag => `<option value="${escapeHtml('tag:' + tag)}">${escapeHtml(tag)}</option>`).join('')}</optgroup>` : '');
  tagFilter.value = currentCategory;
}

let preferenceBusy = false;
let refreshingApps = false;
let detailPackage = null;
let detailGeneration = 0;
let pendingLaunch = null;
let pendingAppAction = false;
let pendingWaydroidRuntime = null;
let runtimeTransition = null;
let defaultRuntimeOperation = null;

function trackRuntimeTransition(work) {
  const task = Promise.resolve().then(work);
  runtimeTransition = task;
  const clear = () => { if (runtimeTransition === task) runtimeTransition = null; };
  task.then(clear, clear);
  return task;
}

var probeInstallBusy = false;
async function openWaydroid(runtimeId) {
  if (controllerMutation || runtimeBatchBusy) { showAppToast('Wait for the current runtime operation to finish.', 'info'); return; }
  if(probeInstallBusy){showAppToast('Installation is pending. Review the Android verification window; Waydroid can be opened when this operation finishes.','info');return;}
  if (pendingWaydroidRuntime !== null) {
    showAppToast('Waydroid is already opening. Please wait for the current request to finish.', 'info');
    return;
  }
  pendingWaydroidRuntime = runtimeId;
  renderApps(allApps);
  let ownsAppAction = false;
  try {
    showAppToast('Opening Waydroid… Waiting for any current Android action to finish.', 'info');
    const activationToken = await waydroidActivationToken();
    // Keep this click while the preceding launch completes its startup check.
    for (let attempt = 0; pendingLaunch || pendingAppAction; attempt++) {
      if (attempt >= 180) throw new Error('The previous Android action is still pending. Check its result before retrying.');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    pendingAppAction = true;
    ownsAppAction = true;
    const record = await refreshBatchRecord(runtimeId, 'open');
    const result = await launchRuntimeWaydroidWindow(record, activationToken);
    showAppToast(`${record.name}: ${describeWaydroidWindow(result)}`, result.window?.state === 'ready' ? 'success' : 'info');
  }catch(e){showAppToast(`Cannot open Waydroid: ${e.message||e}`,'error');}
  finally{if(ownsAppAction)pendingAppAction=false;pendingWaydroidRuntime=null;renderApps(allApps);}
}
document.getElementById('probe-open-waydroid').addEventListener('click',()=>openWaydroid(document.getElementById('probe-runtime').value));
document.getElementById('install-touch-probe').addEventListener('click',async()=>{
  if(probeInstallBusy||pendingAppAction||pendingLaunch)return;
  const select=document.getElementById('probe-runtime'),id=select.value;
  const record=window.AnvilKeymapLibrary?.record(id)||managedLibrary.find(r=>r.id===id),status=document.getElementById('probe-status');
  if(!record||!['Running','Frozen'].includes(record.state)||record.error){status.textContent='Select a running runtime.';return;}
  probeInstallBusy=true;select.disabled=true;
  const button=document.getElementById('install-touch-probe');button.disabled=true;
  try {
    if(!await AppDialog.confirm(`Install Touch Probe in ${record.name}?`,{title:'Touch diagnostics',kind:'info',okLabel:'Install',cancelLabel:'Cancel'}))return;
    status.textContent='Installing…';
    const result=await tauriInvoke('install_touch_probe',{runtimeId:id});
    status.textContent='';
    showAppToast(result.message || (result.success ? 'Touch Probe installed.' : 'Touch Probe installation failed.'), result.success ? 'success' : 'error');
    await refreshManagedLibrary();
  }catch(e){status.textContent='';showAppToast(`Touch Probe: ${e.message||e}`,'error');}
  finally{probeInstallBusy=false;select.disabled=false;button.disabled=false;window.AnvilKeymapLibrary?.updateDiagnostics();}
});

function iconUrl(pkg) {
  return `appicon://localhost/${activeRuntimeId}/${encodeURIComponent(pkg)}.png`;
}

function libraryIconUrl(app) {
  if(app.desktop_entry)return 'waydroid.png';
  if (app.runtime_id && app.runtime_id !== 'default') {
    return typeof app.icon_data === 'string' && app.icon_data.length <= 180000 && /^[A-Za-z0-9+/]+={0,2}$/.test(app.icon_data)
      ? `data:image/png;base64,${app.icon_data}` : null;
  }
  return app.icon_path ? iconUrl(app.package) : null;
}

function escapeHtml(value) {
  const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(value).replace(/[&<>"']/g, char => entities[char]);
}

function sortApps(apps, method) {
  const sorted = [...apps];
  switch (method) {
    case 'name-asc':
      sorted.sort((a, b) => (a.label || a.package).localeCompare(b.label || b.package));
      break;
    case 'name-desc':
      sorted.sort((a, b) => (b.label || b.package).localeCompare(a.label || a.package));
      break;
    case 'recent':
      sorted.sort((a,b) => lastOpened(b) - lastOpened(a) || a.package.localeCompare(b.package));
      break;
    case 'favorites':
      sorted.sort((a,b) => Number(isFavorite(b)) - Number(isFavorite(a)) || (a.label || a.package).localeCompare(b.label || b.package));
      break;
    case 'package':
      sorted.sort((a, b) => a.package.localeCompare(b.package));
      break;
  }
  // Stable partition: runtime desktop launchers are pinned ahead of ordinary
  // apps, whose selected sort order remains unchanged.
  return [...sorted.filter(app=>app.desktop_entry),...sorted.filter(app=>!app.desktop_entry)];
}

function appExecutionLabel(app) {
  if (app.desktop_entry) return 'Runtime launcher';
  if (app.runtime_state && ['Stopped','Prepared','Allocated'].includes(app.runtime_state)) return 'Not running';
  if (app.available === false || (app.runtime_state && app.runtime_state !== 'Running')) return 'Status unknown';
  const age = Date.now()/1000 - Number(app.status_checked_at);
  if (!Number.isFinite(age) || age < 0 || age > 15) return 'Status unknown';
  return {foreground:'Foreground',background:'Background process',not_running:'Not running'}[app.execution_state] || 'Status unknown';
}

function appAccessibleLabel(app) {
  const name = app.label || app.package.split('.').pop();
  const runtime = app.runtime_name || (app.runtime_id === 'default' ? 'Default runtime' : 'Runtime');
  const execution = appExecutionLabel(app);
  return `${name} · ${runtime} · ${execution}`;
}

function appCategory(app) {
  if (app.desktop_entry) return 'system';
  const value = `${app.label || ''} ${app.package || ''}`.toLocaleLowerCase();
  if (/game|play|steam|funtap|pubg|minecraft|genshin/.test(value)) return 'games';
  if (/music|video|youtube|gallery|camera|photo|browser|media/.test(value)) return 'media';
  if (/calendar|calculator|contact|clock|file|settings|message|mail|document|productivity/.test(value) || value.startsWith('com.android')) return 'productivity';
  return app.desktop_entry || value.startsWith('com.google') ? 'system' : 'user';
}

async function refreshAppStates() {
  clearTimeout(appStatesTimer);
  if (appStatesLoading || !libraryPage || document.hidden) return;
  appStatesLoading = true;
  try {
    await Promise.all([refreshLibraryDefaultState(), refreshManagedLibrary({poll: true})]);
    await Promise.all(managedLibrary.filter(r=>r.state==='Running').map(async record => {
      if (pendingAppAction || pendingLaunch) return;
      let report;
      try {
        report = await controllerRequest({op:'app_states',id:record.id});
        if (report?.runtime_id !== record.id) throw new Error('Wrong runtime');
      } catch (error) {
        if (controllerBusy(error)) return;
        report = null; record.error = 'Cannot verify runtime status. Refresh to retry.';
      }
      const states = report?.state === 'Running' && report.states && typeof report.states === 'object' ? report.states : null;
      if (report?.state && report.state !== 'Running') record.state = report.state;
      managedSnapshots.set(record.id, (managedSnapshots.get(record.id)||[]).map(app=>({...app,
        execution_state:states ? (states[app.package] || 'not_running') : 'unknown',
        status_checked_at:states ? report.checked_at : 0})));
    }));
    renderManagedLibraryStatus();
    renderApps(allApps);
  } finally {
    appStatesLoading = false;
    if(libraryPage&&!document.hidden) appStatesTimer=setTimeout(refreshAppStates,5000);
  }
}
document.addEventListener('visibilitychange', () => {
  clearTimeout(appStatesTimer);
  if(!document.hidden&&libraryPage)refreshAppStates();
});

function renderApps(apps) {
  renderGoogleNotices();
  const globalQuery = document.getElementById('search-apps')?.value || '';
  const localQuery = librarySearch?.value || '';
  const query = (localQuery || globalQuery).trim().toLocaleLowerCase();
  const favoritesOnly = document.getElementById('favorites-only')?.checked || false;
  const running = managedLibrary.filter(record => libraryRuntimeRunning(record.id));
  apps = [...(libraryRuntimeRunning('default') ? apps || [] : []), ...running.flatMap(record => (managedSnapshots.get(record.id) || []).map(app => ({...app, runtime_id:record.id, runtime_name:record.name, runtime_state:record.state, available:true})))];
  if (libraryRuntimeRunning('default')) apps.push({package:'org.anvildroid.desktop',label:'Waydroid',runtime_id:'default',desktop_entry:true});
  for(const record of running)if(!(managedSnapshots.get(record.id)||[]).some(app=>app.package==='org.anvildroid.desktop'))apps.push({package:'org.anvildroid.desktop',label:'Waydroid',runtime_id:record.id,runtime_name:record.name,runtime_state:record.state,available:true,desktop_entry:true});
  if (libraryRuntimeSelection !== null && ![...libraryRuntimeSelection].some(libraryRuntimeRunning)) {
    renderLibraryEmpty();
    return;
  }
  const unique = new Map();
  apps.forEach(app => { const key = `${app.runtime_id || 'default'}\u0000${app.package}`; if (!unique.has(key)) unique.set(key, app); });
  apps = [...unique.values()];
  apps = apps.filter(app => libraryRuntimeSelected(app.runtime_id || 'default') && (!favoritesOnly || app.desktop_entry || isFavorite(app)) &&
    (currentCategory === 'all' || (currentCategory.startsWith('tag:') ? tagsForApp(app.package).includes(currentCategory.slice(4)) : appCategory(app) === currentCategory)) &&
    `${app.label || ''} ${app.package} ${app.runtime_name || 'Default runtime'}`.toLocaleLowerCase().includes(query));
  if (!apps || apps.length === 0) {
    if (!running.some(record => libraryRuntimeSelected(record.id)) && !(libraryRuntimeSelected('default') && libraryRuntimeRunning('default'))) {
      renderLibraryEmpty();
      return;
    }
    appsGrid._appMarkup = null;
    appsGrid._appStructure = null;
    appsGrid.innerHTML = `
      <div class="empty-state">
        <p>No matching apps.</p>
        <p class="hint">Try another search, tag or favorites filter.</p>
      </div>`;
    return;
  }

  const sorted = sortApps(apps, currentSort);
  const structure = JSON.stringify(sorted.map(app => [app.runtime_id || 'default', app.package, app.label, app.runtime_name, libraryIconUrl(app), isFavorite(app), pendingWaydroidRuntime === (app.runtime_id || 'default') && !!app.desktop_entry]));
  const markup = sorted.map(app => `
    <div class="app-card${pendingWaydroidRuntime === (app.runtime_id || 'default') && app.desktop_entry ? ' processing' : ''}" tabindex="${pendingWaydroidRuntime === (app.runtime_id || 'default') && app.desktop_entry ? '-1' : '0'}" role="button" aria-disabled="${pendingWaydroidRuntime === (app.runtime_id || 'default') && app.desktop_entry ? 'true' : 'false'}" data-package="${escapeHtml(app.package)}" data-runtime-id="${escapeHtml(app.runtime_id || 'default')}" data-app-status="${escapeHtml(appExecutionLabel(app))}" aria-label="${escapeHtml(appAccessibleLabel(app))}" title="${escapeHtml(`${app.package} · ${app.runtime_name || (app.runtime_id === 'default' ? 'Default runtime' : 'Runtime')}`)}">
      <button type="button" class="app-favorite-button favorite-mark${isFavorite(app) ? ' active' : ''}" aria-label="${isFavorite(app) ? 'Remove favorite' : 'Add favorite'}">♡</button>
      <div class="app-icon">
        ${pendingWaydroidRuntime === (app.runtime_id || 'default') && app.desktop_entry
          ? '<span class="app-processing" role="status" aria-label="Processing">Processing…</span>'
          : libraryIconUrl(app)
            ? `<img src="${libraryIconUrl(app)}" alt="" loading="lazy" decoding="async" onerror="this.parentElement.innerHTML='App'">`
            : 'App'}
      </div>
      <div class="app-caption"><div class="app-name">${pendingWaydroidRuntime === (app.runtime_id || 'default') && app.desktop_entry ? 'Processing…' : escapeHtml(app.label || app.package.split('.').pop())}</div><div class="app-runtime-name" title="${escapeHtml(app.runtime_name || 'Default runtime')}">${escapeHtml(app.runtime_name || 'Default runtime')}</div></div>
      <span class="app-status-dot ${appExecutionLabel(app) === 'Foreground' || appExecutionLabel(app) === 'Background process' ? 'running' : ''}" aria-label="${escapeHtml(appExecutionLabel(app))}"></span>
    </div>
  `).join('');

  appsGrid.setAttribute('aria-busy', String(pendingLaunch !== null));
  // Polling must not replace focused/clicked cards when nothing has changed.
  if (appsGrid._appMarkup === markup) return;
  const cards = appsGrid.querySelectorAll('.app-card');
  if (appsGrid._appStructure === structure && cards.length === sorted.length) {
    // Live status changes must retain cards, images, focus and the scroll container.
    cards.forEach((card, index) => {
      const app = sorted[index];
      const status = appExecutionLabel(app);
      if (card.dataset.appStatus === status) return;
      card.dataset.appStatus = status;
      card.setAttribute('aria-label', appAccessibleLabel(app));
      const dot = card.querySelector('.app-status-dot');
      dot.classList.toggle('running', status === 'Foreground' || status === 'Background process');
      dot.setAttribute('aria-label', status);
    });
    appsGrid._appMarkup = markup;
    return;
  }
  const scrollTop = appsGrid.scrollTop;
  appsGrid._appMarkup = markup;
  appsGrid._appStructure = structure;
  appsGrid.innerHTML = markup;
  appsGrid.scrollTop = scrollTop;

  appsGrid.querySelectorAll('.app-card').forEach(card => {
    card.addEventListener('click', () => {
      if (card.getAttribute('aria-disabled') === 'true') return;
      launchApp(card.dataset.package, card.dataset.runtimeId);
    });
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter') launchApp(card.dataset.package, card.dataset.runtimeId);
      if (e.key === ' ' || e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
        e.preventDefault();
        const r = card.getBoundingClientRect();
        showContextMenu({clientX:r.left, clientY:r.bottom}, card.dataset.package, card.querySelector('.app-name')?.textContent, card.dataset.runtimeId);
      }
    });
    card.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      showContextMenu(e, card.dataset.package, card.querySelector('.app-name')?.textContent, card.dataset.runtimeId);
    });
    card.querySelector('.app-open-button')?.addEventListener('click', e => { e.stopPropagation(); launchApp(card.dataset.package, card.dataset.runtimeId); });
    card.querySelector('.app-menu-button')?.addEventListener('click', e => { e.stopPropagation(); const r = card.getBoundingClientRect(); showContextMenu({clientX:r.right - 12, clientY:r.top + 34}, card.dataset.package, card.querySelector('.app-name')?.textContent, card.dataset.runtimeId); });
    card.querySelector('.app-open-caret')?.addEventListener('click', e => { e.stopPropagation(); const r = card.getBoundingClientRect(); showContextMenu({clientX:r.right - 12, clientY:r.bottom}, card.dataset.package, card.querySelector('.app-name')?.textContent, card.dataset.runtimeId); });
    card.querySelector('.app-favorite-button')?.addEventListener('click', e => { e.stopPropagation(); appMenuAction('favorite', card.dataset.package, card.querySelector('.app-name')?.textContent, null, card.dataset.runtimeId); });
  });
}

const APPS_CACHE_KEY = `anvildroid_apps_cache:${activeRuntimeId}`;

function saveAppsCache(apps) {
  try { localStorage.setItem(APPS_CACHE_KEY, JSON.stringify(apps)); } catch (e) { /* ignore */ }
}

function loadAppsCache() {
  try {
    const raw = localStorage.getItem(APPS_CACHE_KEY) ||
      (activeRuntimeId === 'default' ? localStorage.getItem('anvildroid_apps_cache') : null);
    return raw ? JSON.parse(raw) : null;
  } catch (e) { return null; }
}

function loadCachedApps() {
  const cached = loadAppsCache();
  if (cached && cached.length > 0) {
    allApps = cached;
    renderApps(allApps);
  }
}

async function refreshApps() {
  if (refreshingApps) return;
  refreshingApps = true;
  btnRefreshApps.disabled = true;
  btnRefreshApps.textContent = 'Loading…';
  // Only show loading spinner if no cached data is displayed.
  if (allApps.length === 0) {
    appsGrid._appMarkup = null;
    appsGrid.innerHTML = '<div class="loading-state">Loading apps…</div>';
  }
  const managedRefresh = refreshManagedLibrary();
  try {
    await refreshLibraryDefaultState({loadInventory:false});
    if (!libraryRuntimeRunning('default') || (libraryDefaultState === 'Frozen' && allApps.length > 0)) return;
    const apps = await invoke('get_apps');
    if (!Array.isArray(apps)) throw new Error('Invalid app inventory');
    const changed = JSON.stringify(apps) !== JSON.stringify(allApps);
    allApps = apps;
    saveAppsCache(allApps);
    if (changed || allApps.length === 0) renderApps(allApps);
  } catch (err) {
    // If we have cached data, keep showing it.
    if (allApps.length === 0) {
      appsGrid.innerHTML = `<div class="empty-state"><p>Cannot load apps.</p><p class="hint">${escapeHtml(err)}</p></div>`;
    }
  } finally {
    try {
      await managedRefresh;
      renderApps(allApps);
    } finally {
      refreshingApps = false;
      btnRefreshApps.disabled = false;
      btnRefreshApps.textContent = 'Refresh';
    }
  }
}

function libraryRuntimeRunning(id) {
  if (libraryStarts.has(id)) return false;
  if (id === 'default') return ['Running', 'Frozen'].includes(libraryDefaultState);
  const record = managedLibrary.find(record => record.id === id);
  return ['Running', 'Frozen'].includes(record?.state) && !record.error;
}

async function refreshLibraryDefaultState({loadInventory = true} = {}) {
  const previous = libraryDefaultState;
  try {
    try {
      const record = await controllerRequest({op:'refresh', id:'default'});
      if (record?.id !== 'default' || typeof record.state !== 'string') throw new Error('Invalid Default runtime state');
      libraryDefaultState = record.state;
    } catch (_) {
      const report = await invoke('get_health_report');
      libraryDefaultState = typeof report?.status?.state === 'string' ? report.status.state : report?.status?.state?.type || 'Unknown';
    }
    // Publish live availability immediately so saved apps can appear while the
    // slower Android inventory is being read. Cached state never enables actions.
    if (!appStatesLoading) { renderManagedLibraryStatus(); renderApps(allApps); }
    if (loadInventory && ((libraryDefaultState === 'Running' && !['Running', 'Frozen'].includes(previous)) || (libraryDefaultState === 'Frozen' && allApps.length === 0))) {
      const apps = await invoke('get_apps');
      if (!Array.isArray(apps)) throw new Error('Invalid app inventory');
      allApps = apps;
      saveAppsCache(apps);
    }
  } catch (_) { libraryDefaultState = 'Unknown'; }
  if (!appStatesLoading) { renderManagedLibraryStatus(); renderApps(allApps); }
}

function renderLibraryEmpty() {
  const records = [{id:'default', name:'Default runtime', state:libraryDefaultState}, ...managedLibrary]
    .filter(record => libraryRuntimeSelected(record.id));
  const panels = records.map(record => {
    const busy = libraryStarts.has(record.id) || ['Starting', 'Stopping', 'Provisioning'].includes(record.state);
    const canStart = !record.error && ['Stopped', 'Ready', 'Frozen'].includes(record.state) && record.import_status !== 'pending';
    const error = libraryStartErrors.get(record.id) || record.error;
    const note = error || (busy ? 'Please wait. Apps will appear automatically when this runtime is running.' : canStart ? 'Start this runtime to see and open its apps.' : 'Open runtime settings to check its status and finish setup.');
    return `<section class="library-runtime-empty"><h3>${escapeHtml(record.name || record.id)}</h3><span class="hint">${escapeHtml(busy ? 'Starting / updating' : record.state)}</span><p ${error ? 'role="alert"' : ''}>${escapeHtml(note)}</p><div class="runtime-actions">${canStart || busy ? `<button class="btn btn-primary" data-library-start="${escapeHtml(record.id)}" ${busy ? 'disabled' : ''}>${busy ? 'Please wait...' : 'Start runtime'}</button>` : ''}<button class="btn btn-quiet" data-library-manage="${escapeHtml(record.id)}">Runtime settings</button><button class="btn btn-quiet" data-library-refresh>Refresh</button></div></section>`;
  }).join('');
  const markup = `<div class="library-empty-panel" role="status"><h2>${libraryRuntimeSelection?.size === 0 ? 'Select runtimes to view apps' : 'Start a runtime to view apps'}</h2><p class="hint">Only apps from running runtimes appear here.</p>${panels || '<p>Choose runtimes in the filter above.</p>'}</div>`;
  appsGrid.setAttribute('aria-busy', String(libraryStarts.size > 0));
  if (appsGrid._appMarkup === markup) return;
  appsGrid._appMarkup = markup;
  appsGrid._appStructure = null;
  appsGrid.innerHTML = markup;
}

appsGrid.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button || button.disabled) return;
  if (button.dataset.libraryStart) startLibraryRuntime(button.dataset.libraryStart);
  else if (button.dataset.libraryManage) {
    openRuntimeSettings(button.dataset.libraryManage);
  } else if ('libraryRefresh' in button.dataset) refreshApps();
});

async function startLibraryRuntime(id) {
  // Each runtime owns its own start/poll state, so independent runtimes can
  // start concurrently. Repeated clicks on the same runtime remain blocked.
  if (libraryStarts.has(id) || controllerMutation || pendingLaunch || pendingAppAction) return;
  const record = id === 'default' ? {state:libraryDefaultState} : managedLibrary.find(record => record.id === id);
  if (!record || record.error || !['Stopped', 'Ready', 'Frozen'].includes(record.state) || record.import_status === 'pending') return;
  if (id === 'default' && controllerRecords.some(record => record.import_status === 'pending' && record.state === 'Provisioning')) {
    libraryStartErrors.set(id, 'Wait for the managed copy to finish before starting this runtime.');
    renderApps(allApps);
    return;
  }
  libraryStarts.set(id, Date.now());
  libraryStartErrors.delete(id);
  renderApps(allApps);
  renderManagedLibraryStatus();
  try {
    if (id === 'default') {
      const result = await trackRuntimeTransition(() => invoke('start_runtime'));
      if (!result?.success) throw new Error(result?.message || 'Cannot start runtime');
    } else {
      const result = await controllerRequest({op:'start', id});
      if (result?.id !== id) throw new Error('Invalid runtime response');
    }
    await pollLibraryStart(id, 0);
  } catch (error) {
    libraryStarts.delete(id);
    libraryStartErrors.set(id, `Cannot start runtime: ${error.message || error}`);
    renderManagedLibraryStatus();
    renderApps(allApps);
  }
}

async function pollLibraryStart(id, attempts = 0) {
  try {
    let state;
    if (id === 'default') {
      await refreshLibraryDefaultState();
      state = libraryDefaultState;
    } else {
      const current = await controllerRequest({op:'refresh', id});
      if (current?.id !== id) throw new Error('Cannot verify runtime status');
      managedLibrary = managedLibrary.map(record => record.id === id ? current : record);
      state = current.state;
      if (current.job?.status === 'Failed') throw new Error(current.job.error || 'Startup failed');
    }
    if (state === 'Running') {
      libraryStarts.delete(id);
      await refreshApps();
    } else if (['Failed', 'Error', 'NotInstalled', 'SessionLost'].includes(state) || attempts >= 60 || Date.now() - libraryStarts.get(id) > 120000) {
      throw new Error('Runtime did not start. Check Runtime settings, then retry.');
    } else {
      setTimeout(() => pollLibraryStart(id, attempts + 1), 2000);
    }
  } catch (error) {
    libraryStarts.delete(id);
    libraryStartErrors.set(id, String(error.message || error));
  }
  renderManagedLibraryStatus();
  renderApps(allApps);
}

async function launchApp(pkg, runtimeId = 'default') {
  if(pkg==='org.anvildroid.desktop')return openWaydroid(runtimeId);
  if(probeInstallBusy)return;
  if (runtimeId !== 'default') return managedAppAction('launch', pkg, runtimeId);
  if (pendingLaunch || pendingAppAction || controllerMutation || controllerRecords.some(record => record.import_status === 'pending' && record.state === 'Provisioning')) return;
  pendingLaunch = pkg;
  appsGrid.setAttribute('aria-busy', 'true');
  const appName = allApps.find(app => app.package === pkg)?.name || pkg;
  showAppToast(`Opening ${appName} — waiting for Android if needed…`, 'info');
  try {
    // Preserve this click while an explicit manual startup owns the backend.
    if (runtimeTransition) {
      const startup = await runtimeTransition;
      if (startup && !startup.success) throw new Error(startup.message);
    }
    const result = await invoke('launch_app', { package: pkg });
    if (result.success) await loadLibrary();
    showAppToast(result.success ? `Launch requested: ${appName}` : `Cannot open ${appName}: ${result.message}`,
      result.success ? 'success' : 'error');
  } catch (err) {
    showAppToast(`Cannot open ${appName}: ${err.message || err}`, 'error');
  } finally {
    pendingLaunch = null;
    appsGrid.setAttribute('aria-busy', 'false');
    refreshStatus();
  }
}

if (sortSelect) {
  sortSelect.addEventListener('change', async () => {
    sortSelect.disabled = true;
    try { library = await invoke('set_library_sort', { sort: sortSelect.value }); currentSort = library.sort; renderApps(allApps); }
    catch (e) { sortSelect.value = currentSort; showAppToast(`Cannot save sorting: ${e}`, 'error'); }
    finally { sortSelect.disabled = false; }
  });
}

btnRefreshApps.addEventListener('click', refreshApps);

// --- Context menu ---
let activeMenu = null;
async function appMenuAction(action, pkg, label, blockReason, runtimeId = 'default') {
  if(probeInstallBusy)return;
  if(action==='keymap')return openKeymapOverlay(runtimeId,pkg);
  if (action === 'remove-shortcut') {
    if (pendingLaunch || pendingAppAction) return;
    pendingAppAction = true;
    try {
      const result = await tauriInvoke('remove_app_shortcut', {runtimeId, package:pkg});
      showAppToast(result.success ? result.message : `Failed: ${result.message}`, result.success ? 'success' : 'error');
    } catch (error) { showAppToast(`Cannot remove shortcut: ${error.message || error}`, 'error'); }
    finally { pendingAppAction = false; }
    return;
  }
  if (runtimeId !== 'default') {
    if (action === 'details' || action === 'info') {
      showAppToast(action === 'info' ? 'Android App Info is available for the Default runtime.' : 'Detailed Android info is available for the Default runtime.', 'info');
      return;
    }
    if (action === 'shortcut') {
      if (pendingLaunch || pendingAppAction) return;
      pendingAppAction = true;
      showAppToast(`Creating menu shortcut for ${label || pkg}…`, 'info');
      try {
        const result = await tauriInvoke('create_app_shortcut', {runtimeId, package:pkg});
        showAppToast(result.success ? result.message : `Failed: ${result.message}`, result.success ? 'success' : 'error');
      } catch (error) { showAppToast(`Cannot create shortcut: ${error.message || error}`, 'error'); }
      finally { pendingAppAction = false; }
      return;
    }
    if (action === 'favorite') return toggleManagedFavorite(pkg, runtimeId);
    if (['launch', 'force-stop', 'uninstall'].includes(action)) return managedAppAction(action === 'force-stop' ? 'force_stop' : action, pkg, runtimeId);
    return;
  }
  if (pendingLaunch || pendingAppAction) return;
  if (action === 'launch') return launchApp(pkg);
  if (action === 'details') return showDetails(pkg);
  if (action === 'favorite') return toggleFavorite(pkg);
  if ((action === 'uninstall' || action === 'force-stop') && blockReason) {
    showAppToast(blockReason, 'info');
    return;
  }
  const commands = { 'force-stop': 'force_stop_app', info: 'open_app_info', shortcut: 'create_app_shortcut', uninstall: 'uninstall_app' };
  if (!commands[action]) return;
  // Reserve before opening the native confirmation dialog to avoid duplicate jobs.
  pendingAppAction = true;
  try {
    if (action === 'force-stop') {
      const confirmed = await AppDialog.confirm(
        `Force-stop "${label || pkg}"?\n${pkg}\n\nThis stops all app processes and background work, not just its window. Unsaved work may be lost. Installed data is retained.`,
        {title:'Force-stop app', kind:'warning', okLabel:'Force-stop', cancelLabel:'Cancel'});
      if (!confirmed) return;
    }
    if (action === 'uninstall') {
      const confirmed = await AppDialog.confirm(
        `Uninstall "${label || pkg}"?\n${pkg}\n\nThis permanently removes the app and its Android data.`,
        { title: 'Uninstall app', kind: 'warning', okLabel: 'Uninstall', cancelLabel: 'Cancel' },
      );
      if (!confirmed) return;
    }
    const messages = {
      'force-stop': `Stopping ${label || pkg}…`,
      info: `Opening Android App Info for ${label || pkg}…`,
      shortcut: `Creating menu shortcut for ${label || pkg}…`,
      uninstall: `Uninstalling ${label || pkg}…`,
    };
    showAppToast(messages[action], 'info');
    if (action === 'uninstall') clearTimeout(appToastTimer);
    if (runtimeTransition) {
      const startup = await runtimeTransition;
      if (startup && !startup.success) throw new Error(startup.message);
    }
    const result = await invoke(commands[action], { package: pkg });
    showAppToast(result.success ? result.message : `Failed: ${result.message}`, result.success ? 'success' : 'error');
    if (action === 'uninstall') await refreshApps();
    if (action === 'force-stop' && detailPackage === pkg) await showDetails(pkg);
  } catch (err) {
    showAppToast(`Cannot ${action} ${pkg}: ${err.message || err}`, 'error');
  } finally {
    pendingAppAction = false;
  }
}

function showContextMenu(e, pkg, label, runtimeId = 'default') {
  removeContextMenu();
  const app = runtimeId === 'default' ? allApps.find(app => app.package === pkg) : null;
  const blockReason = app?.uninstall_block_reason;
  const menu = document.createElement('div');
  menu.className = 'context-menu';
  menu.setAttribute('role', 'menu');
  const options = pkg === 'org.anvildroid.desktop' ? [['launch', 'Open Waydroid'], ['details', 'Runtime details'], ['favorite', library.favorites.includes(pkg) ? 'Remove favorite' : 'Add favorite']] : [
    ['launch', 'Open app'],
    ['details', 'App details'],
    ['favorite', library.favorites.includes(pkg) ? 'Remove favorite' : 'Add favorite'],
    ['info', 'Android App Info'],
    ['force-stop', blockReason ? 'Force-stop (protected component)' : 'Force-stop…'],
    ['shortcut', 'Create/update menu shortcut'],
    ['uninstall', blockReason ? 'Uninstall (protected component)' : 'Uninstall…'],
  ];
  for (const [action, title] of options) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = `ctx-item${action === 'uninstall' ? ' ctx-danger' : ''}`;
    item.textContent = title;
    item.setAttribute('role', 'menuitem');
    item.disabled = !!(((action==='details'||action==='info')&&runtimeId!=='default') || pendingLaunch || pendingAppAction || preferenceBusy || (action === 'favorite' && runtimeId !== 'default' && !managedPreferences.has(runtimeId)) || ((action === 'uninstall' || action === 'force-stop') && blockReason));
    if ((action === 'uninstall' || action === 'force-stop') && blockReason) item.title = blockReason;
    item.addEventListener('click', () => {
      removeContextMenu();
      appMenuAction(action, pkg, label, blockReason, runtimeId);
    });
    menu.appendChild(item);
  }
  document.body.appendChild(menu);
  activeMenu = menu;
  const rect = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(4, Math.min(e.clientX, window.innerWidth - rect.width - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(e.clientY, window.innerHeight - rect.height - 4))}px`;
  menu.querySelector('button:not(:disabled)')?.focus();
  if (pkg !== 'org.anvildroid.desktop') {
    tauriInvoke('has_app_shortcut', {runtimeId, package:pkg}).then(exists => {
      // A late reply must never add an action to a different app's menu.
      if (exists !== true || activeMenu !== menu) return;
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'ctx-item';
      item.textContent = 'Remove menu shortcut';
      item.setAttribute('role', 'menuitem');
      item.disabled = !!(pendingLaunch || pendingAppAction || probeInstallBusy);
      item.addEventListener('click', () => {
        removeContextMenu();
        appMenuAction('remove-shortcut', pkg, label, blockReason, runtimeId);
      });
      const create = [...menu.children].find(button => button.textContent === 'Create/update menu shortcut');
      create?.insertAdjacentElement('afterend', item);
      const rect = menu.getBoundingClientRect();
      menu.style.top = `${Math.max(4, Math.min(e.clientY, window.innerHeight - rect.height - 4))}px`;
    }).catch(error => console.debug('Cannot check menu shortcut', error));
  }
}

function removeContextMenu() {
  if (activeMenu) { activeMenu.remove(); activeMenu = null; }
}
async function openKeymapOverlay(runtimeId,pkg){
  if(pendingAppAction||pendingLaunch)return;
  if(runtimeId!=='default'&&!/^r-[0-9a-f]{32}$/.test(runtimeId)){showAppToast('Select a desktop runtime to edit keymaps.','info');return;}
  pendingAppAction=true;
  try{
    showAppToast('Opening keymap overlay on the running app…','info');
    let job=await controllerRequest({op:'app_action',id:runtimeId,action:'keymap_edit',package:pkg});
    if(runtimeId==='default'){
      if(job?.action!=='keymap_edit'||job.package!==pkg||job.success!==true)throw new Error('Overlay request not confirmed');
      showAppToast(job.message||'Overlay attached.','success');return;
    }
    const token=job.id,deadline=Date.now()+15000;
    while(true){
      if(job.id!==token||job.runtime_id!==runtimeId||job.package!==pkg||job.action!=='keymap_edit')throw new Error('App operation changed; overlay request not confirmed');
      if(job.status==='Succeeded'){showAppToast(job.result?.message||'Overlay attached. Edit on the app window; Save or Escape to leave.','success');break;}
      if(job.status!=='Running'||Date.now()>deadline)throw new Error(job.error||'Overlay request timed out');
      await new Promise(resolve=>setTimeout(resolve,150));job=await controllerRequest({op:'app_job',id:runtimeId});
    }
  }catch(error){showAppToast(`Cannot edit keymap: ${error.message||error}`,'error');}
  finally{pendingAppAction=false;}
}
window.AnvilKeymapActions={open:openKeymapOverlay};

document.addEventListener('pointerdown', e => {
  if (activeMenu && !activeMenu.contains(e.target)) removeContextMenu();
});
document.addEventListener('keydown', e => {
  if (!activeMenu) return;
  if (e.key === 'Escape') { removeContextMenu(); return; }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const items = [...activeMenu.querySelectorAll('button:not(:disabled)')];
    if (!items.length) return;
    const index = items.indexOf(document.activeElement);
    items[(index + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
  }
});
window.addEventListener('blur', removeContextMenu);
window.addEventListener('resize', removeContextMenu);

// Persistent library and details. Preferences are independent of Android boot.
async function loadLibrary() {
  try {
    const result = await invoke('get_library');
    if (result) { const changed = JSON.stringify(library) !== JSON.stringify(result); library = result; currentSort = library.sort; sortSelect.value = currentSort; libraryReady = true; if (changed) renderApps(allApps); }
  } catch (e) { showAppToast(`Cannot load library preferences: ${e}`, 'error'); }
}
async function toggleFavorite(pkg) {
  if (preferenceBusy) return;
  if (!libraryReady) { showAppToast('Library preferences are unavailable; retry after reopening the GUI.', 'error'); return; }
  preferenceBusy = true;
  try {
    library = await invoke('set_favorite', {package:pkg, enabled:!library.favorites.includes(pkg)});
    renderApps(allApps);
    if (detailPackage === pkg) document.getElementById('detail-favorite').textContent = library.favorites.includes(pkg) ? 'Remove favorite' : 'Add favorite';
  } catch (e) { showAppToast(`Cannot save favorite: ${e}`, 'error'); }
  finally { preferenceBusy = false; }
}
async function showDetails(pkg) {
  const generation = ++detailGeneration;
  detailPackage = pkg;
  const app = allApps.find(a => a.package === pkg);
  const dialog = document.getElementById('app-details');
  document.getElementById('detail-name').textContent = app?.label || pkg;
  document.getElementById('detail-content').textContent = 'Loading Android app details…';
  document.getElementById('detail-force-stop').disabled = !!app?.uninstall_block_reason;
  document.getElementById('detail-favorite').textContent = library.favorites.includes(pkg) ? 'Remove favorite' : 'Add favorite';
  if (!dialog.open) dialog.showModal();
  try {
    if (runtimeTransition) await runtimeTransition;
    const info = await invoke('get_app_details', {package:pkg});
    if (generation !== detailGeneration) return;
    const last = library.recent[pkg];
    const rows = [ ['Package',pkg], ['Installed',info.installed ? 'Yes' : 'No'],
      ['Version',info.version_name ?? 'Unknown'], ['Version code',info.version_code ?? 'Unknown'],
      ['Native ABI',info.abi ?? 'Not reported'], ['Profile',info.profile ?? 'None configured'],
      ['Stopped',info.stopped == null ? 'Unknown' : info.stopped ? 'Yes' : 'No'],
      ['Last opened',last ? new Date(last).toLocaleString() : 'Not opened through AnvilDroid yet'] ];
    document.getElementById('detail-content').innerHTML = '<dl>' + rows.map(([k,v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('') + '</dl>' + tagEditorMarkup(pkg);
    bindTagEditor(pkg);
  } catch (e) {
    if (generation === detailGeneration) document.getElementById('detail-content').textContent = `Cannot read app details: ${e}`;
  }
}
function tagEditorMarkup(pkg) {
  if (!appTags.tags.length) return '<p class="hint tag-empty">No tags yet. Use “Edit tags” to create one.</p>';
  const assigned = new Set(tagsForApp(pkg));
  return `<fieldset class="app-tags"><legend>Tags</legend>${appTags.tags.map(tag => `<label><input type="checkbox" data-app-tag="${escapeHtml(tag)}" ${assigned.has(tag) ? 'checked' : ''}>${escapeHtml(tag)}</label>`).join('')}</fieldset>`;
}
function bindTagEditor(pkg) { document.getElementById('detail-content').querySelectorAll('[data-app-tag]').forEach(input => input.addEventListener('change', () => { const assigned = new Set(tagsForApp(pkg)); input.checked ? assigned.add(input.dataset.appTag) : assigned.delete(input.dataset.appTag); appTags.assignments[pkg] = [...assigned]; saveAppTags(); renderTagFilters(); renderApps(allApps); })); }
function renderTagManager() {
  const host = document.getElementById('tag-list'); if (!host) return;
  host.innerHTML = appTags.tags.length ? appTags.tags.map(tag => `<span class="tag-manager-item">${escapeHtml(tag)}<button type="button" data-delete-tag="${escapeHtml(tag)}" aria-label="Delete ${escapeHtml(tag)}">×</button></span>`).join('') : '<p class="hint">No custom tags.</p>';
  host.querySelectorAll('[data-delete-tag]').forEach(button => button.addEventListener('click', () => { const tag = button.dataset.deleteTag; appTags.tags = appTags.tags.filter(item => item !== tag); Object.keys(appTags.assignments).forEach(pkg => { appTags.assignments[pkg] = tagsForApp(pkg).filter(item => item !== tag); }); saveAppTags(); renderTagManager(); renderTagFilters(); renderApps(allApps); }));
}
document.getElementById('btn-manage-tags')?.addEventListener('click', () => { renderTagManager(); document.getElementById('tag-manager-dialog')?.showModal(); });
document.getElementById('tag-create')?.addEventListener('click', () => { const input = document.getElementById('new-tag-name'); const tag = input.value.trim().replace(/\s+/g, ' '); if (!tag || appTags.tags.includes(tag)) return; appTags.tags.push(tag); appTags.tags.sort((a,b) => a.localeCompare(b)); input.value = ''; saveAppTags(); renderTagManager(); renderTagFilters(); });
document.getElementById('detail-tags')?.addEventListener('click', () => {
  if (!detailPackage) return;
  if (!appTags.tags.length) { renderTagManager(); document.getElementById('tag-manager-dialog')?.showModal(); return; }
  const content = document.getElementById('detail-content');
  content.querySelector('.app-tags')?.remove();
  content.insertAdjacentHTML('beforeend', tagEditorMarkup(detailPackage));
  bindTagEditor(detailPackage);
});
function closeDetails() { ++detailGeneration; detailPackage = null; document.getElementById('app-details').close(); }
document.getElementById('detail-close').addEventListener('click', closeDetails);
document.getElementById('app-details').addEventListener('close', () => { ++detailGeneration; detailPackage = null; });
for (const [id, action] of [['detail-open','launch'], ['detail-info','info'], ['detail-force-stop','force-stop']]) {
  document.getElementById(id).addEventListener('click', () => {
    const pkg = detailPackage; if (!pkg) return;
    const app = allApps.find(a => a.package === pkg);
    closeDetails(); appMenuAction(action, pkg, app?.label, app?.uninstall_block_reason);
  });
}
document.getElementById('detail-favorite').addEventListener('click', () => { if (detailPackage) toggleFavorite(detailPackage); });
document.getElementById('search-apps').addEventListener('input', () => renderApps(allApps));
librarySearch?.addEventListener('input', () => renderApps(allApps));
tagFilter?.addEventListener('change', () => {
  currentCategory = tagFilter.value || 'all';
  renderApps(allApps);
});
renderTagFilters();
document.getElementById('favorites-only').addEventListener('change', () => renderApps(allApps));
// Inventory refresh is explicit or follows an app mutation; focus is not a refresh.

// --- Install APK ---
let apkInstallBusy = false;
function apkTargetAvailable(record) {
  return ['Running', 'Frozen'].includes(record?.state) && !record.unavailable && !record.deletion_pending &&
    ![record.job, record.appJob, record.storage?.job].some(job => job?.status === 'Running');
}
async function loadApkTargets() {
  const snapshots = await Promise.allSettled([controllerRequest({op:'list'}), controllerRequest({op:'refresh', id:'default'}), invoke('get_runtime_catalog')]);
  if (snapshots.slice(0, 2).every(result => result.status === 'rejected')) throw new Error('Cannot load runtimes. Try again.');
  return [
    ...(snapshots[0].status === 'fulfilled' && Array.isArray(snapshots[0].value) ? snapshots[0].value.filter(record => record.id !== 'default') : []),
    ...(snapshots[1].status === 'fulfilled' && snapshots[1].value?.id === 'default' ? [{...snapshots[1].value, image_label:snapshots[2].status === 'fulfilled' ? snapshots[2].value?.configured?.settings?.image_kind : null}] : []),
  ];
}
function apkRuntimeImageLabel(record) {
  const image = record.image_selection;
  if (image?.flavor === 'CUSTOM') {
    const cached = (imageDownloadState?.available_images || []).find(item =>
      item.images?.system?.sha256 === image.images?.system?.sha256 &&
      item.images?.vendor?.sha256 === image.images?.vendor?.sha256);
    return image.custom_name || cached?.custom_name || 'Custom image';
  }
  if (image?.flavor) return `Waydroid ${image.flavor}`;
  if (record.image_label) return `Waydroid ${record.image_label}`;
  return record.id === 'default' || record.import_source === 'default' ? 'System Waydroid image' : 'Installed Waydroid image';
}
function chooseApkTargets(records) {
  const dialog = document.getElementById('apk-install-dialog');
  const form = document.getElementById('apk-install-form');
  const targets = document.getElementById('apk-install-targets');
  const submit = document.getElementById('apk-install-submit');
  const cancel = document.getElementById('apk-install-cancel');
  const pick = document.getElementById('apk-install-pick');
  const error = document.getElementById('apk-install-error');
  let path = '', picking = false, finished = false;
  const preferred = libraryRuntimeSelection;
  targets.innerHTML = `<table class="apk-runtime-table"><thead><tr><th scope="col"><input id="apk-install-all" type="checkbox" aria-label="Select all available runtimes" title="Select all"></th><th scope="col">Runtime</th><th scope="col">Image</th></tr></thead><tbody>${records.map(record => `<tr><td><input id="apk-target-${escapeHtml(record.id)}" type="checkbox" aria-label="Select ${escapeHtml(record.name)}" value="${escapeHtml(record.id)}" ${!apkTargetAvailable(record) ? 'disabled' : preferred?.has(record.id) || records.filter(apkTargetAvailable).length === 1 ? 'checked' : ''}></td><td><label for="apk-target-${escapeHtml(record.id)}">${escapeHtml(record.name)}</label><small id="apk-target-state-${escapeHtml(record.id)}" class="apk-target-state ${record.state === 'Running' ? 'running' : 'idle'}">${escapeHtml(record.state || 'Unavailable')}</small></td><td>${escapeHtml(apkRuntimeImageLabel(record))}</td></tr>`).join('') || '<tr><td colspan="3" class="hint">No runtimes available.</td></tr>'}</tbody></table>`;
  error.textContent = records.some(apkTargetAvailable) ? '' : 'Start a runtime to install an APK.';
  const selected = () => Array.from(targets.querySelectorAll('tbody input:checked')).filter(input => !input.disabled).map(input => input.value);
  const selectAll = document.getElementById('apk-install-all');
  const update = () => {
    const available = Array.from(targets.querySelectorAll('tbody input')).filter(input => !input.disabled);
    const count = selected().length;
    submit.disabled = !path || !count || picking;
    selectAll.disabled = !available.length;
    selectAll.checked = available.length > 0 && count === available.length;
    selectAll.indeterminate = count > 0 && count < available.length;
  };
  targets.onchange = update;
  selectAll.onchange = () => {
    targets.querySelectorAll('tbody input').forEach(input => { if (!input.disabled) input.checked = selectAll.checked; });
    update();
  };
  pick.disabled = false;
  pick.onclick = async () => {
    if (picking) return;
    picking = true; pick.disabled = true; update();
    try {
      const file = await window.__TAURI__.dialog.open({multiple:false, filters:[{name:'APK', extensions:['apk']}], title:'Choose APK'});
      if (finished || !file) return;
      path = typeof file === 'string' ? file : file.path;
      if (!path) throw new Error('Invalid APK path.');
      document.getElementById('apk-install-file').textContent = path.split('/').pop();
    } catch (e) { if (!finished) error.textContent = String(e.message || e); }
    finally { picking = false; if (!finished) { pick.disabled = false; update(); } }
  };
  return new Promise(resolve => {
    const finish = value => {
      if (finished) return;
      finished = true;
      form.onsubmit = dialog.oncancel = dialog.onclose = cancel.onclick = targets.onchange = pick.onclick = null;
      selectAll.onchange = null;
      resolve(value);
    };
    form.onsubmit = event => { event.preventDefault(); if (!submit.disabled) finish({ids:selected(), path}); };
    cancel.onclick = () => { finish(null); dialog.close(); };
    dialog.oncancel = event => { event.preventDefault(); finish(null); dialog.close(); };
    dialog.onclose = () => finish(null);
    update();
  });
}
function apkInstallErrorSummary(message) {
  if (/not enough space|insufficient.storage|ENOSPC|no space left/i.test(message)) return 'Not enough Android storage';
  if (/version.downgrade/i.test(message)) return 'A newer version is already installed';
  if (/update.incompatible|signatures? .*match/i.test(message)) return 'APK signature does not match the installed app';
  return 'Installation failed';
}
function updateApkProgress(results, total, active = null) {
  document.getElementById('apk-install-progress').hidden = false;
  const bar = document.getElementById('apk-install-bar');
  bar.max = total; bar.value = results.length;
  bar.className = active ? 'is-installing' : '';
  if (active) bar.removeAttribute('value');
  document.getElementById('apk-install-progress-label').textContent = active
    ? `Installing ${results.length + 1}/${total} · ${active.name}` : `${results.length}/${total} runtimes processed`;
  if (active) {
    const state = document.getElementById(`apk-target-state-${active.id}`);
    state.textContent = 'Installing…'; state.className = 'apk-target-state installing';
  }
  results.forEach(result => {
    const state = document.getElementById(`apk-target-state-${result.id}`);
    state.textContent = {completed:'Installed', failed:'Failed', skipped:'Skipped'}[result.status];
    state.className = `apk-target-state ${result.status}`;
    state.title = result.message;
    if (result.status === 'failed') state.textContent += ` · ${apkInstallErrorSummary(result.message)}`;
  });
  document.getElementById('apk-install-error-details').innerHTML = results.filter(result => result.status !== 'completed').map(result => `<details class="apk-install-error-detail"><summary>${escapeHtml(result.name)} · ${escapeHtml(result.status === 'failed' ? apkInstallErrorSummary(result.message) : 'Skipped')}</summary>${/not enough space|insufficient.storage|ENOSPC|no space left/i.test(result.message) ? '<p>Free space on the runtime data disk, then retry.</p>' : ''}<pre>${escapeHtml(result.message)}</pre></details>`).join('');

}
async function runApkInstall(records, ids, path) {
  const results = [];
  for (const id of ids) {
    const target = records.find(record => record.id === id);
    if (!target) continue;
    updateApkProgress(results, ids.length, target);
    try {
      const current = await refreshBatchRecord(id, 'install');
      if (!apkTargetAvailable(current) || current.name !== target.name ||
          (target.generation != null && current.generation !== target.generation)) {
        results.push({id, name:target.name, status:'skipped', message:'Runtime changed or is no longer available'});
      } else {
        const result = await tauriInvoke('install_apk', {runtimeId:id, path});
        results.push({id, name:target.name, status:result?.success ? 'completed' : 'failed', message:result?.message || 'Installation failed'});
      }
    } catch (error) { results.push({id, name:target.name, status:'failed', message:String(error.message || error)}); }
    updateApkProgress(results, ids.length);
  }
  return results;
}
async function installApk() {
  if (probeInstallBusy || pendingLaunch || pendingAppAction || runtimeTransition || controllerMutation || runtimeBatchBusy) {
    showAppToast('Wait for the current operation to complete.', 'info'); return;
  }
  pendingAppAction = true;
  apkInstallBusy = true;
  renderController(); renderManagedLibraryStatus();
  const dialog = document.getElementById('apk-install-dialog');
  const cancel = document.getElementById('apk-install-cancel');
  const submit = document.getElementById('apk-install-submit');
  const form = document.getElementById('apk-install-form');
  document.getElementById('apk-install-file').textContent = 'No APK selected';
  document.getElementById('apk-install-targets').textContent = 'Loading runtimes…';
  document.getElementById('apk-install-error').textContent = '';
  document.getElementById('apk-install-error-details').innerHTML = '';
  document.getElementById('apk-install-progress').hidden = true;
  document.getElementById('apk-install-config').hidden = false;
  document.getElementById('apk-install-pick').hidden = false;
  document.getElementById('apk-install-target-hint').textContent = 'Choose runtimes';
  document.getElementById('apk-install-pick').disabled = true;
  submit.hidden = false; submit.disabled = true;
  cancel.disabled = false; cancel.textContent = 'Cancel'; cancel.onclick = () => dialog.close();
  dialog.showModal();
  try {
    const records = await loadApkTargets();
    if (!dialog.open) return;
    const choice = await chooseApkTargets(records);
    if (!choice) return;
    document.getElementById('apk-install-pick').hidden = true;
    document.getElementById('apk-install-target-hint').textContent = 'Target runtimes';
    document.getElementById('apk-install-targets').querySelectorAll('input').forEach(input => { input.disabled = true; });
    choice.ids.forEach(id => { document.getElementById(`apk-target-state-${id}`).textContent = 'Waiting…'; });
    submit.hidden = true; cancel.disabled = true;
    form.onsubmit = event => event.preventDefault();
    dialog.oncancel = event => event.preventDefault();
    const results = await runApkInstall(records, choice.ids, choice.path);
    const completed = results.filter(result => result.status === 'completed').length;
    const failed = results.filter(result => result.status === 'failed').length;
    const skipped = results.filter(result => result.status === 'skipped').length;
    document.getElementById('apk-install-progress-label').textContent = `${completed} completed · ${failed} failed · ${skipped} skipped`;
    if (completed) await Promise.allSettled([refreshApps(), refreshManagedLibrary()]);
  } catch (err) { document.getElementById('apk-install-error').textContent = String(err.message || err); }
  finally {
    dialog.oncancel = dialog.onclose = null;
    form.onsubmit = event => event.preventDefault();
    submit.disabled = true;
    cancel.disabled = false; cancel.textContent = 'Close'; cancel.onclick = () => dialog.close();
    pendingAppAction = false; apkInstallBusy = false;
    renderController(); renderManagedLibraryStatus();
  }
}
btnInstallApk.addEventListener('click', installApk);

// Browsing and refreshing never start Android. Explicit launch/start owns boot.
let appLibraryStarted = false;
function initializeAppLibrary() {
  if (appLibraryStarted) return;
  appLibraryStarted = true;
  loadLibrary();
  // WebKit may synchronously open its storage database on the first read.
  // Restore preferences only after the navigation shell has painted.
  restoreLibraryPreferences();
  loadCachedApps();
  return refreshApps();
}

// P5.3.0: configured file values are deliberately distinct from live readings.
async function refreshSettings() {
  const button = document.getElementById('btn-refresh-settings');
  const panel = document.getElementById('settings-runtime');
  button.disabled = true;
  refreshShortcutPreferences();
  refreshApplicationPreferences();
  refreshRuntimeStartupPreferences();
  const armRefresh = refreshArmSources();
  try {
    const report = await invoke('get_runtime_catalog');
    const selected = report.registry.runtimes.find(r => r.id === report.registry.default_runtime_id);
    if (selected?.id === 'default') { runtimeStartupDefaultName = selected.name || 'Default runtime'; renderRuntimeStartupPreferences(); }
    const settings = report.configured.settings;
    const rows = [['Runtime', selected.name], ['Runtime ID', selected.id],
      ['Image type (configured)', settings.image_kind], ['Renderer (configured)', settings.renderer],
      ['GPU device (configured)', settings.gpu_device || 'Auto / not explicitly set'],
      ['ARM translation (configured)', report.configured.native_bridge || 'Not configured'], ['Android data', selected.data_dir], ['Images', report.configured.images_dir],
      ['System runtime resources', 'Manage GPU, RAM/CPU, ARM and inspect active limits under Runtime > Default runtime.'],
      ['Last settings apply', selected.last_applied ? 'Recorded' : 'No settings applied by AnvilDroid'],
      ['Concurrent runtimes', report.concurrent_supported ? 'Supported' : 'Not available yet']];
    panel.innerHTML = rows.map(([k,v]) => `<div class="status-row"><span class="status-label">${escapeHtml(k)}</span><span class="status-value">${escapeHtml(v)}</span></div>`).join('') +
      `<p class="hint">${escapeHtml(report.management_note)}</p><p class="hint">Configured GPU/renderer values do not verify hardware acceleration.</p>`;
  } catch (e) { panel.textContent = `Cannot load runtime configuration: ${e}`; }
  finally { await armRefresh; button.disabled = false; }
}
document.getElementById('btn-refresh-settings').addEventListener('click', refreshSettings);

async function refreshShortcutPreferences() {
  const checkbox = document.getElementById('automatic-shortcuts');
  if (shortcutPreferenceSaving) return;
  checkbox.disabled = true;
  try {
    checkbox.checked = await tauriInvoke('get_shortcut_preferences');
    checkbox.disabled = false;
  } catch (error) { showAppToast(`Cannot load shortcut preferences: ${error.message || error}`, 'error'); }
}
const applicationPreferenceFields = {'minimize-to-panel': 'minimize_to_panel', 'auto-startup': 'auto_startup', 'startup-minimize': 'startup_minimize'};
let applicationPreferenceSaving = false;
async function refreshApplicationPreferences() {
  if (applicationPreferenceSaving) return;
  const controls = Object.keys(applicationPreferenceFields).map(id => document.getElementById(id));
  controls.forEach(control => { control.disabled = true; });
  try {
    const prefs = await tauriInvoke('get_application_preferences');
    controls.forEach(control => {
      control.checked = !!prefs[applicationPreferenceFields[control.id]];
      control.disabled = false;
    });
  } catch (error) { showAppToast(`Cannot load application preferences: ${error.message || error}`, 'error'); }
}
for (const [id, preference] of Object.entries(applicationPreferenceFields)) {
  document.getElementById(id).addEventListener('change', async event => {
    if (applicationPreferenceSaving) return;
    const checkbox = event.target;
    const enabled = checkbox.checked;
    applicationPreferenceSaving = true;
    Object.keys(applicationPreferenceFields).forEach(id => { document.getElementById(id).disabled = true; });
    try {
      const prefs = await tauriInvoke('set_application_preference', {preference, enabled});
      for (const [id, key] of Object.entries(applicationPreferenceFields)) {
        document.getElementById(id).checked = !!prefs[key];
      }
      showAppToast('Preference saved.', 'success');
    } catch (error) {
      checkbox.checked = !enabled;
      showAppToast(`Cannot save application preference: ${error.message || error}`, 'error');
    } finally {
      applicationPreferenceSaving = false;
      Object.keys(applicationPreferenceFields).forEach(id => { document.getElementById(id).disabled = false; });
    }
  });
}
let shortcutPreferenceSaving = false;
document.getElementById('automatic-shortcuts').addEventListener('change', async event => {
  if (shortcutPreferenceSaving) return;
  const checkbox = event.target;
  const enabled = checkbox.checked;
  shortcutPreferenceSaving = true;
  checkbox.disabled = true;
  try {
    await tauriInvoke('set_automatic_shortcuts', {enabled});
    showAppToast('Shortcut preference saved.', 'success');
  } catch (error) {
    checkbox.checked = !enabled;
    showAppToast(`Cannot save shortcut preference: ${error.message || error}`, 'error');
  } finally { shortcutPreferenceSaving = false; checkbox.disabled = false; }
});

// Boot preferences use the lightweight inventory; opening Settings does not
// load GPU/ARM/storage details or start an Android runtime.
let runtimeStartupRecords = [], runtimeStartupLoading = false, runtimeStartupError = '';
let runtimeStartupDefaultName = 'Default runtime';
const runtimeStartupSaving = new Set();
function renderRuntimeStartupPreferences() {
  const panel = document.getElementById('runtime-startup-list');
  const update = markup => { if (panel._startupMarkup !== markup) { panel.innerHTML = markup; panel._startupMarkup = markup; } };
  // Default is the system Waydroid adapter, not a controller registry entry.
  // Show its separate startup mechanism without pretending a managed-runtime
  // flag can control the system service or start an Android login session.
  const defaultRow = `<tr class="runtime-summary"><td><span class="runtime-startup-name">${escapeHtml(runtimeStartupDefaultName)}</span></td><td><span class="runtime-startup-system" title="Default runtime uses waydroid-container.service. Android starts when you launch it from your desktop.">Waydroid service</span></td></tr>`;
  const rows = runtimeStartupRecords.map(record => {
    const busy = runtimeStartupLoading || runtimeStartupSaving.size > 0 || record.deletion_pending || ['Provisioning', 'Starting', 'Stopping'].includes(record.state) || record.job?.status === 'Running';
    return `<tr class="runtime-summary"><td><span class="runtime-startup-name">${escapeHtml(record.name)}</span></td><td><label class="shortcut-preference"><input type="checkbox" data-runtime-startup="${escapeHtml(record.id)}" aria-label="Start ${escapeHtml(record.name)} on boot" ${record.start_on_boot ? 'checked' : ''} ${busy ? 'disabled' : ''}></label></td></tr>`;
  }).join('');
  const message = runtimeStartupError || (runtimeStartupLoading && !runtimeStartupRecords.length ? 'Loading runtimes…' : '');
  const markup = `<div class="runtime-table-scroll"><table class="runtime-table runtime-startup-table"><thead><tr><th>Runtime</th><th>Start on boot</th></tr></thead><tbody>${defaultRow}${rows}</tbody></table></div>${message ? `<p class="hint" role="status">${escapeHtml(message)}</p>` : ''}`;
  update(markup);
}
async function refreshRuntimeStartupPreferences() {
  if (runtimeStartupLoading || runtimeStartupSaving.size) return;
  runtimeStartupLoading = true; runtimeStartupError = '';
  renderRuntimeStartupPreferences();
  try {
    const records = await controllerRequest({op:'list'});
    if (!Array.isArray(records) || records.some(record => !/^r-[a-f0-9]{32}$/.test(record.id) || typeof record.start_on_boot !== 'boolean')) throw new Error('Invalid runtime startup inventory');
    runtimeStartupRecords = records;
  } catch (error) { runtimeStartupRecords = []; runtimeStartupError = `Cannot load runtimes: ${error.message || error}`; }
  finally { runtimeStartupLoading = false; renderRuntimeStartupPreferences(); }
}
async function changeRuntimeStartupPreference(event) {
  const input = event.target;
  const id = input.dataset?.runtimeStartup;
  if (!id || runtimeStartupLoading || runtimeStartupSaving.size) return;
  const record = runtimeStartupRecords.find(record => record.id === id);
  if (!record) return;
  const enabled = input.checked, previous = record.start_on_boot;
  runtimeStartupSaving.add(id);
  record.start_on_boot = enabled;
  renderRuntimeStartupPreferences();
  try {
    const result = await controllerRequest({op:'start_on_boot', id, enabled});
    if (result?.id !== id || result.start_on_boot !== enabled) throw new Error('Invalid runtime startup response');
    Object.assign(record, result);
    const cached = runtimeRecord(id);
    if (cached) cached.start_on_boot = enabled;
    showAppToast('Startup preference saved.', 'success');
  } catch (error) {
    record.start_on_boot = previous;
    showAppToast(`Cannot save startup preference: ${error.message || error}`, 'error');
  } finally { runtimeStartupSaving.delete(id); renderRuntimeStartupPreferences(); }
}
document.getElementById('runtime-startup-list').addEventListener('change', changeRuntimeStartupPreference);

// Controller requests bypass the legacy default-runtime wrapper deliberately.
let controllerRecords = [];
let selectedRuntimeId = null;
let runtimeSettingsId = null;
let controllerLoading = false;
let controllerRefreshDone = Promise.resolve();
let finishControllerRefresh = null;
let controllerMutation = false;
let reinstallTargetId = null;
let controllerTimer = null;
let runtimeUsageTimer = null, runtimeUsagePolling = false;
let controllerError = '';
let controllerPending = null;
let controllerRequirements = null;
let controllerSetupError = '';
let waydroidInitBusy = false;
let waydroidInitPolling = false;
let renameRuntimeId = null;
let storageDialogTarget = null;
const runtimeHealth = new Map();
const runtimeGraphics = new Map();
const runtimeSettingsOpen = new Set();
const runtimeBatchSelection = new Set();
let runtimeBatchBusy = false, runtimeBatchPlan = null;
document.addEventListener('toggle', event => {
  const key = event.target.dataset?.settingsKey;
  if (!key || !event.target.isConnected) return;
  event.target.open ? runtimeSettingsOpen.add(key) : runtimeSettingsOpen.delete(key);
}, true);
const runtimeArmDraft = new Map();
const runtimeGpuDraft = new Map();
const runtimeResourceDraft = new Map();
const runtimeGoogle = new Map();
let googleRegistrationTarget = null;
const googlePlayAcknowledged = new Map();

function googlePlayWorks(id) {
  let saved = googlePlayAcknowledged.get(id);
  if (saved === undefined) {
    try { saved = JSON.parse(localStorage.getItem(`anvildroid_google_play_works:${id}`) || 'null'); }
    catch (_) { saved = null; }
    googlePlayAcknowledged.set(id, saved);
  }
  const currentId = runtimeGoogle.get(id)?.report?.gsf_android_id;
  return saved?.confirmed === true && !(saved.gsfId && currentId && saved.gsfId !== currentId);
}

function setGooglePlayWorks(id, confirmed) {
  const value = {confirmed, gsfId:runtimeGoogle.get(id)?.report?.gsf_android_id || null};
  googlePlayAcknowledged.set(id, value);
  try { localStorage.setItem(`anvildroid_google_play_works:${id}`, JSON.stringify(value)); }
  catch (_) { showAppToast('Preference saved for this session only.', 'info'); }
  renderGoogleNotices();
  if (googleRegistrationTarget) renderGoogleRegistrationDialog();
  else renderController();
}

function googleRegistrationRecord(id) {
  return (id === 'default' ? {id, name:'Default runtime', state:libraryDefaultState, display:{mode:'desktop'}}
    : managedLibrary.find(record => record.id === id)) || runtimeRecord(id);
}

function renderGoogleNotices() {
  const records = [{id:'default', name:'Default runtime'}, ...managedLibrary];
  const markup = records.filter(record => {
    if (!libraryRuntimeSelected(record.id) || !libraryRuntimeRunning(record.id) || googlePlayWorks(record.id)) return false;
    const apps = record.id === 'default' ? allApps : managedSnapshots.get(record.id) || [];
    return record.image_selection?.flavor === 'GAPPS' || apps.some(app => app.package === 'com.android.vending') ||
      runtimeGoogle.get(record.id)?.report?.packages?.['com.google.android.gsf'];
  }).map(record => `<div class="google-registration-notice"><div><strong>${escapeHtml(record.name)} · Google Play setup reminder</strong><p>Play Store working? Dismiss this reminder. If it reports an uncertified device, open registration help. Registration not verified automatically.</p></div><div class="runtime-actions"><button type="button" class="btn btn-primary" data-google-registration="${escapeHtml(record.id)}">Register device / view ID</button><button type="button" class="btn btn-quiet" data-google-working="${escapeHtml(record.id)}">Google Play works — hide reminder</button></div></div>`).join('');
  const panel = document.getElementById('library-google-notices');
  if (panel.innerHTML !== markup) panel.innerHTML = markup;
}

function renderGoogleRegistrationDialog() {
  if (!googleRegistrationTarget) return;
  const record = googleRegistrationRecord(googleRegistrationTarget);
  document.getElementById('google-registration-content').innerHTML = record
    ? `<h3>${escapeHtml(record.name)}</h3>${renderRuntimeGoogle(record)}`
    : '<p role="status">Runtime unavailable. Close this dialog and refresh Apps.</p>';
}

document.getElementById('library-google-notices').addEventListener('click', async event => {
  const working = event.target.closest('[data-google-working]');
  if (working?.dataset.googleWorking) { setGooglePlayWorks(working.dataset.googleWorking, true); return; }
  const button = event.target.closest('[data-google-registration]');
  if (!button) return;
  googleRegistrationTarget = button.dataset.googleRegistration;
  renderGoogleRegistrationDialog();
  document.getElementById('google-registration-dialog').showModal();
  await checkRuntimeGoogle(googleRegistrationTarget);
});
document.getElementById('google-registration-close').addEventListener('click', () => {
  document.getElementById('google-registration-dialog').close();
  googleRegistrationTarget = null;
});
document.getElementById('google-registration-dialog').addEventListener('close', () => { googleRegistrationTarget = null; });
function controllerBusy(error) {
  // The current Rust transport serializes controller errors as message strings.
  const message = String(error?.message || error);
  return error?.code === 'BUSY' || /(?:Runtime already has an operation|Wait for the runtime operation to finish|Runtime operation is completing|Controller operation capacity reached)/.test(message);
}
const controllerRequest = async request => {
  if (request.id === 'default' && request.op === 'app_action' && request.action !== 'keymap_edit') {
    const command = {launch:'launch_app', force_stop:'force_stop_app'}[request.action];
    if (!command) throw new Error('Unsupported Default runtime app action');
    const result = await invoke(command, {package:request.package});
    if (!result?.success) throw new Error(result?.message || 'Android app action failed');
    return {runtime_id:'default', action:request.action, package:request.package, status:'Succeeded', result};
  }
  return tauriInvoke('runtime_controller', { request });
};
let existingRuntime = null, existingRuntimeError = '', existingRuntimeLoading = false;
function runtimeRecord(id) {
  return id === 'default' ? existingRuntime : controllerRecords.find(record => record.id === id);
}
async function refreshExistingRuntime({light = false} = {}) {
  if (existingRuntimeLoading) return;
  existingRuntimeLoading = true;
  try {
    const record = await controllerRequest({op:'refresh', id:'default'});
    if (record?.id !== 'default') throw new Error('Invalid Default runtime response');
    const previous = existingRuntime;
    if (currentPage === 'runtime' && !previous) {
      existingRuntime = {...record, detailsLoading:true};
      renderController();
    }
    await Promise.all(['resources','gpu','arm','storage'].map(async kind => {
      if (light && ['gpu', 'arm'].includes(kind) && previous?.[kind] && previous.state === record.state && !previous.unavailable) { record[kind] = previous[kind]; return; }
      try { record[kind] = await controllerRequest({op:kind + '_info', id:'default'}); if (kind === 'resources') RuntimeUsage.sample(record); }
      catch (error) { record[kind + 'Error'] = String(error); }
    }));
    invalidateRuntimeGraphics(existingRuntime, record);
    existingRuntime = record;
    existingRuntimeError = '';
  } catch (error) {
    // Keep saved settings visible, but disable edits until ownership/status
    // can be verified again (for example while Waydroid is stopping).
    if (existingRuntime) existingRuntime = {...existingRuntime, unavailable: true};
    existingRuntimeError = String(error);
  } finally {
    existingRuntimeLoading = false;
    renderImageSourceOptions();
  }
}
function renderExistingRuntime() {
  const panel = document.getElementById('existing-runtime-management');
  const record = existingRuntime;
  // Lifecycle commands have their own session checks in the backend. Loading
  // management settings (or failing to authorize those settings) must not lock
  // Start/Stop/Recover, including while another runtime is stopping.
  [btnStart, btnStop, btnRecover].forEach(button => {
    button.disabled = runtimeOperationBusy('default');
  });
  if (!record) {
    document.getElementById('existing-runtime-usage').innerHTML = renderRuntimeUsage({id:'default'});
    panel._runtimeMarkup = null;
    panel.innerHTML = `<p class="runtime-blocked">${escapeHtml(existingRuntimeError || 'Refresh to load Default runtime management.')}</p>`;
    return;
  }
  document.getElementById('existing-runtime-name').textContent = record.name + ' · Default';
  document.getElementById('existing-runtime-label').textContent = record.name;
  document.getElementById('existing-runtime-usage').innerHTML = renderRuntimeUsage(record.unavailable ? {id:record.id} : record);
  updateDefaultRuntimeBadge(record.unavailable ? 'Unavailable' : record.state, existingRuntimeError);
  if (!record.unavailable) updateRuntimeButtons(record.state);
  document.querySelectorAll('#legacy-runtime-row .runtime-action-icon').forEach(button => button.setAttribute('aria-label', button.title + ' · ' + record.name));
  const busy = runtimeOperationBusy('default') || apkInstallBusy || !record.managed || record.unavailable || record.detailsLoading;
  const button = (op,label,disabled=false) => `<button class="btn btn-quiet" data-controller-op="${op}" data-controller-id="default" ${disabled ? 'disabled' : ''}>${label}</button>`;
  const markup = `${existingRuntimeError ? `<p class="runtime-blocked">${escapeHtml(existingRuntimeError)}</p>` : ''}<div class="runtime-actions runtime-settings-actions">${!record.managed ? `<p class="hint">Start Default runtime, then enable management once to keep its settings available after stopping. App data stays in place.</p>${button('existing_claim','Enable management',controllerMutation || record.unavailable || record.state !== 'Running')}` : `${button('rename','Rename…',controllerMutation || record.unavailable)}${button('reinstall','Reinstall runtime…',busy || !['Stopped','Running','Frozen'].includes(record.state))}`}</div>
    <details class="runtime-settings-group" data-settings-key="${escapeHtml(record.id)}:gpu" ${runtimeSettingsOpen.has(record.id + ':gpu') ? 'open' : ''}><summary>Graphics</summary><div class="runtime-gpu-panel runtime-primary-setting" id="runtime-gpu-default">${renderGpuControls(record,busy)}<div class="runtime-actions">${button('gpu_check','Check GPU',controllerMutation)}</div>${renderRuntimeGraphics('default')}</div></details>
    <div class="runtime-config-grid">
      <details class="runtime-settings-group" data-settings-key="${escapeHtml(record.id)}:storage" ${runtimeSettingsOpen.has(record.id + ':storage') ? 'open' : ''}><summary>Storage</summary><div class="runtime-storage-panel"><p class="runtime-storage-path">${escapeHtml(record.storage?.path || record.storageError || 'Unavailable')}</p>${record.storage ? `<p>Disk free: ${(record.storage.free_bytes / 1024**3).toFixed(2)} / ${(record.storage.total_bytes / 1024**3).toFixed(2)} GiB</p>` : ''}<p class="hint">Shared filesystem; no disk quota.</p></div></details>
      <details class="runtime-settings-group" data-settings-key="${escapeHtml(record.id)}:arm" ${runtimeSettingsOpen.has(record.id + ':arm') ? 'open' : ''}><summary>ARM translation</summary><div class="runtime-arm-panel">${renderRuntimeArm(record,busy)}</div></details>
      <div class="runtime-settings-list runtime-settings-visible">
        <details class="runtime-setting-row" data-settings-key="${escapeHtml(record.id)}:resources" ${runtimeSettingsOpen.has(record.id + ':resources') ? 'open' : ''}><summary><span><strong>Performance</strong><small>RAM and CPU limits</small></span></summary><div class="runtime-setting-body"><div class="runtime-resources-panel">${renderResourceControls(record,busy)}</div></div></details>
        <details class="runtime-setting-row" data-settings-key="${escapeHtml(record.id)}:google" ${runtimeSettingsOpen.has(record.id + ':google') ? 'open' : ''}><summary><span><strong>Google Play</strong></span></summary><div class="runtime-setting-body"><div class="runtime-google-panel">${renderRuntimeGoogle(record)}</div></div></details>
        <details class="runtime-setting-row" data-settings-key="${escapeHtml(record.id)}:health" ${runtimeSettingsOpen.has(record.id + ':health') ? 'open' : ''}><summary><span><strong>Diagnostics</strong></span></summary><div class="runtime-setting-body"><div class="runtime-health-panel">${button('health','Check health',controllerMutation)}${renderRuntimeHealth('default')}</div></div></details>
        <details class="runtime-setting-row" data-settings-key="${escapeHtml(record.id)}:import" ${runtimeSettingsOpen.has(record.id + ':import') ? 'open' : ''}><summary><span><strong>Managed copy</strong><small>Import existing Android data</small></span></summary><div class="runtime-setting-body"><div class="runtime-import-panel"><h4>Full isolated management</h4><p class="hint">Copy images, apps and data to a separate runtime. Stop this runtime first; the original is kept.</p>${button('existing_import','Import managed copy…',busy || record.state !== 'Stopped')}</div></div></details>
      </div>
    </div>`;
  if (panel._runtimeMarkup !== markup) { panel.innerHTML = markup; panel._runtimeMarkup = markup; }
}

let imageCatalogBusy = false;
let imageCatalogTimer = null;
let imageCatalogState = null;
let imageDownloadState = null;
let imageDownloadBusy = false;
let imageDownloadTimer = null;
let imageDownloadEpoch = 0;
let selectedImageLibraryId = '';
let armSourceState = {sources: [], busy: false, error: '', url:'', sha256:'', engine:'libndk', version:''};
let armSourcesRefresh = null;
function refreshArmSources({light = false} = {}) {
  if (armSourcesRefresh) return armSourcesRefresh;
  if (light && armSourceState.sources?.length && !armSourceState.error) {
    renderArmSources();
    return Promise.resolve();
  }
  armSourcesRefresh = (async () => {
    try {
      const sources = await controllerRequest({op:'arm_sources'});
      if (!Array.isArray(sources)) throw new Error('Invalid ARM source list');
      armSourceState.sources = sources;
      armSourceState.error = '';
    } catch (error) {
      armSourceState.error = `Cannot load ARM sources: ${error}`;
    } finally {
      renderArmSources();
    }
  })().finally(() => { armSourcesRefresh = null; });
  return armSourcesRefresh;
}
let customUrlState = null;
let customUrlTimer = null;
const imageDownloadActive = () => ['Connecting', 'Downloading'].includes(customUrlState?.status) || ['Downloading', 'Verifying', 'Cancelling'].includes(imageDownloadState?.status);
const IMAGE_FLAVOR_NOTE = flavor => flavor === 'GAPPS'
  ? 'Includes Play Store, Google Play services and Google Services Framework.'
  : 'Android without Google services or Play Store.';
function renderImageCatalog(state) {
  if (!state || !['Idle', 'Checking', 'Ready', 'Failed'].includes(state.status)) throw new Error('Invalid image catalog response');
  imageCatalogState = state;
  const button = document.getElementById('btn-check-images');
  button.disabled = state.status === 'Checking';
  button.textContent = state.status === 'Checking' ? 'Checking official images…' : 'Check official images';
  const panel = document.getElementById('settings-images');
  let message = state.status === 'Checking' ? '<p class="runtime-feedback">Contacting official Waydroid OTA channels…</p>' : '';
  if (state.status === 'Failed') message = `<p class="runtime-feedback error">Image source check failed: ${escapeHtml(state.error || 'Unknown error')}. Retry after 30 seconds.</p>`;
  if (state.report) {
    const report = state.report;
    message += `<p class="hint">${state.status === 'Ready' ? 'Checked' : 'Previous result (not current)'}: ${escapeHtml(new Date(report.checked_at * 1000).toLocaleString())} · ${escapeHtml(report.architecture)}</p>`;
    message += `<p class="hint">${report.variants.length} official image pair(s) found. Download actions are available in Available images above.</p>`;
  }
  panel.innerHTML = message || '<p class="hint">No online check performed.</p>';
  renderAvailableImages();
}
async function checkOfficialImages(refresh = true) {
  if (imageCatalogBusy) return;
  imageCatalogBusy = true;
  clearTimeout(imageCatalogTimer);
  const button = document.getElementById('btn-check-images');
  button.disabled = true;
  button.textContent = 'Checking official images…';
  try {
    const state = await controllerRequest({op:'image_catalog', refresh});
    renderImageCatalog(state);
    if (state.status === 'Checking') imageCatalogTimer = setTimeout(() => checkOfficialImages(false), 1000);
  } catch (error) {
    document.getElementById('settings-images').textContent = `Cannot check official images: ${error}`;
    button.disabled = false;
    button.textContent = 'Check official images';
  } finally { imageCatalogBusy = false; }
}
document.getElementById('btn-check-images').addEventListener('click', () => checkOfficialImages(true));
document.getElementById('btn-import-images').addEventListener('click', () => {
  const panel = document.getElementById('image-library-import');
  panel.hidden = !panel.hidden;
  document.getElementById('btn-import-images').setAttribute('aria-expanded', String(!panel.hidden));
  if (!panel.hidden) {
    panel.scrollIntoView({block:'center', behavior:'smooth'});
    document.getElementById('btn-import-runtime-folder').focus({preventScroll:true});
  }
});
function renderImageDownload(state) {
  if (!state || !['Idle', 'Downloading', 'Verifying', 'Cancelling', 'Ready', 'Failed', 'Cancelled'].includes(state.status)) throw new Error('Invalid image download response');
  imageDownloadState = state;
  renderImageSourceOptions();
  renderAvailableImages();
  const gib = bytes => (bytes / 1024**3).toFixed(2);
  const total = Number(state.download_bytes);
  const received = Math.max(0, Number(state.received_bytes) || 0);
  const progress = `<progress aria-label="Image download" ${total > 0 ? `max="${total}" value="${received}"` : ''}></progress>`;
  const percent = total > 0 ? `${Math.min(100, received / total * 100).toFixed(1)}% · ` : '';
  const panel = document.getElementById('settings-image-download');
  let message = `<p class="hint">Image cache: ${escapeHtml(state.cache_path)} · ${gib(state.free_bytes)} GiB free</p>`;
  if (state.status !== 'Idle') {
    message += `<p><strong>${escapeHtml(state.flavor)} · ${state.status === 'Ready' ? 'Downloaded · not installed' : escapeHtml(state.status)}</strong></p>`;
    if (imageDownloadActive()) message += `<p>${escapeHtml(state.phase)}</p>${progress}<p class="hint">${percent}${gib(state.received_bytes)} / ${gib(state.download_bytes)} GiB downloaded</p>`;
    if (state.status === 'Ready') message += `<p class="setup-ready">System and vendor archives downloaded and SHA-256 verified.</p><p class="hint">Unpacked images: ${gib(state.image_bytes)} GiB, plus runtime data and preparation space. Select ${escapeHtml(state.flavor)} when creating a runtime, then Prepare to build compatible Android support. New compatible runtimes enable ARM translation during Prepare.</p>`;
    if (state.error) message += `<p class="runtime-feedback ${state.status === 'Failed' ? 'error' : 'warning'}">${escapeHtml(state.error)}</p><p class="hint">Completed archives stay cached. Download again to verify and reuse them; partial files are removed.</p>`;
  }
  if (state.flavor === 'CUSTOM') {
    message = `<p><strong>Custom image import · ${escapeHtml(state.status)}</strong></p><p>${escapeHtml(state.error || state.phase || '')}</p>`;
    if (imageDownloadActive()) message += `${progress}<p class="hint">${percent}${gib(received)} GiB processed</p>`;
    if (state.status === 'Ready') message += '<p class="hint">Local ZIP inspected and SHA-256 recorded, not official source verification. Select Custom image ZIP when creating a runtime, then Prepare.</p>';
  }
  if (imageDownloadActive()) message += '<p class="hint">Image actions become available when this download or import finishes.</p>';
  panel.innerHTML = ['Downloading', 'Verifying', 'Cancelling', 'Failed'].includes(state.status) || state.error ? message : '';
  const cancel = document.getElementById('btn-cancel-image-download');
  cancel.hidden = !state.can_cancel;
  cancel.disabled = imageDownloadBusy;
  renderCreateImageHint();
  updateWaydroidSetupActions();
}
function renderImageSourceOptions() {
  const select = document.getElementById('controller-image');
  if (!select) return;
  const previous = select.value;
  const images = imageDownloadState?.available_images || [];
  const options = [];
  if (existingRuntime && !existingRuntime.unavailable) {
    options.push('<option value="installed">Existing Waydroid image</option>');
  }
  for (const item of images) {
    const value = item.flavor === 'CUSTOM' ? `custom:${item.library_id}` : item.flavor;
    const label = item.flavor === 'CUSTOM'
      ? (item.custom_name || `Custom image · ${String(item.library_id || '').slice(0, 8)}`)
      : `Waydroid ${item.flavor}`;
    if (item.flavor === 'CUSTOM' || !options.some(option => option.includes(`value="${value}"`))) {
      options.push(`<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`);
    }
  }
  select.innerHTML = options.length ? options.join('') : '<option value="" disabled>No available images</option>';
  if (options.some(option => option.includes(`value="${previous}"`))) select.value = previous;
  else if (previous === 'CUSTOM') {
    const latest = [...images].reverse().find(item => item.flavor === 'CUSTOM');
    if (latest) select.value = `custom:${latest.library_id}`;
    else if (options.length) select.selectedIndex = 0;
  }
  else if (options.length) select.selectedIndex = 0;
  const chosen = select.value.startsWith('custom:') ? select.value.slice(7) : '';
  selectedImageLibraryId = chosen;
  select.disabled = !options.length;
}
function selectedImageEntry() {
  const flavor = document.getElementById('controller-image')?.value || '';
  if (flavor.startsWith('custom:')) {
    return (imageDownloadState?.available_images || []).find(item => item.library_id === flavor.slice(7)) || null;
  }
  return (imageDownloadState?.available_images || []).find(item => item.flavor === flavor) || null;
}
function imageActionButton(action, attribute, icon, disabled, primary = false) {
  const paths = {
    create: '<path d="m12 2 9 5v10l-9 5-9-5V7zM3 7l9 5 9-5M12 12v10m-3-9h6m-3-3v6"/>',
    reinstall: '<path d="M3 11a9 9 0 1 1 2.6 7M3 4v7h7"/>',
    rename: '<path d="m16 3 5 5-12 12-6 1 1-6zM14 5l5 5"/>',
    download: '<path d="M12 3v12m-5-5 5 5 5-5M3 16v5h18v-5"/>',
  };
  return `<button type="button" class="btn runtime-action-icon${primary ? ' btn-primary' : ''}" ${attribute} title="${escapeHtml(action)}" aria-label="${escapeHtml(action)}" ${disabled ? 'disabled' : ''}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[icon]}</svg></button>`;
}
function renderAvailableImages() {
  const images = imageDownloadState?.available_images || [];
  const panel = document.getElementById('available-runtime-images');
  const official = imageCatalogState?.report?.variants || [];
  const officialRows = official.filter(variant => !images.some(item => item.flavor === variant.flavor &&
    item.images?.system?.sha256 === variant.system.sha256 && item.images?.vendor?.sha256 === variant.vendor.sha256))
    .map(variant => ({official: variant, flavor: variant.flavor, image_bytes: variant.download_bytes}));
  if (!images.length && !officialRows.length) { panel.innerHTML = '<p class="hint">No images yet. Import a ZIP/folder or check official images below.</p>'; return; }
  const busy = controllerMutation;
  const target = runtimeRecord(selectedRuntimeId);
  const canReinstall = target && ['Allocated', 'Prepared', 'Stopped', 'Error'].includes(target.state) && (target.id !== 'default' || target.managed);
  panel.innerHTML = `<div class="runtime-table-scroll"><table class="runtime-table image-catalog-table"><thead><tr><th>Image</th><th>Source</th><th>Size</th><th>Actions</th></tr></thead><tbody>${images.concat(officialRows).map(item => {
    if (item.official) {
      const variant = item.official;
      return `<tr class="image-not-cached"><td><strong>Waydroid ${escapeHtml(variant.flavor)}</strong><br><span class="hint">${escapeHtml(variant.system.version)} · ${escapeHtml(imageCatalogState.report.architecture)}</span></td><td>Official<br><span class="hint">Not downloaded</span></td><td>${(variant.download_bytes / 1024**3).toFixed(2)} GiB</td><td>${imageActionButton('Download Waydroid ' + variant.flavor, `data-download-flavor="${escapeHtml(variant.flavor)}"`, 'download', busy || imageDownloadBusy || imageDownloadActive() || !variant.matching_versions, true)}</td></tr>`;
    }
    const label = item.flavor === 'CUSTOM' ? (item.custom_name || 'Custom image ' + item.library_id.slice(0, 8)) : 'Waydroid ' + item.flavor;
    return `<tr><td><strong>${escapeHtml(label)}</strong><details><summary>Image details</summary>${['system', 'vendor'].map(kind => `<p class="hint">${kind}: ${escapeHtml(item.images[kind].filename)}<br>SHA-256: <code>${escapeHtml(item.images[kind].sha256)}</code></p>`).join('')}</details></td><td>${item.flavor === 'CUSTOM' ? 'Imported' : 'Official'}</td><td>${(item.image_bytes / 1024**3).toFixed(2)} GiB</td><td><div class="runtime-actions">${imageActionButton('Create runtime · ' + label, `data-image-use="${escapeHtml(item.library_id)}"`, 'create', busy, true)}${imageActionButton(canReinstall ? 'Reinstall ' + target.name + ' · ' + label : 'Select a stopped runtime to reinstall', `data-image-reinstall="${escapeHtml(item.library_id)}"`, 'reinstall', busy || !canReinstall)}${item.flavor === 'CUSTOM' ? imageActionButton('Rename · ' + label, `data-image-rename="${escapeHtml(item.library_id)}"`, 'rename', busy) : ''}</div></td></tr>`;
  }).join('')}</tbody></table></div>`;
}
document.getElementById('available-runtime-images').addEventListener('click', async event => {
  const download = event.target.closest('[data-download-flavor]');
  if (download && !download.disabled) { startImageDownload(download.dataset.downloadFlavor); return; }
  const button = event.target.closest('[data-image-use], [data-image-reinstall]');
  const rename = event.target.closest('[data-image-rename]');
  if (rename && !rename.disabled) {
    const item = (imageDownloadState?.available_images || []).find(entry => entry.library_id === rename.dataset.imageRename);
    const name = await AppDialog.prompt('Custom image name', item?.custom_name || 'Custom image', {title:'Rename image', okLabel:'Save name'});
    if (!name?.trim()) return;
    try {
      renderImageDownload(await controllerRequest({op:'image_rename', image_id:rename.dataset.imageRename, name:name.trim()}));
      showAppToast('Image name saved.', 'success');
    } catch (error) { showAppToast(`Cannot rename image: ${error}`, 'error'); }
    return;
  }
  if (!button || button.disabled || runtimeBatchBusy || imageDownloadBusy || imageDownloadActive()) return;
  const targetId = selectedRuntimeId;
  imageDownloadBusy = true;
  renderAvailableImages();
  try {
    const state = await controllerRequest({op:'image_select', image_id:button.dataset.imageUse || button.dataset.imageReinstall});
    if (button.dataset.imageReinstall) beginReinstall(targetId);
    else cancelReinstall();
    // Custom images use their library id as the select value; `CUSTOM` is
    // only the backend flavor and is not an actual option in this select.
    const selectedValue = state.flavor === 'CUSTOM'
      ? `custom:${button.dataset.imageUse || button.dataset.imageReinstall}`
      : state.flavor;
    renderImageDownload(state);
    document.getElementById('controller-image').value = selectedValue;
    selectedImageLibraryId = selectedValue.startsWith('custom:') ? selectedValue.slice(7) : '';
    renderCreateImageHint();
    navigatePage('runtime', false);
    const panel = document.getElementById('runtime-create-panel');
    panel.open = true;
    panel.scrollIntoView({block:'center', behavior:'smooth'});
    document.getElementById('controller-name').focus();
  } catch (error) {
    showAppToast(`Cannot select image: ${error}`, 'error');
  } finally { imageDownloadBusy = false; renderAvailableImages(); renderCreateImageHint(); }
});
function renderArmSources(force = false) {
  const panel = document.getElementById('arm-source-panel');
  if (!panel) return;
  const sources = armSourceState.sources || [];
  const versions = sources.filter(source => (source.engine || 'libndk') === armSourceState.engine);
  const selected = versions.find(source => source.sha256 === armSourceState.version) || versions[0];
  const status = armSourceState.error ? `<p class="runtime-feedback error" role="alert">${escapeHtml(armSourceState.error)}</p>` : armSourceState.busy
    ? '<p role="status">Preparing ARM source… Downloading or reading files, then validating payload.</p><progress aria-label="Preparing ARM source"></progress>'
    : '';
  const list = sources.filter(source => source.installed).length ? `<details class="arm-source-list"><summary>Downloaded versions</summary>${sources.filter(source => source.installed).map(source => `<div class="arm-source-item"><span>${escapeHtml(source.label || source.provider)}<br><small>${escapeHtml(source.sha256.slice(0,12))}</small></span><span class="hint">${(Number(source.bytes || 0) / 1024 ** 2).toFixed(1)} MiB</span></div>`).join('')}</details>` : '';
  const markup = `<div class="arm-provider-fields"><label>Engine<select data-arm-catalog="engine"><option value="libndk" ${armSourceState.engine === 'libndk' ? 'selected' : ''}>libndk_translation</option><option value="libhoudini" ${armSourceState.engine === 'libhoudini' ? 'selected' : ''}>libhoudini</option></select></label><label>Version<select data-arm-catalog="version">${versions.map(source => `<option value="${escapeHtml(source.sha256)}" ${source === selected ? 'selected' : ''}>${escapeHtml(source.version || source.label)}</option>`).join('')}</select></label></div><button class="btn btn-primary" data-arm-source="builtin" data-arm-version="${escapeHtml(selected?.sha256 || '')}" ${armSourceState.busy || !selected || selected.installed ? 'disabled' : ''}>${selected?.installed ? 'Downloaded' : 'Download selected version'}</button><p class="hint">${escapeHtml(selected?.android || 'Select a version')}. Source: supremegamers GitHub. Catalog archives are SHA-256 pinned.</p><details class="arm-custom-import"><summary>Import custom version from URL, ZIP or folder</summary><div class="arm-source-form"><label>HTTPS archive URL<input id="arm-source-url" value="${escapeHtml(armSourceState.url)}" type="url" placeholder="https://…/libndk-translation.zip" spellcheck="false"></label><label>SHA-256 (optional for custom source)<input id="arm-source-sha256" value="${escapeHtml(armSourceState.sha256)}" type="text" inputmode="text" placeholder="64 hex characters" spellcheck="false"></label></div><div class="arm-source-actions"><button class="btn btn-quiet" data-arm-source="url" ${armSourceState.busy ? 'disabled' : ''}>Download URL</button><button class="btn btn-quiet" data-arm-source="archive" ${armSourceState.busy ? 'disabled' : ''}>Import archive…</button><button class="btn btn-quiet" data-arm-source="folder" ${armSourceState.busy ? 'disabled' : ''}>Import folder…</button></div><p class="hint">Accepted payload must contain <code>libndk_translation.so</code> or <code>libhoudini.so</code> under both <code>system/lib</code> and <code>system/lib64</code>. Installer scripts are never executed. Custom sources are experimental; verify publisher and checksum.</p></details>${status}${list}`;
  if (panel._armMarkup !== markup && (force || !panel.contains?.(document.activeElement))) { panel.innerHTML = markup; panel._armMarkup = markup; }
}
async function importArmSource(kind, version = '') {
  if (armSourceState.busy) return;
  let source = kind === 'builtin' ? version : '', expectedSha256 = '';
  if (kind === 'url') {
    source = document.getElementById('arm-source-url')?.value.trim() || '';
    expectedSha256 = document.getElementById('arm-source-sha256')?.value.trim() || '';
    if (!source) { armSourceState.error = 'Enter an HTTPS ARM translation archive URL.'; renderArmSources(); return; }
  } else if (kind !== 'builtin') {
    try { source = await window.__TAURI__.dialog.open(kind === 'folder'
      ? {multiple:false, directory:true, title:'Choose ARM translation payload folder'}
      : {multiple:false, directory:false, title:'Choose ARM translation archive', filters:[{name:'ARM translation archive', extensions:['zip']} ]}); }
    catch (error) { armSourceState.error = `Cannot open ARM source picker: ${error}`; renderArmSources(); return; }
    if (!source) return;
  }
  armSourceState = {...armSourceState, busy:true, error:''}; renderArmSources(true);
  try {
    const result = await tauriInvoke('import_arm_translation', {source, sourceKind:kind, expectedSha256});
    if (!result?.sha256) throw new Error('Controller returned an invalid ARM source record');
    armSourceState.sources = [result, ...(armSourceState.sources || []).filter(item => item.sha256 !== result.sha256)];
    armSourceState.error = '';
    showAppToast('ARM source ready. Select it in a stopped runtime, then save ARM translation.', 'success');
    await refreshController();
  } catch (error) { armSourceState.error = `ARM source import failed: ${error}`; }
  finally { armSourceState.busy = false; renderArmSources(true); }
}
document.getElementById('arm-source-panel')?.addEventListener('click', event => {
  const button = event.target.closest('[data-arm-source]');
  if (button && !button.disabled) importArmSource(button.dataset.armSource, button.dataset.armVersion);
});
document.getElementById('arm-source-panel')?.addEventListener('change', event => {
  if (event.target.dataset.armCatalog === 'engine') { armSourceState.engine = event.target.value; armSourceState.version = ''; }
  if (event.target.dataset.armCatalog === 'version') armSourceState.version = event.target.value;
  renderArmSources(true);
});
document.getElementById('arm-source-panel')?.addEventListener('input', event => {
  if (event.target.id === 'arm-source-url') armSourceState.url = event.target.value;
  if (event.target.id === 'arm-source-sha256') armSourceState.sha256 = event.target.value;
});
function renderCreateImageHint() {
  const rawFlavor = document.getElementById('controller-image').value;
  const selectedImage = selectedImageEntry();
  const flavor = rawFlavor.startsWith('custom:') ? 'CUSTOM' : rawFlavor;
  const panel = document.getElementById('controller-image-hint');
  if (!flavor) {
    panel.textContent = 'No image is available yet. Import an image or download an official image first.';
    return;
  }
  if (flavor === 'CUSTOM') {
    const state = selectedImage || imageDownloadState;
    const custom = state?.flavor === 'CUSTOM';
    const ready = custom && (selectedImage ? true : state.status === 'Ready');
    document.getElementById('btn-import-runtime-image').disabled = imageDownloadBusy || imageDownloadActive();
    document.getElementById('btn-import-runtime-folder').disabled = imageDownloadBusy || imageDownloadActive();
    document.getElementById('btn-import-runtime-url').disabled = imageDownloadBusy || imageDownloadActive();
    document.getElementById('btn-cancel-custom-image').hidden = !custom || !state.can_cancel;
    document.getElementById('btn-cancel-custom-image').disabled = imageDownloadBusy;
    document.getElementById('custom-image-status').className = custom && state.error ? 'custom-import-feedback error' : 'custom-import-feedback';
    document.getElementById('custom-image-status').setAttribute('role', custom && state.error ? 'alert' : 'status');
    const status = document.getElementById('custom-image-status');
    status.innerHTML = imageDownloadBusy && custom
      ? '<strong>Downloading custom ZIP…</strong><progress class="runtime-progress" aria-label="Downloading custom image"></progress><span>Download and image validation can take several minutes.</span>'
      : custom
        ? (state.error ? `Image import failed: ${escapeHtml(state.error)}` : '') || (imageDownloadActive() ? `${escapeHtml(state.phase || state.status)} · ${((state.received_bytes || 0) / 1024**3).toFixed(2)} GiB processed` : '')
        : '';
    renderCustomUrlProgress();
    panel.textContent = ready ? 'Custom image · Experimental compatibility. ARM translation must be configured separately.' : 'Import both system.img and vendor.img from a folder or ZIP before creating this runtime.';
    return;
  }
  if (flavor === 'installed') { panel.textContent = 'Creates a separate runtime from the installed image.'; return; }
  const state = selectedImage || imageDownloadState;
  if (state?.status !== 'Ready' || state.flavor !== flavor) {
    panel.textContent = `Waydroid ${flavor}: ${IMAGE_FLAVOR_NOTE(flavor)} Download & verify this image in the Images tab first. It will be used for a new runtime; existing runtimes are kept.`;
    return;
  }
  panel.textContent = `Waydroid ${flavor} · ${(state.image_bytes / 1024**3).toFixed(2)} GiB + runtime data. ${IMAGE_FLAVOR_NOTE(flavor)}${flavor === 'GAPPS' ? ' Google sign-in requires device registration.' : ''}`;
}
document.getElementById('controller-image').addEventListener('change', event => {
  selectedImageLibraryId = event.target.value.startsWith('custom:') ? event.target.value.slice(7) : '';
  renderCreateImageHint();
});
async function importCustomImage(folder = false) {
  if (runtimeBatchBusy || imageDownloadBusy || imageDownloadActive()) return;
  customUrlState = null;
  document.getElementById('controller-image').value = 'CUSTOM';
  imageDownloadBusy = true;
  imageDownloadEpoch++;
  const name = document.getElementById('custom-image-name').value.trim() || 'Custom image';
  renderCreateImageHint();
  try {
    const path = await window.__TAURI__.dialog.open(folder
      ? {multiple:false, directory:true, title:'Choose folder containing system.img and vendor.img'}
      : {multiple:false, directory:false, title:'Choose custom Waydroid image ZIP', filters:[{name:'Waydroid image bundle', extensions:['zip']}]});
    if (!path) return;
    renderImageDownload(await tauriInvoke(folder ? 'import_runtime_image_folder' : 'import_runtime_image', {path, name}));
  } catch (error) {
    document.getElementById('custom-image-status').className = 'custom-import-feedback error';
    document.getElementById('custom-image-status').setAttribute('role', 'alert');
    document.getElementById('custom-image-status').textContent = `Cannot import custom image: ${error}`;
  } finally {
    imageDownloadBusy = false;
    document.getElementById('btn-import-runtime-image').disabled = imageDownloadActive();
    document.getElementById('btn-import-runtime-folder').disabled = imageDownloadActive();
    if (imageDownloadActive()) imageDownloadTimer = setTimeout(refreshImageDownload, 1000);
  }
}
function renderCustomUrlProgress() {
  if (!customUrlState) return;
  const state = customUrlState;
  const status = document.getElementById('custom-image-status');
  const busy = ['Downloading', 'Connecting'].includes(state.status);
  const failed = state.status === 'Failed';
  const importing = state.phase && state.phase !== 'Downloading ZIP' && state.status === 'Downloading';
  status.className = failed ? 'custom-import-feedback error' : 'custom-import-feedback';
  status.setAttribute('role', failed ? 'alert' : 'status');
  if (busy) {
    const known = state.total_bytes > 0;
    const percent = known ? `${state.percent}%` : 'Waiting for total size';
    status.innerHTML = `<strong>${state.status === 'Connecting' ? 'Connecting...' : importing ? escapeHtml(state.phase) : 'Downloading custom ZIP...'} ${importing ? '' : percent}</strong>${importing ? '<progress class="runtime-progress" aria-label="Importing custom image"></progress><span>The ZIP is complete; validating images and running Waydroid CLI import. This step may take several minutes.</span>' : `<progress class="runtime-progress" aria-label="Custom ZIP download" ${known ? `max="100" value="${Number(state.percent) || 0}"` : ''}></progress><span>${((state.received_bytes || 0) / 1024 ** 2).toFixed(1)}${known ? ' / ' + (state.total_bytes / 1024 ** 2).toFixed(1) : ''} MiB received</span>`}`;
  } else if (failed) {
    status.textContent = `Download failed: ${state.error || 'Unknown error'}. Partial download kept. Retry the same URL to resume if the server supports it.`;
  }
  const button = document.getElementById('btn-import-runtime-url');
  button.textContent = busy ? 'Downloading...' : failed ? 'Resume download' : 'Download custom image';
  for (const id of ['btn-import-runtime-image', 'btn-import-runtime-folder', 'btn-import-runtime-url']) document.getElementById(id).disabled = busy || imageDownloadBusy || imageDownloadActive();
  document.getElementById('custom-image-url').readOnly = busy;
}
async function importCustomImageUrl() {
  const input = document.getElementById('custom-image-url');
  const url = input.value.trim();
  if (!url.startsWith('https://')) { document.getElementById('custom-image-status').textContent = 'Enter a direct HTTPS URL for a ZIP containing system.img and vendor.img.'; return; }
  if (runtimeBatchBusy || imageDownloadBusy || imageDownloadActive()) return;
  document.getElementById('controller-image').value = 'CUSTOM';
  imageDownloadBusy = true;
  imageDownloadEpoch++;
  const name = document.getElementById('custom-image-name').value.trim() || 'Custom image';
  customUrlState = {status:'Connecting'};
  renderCreateImageHint();
  try {
    customUrlState = await tauriInvoke('import_runtime_image_url', {url, name});
  } catch (error) {
    customUrlState = {status:'Failed', error:String(error)};
  } finally {
    imageDownloadBusy = false;
    renderCustomUrlProgress();
    if (customUrlState.status === 'Downloading') refreshCustomImageUrlStatus();
  }
}
async function refreshCustomImageUrlStatus() {
  clearTimeout(customUrlTimer);
  try {
    customUrlState = await tauriInvoke('custom_image_url_status');
    if (customUrlState.status === 'Ready' && customUrlState.result) {
      const result = customUrlState.result;
      customUrlState = null;
      renderImageDownload(result);
      document.getElementById('custom-image-url').readOnly = false;
      document.getElementById('btn-import-runtime-url').textContent = 'Download custom image';
      if (imageDownloadActive()) imageDownloadTimer = setTimeout(refreshImageDownload, 1000);
    } else {
      renderCustomUrlProgress();
      if (customUrlState.status === 'Downloading') customUrlTimer = setTimeout(refreshCustomImageUrlStatus, 500);
    }
  } catch (error) {
    document.getElementById('custom-image-status').textContent = `Cannot read download progress: ${error}. Retrying...`;
    customUrlTimer = setTimeout(refreshCustomImageUrlStatus, 2000);
  }
}
document.getElementById('btn-import-runtime-image').addEventListener('click', () => importCustomImage(false));
document.getElementById('btn-import-runtime-folder').addEventListener('click', () => importCustomImage(true));
document.getElementById('btn-import-runtime-url').addEventListener('click', importCustomImageUrl);
document.getElementById('btn-cancel-custom-image').addEventListener('click', cancelImageDownload);
function openGappsCreation() {
  document.getElementById('runtime-create-panel').open = true;
  document.getElementById('controller-image').value = 'GAPPS';
  const name = document.getElementById('controller-name');
  if (!name.value?.trim()) name.value = 'Google Play';
  navigatePage('runtime');
  renderCreateImageHint();
  name.focus();
}
function openImageRuntimeCreation() {
  if (reinstallTargetId) cancelReinstall();
  const panel = document.getElementById('runtime-create-panel');
  panel.open = true;
  navigatePage('runtime');
  renderCreateImageHint();
  panel.scrollIntoView?.({block:'center', behavior:'smooth'});
  document.getElementById('controller-name').focus();
}
document.getElementById('btn-create-runtime-images').addEventListener('click', openImageRuntimeCreation);
async function refreshImageDownload() {
  if (imageDownloadBusy || ['Connecting', 'Downloading'].includes(customUrlState?.status)) return;
  const epoch = imageDownloadEpoch;
  clearTimeout(imageDownloadTimer);
  try {
    const state = await controllerRequest({op:'image_download_status'});
    if (epoch !== imageDownloadEpoch || imageDownloadBusy) return;
    renderImageDownload(state);
    if (imageDownloadActive()) imageDownloadTimer = setTimeout(refreshImageDownload, 1000);
  } catch (error) {
    if (epoch !== imageDownloadEpoch || imageDownloadBusy) return;
    document.getElementById('settings-image-download').textContent = `Cannot read image download status: ${error}`;
    if (document.getElementById('controller-image').value === 'CUSTOM') document.getElementById('custom-image-status').textContent = `Cannot read import status: ${error}. Refresh Images to retry.`;
  }
}
async function startImageDownload(flavor) {
  if (runtimeBatchBusy || imageDownloadBusy || imageDownloadActive() || imageCatalogState?.status !== 'Ready') return;
  const variant = imageCatalogState.report.variants.find(item => item.flavor === flavor);
  if (!variant?.matching_versions) return;
  imageDownloadBusy = true;
  imageDownloadEpoch++;
  renderImageCatalog(imageCatalogState);
  document.getElementById('settings-image-download').textContent = `Starting ${flavor} download…`;
  try {
    renderImageDownload(await controllerRequest({op:'image_download', flavor, system_sha256:variant.system.sha256, vendor_sha256:variant.vendor.sha256}));
  } catch (error) {
    document.getElementById('settings-image-download').textContent = `Cannot download images: ${error}`;
  } finally {
    imageDownloadBusy = false;
    // Restore the catalog's download-button state now that the busy flag is
    // clear (the per-tick re-render was removed; transitions still re-render).
    if (imageCatalogState) renderImageCatalog(imageCatalogState);
    if (imageDownloadActive()) imageDownloadTimer = setTimeout(refreshImageDownload, 1000);
  }
}
async function cancelImageDownload() {
  if (imageDownloadBusy || !imageDownloadState?.can_cancel) return;
  const jobId = imageDownloadState.id;
  imageDownloadBusy = true;
  imageDownloadEpoch++;
  document.getElementById('btn-cancel-image-download').disabled = true;
  try { renderImageDownload(await controllerRequest({op:'image_download_cancel', job_id:jobId})); }
  catch (error) { document.getElementById('settings-image-download').textContent = `Cannot cancel image download: ${error}`; }
  finally { imageDownloadBusy = false; imageDownloadTimer = setTimeout(refreshImageDownload, 1000); }
}
document.getElementById('settings-images').addEventListener('click', event => {
  const button = event.target.closest('[data-download-flavor]');
  if (button && !button.disabled) startImageDownload(button.dataset.downloadFlavor);
});
document.getElementById('btn-cancel-image-download').addEventListener('click', cancelImageDownload);
let runtimeSetupAutoOpened = false;
function setRuntimeSetupVisible(visible) {
  document.getElementById('runtime-setup-panel').hidden = !visible;
  document.getElementById('btn-runtime-setup-details').setAttribute('aria-expanded', String(visible));
}
document.getElementById('btn-runtime-setup-details').addEventListener('click', () => {
  runtimeSetupAutoOpened = false;
  setRuntimeSetupVisible(document.getElementById('runtime-setup-panel').hidden);
});
function renderControllerSetup() {
  const panel = document.getElementById('controller-setup');
  if (!controllerRequirements) {
    runtimeSetupAutoOpened = true;
    setRuntimeSetupVisible(true);
    updateWaydroidSetupActions();
    document.getElementById('controller-setup-help').open = true;
    panel.innerHTML = `<p class="runtime-feedback warning">${escapeHtml(controllerSetupError || 'Checking Waydroid and Android images…')}</p>`;
    return;
  }
  const missing = controllerRequirements.checks.filter(check => !check.available && check.id !== 'desktop');
  const desktop = controllerRequirements.desktop;
  // Desktop is optional: headless Android can run without a graphical login.
  if (missing.length) {
    runtimeSetupAutoOpened = true;
    setRuntimeSetupVisible(true);
    document.getElementById('controller-setup-help').open = true;
  } else if (runtimeSetupAutoOpened) {
    setRuntimeSetupVisible(false);
    runtimeSetupAutoOpened = false;
  }
  const sessionLabels = { wayland: 'Wayland', x11: 'X11', none: 'none', unknown: 'unknown' };
  const desktopLine = desktop?.desktop
    ? `<p class="hint">Desktop session: ${sessionLabels[desktop.session] ?? 'desktop'} · Experimental — window controls require verification · decoration ${escapeHtml(desktop.decoration || 'unverified')} · placement ${escapeHtml(desktop.placement || 'compositor-managed')}</p><p class="hint">${escapeHtml(desktop.detail || 'Socket presence does not verify window controls. Startup checks the selected compositor for SSD or CSD prerequisites.')}</p>`
    : `<p class="hint">Desktop preview unavailable: ${escapeHtml(desktop?.detail || 'No graphical session detected.')} Headless mode remains available.</p>`;
  panel.innerHTML = (missing.length ? `<div class="runtime-feedback warning"><strong>Setup required</strong><ul>${missing.map(check => `<li><strong>${escapeHtml(check.label)}:</strong> ${escapeHtml(check.detail)}</li>`).join('')}</ul><p>You can name a runtime now. Prepare / Start become available when these requirements are met.</p></div>` : '<p class="setup-ready">Waydroid setup detected · Ready to prepare and start</p>') + desktopLine;
  updateWaydroidSetupActions();
}
const waydroidCheckMissing = id => controllerRequirements?.checks?.some(check => check.id === id && !check.available) ?? false;
function updateWaydroidSetupActions() {
  const block = document.getElementById('waydroid-setup-actions');
  const button = document.getElementById('btn-init-waydroid');
  const select = document.getElementById('waydroid-init-flavor');
  const show = controllerRequirements && ['waydroid', 'support', 'tools', 'images', 'arm_payload'].some(waydroidCheckMissing);
  block.hidden = !show;
  if (!show) return;
  const installed = controllerRequirements.installed_images || !waydroidCheckMissing('support');
  const images = imageDownloadState?.available_images || [];
  const previous = select.value;
  const markup = installed ? '<option value="installed">Current Waydroid images</option>'
    : images.map(item => `<option value="${escapeHtml(item.library_id)}">${escapeHtml(item.custom_name || `Waydroid ${item.flavor}`)}</option>`).join('')
      || '<option value="">Choose an image in Images</option>';
  if (select.innerHTML !== markup) {
    select.innerHTML = markup;
    if (images.some(item => item.library_id === previous)) select.value = previous;
  }
  select.hidden = installed;
  button.textContent = !installed && !images.length ? 'Choose image' : 'Set up Default runtime';
  button.disabled = waydroidInitBusy || controllerMutation || imageDownloadBusy || imageDownloadActive();
  select.disabled = button.disabled;

}
function renderWaydroidInitStatus(status) {
  const line = document.getElementById('waydroid-init-status');
  if (!status) { line.textContent = ''; return; }
  if (status.state === 'installing' || status.state === 'initializing') line.textContent = status.detail || 'Working…';
  else if (status.state === 'failed') line.textContent = `Waydroid setup failed: ${status.error || status.detail || 'Unknown error'}`;
  else if (status.state === 'timeout') line.textContent = 'Waydroid initialization is still running in the background. Refresh to re-check.';
  else line.textContent = '';
}
async function pollJob({ poll, intervalMs, deadlineMs, predicate, onUpdate, onDone }) {
  const deadline = Date.now() + deadlineMs;
  let state;
  while (Date.now() < deadline) {
    try { state = await poll(); } catch (error) { state = { state: 'error', error: String(error) }; }
    if (predicate(state)) { await onDone(state); return state; }
    await onUpdate(state);
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
  await onDone(state || { state: 'timeout' });
  return state;
}
async function startWaydroidInitPolling() {
  if (waydroidInitPolling) return;
  waydroidInitPolling = true;
  waydroidInitBusy = true;
  updateWaydroidSetupActions();
  await pollJob({
    poll: () => tauriInvoke('waydroid_init_status'),
    intervalMs: 2000,
    deadlineMs: 60 * 60 * 1000,
    predicate: status => ['done', 'failed'].includes(status?.state),
    onUpdate: renderWaydroidInitStatus,
    onDone: async status => {
      renderWaydroidInitStatus(status);
      if (status.state === 'done') showAppToast(status.detail || 'Waydroid setup complete.', 'success');
      waydroidInitPolling = false;
      waydroidInitBusy = false;
      updateWaydroidSetupActions();
      await refreshController();
    },
  });
}
async function resumeWaydroidInitCheck() {
  if (waydroidInitPolling) return;
  try {
    const status = await tauriInvoke('waydroid_init_status');
    if (['installing', 'initializing'].includes(status?.state)) startWaydroidInitPolling();
  } catch (_) { /* status unavailable; ignore */ }
}
document.getElementById('btn-init-waydroid').addEventListener('click', initializeWaydroid);
async function initializeWaydroid() {
  if (waydroidInitBusy || controllerMutation || imageDownloadBusy || imageDownloadActive()) return;
  const select = document.getElementById('waydroid-init-flavor');
  const line = document.getElementById('waydroid-init-status');
  waydroidInitBusy = true;
  updateWaydroidSetupActions();
  let started = false;
  try {
    try {
      const current = await tauriInvoke('waydroid_init_status');
      if (['installing', 'initializing'].includes(current?.state)) {
        started = true;
        startWaydroidInitPolling();
        return;
      }
    } catch (_) { /* No existing setup job; continue. */ }
    const installed = controllerRequirements?.installed_images || !waydroidCheckMissing('support');
    const selected = (imageDownloadState?.available_images || []).find(item => item.library_id === select.value);
    if (!installed && !selected) { navigatePage('images'); return; }
    const confirmed = await AppDialog.confirm(
      `Set up Default runtime using ${installed ? 'the current Waydroid images' : (selected.custom_name || 'Waydroid ' + selected.flavor)}?\n\nAndroid images will be reused. Missing host tools and ARM support may need a download. Your administrator password is required.`,
      { title: 'Set up Default runtime', kind: 'warning', okLabel: 'Set up', cancelLabel: 'Cancel' });
    if (!confirmed) return;
    line.textContent = 'Requesting administrator access…';
    const result = await tauriInvoke('setup_waydroid', {
      flavor: selected?.flavor === 'GAPPS' ? 'GAPPS' : 'VANILLA',
      imageId: installed ? null : selected.library_id,
    });
    line.textContent = result.message || '';
    if (result.ok) { started = true; startWaydroidInitPolling(); }
  } catch (error) {
    line.textContent = `Cannot set up Default runtime: ${error}`;
  } finally {
    if (!started) { waydroidInitBusy = false; updateWaydroidSetupActions(); }
  }
}
function scheduleRuntimeUsage() {
  clearTimeout(runtimeUsageTimer);
  runtimeUsageTimer = null;
  if (!document.hidden && document.getElementById('page-runtime').classList.contains('active')) {
    runtimeUsageTimer = setTimeout(refreshRuntimeUsage, 3000);
  }
}
async function refreshRuntimeUsage() {
  if (runtimeUsagePolling) return;
  if (document.hidden || !document.getElementById('page-runtime').classList.contains('active')) return;
  if (controllerLoading || controllerMutation || runtimeTransition) { scheduleRuntimeUsage(); return; }
  runtimeUsagePolling = true;
  try {
    const records = [...controllerRecords, ...(existingRuntime ? [existingRuntime] : [])].filter(record => !record.refreshError && !record.unavailable);
    const updated = new Map();
    for (const record of records) {
      if (controllerLoading || controllerMutation || runtimeTransition || document.hidden || !document.getElementById('page-runtime').classList.contains('active')) break;
      const sampledState = record.state;
      const live = ['Running', 'Frozen'].includes(record.state);
      let resources, storage, resourceFailed = false, diskFailed = false;
      if (live) {
        try { resources = await controllerRequest({op:'resources_info', id:record.id}); }
        catch (_) { resourceFailed = true; }
      }
      try { storage = await controllerRequest({op:'storage_info', id:record.id}); }
      catch (_) { diskFailed = true; }
      if (runtimeRecord(record.id) !== record || record.state !== sampledState || controllerLoading || controllerMutation || runtimeTransition || document.hidden || !document.getElementById('page-runtime').classList.contains('active')) continue;
      if (resources) record.resources = resources;
      if (storage) record.storage = storage;
      record.usageResourcesUnavailable = resourceFailed;
      record.usageDiskUnavailable = diskFailed;
      RuntimeUsage.sample(record);
      updated.set(record.id, record);
    }
    if (!controllerLoading && !controllerMutation && !runtimeTransition && !document.hidden && document.getElementById('page-runtime').classList.contains('active')) {
      const rendered = new Map();
      document.querySelectorAll('[data-runtime-usage]').forEach(cell => {
        const id = cell.dataset.runtimeUsage, record = updated.get(id);
        if (!record || runtimeRecord(id) !== record) return;
        if (!rendered.has(id)) rendered.set(id, renderRuntimeUsage(record));
        const html = rendered.get(id);
        if (cell.innerHTML !== html) cell.innerHTML = html;
      });
    }
  } finally { runtimeUsagePolling = false; scheduleRuntimeUsage(); }
}

function renderRuntimeUsage(record) {
  const gpu = RuntimeUsage.gpu(record);
  return `<div class="runtime-usage">${RuntimeUsage.metrics(record).map(metric => {
    const percent = metric.percent;
    const hint = metric.text === '—' ? 'Usage unavailable; CPU needs two live samples' : metric.hint;
    return `<div class="runtime-usage-metric" title="${escapeHtml(hint)}"><span class="runtime-usage-label">${metric.name}</span><div class="runtime-usage-track ${percent == null ? 'unavailable' : ''}" ${percent == null ? 'aria-hidden="true"' : `role="progressbar" aria-label="${metric.name}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent.toFixed(1)}" aria-valuetext="${escapeHtml(metric.text)}"`}><span style="width:${percent == null ? 0 : percent.toFixed(2)}%"></span></div><span class="runtime-usage-value">${escapeHtml(metric.text)}</span></div>`;
  }).join('')}<div class="runtime-usage-metric runtime-usage-gpu" title="${escapeHtml(gpu.hint)}"><span class="runtime-usage-label">GPU</span><span class="runtime-usage-gpu-value">${escapeHtml(gpu.text)}</span></div></div>`;
}

function renderController() {
  // Replacing the table destroys WebKit's native select popup. Keep its DOM
  // while focused (including keyboard selection and an in-flight poll reply).
  // Data still refreshes; the next render after focus leaves catches up.
  if (document.activeElement?.dataset?.gpuSelect || document.activeElement?.dataset?.resourceField || document.activeElement?.dataset?.armSelect || document.activeElement?.dataset?.armEngine) return;
  renderExistingRuntime();
  renderRuntimeBatchSelection();
  const focusedRuntime = document.activeElement?.dataset?.runtimeSelect;
  const panel = document.getElementById('controller-runtimes');
  if (selectedRuntimeId !== 'default' && !controllerRecords.some(record => record.id === selectedRuntimeId)) selectedRuntimeId = null;
  document.getElementById('legacy-runtime-detail').hidden = runtimeSettingsId !== 'default';
  const settingsRecord = runtimeRecord(runtimeSettingsId);
  document.getElementById('runtime-settings-title').textContent = settingsRecord?.name || 'Runtime unavailable';
  let settingsMarkup = '';
  document.getElementById('legacy-runtime-row').classList.toggle('selected', selectedRuntimeId === 'default');
  document.querySelectorAll('button.runtime-select[data-runtime-select="default"]').forEach(button => button.setAttribute('aria-pressed', String(selectedRuntimeId === 'default')));
  const markup = controllerRecords.length ? controllerRecords.map(record => {
    const selected = selectedRuntimeId === record.id;
    const nameCell = `<button type="button" class="runtime-select" data-runtime-select="${escapeHtml(record.id)}" aria-pressed="${selected}">${escapeHtml(record.name)}</button>`;

    if (record.refreshError) {
      if (runtimeSettingsId === record.id) settingsMarkup = `<div class="runtime-feedback error" role="alert"><strong>Status unavailable</strong><p>${escapeHtml(record.refreshError)}</p><p>Use Refresh to retry.</p></div><p class="hint">ID: ${escapeHtml(record.id)}</p>`;
      return `<tr class="runtime-summary ${selected ? 'selected' : ''}" data-runtime-select="${escapeHtml(record.id)}"><td class="runtime-checkbox-cell"><input type="checkbox" data-runtime-check="${escapeHtml(record.id)}" aria-label="Select ${escapeHtml(record.name)}" ${runtimeBatchSelection.has(record.id) ? 'checked' : ''} ${runtimeBatchBusy ? 'disabled' : ''}></td><td>${nameCell}</td><td><span class="runtime-status error">Unavailable</span></td><td class="runtime-usage-cell" data-runtime-usage="${escapeHtml(record.id)}">${renderRuntimeUsage({id:record.id})}</td><td><div class="runtime-actions"><button type="button" class="btn runtime-action-icon" data-controller-op="settings" data-controller-id="${escapeHtml(record.id)}" title="Settings" aria-label="Settings · ${escapeHtml(record.name)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 3v3m0 12v3M3 12h3m12 0h3M6 6l2 2m8 8 2 2M6 18l2-2m8-8 2-2"/></svg></button></div></td></tr>`;
    }
    const pending = controllerPending?.id === record.id ? controllerPending : null;
    const storageMoving = record.storage?.job?.status === 'Running';
    const appWorking = record.appJob?.status === 'Running';
    const desktopMode = (record.display?.effective_mode || record.display?.mode) === 'desktop';
    const active = appWorking || storageMoving || ['Provisioning', 'Starting', 'Stopping'].includes(record.state) || record.job?.status === 'Running';
    const busy = runtimeOperationBusy(record.id) || apkInstallBusy || active || record.detailsLoading;
    const failed = record.state === 'Error';
    const needsImages = record.state === 'Allocated' || (failed && record.job?.operation === 'prepare');
    const canDelete = record.deletion_pending || ['Allocated', 'Prepared', 'Stopped'].includes(record.state) || (failed && record.job?.operation === 'prepare');
    const canMoveState = !record.deletion_pending && (['Allocated', 'Prepared', 'Stopped'].includes(record.state) || (failed && record.job?.operation === 'prepare'));
    const canPrepare = controllerRequirements?.can_prepare === true && !record.storage?.error;
    const canStart = controllerRequirements?.can_start === true && !record.storage?.error;
    const canReinstall = ['Allocated', 'Prepared', 'Stopped', 'Error', 'Running', 'Frozen'].includes(record.state);
    const labels = { Allocated:'Allocated', Provisioning:'Preparing', Prepared:'Prepared', Starting:'Starting', Running:'Running', Frozen:'Frozen', Stopping:'Stopping', Stopped:'Stopped', Error:'Error' };
    const descriptions = {
      Allocated: record.image_selection ? `Prepare the selected ${record.image_selection.flavor} images with independent Android data.` : 'Prepare an independent copy of the installed Android images.',
      Provisioning: record.prepare_phase || 'Copying and verifying Android images. This can take a few minutes. You can leave this page; the job continues.',
      Prepared: 'Images are ready. Start Android to create its data and boot this runtime.',
      Starting: desktopMode ? 'Waiting for Android to boot in your desktop session. Use List apps to launch an app when ready.' : 'Waiting for Android to finish booting. The first start can take several minutes. No desktop app window opens in this headless preview.',
      Running: desktopMode ? 'Android is ready. Launch apps below to open separate desktop windows for this runtime.' : 'Android boot is complete. This runtime uses a virtual display. Stop Android and choose Desktop mode to open app windows.',
      Stopping: 'Stopping Android and releasing its resources. Saved app data is retained.',
      Stopped: record.shutdown_warning || 'Android has stopped. Start again to use this runtime.',
      Error: 'The last operation failed. Review the error below before retrying.'
    };
    const phase = pending?.phase;
    const waiting = ({stopping:'Stopping Android to apply settings…', applying:'Applying settings…', starting:'Restarting Android…'})[phase] || (phase === 'confirm' ? 'Waiting for confirmation…' : phase === 'send' ? 'Sending request…' : 'Waiting for current refresh…');
    const storagePhase = ({copy: 'Copying runtime data…', verify: 'Verifying the copied data…', switch: 'Activating the new location…', cleanup: 'Removing the retained copy…'})[record.storage?.job?.phase];
    const title = runtimeBatchRestarting.has(record.id) ? 'Restarting…' : RuntimeWindow.isPending(record.id) ? 'Opening Waydroid…' : pending ? waiting : appWorking ? (record.appJob.action === 'delete' ? 'Deleting runtime…' : record.appJob.action === 'install' ? 'Installing APK…' : record.appJob.action === 'full_ui' ? 'Opening Waydroid…' : record.appJob.action === 'launch' ? (desktopMode ? 'Launching desktop app…' : 'Launching app in headless Android…') : 'Force-stopping app…') : storageMoving ? storagePhase || 'Moving runtime data…' : record.deletion_pending ? 'Deletion incomplete' : labels[record.state] || record.state;
    const status = runtimeOperations.get(record.id) === 'restart' ? 'Restarting' : pending?.op === 'reinstall' && phase === 'stopping' ? 'Stopping' : pending?.op === 'reinstall' && phase === 'applying' ? 'Reinstalling' : runtimeBatchRestarting.has(record.id) || pending && pending.op !== 'reinstall' && ['stopping', 'starting'].includes(phase) ? 'Restarting' : labels[record.state] || record.state;
    const button = (op, label, allowed, style = '') => `<button class="btn ${style}" data-controller-op="${op}" data-controller-id="${escapeHtml(record.id)}" ${busy || !allowed || (record.deletion_pending && op !== 'delete') ? 'disabled' : ''} ${op === 'delete' && !canDelete ? 'title="Stop this runtime before deleting it"' : ''}>${label}</button>`;
    const actionIcons = {
      start: '<path d="m8 5 11 7-11 7z"/>',
      stop: '<rect x="6" y="6" width="12" height="12" rx="1"/>',
      restart: '<path d="M3 11a9 9 0 1 1 2.6 7M3 4v7h7"/>',
      open_waydroid: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 8h18m-11 4 4 2-4 2z"/>',
      settings: '<circle cx="12" cy="12" r="3"/><path d="m9 3-.5 2-2 1L4.5 5l-2 3 1.5 1.5v5L2.5 16l2 3 2-1 2 1 .5 2h6l.5-2 2-1 2 1 2-3-1.5-1.5v-5L21.5 8l-2-3-2 1-2-1L15 3z"/>',
      prepare: '<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',
    };
    const rowButton = (op, label, allowed, style = '') => `<button type="button" class="btn runtime-action-icon ${style}" data-controller-op="${op}" data-controller-id="${escapeHtml(record.id)}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label + ' · ' + record.name)}" ${op !== 'settings' && (busy || !allowed || record.deletion_pending) ? 'disabled' : ''}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${actionIcons[op]}</svg></button>`;
    let actions = '';
    if (record.state === 'Provisioning') actions = rowButton('prepare', 'Preparing…', false, 'btn-primary');
    else if (record.state === 'Starting') actions = rowButton('start', 'Starting…', false, 'btn-primary');
    else if (record.state === 'Stopping') actions = rowButton('stop', 'Stopping…', false, 'btn-danger');
    else if (needsImages) actions = rowButton('prepare', failed ? 'Retry preparation' : 'Prepare', canPrepare, 'btn-primary');
    else if (['Running', 'Frozen'].includes(record.state)) actions = rowButton('stop', 'Stop', true, 'btn-danger');
    else {
      actions = rowButton('start', failed && record.job?.operation === 'start' ? 'Retry start' : 'Start', canStart, 'btn-primary');
      if (failed) actions += rowButton('stop', record.job?.operation === 'stop' ? 'Retry stop' : 'Clean up', true, 'btn-danger');
    }
    actions += rowButton('restart', 'Restart', RuntimeBatch.eligible(record, 'restart', controllerRequirements || {}));
    actions += rowButton('open_waydroid', 'Open Waydroid', RuntimeBatch.eligible(record, 'open'));
    if (record.deletion_pending) actions = '';
    actions += rowButton('settings', 'Settings', true);
    const deleteAction = button('delete', record.deletion_pending ? 'Retry delete…' : 'Delete…', canDelete, 'btn-danger');
    const blocked = !active && ((needsImages && !canPrepare) || (!['Running', 'Frozen'].includes(record.state) && !needsImages && !canStart));
    const blockedReasons = [];
    if (blocked) {
      if (!controllerRequirements) blockedReasons.push('the runtime controller setup check is unavailable — start/update the controller, then refresh');
      else {
        const missingCheck = (controllerRequirements.checks ?? []).find(check => check.id !== 'desktop' && !check.available);
        if (missingCheck) blockedReasons.push(`${missingCheck.label}: ${missingCheck.detail}`);
        else if (needsImages) blockedReasons.push('Android images are not available for preparation');
        else if (!controllerRequirements.can_start) blockedReasons.push('start requirements are not met');
      }
      if (record.storage?.error) blockedReasons.push(`Storage: ${record.storage.error}`);
    }
    const kind = runtimeBatchRestarting.has(record.id) ? 'info' : failed || record.appJob?.status === 'Failed' ? 'error' : record.state === 'Stopped' && record.shutdown_warning ? 'warning' : runtimeBadgeKind(record.state);
    const showDetailFeedback = Boolean(pending || active || failed || record.shutdown_warning || storageMoving || record.appJob?.status === 'Failed');
    const detailFeedback = showDetailFeedback ? `<div class="runtime-feedback ${kind}" role="status">${pending || active ? '<span class="runtime-spinner" aria-hidden="true"></span>' : ''}<strong>${escapeHtml(title)}</strong><p>${escapeHtml(appWorking ? (record.appJob.action === 'delete' ? 'Removing runtime data. This may take a few minutes.' : record.appJob.action === 'install' ? 'Waiting for Android to finish verification and app optimization. A confirmation dialog is not always required.' : record.appJob.package) : storageMoving ? (record.storage?.job?.phase === 'cleanup' ? 'Removing only the retained copy. The active runtime data is kept.' : 'Keep both disks connected. The original data remains available until the copy is verified.') : descriptions[record.state] || '')}</p></div>` : '';
    const summary = `<tr class="runtime-summary ${selected ? 'selected' : ''}" data-runtime-select="${escapeHtml(record.id)}" aria-busy="${Boolean(runtimeOperations.has(record.id) || runtimeBatchRestarting.has(record.id) || pending || active)}"><td class="runtime-checkbox-cell"><input type="checkbox" data-runtime-check="${escapeHtml(record.id)}" aria-label="Select ${escapeHtml(record.name)}" ${runtimeBatchSelection.has(record.id) ? 'checked' : ''} ${runtimeBatchBusy ? 'disabled' : ''}></td><td>${nameCell}</td><td><span class="runtime-status ${kind}" role="status">${escapeHtml(status)}</span></td><td class="runtime-usage-cell" data-runtime-usage="${escapeHtml(record.id)}">${renderRuntimeUsage(record)}</td><td><div class="runtime-actions">${actions}${record.deletion_pending ? deleteAction : ''}</div></td></tr>`;
    const detail = `<article class="runtime-card managed-runtime">
      <div class="runtime-actions">${button('rename', 'Rename…', true, 'btn-quiet')}${!record.deletion_pending ? button('reinstall', 'Reinstall…', canReinstall, 'btn-warning') + deleteAction : ''}</div>
      ${detailFeedback}
      ${pending || active ? '<div class="runtime-progress runtime-progress-indeterminate" role="progressbar" aria-label="Runtime operation in progress"><span></span></div>' : ''}
      ${record.image_selection ? (() => {
        const image = record.image_selection;
        const systemSha = image.images?.system?.sha256 || '';
        const vendorSha = image.images?.vendor?.sha256 || '';
        const cachedImage = (imageDownloadState?.available_images || []).find(item =>
          item.images?.system?.sha256 === systemSha && item.images?.vendor?.sha256 === vendorSha);
        const label = image.flavor === 'CUSTOM'
          ? (image.custom_name || cachedImage?.custom_name || `Custom image · ${systemSha.slice(0, 8)}`)
          : `Waydroid ${image.flavor}`;
        return `<details class="runtime-image-details"><summary><strong>Image: ${escapeHtml(label)}</strong> · ${(image.image_bytes / 1024**3).toFixed(2)} GiB</summary><div class="hint"><p>${escapeHtml(image.images.system.filename)}<br>${escapeHtml(image.images.vendor.filename)}</p><p>System SHA-256: <code>${escapeHtml(systemSha.slice(0, 16))}…</code><br>Vendor SHA-256: <code>${escapeHtml(vendorSha.slice(0, 16))}…</code></p><p></p></div></details>`;
      })() : ''}
      ${record.job?.error ? `<div class="runtime-feedback error" role="alert"><strong>Could not ${escapeHtml(record.job.operation)}</strong><p>${escapeHtml(record.job.error)}</p></div>` : ''}
      ${blocked ? `<p class="runtime-blocked">Prepare / Start is unavailable — ${escapeHtml(blockedReasons.join(' · '))}</p>` : ''}
      ${record.appJob && !(record.appJob.action === 'launch' && record.appJob.status === 'Succeeded') ? `<div class="runtime-feedback ${record.appJob.status === 'Failed' ? 'error' : 'info'}" role="status"><strong>${escapeHtml(record.appJob.action)} · ${escapeHtml(record.appJob.status)}</strong><p>${escapeHtml(record.appJob.package || (record.appJob.action === 'delete' ? 'Runtime deletion' : record.appJob.action === 'install' ? 'APK installation' : ''))}</p><p>${escapeHtml(record.appJob.error || record.appJob.result?.message || 'Waiting for the Android result…')}</p></div>` : ''}
      <details class="runtime-settings-group" data-settings-key="${escapeHtml(record.id)}:gpu" ${runtimeSettingsOpen.has(record.id + ':gpu') ? 'open' : ''}><summary>Graphics</summary><div class="runtime-gpu-panel runtime-primary-setting" id="runtime-gpu-${escapeHtml(record.id)}">${renderGpuControls(record, busy)}<div class="runtime-actions">${button('gpu_check', runtimeGraphics.get(record.id)?.loading ? 'Checking GPU…' : 'Check GPU', !runtimeGraphics.get(record.id)?.loading, 'btn-quiet')}</div>${renderRuntimeGraphics(record.id)}</div></details>
      <div class="runtime-config-grid"><details class="runtime-settings-group" data-settings-key="${escapeHtml(record.id)}:storage" ${runtimeSettingsOpen.has(record.id + ':storage') ? 'open' : ''}><summary>Storage</summary><div class="runtime-storage-panel">
        <p class="runtime-storage-path">${escapeHtml(record.storage?.path || record.storageLoadError || 'Loading storage location…')}</p>
        ${record.storage?.total_bytes != null ? `<p class="hint">Runtime used: ${record.storage.used_bytes == null ? 'Measuring…' : (record.storage.used_bytes / 1024 ** 3).toFixed(2) + ' GiB'} · free: ${(record.storage.free_bytes / 1024 ** 3).toFixed(2)} GiB / ${(record.storage.total_bytes / 1024 ** 3).toFixed(2)} GiB.</p>` : ''}
        ${record.storage?.limit_status === 'unavailable' ? '<p class="hint">Disk quota unavailable.</p>' : ''}
        <div class="runtime-actions">${button('move', 'Change location…', Boolean(record.storage) && !record.storage.error && !record.storage.previous_path && canMoveState, 'btn-quiet')}
        ${record.storage?.previous_path ? button('discard_previous', 'Remove retained copy…', !record.storage.error, 'btn-quiet') : ''}</div>
        ${!canMoveState && !storageMoving ? '<p class="hint">Stop Android before changing its storage location.</p>' : ''}
        ${record.storage?.path?.split('/').pop().startsWith('.anvildroid-') ? '<p class="hint">Hidden folder: Ctrl+H to show. Administrator access required.</p>' : ''}
        ${needsImages && canMoveState ? '<p class="hint">Choose a storage location before Prepare.</p>' : ''}
        ${record.storage?.previous_path ? `<p class="hint">Retained copy: ${escapeHtml(record.storage.previous_path)}. Verify the runtime at its new location before removing this copy to reclaim space. Moving alone does not free space on the original disk.</p>` : ''}
        ${record.storage?.job?.error || record.storage?.error ? `<p class="runtime-feedback error" role="alert">${escapeHtml(record.storage.error || record.storage.job.error)}</p>` : ''}
      </div></details>
      <details class="runtime-settings-group" data-settings-key="${escapeHtml(record.id)}:arm" ${runtimeSettingsOpen.has(record.id + ':arm') ? 'open' : ''}><summary>ARM translation</summary><div class="runtime-arm-panel">${renderRuntimeArm(record, busy)}</div></details>
      <div class="runtime-settings-list runtime-settings-visible">
      <details class="runtime-setting-row" data-settings-key="${escapeHtml(record.id)}:google" ${runtimeSettingsOpen.has(record.id + ':google') ? 'open' : ''}><summary><span><strong>Google Play</strong></span></summary><div class="runtime-setting-body"><div class="runtime-google-panel">${renderRuntimeGoogle(record)}</div></div></details>
      <details class="runtime-setting-row" data-settings-key="${escapeHtml(record.id)}:display" ${runtimeSettingsOpen.has(record.id + ':display') ? 'open' : ''}><summary><span><strong>Display</strong><small>Desktop windows or headless</small></span></summary><div class="runtime-setting-body"><div class="runtime-display-panel"><strong>Display mode: ${record.display?.mode === 'desktop' ? 'Desktop (preview)' : 'Headless'}</strong>
      <p class="hint">${record.display?.mode === 'desktop' ? 'Desktop app windows.' : 'Background mode; no app windows.'}</p>
      <div class="runtime-actions"><button class="btn btn-quiet" data-controller-op="display_set" data-controller-id="${escapeHtml(record.id)}" data-display-mode="${record.display?.mode === 'desktop' ? 'headless' : 'desktop'}" ${busy || (!canMoveState && !['Running', 'Frozen'].includes(record.state)) || !record.display ? 'disabled' : ''}>${record.display?.mode === 'desktop' ? 'Use Headless' : 'Use Desktop'}${['Running', 'Frozen'].includes(record.state) ? ' & restart' : ''}</button></div>
      <p class="hint">Backend: ${record.display?.backend === 'native-x11' ? 'Native X11 · bridge CSD' : record.display?.backend === 'legacy' ? 'Compatibility · Wayland / Weston on X11' : 'Automatic · native X11 / Wayland'}</p>
      <p class="hint">Native X11 requires Android 13 and NVIDIA/Venus in Graphics. Individual app windows with the shared Android composer and bridge CSD. Other Android versions and AMD/Intel guest rendering are not supported yet.</p>
      <div class="runtime-actions">${['amd', 'intel', 'nvidia'].map(presenter => `<button class="btn btn-quiet" data-controller-op="backend_set" data-controller-id="${escapeHtml(record.id)}" data-backend="native-x11" data-presenter="${presenter}" ${busy || !record.display || !['Prepared', 'Stopped', 'Error', 'Running', 'Frozen'].includes(record.state) ? 'disabled' : ''}>Native X11 · ${presenter.toUpperCase()}</button>`).join('')}
      <button class="btn btn-quiet" data-controller-op="backend_set" data-controller-id="${escapeHtml(record.id)}" data-backend="legacy" data-presenter="${record.display?.presenter || 'amd'}" ${busy || !record.display ? 'disabled' : ''}>Use Wayland / Weston compatibility</button>
      <button class="btn btn-quiet" data-controller-op="backend_set" data-controller-id="${escapeHtml(record.id)}" data-backend="auto" data-presenter="${record.display?.presenter || 'amd'}" ${busy || !record.display ? 'disabled' : ''}>Use automatic backend</button></div>
      ${record.state === 'Running' ? '<p class="hint">Changing mode restarts Android.</p>' : ''}${record.displayError ? `<p class="runtime-feedback error">${escapeHtml(record.displayError)}</p>` : ''}</div></div></details>

      <details class="runtime-setting-row" data-settings-key="${escapeHtml(record.id)}:resources" ${runtimeSettingsOpen.has(record.id + ':resources') ? 'open' : ''}><summary><span><strong>Performance</strong><small>RAM and CPU limits</small></span></summary><div class="runtime-setting-body"><div class="runtime-resources-panel">${renderResourceControls(record, busy)}</div></div></details>
      <details class="runtime-setting-row" data-settings-key="${escapeHtml(record.id)}:health" ${runtimeSettingsOpen.has(record.id + ':health') ? 'open' : ''}><summary><span><strong>Diagnostics</strong></span></summary><div class="runtime-setting-body"><div class="runtime-health-panel"><div class="runtime-actions">${button('health', runtimeHealth.get(record.id)?.loading ? 'Checking health…' : 'Check health', !runtimeHealth.get(record.id)?.loading, 'btn-quiet')}</div>${renderRuntimeHealth(record.id)}</div></div></details>
      </div></div>${record.compatibility?.status === 'experimental' ? `<p class="runtime-feedback warning">Android SDK ${escapeHtml(String(record.compatibility.sdk))} · Experimental compatibility. Verify boot, app controls and desktop behavior with this image.</p>` : ''}<details class="runtime-details"><summary>Runtime details</summary><p>ID: ${escapeHtml(record.id)}</p>${record.compatibility?.caption_detail ? `<p>${escapeHtml(record.compatibility.caption_detail)}</p>` : ''}${record.job ? `<p>Last operation: ${escapeHtml(record.job.operation)} · ${escapeHtml(record.job.status)}</p><p>Job: ${escapeHtml(record.job.id || '')}</p>` : ''}</details>
      </article>`;
    if (runtimeSettingsId === record.id) settingsMarkup = detail;
    return summary;
  }).join('') : '<tr><td colspan="5" class="hint">No additional runtimes for this user. Create one to begin.</td></tr>';
  const settingsPanel = document.getElementById('runtime-settings-content');
  const nextSettings = runtimeSettingsId === 'default' ? '' : settingsMarkup || (runtimeSettingsId ? '<p class="runtime-blocked">This runtime is no longer available.</p>' : '');
  if (settingsPanel._runtimeMarkup !== nextSettings) {
    settingsPanel._runtimeMarkup = nextSettings;
    settingsPanel.innerHTML = nextSettings;
  }
  if (panel._runtimeMarkup !== markup) {
    panel._runtimeMarkup = markup;
    panel.innerHTML = markup;
    renderRuntimeBatchSelection();
  }
  if (focusedRuntime) document.querySelectorAll('button.runtime-select').forEach(button => {
    if (button.dataset.runtimeSelect === focusedRuntime) button.focus({preventScroll:true});
  });
}
function beginReinstall(id) {
  const record = id === 'default' ? existingRuntime : controllerRecords.find(item => item.id === id);
  if (!record || record.deletion_pending || !['Allocated', 'Prepared', 'Stopped', 'Error', 'Running', 'Frozen'].includes(record.state)) return;
  navigatePage('runtime', false);
  reinstallTargetId = id;
  document.getElementById('controller-name').value = record.name || '';
  document.getElementById('controller-name').readOnly = true;
  document.querySelector('#runtime-create-panel > summary').textContent = 'Reinstall: ' + (record.name || 'runtime');
  document.getElementById('controller-create-button').textContent = 'Erase data and reinstall';
  document.getElementById('controller-reinstall-cancel').hidden = false;
  document.getElementById('controller-image').value = 'installed';
  renderCreateImageHint();
  const panel = document.getElementById('runtime-create-panel');
  panel.open = true;
  panel.scrollIntoView({block:'center', behavior:'smooth'});
}
function controllerHasActiveWork() {
  return existingRuntime?.state === 'Starting' || controllerRecords.some(record =>
    ['Provisioning', 'Starting', 'Stopping'].includes(record.state) ||
    record.job?.status === 'Running' || record.appJob?.status === 'Running' ||
    record.storage?.job?.status === 'Running');
}
document.addEventListener('visibilitychange', () => {
  scheduleRuntimeUsage();
  if (!document.hidden && (controllerHasActiveWork() || document.getElementById('page-runtime').classList.contains('active') || document.getElementById('page-images').classList.contains('active') || document.getElementById('page-runtime-settings').classList.contains('active'))) refreshController();
});
async function refreshController({light = false} = {}) {
  if (controllerLoading || controllerMutation || runtimeOperations.size) return;
  clearTimeout(controllerTimer);
  controllerLoading = true;
  controllerRefreshDone = new Promise(resolve => { finishControllerRefresh = resolve; });
  const message = document.getElementById('controller-message');
  const create = document.getElementById('controller-create-button');
  let defaultRefresh = null;
  try {
    const records = await controllerRequest({ op: 'list' });
    if (!Array.isArray(records)) throw new Error('Invalid runtime list');
    const deleted = controllerRecords.filter(record => record.deletion_pending && !records.some(current => current.id === record.id));
    if (deleted.length) showAppToast(`Deleted runtime: ${deleted.map(record => record.name).join(', ')}.`, 'success');
    const previousRecords = controllerRecords;
    const states = await Promise.all(records.map(async record => {
      try {
        const current = await controllerRequest({op:'refresh', id:record.id});
        if (current?.id !== record.id) throw new Error('Runtime response does not match the requested ID');
        return current;
      } catch (error) { return {...record, refreshError:String(error)}; }
    }));
    if (!previousRecords.length) {
      controllerRecords = states.map(record => record.refreshError ? record : {...record, detailsLoading:true});
      renderController();
    }
    defaultRefresh = refreshExistingRuntime({light});
    try {
      const setup = light && controllerRequirements ? controllerRequirements : await controllerRequest({ op: 'requirements' });
      if (!setup || typeof setup.can_prepare !== 'boolean' || typeof setup.can_start !== 'boolean' || !Array.isArray(setup.checks)) throw new Error('Controller setup check unavailable');
      controllerRequirements = setup;
      controllerSetupError = '';
    } catch (error) {
      controllerRequirements = null;
      controllerSetupError = `Cannot verify setup: ${error}. Update/start the controller, then refresh.`;
    }
    renderControllerSetup();
    await refreshArmSources({light});
    // Refresh queries the authenticated worker; list alone is persisted metadata.
    controllerRecords = await Promise.all(states.map(async record => {
      if (record.refreshError) return record;
      const r = record;
      try { record.storage = await controllerRequest({ op: 'storage_info', id: r.id }); }
      catch (error) { record.storageLoadError = `Cannot load storage: ${error}`; }
      const previous = previousRecords.find(item => item.id === r.id);
      if (previous?.storage?.job?.status === 'Running' && record.storage?.job?.status === 'Succeeded') {
        showAppToast(`Storage operation completed for ${record.name}.`, 'success');
      }
      const cached = kind => light && previous?.[kind] && previous.state === record.state && !previous.refreshError;
      try { record.display = cached('display') ? previous.display : await controllerRequest({op: 'display_info', id: r.id}); }
      catch (error) { record.displayError = `Cannot read display mode: ${error}`; }
      try { record.resources = await controllerRequest({op:'resources_info', id:r.id}); RuntimeUsage.sample(record); }
      catch (error) { record.resourcesError = `Cannot read resource limits: ${error}`; }
      try { record.gpu = cached('gpu') ? previous.gpu : await controllerRequest({op: 'gpu_info', id: r.id}); }
      catch (error) { record.gpuError = `Cannot read GPU configuration: ${error}`; }
      try { record.arm = cached('arm') ? previous.arm : await controllerRequest({op: 'arm_info', id: r.id}); }
      catch (error) { record.armError = `Cannot read ARM configuration: ${error}`; }
      try { record.appJob = await controllerRequest({ op: 'app_job', id: r.id }); }
      catch (error) { record.appJob = previousRecords.find(previous => previous.id === r.id)?.appJob; }
      invalidateRuntimeGraphics(previousRecords.find(previous => previous.id === r.id), record);
      return record;
    }));
    RuntimeUsage.retain(new Set(['default', ...controllerRecords.map(record => record.id)]));
    const armSources = controllerRecords.flatMap(record => record.arm?.sources || []);
    if (armSources.length) armSourceState.sources = [...new Map(armSources.map(source => [source.sha256, source])).values()];
    renderArmSources();
    renderController();
    create.disabled = controllerMutation;
    document.getElementById('controller-connection').textContent = 'Service connected';
    document.getElementById('controller-connection').className = 'status-badge connected';
    document.getElementById('controller-start').style.display = 'none';
    document.getElementById('btn-start-controller').textContent = 'Start runtime controller';
    message.textContent = '';
  } catch (error) {
    create.disabled = true;
    controllerRequirements = null;
    controllerSetupError = 'Setup could not be checked. The runtime controller must be available first; Waydroid and initialized Android images are also required.';
    renderControllerSetup();
    document.getElementById('controller-connection').textContent = 'Service unavailable';
    document.getElementById('controller-connection').className = 'status-badge disconnected';
    document.getElementById('controller-start').style.display = '';
    document.getElementById('btn-start-controller').textContent = 'Restart runtime controller';
    controllerRecords = [];
    renderController();
    message.textContent = `Cannot reach runtime controller: ${error}. Start the controller below, or install/start anvildroid-controller.service, then refresh Runtime.`;
  } finally {
    await (defaultRefresh || refreshExistingRuntime({light}));
    renderController();
    controllerLoading = false;
    renderRuntimeBatchSelection();
    finishControllerRefresh();
    scheduleRuntimeUsage();
    // Stop polling at completion. Merely running Android is not an active job.
    controllerTimer = null;
    if (controllerHasActiveWork()) controllerTimer = setTimeout(() => {
      if (!document.hidden) refreshController();
    }, 2000);
  }
}
function imageForRequest(request) {
  return (imageDownloadState?.available_images || []).find(item =>
    item.flavor === request.flavor && ['system', 'vendor'].every(kind =>
      item.images?.[kind]?.sha256 === request[kind + '_sha256']));
}
async function activateRequestImage(request) {
  if (!['create_image', 'reinstall'].includes(request.op) || request.flavor === 'installed') return;
  const item = imageForRequest(request);
  if (!item) throw new Error('Selected image is unavailable. Refresh the image library and choose it again.');
  const state = await controllerRequest({op:'image_select', image_id:item.library_id});
  if (state.status !== 'Ready' || state.flavor !== request.flavor ||
      !['system', 'vendor'].every(kind => state.images?.[kind]?.sha256 === request[kind + '_sha256'])) {
    throw new Error('Image selection changed. Refresh the image library and choose it again.');
  }
}
async function mutateController(request) {
  if (['start', 'stop'].includes(request.op)) {
    const record = runtimeRecord(request.id);
    if (!record || runtimeOperationBusy(request.id)) return;
    const targets = [{id:record.id, name:record.name}];
    if (request.op === 'stop') showRuntimeBatchDialog('stop', [record], 'Stop');
    else return runRuntimeBatchPlan({op:'start', targets, skipped:0});
    return;
  }
  if (runtimeOperations.size || controllerMutation || apkInstallBusy || (request.id === 'default' && (runtimeTransition || pendingLaunch || pendingAppAction))) return;
  controllerMutation = true;
  document.getElementById('runtime-rename-save').disabled = true;
  document.getElementById('runtime-rename-cancel').disabled = true;
  controllerError = '';
  controllerPending = { ...request, phase: 'wait' };
  document.getElementById('controller-message').textContent = 'Processing your request…';
  clearTimeout(controllerTimer);
  document.getElementById('controller-create-button').disabled = true;
  renderController();
  const message = document.getElementById('controller-message');
  let failure = null;
  let completionNotice = '';
  try {
    await controllerRefreshDone;
    await lazyFeatures.ensure('runtime');
    if (['prepare', 'stop'].includes(request.op)) { controllerPending.phase = 'confirm'; renderController(); }
    if (request.op === 'prepare' && !await AppDialog.confirm(
      runtimeRecord(request.id)?.image_selection
        ? 'Extract the selected downloaded images and build compatible Android support? This uses several GB at this runtime’s storage location. Android data will be created on first start.'
        : 'Copy the installed Android images into this runtime? This uses several GB of disk space. Android data will be created on first start.',
      { title: 'Prepare runtime images', kind: 'info' })) return;
    if (request.op === 'stop' && !await AppDialog.confirm(
      'Stop this Android runtime? Its apps will close; saved data will be retained.',
      { title: 'Stop runtime', kind: 'warning' })) return;
    if (request.op === 'existing_import' && !await AppDialog.confirm(
      'Copy Default runtime images, apps and Android data into a new managed runtime? This requires extra disk space. Keep Default runtime stopped until copying and verification finish. The original is retained. App logins and device-bound credentials may require signing in again.',
      {title:'Import Default runtime',kind:'info'})) return;
    if (request.op === 'reinstall' && !await AppDialog.confirm(
      `Reinstall this runtime with ${imageForRequest(request)?.custom_name || (request.flavor === 'installed' ? 'Existing Waydroid image' : 'Waydroid ' + request.flavor)}?\nSystem SHA-256: ${request.system_sha256 || 'installed source'}\n\nAndroid will stop first if running. All apps, data and settings will be erased. This cannot be undone.`,
      {title:'Reinstall runtime',kind:'warning'})) return;
    if (request.op === 'app_action') {
      controllerPending.phase = 'confirm'; renderController();
      const name = runtimeRecord(request.id)?.name || request.id;
      const desktop = (runtimeRecord(request.id)?.display?.effective_mode || runtimeRecord(request.id)?.display?.mode) === 'desktop';
      const prompt = request.action === 'launch'
        ? (desktop ? `Open ${request.package} from runtime ${name} on your desktop?` : `Launch ${request.package} in runtime ${name}? This runs inside headless Android; no desktop window will open yet.`)
        : `Force-stop ${request.package} in runtime ${name}? Unsaved app work may be lost.`;
      if (!await AppDialog.confirm(prompt, { title: 'Runtime app action', kind: request.action === 'launch' ? 'info' : 'warning' })) return;
    }
    controllerPending.phase = 'send';
    renderController();
    await activateRequestImage(request);
    if (request.op === 'reinstall') await RuntimeSettings.prepareReinstall(request, {
      send: query => refreshBatchRecord(query.id, 'reinstall'),
      transition: (op, id) => executeBatchOperation(runtimeRecord(id), op),
      progress: phase => {
        controllerPending.phase = phase;
        message.textContent = phase === 'stopping' ? 'Stopping Android before reinstall…' : 'Reinstalling…';
        renderController();
      },
    });
    const {result, restarted} = request.id
      ? await RuntimeSettings.apply(request, {
        send: controllerRequest,
        progress: phase => {
          controllerPending.phase = phase;
          message.textContent = ({stopping:'Stopping Android to apply settings…', applying:'Saving settings…', starting:'Restarting Android…'})[phase] + ' Keep AnvilDroid open until this finishes.';
          renderController();
        },
        transition: async (op, id) => {
          if (id === 'default') {
            const updated = await executeBatchOperation(runtimeRecord(id), op);
            Object.assign(runtimeRecord(id), updated);
            return;
          }
          const job = await controllerRequest({op, id});
          if (job?.id !== id || !job.job?.id) throw new Error('Invalid runtime transition response');
          const updated = await RuntimeSettings.wait(controllerRequest, id, job.job.id, op === 'stop' ? 'Stopped' : 'Running');
          Object.assign(runtimeRecord(id), updated);
        },
      }) : {result: await controllerRequest(request), restarted:false};
    if (request.op === 'existing_import' && result?.id) {
      selectedRuntimeId = result.id;
    }
    if (['create', 'create_image'].includes(request.op) && result?.id) {
      selectedRuntimeId = result.id;
      try { result.display = await controllerRequest({op:'display_set', id:result.id, mode:'desktop'}); }
      catch (error) { throw new Error(`Runtime ${result.name} was created, but Desktop mode could not be saved: ${error}. Select Use Desktop before starting it.`); }
    }
    if (request.op === 'display_set' || request.op === 'backend_set') {
      const record = runtimeRecord(request.id);
      if (record) record.display = result;
    } else if (request.op === 'resources_set') {
      const record = runtimeRecord(request.id);
      if (record) record.resources = result;
      runtimeResourceDraft.delete(request.id);
    } else if (request.op === 'gpu_set' || request.op === 'system_integration') {
      const record = runtimeRecord(request.id);
      if (record) record.gpu = result;
      runtimeGraphics.delete(request.id);
      runtimeHealth.delete(request.id);
      runtimeGpuDraft.delete(request.id);
    } else if (request.op === 'arm_set') {
      const record = runtimeRecord(request.id);
      if (record) record.arm = result;
    } else if (request.op === 'app_action' || request.op === 'delete') {
      const record = runtimeRecord(request.id);
      if (record) { record.appJob = result; if (request.op === 'delete') record.deletion_pending = true; }
    } else if (result?.id === 'default') {
      existingRuntime = {...existingRuntime, ...result};
    } else if (result?.id) {
      const position = controllerRecords.findIndex(record => record.id === result.id);
      if (position < 0) controllerRecords.push(result);
      else controllerRecords[position] = result;
    }
    controllerPending = null;
    completionNotice = ({resources_set: 'RAM and CPU limits saved. Start this runtime to apply them.', gpu_set: 'GPU saved. Start this runtime to apply it.', arm_set: `ARM translation ${request.enabled ? 'installed' : 'disabled'}. Start this runtime to apply it.`, delete: 'Deletion accepted. The runtime will disappear when its data has been removed.', display_set: 'Display mode saved. Start Android to apply it.', backend_set: 'Display backend saved. Start Android to apply it.', app_action: 'App action accepted. Watch its job result below.', create: 'Runtime created with Desktop mode. Next: prepare images.', rename: 'Runtime name saved.', prepare: 'Preparation accepted. Watch the runtime status below.', start: 'Start request accepted. The runtime card below shows boot progress and the result.', stop: 'Stop request accepted. The runtime card below shows progress and the result.', move: 'Storage move accepted. Keep both disks connected until verification completes.', discard_previous: 'Removal accepted. Watch storage status below; active runtime data is kept.', existing_import: 'Import started. Keep Default runtime stopped until preparation succeeds.'})[request.op] || 'Request accepted.';
    if (restarted) {
      completionNotice = 'Settings applied. Android restarted successfully.';
    }
    renderController();
    if (request.op === 'reinstall') {
      cancelReinstall();
      document.getElementById('runtime-create-panel').open = false;
      completionNotice = 'Runtime reinstall started. Wait for image preparation to finish before starting it.';
    }
    if (['create', 'create_image'].includes(request.op)) {
      document.getElementById('controller-name').value = '';
      document.getElementById('runtime-create-panel').open = false;
      if (request.op === 'create_image') completionNotice = `${request.flavor} runtime created. Prepare selects a compatible GPU and installs ARM translation.`;
    }
  } catch (error) { failure = String(error); }
  finally {
    controllerMutation = false;
    controllerPending = null;
    document.getElementById('runtime-rename-save').disabled = false;
    document.getElementById('runtime-rename-cancel').disabled = false;
    message.textContent = '';
    await refreshController();
    if (failure) {
      controllerError = `Runtime operation failed: ${failure}`;
      // Dialogs keep their own actionable errors beside the fields.
      if (!document.getElementById('runtime-rename-dialog').open && !document.getElementById('runtime-storage-dialog').open) showAppToast(controllerError, 'error');
    } else if (completionNotice) showAppToast(completionNotice, 'success');
  }
}
function submitRuntimeCreate(event) {
  event.preventDefault();
  const name = document.getElementById('controller-name').value.trim();
  const rawFlavor = document.getElementById('controller-image').value || 'installed';
  const flavor = rawFlavor.startsWith('custom:') ? 'CUSTOM' : rawFlavor;
  const selectedImage = selectedImageEntry();
  if (!name) return;
  if (!document.getElementById('controller-image').value) {
    document.getElementById('controller-message').textContent = 'Select an available image first.';
    return;
  }
  if (reinstallTargetId) {
    if (flavor === 'installed') { mutateController({ op: 'reinstall', id: reinstallTargetId, confirmed_name: name, flavor }); return; }
    const selected = selectedImage || imageDownloadState;
    if (selected?.status !== 'Ready' || selected.flavor !== flavor) {
      document.getElementById('controller-message').textContent = flavor === 'CUSTOM' ? 'Choose and import a custom image ZIP or image folder first.' : `Download and verify ${flavor} in Runtime images below first.`;
      return;
    }
    mutateController({op:'reinstall', id:reinstallTargetId, confirmed_name:name, flavor, system_sha256:selected.images.system.sha256, vendor_sha256:selected.images.vendor.sha256});
    return;
  }
  if (flavor === 'installed') { mutateController({ op: 'create', name }); return; }
  const state = selectedImage || imageDownloadState;
  if (state?.status !== 'Ready' || state.flavor !== flavor) {
    document.getElementById('controller-message').textContent = flavor === 'CUSTOM' ? 'Choose and import a custom image ZIP or image folder before creating this runtime.' : `Download and verify ${flavor} in Settings before creating this runtime.`;
    return;
  }
  mutateController({op:'create_image', name, flavor, system_sha256:state.images.system.sha256, vendor_sha256:state.images.vendor.sha256});
}
function cancelReinstall() {
  reinstallTargetId = null;
  document.getElementById('controller-name').readOnly = false;
  document.getElementById('controller-name').value = '';
  document.querySelector('#runtime-create-panel > summary').textContent = 'New runtime';
  document.getElementById('controller-create-button').textContent = 'Create runtime';
  document.getElementById('controller-reinstall-cancel').hidden = true;
}
document.getElementById('controller-reinstall-cancel').addEventListener('click', cancelReinstall);
document.getElementById('controller-create').addEventListener('submit', submitRuntimeCreate);
function openRuntimeSettings(id) {
  if (!runtimeRecord(id)) return;
  for (const key of ['gpu', 'resources', 'arm', 'display']) runtimeSettingsOpen.add(id + ':' + key);
  runtimeSettingsId = id;
  selectedRuntimeId = id;
  renderController();
  navigatePage('runtime-settings', false);
  controllerRefreshDone.then(() => {
    if (currentPage === 'runtime-settings' && runtimeSettingsId === id) return refreshController();
  });
  document.getElementById('runtime-settings-title').focus();
}
document.getElementById('runtime-settings-back').addEventListener('click', () => {
  navigatePage('runtime', false);
  document.querySelectorAll('[data-controller-op="settings"]').forEach(button => {
    if (button.dataset.controllerId === runtimeSettingsId) button.focus();
  });
});
function selectRuntime(id) {
  if (id !== 'default' && !controllerRecords.some(record => record.id === id)) return;
  selectedRuntimeId = selectedRuntimeId === id ? null : id;
  renderController();
  renderAvailableImages();
}
document.getElementById('runtime-table').addEventListener('click', event => {
  const action = event.target.closest('button');
  if (event.target.closest('input[type=checkbox], .runtime-checkbox-cell')) return;
  if (action && !action.hasAttribute('data-runtime-select')) return;
  const row = event.target.closest('[data-runtime-select]');
  if (row) selectRuntime(row.dataset.runtimeSelect);
});
function handleRuntimeAction(event) {
  const button = event.target.closest('[data-controller-op]');
  if (!button || button.disabled) return;
  if (button.dataset.controllerOp === 'settings') openRuntimeSettings(button.dataset.controllerId);
  else if (button.dataset.controllerOp === 'open_waydroid') {
    const record = runtimeRecord(button.dataset.controllerId);
    if (!record || controllerMutation || controllerLoading || runtimeBatchBusy || runtimeTransition || pendingLaunch || pendingAppAction || probeInstallBusy) return;
    runRuntimeBatchPlan({op:'open', targets:[{id:record.id, name:record.name}], skipped:0});
  }
  else if (button.dataset.controllerOp === 'restart') {
    const record = runtimeRecord(button.dataset.controllerId);
    if (record) showRuntimeBatchDialog('restart', [record], 'Restart');
  }
  else if (button.dataset.controllerOp === 'existing_apps') { setLibraryRuntimeFilter(['default']); navigatePage('apps'); }
  else if (button.dataset.controllerOp === 'existing_import') mutateController({op:'existing_import',id:'default',name:(existingRuntime?.name || 'Default runtime') + ' (managed copy)'});
  else if (button.dataset.controllerOp === 'reinstall') {
    beginReinstall(button.dataset.controllerId);
  }
  else if (button.dataset.controllerOp === 'app_action') mutateController({op:'app_action',id:button.dataset.controllerId,action:button.dataset.appAction,package:button.dataset.package});
  else if (button.dataset.controllerOp === 'backend_set') mutateController({op:'backend_set',id:button.dataset.controllerId,backend:button.dataset.backend,presenter:button.dataset.presenter});
  else if (button.dataset.controllerOp === 'display_set') mutateController({op:'display_set',id:button.dataset.controllerId,mode:button.dataset.displayMode});
  else if (button.dataset.controllerOp === 'arm_set') installRuntimeArm(button.dataset.controllerId);
  else if (button.dataset.controllerOp === 'arm_sources') {
    navigatePage('settings');
    renderArmSources();
    document.getElementById('arm-source-panel')?.scrollIntoView({block:'center',behavior:'smooth'});
  }
  else if (button.dataset.controllerOp === 'rename') openRuntimeRename(button.dataset.controllerId);
  else if (button.dataset.controllerOp === 'google_services') checkRuntimeGoogle(button.dataset.controllerId);
  else if (button.dataset.controllerOp === 'create_gapps') openGappsCreation();
  else if (button.dataset.controllerOp === 'google_guide') {
    tauriInvoke('open_google_play_guide').catch(error => { showAppToast(`Cannot open guide: ${error}`, 'error'); });
  }
  else if (button.dataset.controllerOp === 'google_register') {
    tauriInvoke('open_google_device_registration').catch(error => showAppToast(`Cannot open Google registration: ${error}`, 'error'));
  }
  else if (button.dataset.controllerOp === 'google_working') setGooglePlayWorks(button.dataset.controllerId, true);
  else if (button.dataset.controllerOp === 'google_remind') setGooglePlayWorks(button.dataset.controllerId, false);
  else if (button.dataset.controllerOp === 'copy_android_id') {
    const id = runtimeGoogle.get(button.dataset.controllerId)?.report?.gsf_android_id;
    if (id) copyTextToClipboard(id).then(() => showAppToast('GSF Android ID copied.', 'success'))
      .catch(error => showAppToast(`Cannot copy Android ID: ${error}`, 'error'));
  }
  else if (button.dataset.controllerOp === 'resources_set') {
    const id = button.dataset.controllerId;
    const record = runtimeRecord(id);
    const value = runtimeResourceDraft.get(id) || record?.resources?.configured;
    if (value) mutateController({op:'resources_set', id, ...value});
  }
  else if (button.dataset.controllerOp === 'gpu_check') checkRuntimeGraphics(button.dataset.controllerId);
  else if (button.dataset.controllerOp === 'gpu_set') {
    const id = button.dataset.controllerId;
    const record = runtimeRecord(id);
    mutateController({op:'gpu_set', id, node:runtimeGpuDraft.get(id) || record?.gpu?.selection || 'auto'});
  }
  else if (button.dataset.controllerOp === 'health') checkRuntimeHealth(button.dataset.controllerId);
  else if (['move', 'discard_previous', 'delete'].includes(button.dataset.controllerOp)) openRuntimeStorage(button.dataset.controllerId, button.dataset.controllerOp);
  else mutateController({ op: button.dataset.controllerOp, id: button.dataset.controllerId });
}

async function copyTextToClipboard(value) {
  if (window.__TAURI__?.clipboard?.writeText) {
    await window.__TAURI__.clipboard.writeText(value);
    return;
  }
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const field = document.createElement('textarea');
  field.value = value;
  field.setAttribute('readonly', '');
  field.style.position = 'fixed';
  field.style.opacity = '0';
  document.body.appendChild(field);
  field.select();
  if (!document.execCommand('copy')) throw new Error('Clipboard permission denied');
  field.remove();
}
document.getElementById('runtime-table').addEventListener('click', handleRuntimeAction);
document.getElementById('page-runtime-settings').addEventListener('click', handleRuntimeAction);
document.getElementById('google-registration-content').addEventListener('click', handleRuntimeAction);

function openRuntimeRename(id) {
  if (controllerMutation || controllerLoading) return;
  const record = runtimeRecord(id);
  if (!record) return;
  renameRuntimeId = id;
  document.getElementById('runtime-rename-name').value = record.name;
  document.getElementById('runtime-rename-error').textContent = '';
  document.getElementById('runtime-rename-dialog').showModal();
  document.getElementById('runtime-rename-name').focus();
}
async function saveRuntimeRename() {
  const id = renameRuntimeId;
  const name = document.getElementById('runtime-rename-name').value.trim();
  const error = document.getElementById('runtime-rename-error');
  if (!id || controllerMutation) return;
  if (!name || name.length > 128 || /[\x00-\x1f\x7f]/.test(name)) {
    error.textContent = 'Enter a name between 1 and 128 characters without control characters.';
    return;
  }
  // Capture ID before awaiting; polling cannot change the dialog's target.
  await mutateController({ op: 'rename', id, name });
  if (controllerError) error.textContent = controllerError;
  else { document.getElementById('runtime-rename-dialog').close(); renameRuntimeId = null; }
}
document.getElementById('runtime-rename-form').addEventListener('submit', event => {
  event.preventDefault();
  saveRuntimeRename();
});
document.getElementById('runtime-rename-cancel').addEventListener('click', () => {
  if (!controllerMutation) { document.getElementById('runtime-rename-dialog').close(); renameRuntimeId = null; }
});
document.getElementById('runtime-rename-dialog').addEventListener('cancel', event => {
  if (controllerMutation) event.preventDefault();
  else renameRuntimeId = null;
});

if (openSettingsOnLaunch) navigatePage('settings');
if (openControlsOnLaunch) navigatePage('controls');

document.getElementById('btn-waydroid-guide').addEventListener('click', async () => {
  try { await tauriInvoke('open_waydroid_setup_guide'); }
  catch (error) { showAppToast(`Cannot open guide: ${error}. See https://docs.waydro.id/usage/install-on-desktops`, 'error'); }
});

function openRuntimeStorage(id, operation) {
  if (controllerMutation) return;
  const record = runtimeRecord(id);
  if (!record || (operation !== 'delete' && !record.storage)) return;
  storageDialogTarget = { id, operation, name: record.name };
  const deleting = operation === 'delete';
  const removing = deleting || operation === 'discard_previous';
  document.getElementById('runtime-storage-title').textContent = deleting ? `Delete ${record.name}` : removing ? 'Remove retained copy' : `Move ${record.name}`;
  document.getElementById('runtime-storage-description').textContent = deleting
    ? `Permanently delete runtime "${record.name}" (${record.id}) and all its apps, app data, images and retained storage copies? This cannot be undone. Storage: ${record.storage?.path || 'This runtime’s storage'}. Type the runtime name to confirm.`
    : removing
    ? `Only this retained copy will be deleted: ${record.storage.previous_path}. The active runtime at ${record.storage.path} will be kept.`
    : `Current location: ${record.storage.path}. Choose a permanent local Linux folder owned by your account. Data, images and settings will be copied and verified. The old copy is retained until you remove it. Android must be stopped.`;
  document.getElementById('runtime-storage-path').value = '';
  document.getElementById('runtime-storage-confirm-name').value = '';
  document.getElementById('runtime-storage-error').textContent = '';
  document.getElementById('runtime-storage-picker').style.display = removing ? 'none' : 'flex';
  document.getElementById('runtime-storage-confirmation').style.display = removing ? 'block' : 'none';
  document.getElementById('runtime-storage-save').textContent = deleting ? 'Delete runtime' : removing ? 'Delete retained copy' : 'Move data';
  document.getElementById('runtime-storage-dialog').showModal();
}
document.getElementById('runtime-storage-browse').addEventListener('click', async () => {
  try {
    const selected = await window.__TAURI__.dialog.open({ directory: true, multiple: false, title: 'Choose runtime storage folder' });
    if (typeof selected === 'string') document.getElementById('runtime-storage-path').value = selected;
  } catch (error) {
    document.getElementById('runtime-storage-error').textContent = `Could not open folder picker: ${error}`;
  }
});
async function saveRuntimeStorage() {
  if (!storageDialogTarget || controllerMutation) return;
  const target = { ...storageDialogTarget };
  const error = document.getElementById('runtime-storage-error');
  let request = { op: target.operation, id: target.id };
  if (target.operation === 'move') {
    request.destination = document.getElementById('runtime-storage-path').value;
    if (!request.destination) { error.textContent = 'Choose a destination folder first.'; return; }
  } else {
    request.confirmed_name = document.getElementById('runtime-storage-confirm-name').value;
    if (request.confirmed_name !== target.name) { error.textContent = `Type exactly: ${target.name}`; return; }
  }
  document.getElementById('runtime-storage-save').disabled = true;
  document.getElementById('runtime-storage-cancel').disabled = true;
  try {
    await mutateController(request);
    if (controllerError) error.textContent = controllerError;
    else { document.getElementById('runtime-storage-dialog').close(); storageDialogTarget = null; }
  } finally {
    document.getElementById('runtime-storage-save').disabled = false;
    document.getElementById('runtime-storage-cancel').disabled = false;
  }
}
document.getElementById('runtime-storage-form').addEventListener('submit', event => { event.preventDefault(); saveRuntimeStorage(); });
document.getElementById('runtime-storage-cancel').addEventListener('click', () => {
  if (!controllerMutation) { document.getElementById('runtime-storage-dialog').close(); storageDialogTarget = null; }
});
document.getElementById('runtime-storage-dialog').addEventListener('cancel', event => {
  if (controllerMutation) event.preventDefault(); else storageDialogTarget = null;
});


function renderRuntimeGoogle(record) {
  const entry = runtimeGoogle.get(record.id);
  const running = ['Running', 'Frozen'].includes(record.state) && !record.deletion_pending;
  const busy = controllerMutation || record.appJob?.status === 'Running';
  const report = entry?.report;
  const packages = report?.packages;
  const button = (op, label, disabled, extra = '') => `<button class="btn btn-quiet" data-controller-op="${op}" data-controller-id="${escapeHtml(record.id)}" ${extra} ${disabled ? 'disabled' : ''}>${label}</button>`;
  const names = {'com.android.vending':'Play Store', 'com.google.android.gms':'Google Play services', 'com.google.android.gsf':'Google Services Framework'};
  const present = packages && Object.values(packages).filter(Boolean).length;
  const acknowledged = googlePlayWorks(record.id);
  let text = !running ? '<p class="hint">Start Android to check Google services.</p>' : '<p class="hint">Check Google components and GSF ID.</p>';
  if (entry?.loading) text += '<p role="status">Checking Google services…</p>';
  if (entry?.error) text += `<p class="runtime-feedback error" role="alert">${escapeHtml(entry.error)}</p>`;
  if (packages) {
    text += `<p class="hint">Last check: ${escapeHtml(new Date(report.checked_at * 1000).toLocaleTimeString())}${running ? '' : ' · saved result; runtime is not running'}</p>`;
    text += Object.entries(names).map(([pkg,name]) => `<p>${name}: <strong>${packages[pkg] ? 'Installed' : 'Not installed'}</strong></p>`).join('');
    if (present === 3 && !acknowledged) {
      text += `<details class="play-certification-help" role="note"><summary>Device registration</summary><p>New GAPPS runtimes may show “This device isn’t Play Protect certified”. Register this runtime once:</p><ol><li>Copy the GSF Android ID shown here.</li><li>Click Open Google registration, sign in to Google, paste this ID and submit.</li><li>Wait a few minutes, then restart this runtime and open Play Store again.</li></ol><p class="hint">Registration is tied to this Android runtime. Repeat it after creating a new runtime.</p></details>`;
    } else if (present !== 3) {
      text += `<p class="hint">${present ? 'Google components are incomplete. Installing only the Play Store APK does not provide a complete Google services setup.' : 'No Google components detected. A compatible GAPPS image is required for Google Play.'}</p>`;
    }
  }
  const gsfId = typeof report?.gsf_android_id === 'string' && /^[0-9]{1,20}$/.test(report.gsf_android_id) ? report.gsf_android_id : null;
  text += `<div class="android-id-copy"><span>GSF Android ID</span><code>${gsfId ? escapeHtml(gsfId) : 'Not available yet'}</code>${button('copy_android_id', 'Copy Android ID', !gsfId || !running || !!entry?.error || !!entry?.loading)}</div>`;
  if (!gsfId && report?.gsf_id_error) text += `<p class="runtime-feedback warning" role="status">${escapeHtml(report.gsf_id_error)}</p>`;
  if (!gsfId) text += `<p class="hint">${!running ? 'Start this runtime, then click Check Google services.' : !packages ? 'Click Check Google services to read the registration ID.' : packages['com.google.android.gsf'] ? 'Open Play Store, wait a few minutes, then check again. After updating AnvilDroid, stop and start this runtime to load the new worker.' : 'Google Services Framework is required. Use a GAPPS runtime.'}</p>`;
  const desktop = (record.display?.effective_mode || record.display?.mode) === 'desktop';
  const canOpen = running && packages?.['com.android.vending'] && !entry?.error && !entry?.loading && desktop && !busy;
  text += '<div class="runtime-actions">' + button('google_services', entry?.loading ? 'Checking…' : 'Check Google services', !running || busy || entry?.loading);
  text += button('app_action', 'Open Play Store', !canOpen, canOpen ? 'data-app-action="launch" data-package="com.android.vending"' : '');
  text += button('google_register', 'Open Google registration', false);
  text += button(acknowledged ? 'google_remind' : 'google_working', acknowledged ? 'Show setup reminder again' : 'Google Play works — hide reminder', false);
  text += button('google_guide', 'Certification guide', false) + '</div>';
  if (acknowledged) text += '<p role="status">Google Play marked as working by you. Setup reminder hidden for this runtime; this is not a certification check.</p>';
  if (entry?.result && !entry.loading) text += `<div class="google-check-result" role="status" aria-live="polite"><strong>Check complete</strong><p>${escapeHtml(entry.result)}</p><p>This checks installed components and the GSF ID only. Google registration status cannot be verified by AnvilDroid.</p></div>`;
  if (!acknowledged) text += '<details class="play-certification-help"><summary>Already registered?</summary><p>Allow time for Google to process your registration. Save your work, stop and start this runtime, then open Play Store and try signing in or downloading an app.</p><p class="hint">To inspect Play Protect certification, open Play Store &gt; profile icon &gt; Settings &gt; About. Registering a GSF ID does not guarantee a certified label or compatibility with every app. Repeating this check does not submit or verify your registration.</p></details>';
  if (packages && present === 0) text += '<p class="runtime-feedback warning">Google services are not installed in this runtime. A downloaded GAPPS image is not applied to existing runtimes.</p>' + button('create_gapps', 'Create GAPPS runtime…', busy);
  if (running && packages?.['com.android.vending'] && !desktop) text += '<p class="hint">Use Desktop mode to open Play Store.</p>';
  return text;
}
async function checkRuntimeGoogle(id) {
  if (controllerMutation || runtimeGoogle.get(id)?.loading) return;
  const record = googleRegistrationTarget === id ? googleRegistrationRecord(id) : runtimeRecord(id);
  if (!record || !['Running', 'Frozen'].includes(record.state) || record.deletion_pending || record.appJob?.status === 'Running') return;
  const dialog = document.getElementById('google-registration-dialog');
  const scrollTop = dialog.scrollTop;
  const redraw = () => {
    if (googleRegistrationTarget === id) {
      renderGoogleRegistrationDialog();
      dialog.scrollTop = scrollTop;
    } else renderController();
  };
  runtimeGoogle.set(id, {report:runtimeGoogle.get(id)?.report, loading:true});
  redraw();
  try {
    const report = await controllerRequest({op:'google_services',id});
    const keys = ['com.android.vending','com.google.android.gms','com.google.android.gsf'];
    if (report?.runtime_id !== id || !report.generation || !Number.isFinite(report.checked_at) || !report.packages || Object.keys(report.packages).length !== 3 || !keys.every(key => typeof report.packages[key] === 'boolean')) throw new Error('Invalid Google services result');
    const count = keys.filter(key => report.packages[key]).length;
    const hasId = typeof report.gsf_android_id === 'string' && /^[0-9]{1,20}$/.test(report.gsf_android_id);
    const result = `${count}/3 Google components installed. ${hasId ? 'GSF Android ID read successfully; it is ready to copy.' : 'GSF Android ID is unavailable; see the details below.'}`;
    runtimeGoogle.set(id,{report,result});
  } catch (error) { runtimeGoogle.set(id,{error:`Google services check failed: ${error}`}); }
  redraw();
  renderGoogleNotices();
}

function handleRuntimeConfigChange(event) {
  const id = event.target.dataset?.gpuSelect;
  if (id) runtimeGpuDraft.set(id, event.target.value);
  const resourceId = event.target.dataset?.resourceId;
  if (event.target.dataset?.resourceField === 'preset' && resourceId) {
    applyResourcePreset(resourceId, event.target.value);
  }
}

function handleRuntimeConfigInput(event) {
  const id = event.target.dataset?.resourceId;
  const field = event.target.dataset?.resourceField;
  if (!id || !['memory_mib', 'cpu_count'].includes(field)) return;
  const record = runtimeRecord(id);
  if (!record?.resources) return;
  runtimeResourceDraft.set(id, {...(runtimeResourceDraft.get(id) || record.resources.configured), [field]: Number(event.target.value)});
  document.querySelectorAll('select[data-resource-id]').forEach(select => { if (select.dataset.resourceId === id) select.value = 'custom'; });
  updateResourceBudget(id);
}
for (const id of ['runtime-table', 'page-runtime-settings']) {
  document.getElementById(id).addEventListener('change', handleRuntimeConfigChange);
  document.getElementById(id).addEventListener('input', handleRuntimeConfigInput);
}

function applyResourcePreset(id, name) {
  const record = runtimeRecord(id);
  if (!record || controllerMutation || !['Prepared','Stopped','Error'].includes(record.state)) return;
  const preset = record.resources?.presets?.find(p => p.name === name);
  if (!preset) return;
  runtimeResourceDraft.set(id, {memory_mib:preset.memory_mib, cpu_count:preset.cpu_count});
  // Update fields in place: replacing the table here would close native controls.
  document.querySelectorAll('input[data-resource-id]').forEach(input => {
    if (input.dataset.resourceId === id) input.value = String(preset[input.dataset.resourceField]);
  });
  updateResourceBudget(id);
}

function resourceBudgetHtml(data, draft) {
  const b = data.budget;
  if (!b) return `<p class="hint">${escapeHtml(data.scope || 'Resource budget is unavailable; update the controller and refresh.')}</p>`;
  const blocked = draft.memory_mib > 0 && Math.max(0, draft.memory_mib - b.target_current_mib) > b.available_for_growth_mib;
  const cpuOver = draft.cpu_count > 0 && draft.cpu_count + b.cpu_quota_count > data.host.cpu_count;
  return `<details><summary>Host budget</summary><p class="hint">Other active runtimes (your account): ${b.active_count} · RAM limits ${b.memory_limit_mib} MiB · CPU quotas ${b.cpu_quota_count}</p>
    <p class="hint">RAM headroom for additional usage: ${b.available_for_growth_mib} MiB, after ${b.host_reserve_mib} MiB host margin and ${b.unused_memory_mib} MiB potential growth of other runtimes.</p><p class="hint">Estimate only; checked again on Start.</p></details>
    ${blocked ? '<p class="runtime-blocked">Insufficient RAM headroom to start. Lower the limit or stop another runtime.</p>' : ''}
    ${cpuOver ? '<p class="runtime-blocked">CPU quotas exceed host capacity.</p>' : ''}
    ${!draft.memory_mib || b.unbounded_memory_count || b.unbounded_cpu_count || b.unknown_count ? '<p class="runtime-blocked">Some resource limits are unlimited or unknown.</p>' : ''}
    `;
}

function updateResourceBudget(id) {
  const record = runtimeRecord(id);
  if (!record?.resources) return;
  const panel = document.getElementById(`resource-budget-${id}`);
  if (panel) panel.innerHTML = resourceBudgetHtml(record.resources, runtimeResourceDraft.get(id) || record.resources.configured);
}

function renderResourceControls(record, busy) {
  const data = record.resources;
  if (!data?.host || !data?.configured) return `<p class="runtime-blocked">${escapeHtml(record.resourcesError || 'Loading resource limits…')}</p>`;
  const draft = runtimeResourceDraft.get(record.id) || data.configured;
  const host = data.host;
  const disabled = busy || !['Prepared','Stopped','Error','Running','Frozen'].includes(record.state) || !host.supported;
  const selectedPreset = (data.presets || []).find(p => p.memory_mib === draft.memory_mib && p.cpu_count === draft.cpu_count)?.name || 'custom';
  const memory = bytes => bytes == null ? 'No explicit limit' : `${(bytes / 1024**2).toFixed(0)} MiB`;
  return `<p class="hint">Host: ${host.memory_total_mib} MiB RAM · ${host.memory_available_mib} MiB available · ${host.cpu_count} logical CPUs</p>
    <label>Preset <select data-resource-id="${escapeHtml(record.id)}" data-resource-field="preset" ${disabled ? 'disabled' : ''}>
      ${(data.presets || []).map(p => `<option value="${escapeHtml(p.name)}" ${selectedPreset === p.name ? 'selected' : ''}>${escapeHtml(p.name)} · ${p.memory_mib || 'unlimited'}${p.memory_mib ? ' MiB' : ''} / ${p.cpu_count || 'unlimited'} CPU</option>`).join('')}
      <option value="custom" ${selectedPreset === 'custom' ? 'selected' : ''}>Custom</option></select></label>
    <div class="runtime-resource-fields"><label>RAM limit (MiB)<input type="number" min="0" max="${Math.max(0, host.memory_total_mib - 1024)}" step="1" value="${escapeHtml(String(draft.memory_mib))}" data-resource-id="${escapeHtml(record.id)}" data-resource-field="memory_mib" ${disabled ? 'disabled' : ''}></label>
    <label>CPU limit<input type="number" min="0" max="${host.cpu_count}" step="1" value="${escapeHtml(String(draft.cpu_count))}" data-resource-id="${escapeHtml(record.id)}" data-resource-field="cpu_count" ${disabled ? 'disabled' : ''}></label></div>
    <p class="hint" title="CPU limits total CPU time. RAM limits disable swap; exhausting RAM may stop Android. Resources are shared, not reserved.">0 = unlimited · RAM minimum: 1024 MiB</p>
    <button class="btn btn-quiet" data-controller-op="resources_set" data-controller-id="${escapeHtml(record.id)}" ${disabled ? 'disabled' : ''}>${['Running', 'Frozen'].includes(record.state) ? 'Save & restart' : 'Save limits'}</button>
    <p class="hint">Saved: RAM ${data.configured.memory_mib || 'unlimited'}${data.configured.memory_mib ? ' MiB' : ''} · CPU ${data.configured.cpu_count || 'unlimited'}. ${['Running', 'Frozen'].includes(record.state) ? 'Saving restarts Android; apps will close.' : 'Applies on next start.'}</p>
    ${disabled ? `<p class="hint">${escapeHtml(host.reason || 'Stop the runtime to change limits.')}</p>` : ''}
    <div id="resource-budget-${escapeHtml(record.id)}">${resourceBudgetHtml(data, draft)}</div><details class="runtime-resource-diagnostics"><summary>Active limits</summary>
    ${data.effective ? `<div class="runtime-graphics"><strong>Active limits · kernel readback</strong><p>RAM: ${memory(data.effective.memory_current_bytes)} used / ${memory(data.effective.memory_max_bytes)}</p><p>CPU quota: ${data.effective.cpu_quota_count ?? 'No explicit quota'} · throttled: ${(data.effective.cpu_throttled_usec / 1000000).toFixed(2)} s</p><p>Swap: ${memory(data.effective.swap_current_bytes)} used / ${memory(data.effective.swap_max_bytes)} · OOM kills: ${data.effective.oom_kills}</p><p class="hint">Host or parent limits may be tighter. Values refresh automatically.</p></div>` : `<p class="hint">${escapeHtml(data.error || 'Active limits are measured after Start.')}</p>`}</details>`;
}

function renderGpuControls(record, busy) {
  if (!record.gpu) return `<p class="runtime-blocked">${escapeHtml(record.gpuError || 'Loading GPU configuration…')}</p>`;
  const selected = runtimeGpuDraft.get(record.id) || record.gpu.selection;
  const stopped = ['Prepared', 'Stopped', 'Error'].includes(record.state);
  const canConfigure = stopped || ['Running', 'Frozen'].includes(record.state);
  const devices = record.gpu.devices || [];
  const defaultGpuWarning = record.id === 'default' && !record.gpu.system_nvidia_supported ? `<div class="runtime-gpu-warning" role="note"><p>NVIDIA integration is not active. Stop Default runtime, then enable integration. Intel/AMD remain available.${devices.some(d => d.node === record.gpu.selection && (d.vendor_id === '0x10de' || d.driver === 'nvidia')) ? ' Current NVIDIA selection cannot boot until integration is active.' : ''}</p><button class="btn btn-quiet" data-controller-op="system_integration" data-controller-id="default" ${busy || !stopped ? 'disabled' : ''}>Enable NVIDIA integration</button></div>` : '';
  return `${defaultGpuWarning}<div class="runtime-gpu-controls"><label>GPU <select data-gpu-select="${escapeHtml(record.id)}" ${busy || !canConfigure ? 'disabled' : ''}>
    <option value="auto" ${selected === 'auto' ? 'selected' : ''}>${'Automatic (recommended)'}</option>
    ${record.gpu.software_supported ? `<option value="software" ${selected === 'software' ? 'selected' : ''}>CPU · software rendering</option>` : ''}
    ${selected !== 'auto' && !(selected === 'software' && record.gpu.software_supported) && !devices.some(d => d.node === selected) ? `<option selected disabled value="${escapeHtml(selected)}">${escapeHtml(selected)} · unavailable</option>` : ''}
    ${devices.map(d => `<option value="${escapeHtml(d.node)}" ${d.node === selected ? 'selected' : ''} ${d.selectable ? '' : 'disabled'}>${escapeHtml(d.driver || 'Unknown driver')}${d.experimental ? ' · Experimental' : ''} · ${escapeHtml(d.node)} · ${escapeHtml(d.vendor_id || '?')}:${escapeHtml(d.device_id || '?')}${d.selectable ? '' : ' · ' + escapeHtml(d.reason || 'unsupported / unavailable')}</option>`).join('')}
  </select></label><button class="btn btn-quiet" data-controller-op="gpu_set" data-controller-id="${escapeHtml(record.id)}" ${busy || !canConfigure ? 'disabled' : ''}>${['Running', 'Frozen'].includes(record.state) ? 'Save & restart' : 'Save graphics'}</button></div>
  ${record.gpu.software_supported ? `<p class="hint">${escapeHtml('Selects compatible hardware automatically.')}</p>` : ''}
  ${record.gpu.nvidia ? `<details class="runtime-graphics"><summary>NVIDIA compatibility</summary><p class="hint">${escapeHtml(record.gpu.nvidia.detail)}</p><ul>${record.gpu.nvidia.checks.map(c => `<li>${escapeHtml(c.status)}: ${escapeHtml(c.detail)}</li>`).join('')}</ul></details>` : ''}
  <p class="hint">${['Running', 'Frozen'].includes(record.state) ? 'Saving restarts Android.' : stopped ? 'Applies on next start.' : 'Prepare runtime to change GPU.'}</p>`;;
}

async function checkRuntimeGraphics(id) {
  if (controllerMutation || runtimeGraphics.get(id)?.loading) return;
  runtimeGraphics.set(id, {loading:true});
  renderController();
  document.getElementById(`runtime-gpu-${id}`)?.scrollIntoView?.({block:'nearest'});
  try {
    const report = await controllerRequest({op:'health', id});
    if (report?.runtime_id !== id || !Array.isArray(report.checks)) throw new Error('Invalid graphics report');
    runtimeGraphics.set(id, {report});
  } catch (error) { runtimeGraphics.set(id, {error:`GPU check failed: ${error}`}); }
  renderController();
  document.getElementById(`runtime-gpu-${id}`)?.scrollIntoView?.({block:'nearest'});
}

function invalidateRuntimeGraphics(previous, current) {
  if (previous && (previous.state !== current.state || previous.job?.id !== current.job?.id || previous.gpu?.selection !== current.gpu?.selection)) {
    runtimeGraphics.delete(current.id);
    runtimeHealth.delete(current.id);
  }
}

function renderRuntimeGraphics(id) {
  const entry = runtimeGraphics.get(id) || runtimeHealth.get(id);
  if (!entry) return '';
  if (entry.loading) return '<p role="status">Checking GPU and Android renderer…</p>';
  if (entry.error) return `<p class="runtime-feedback error" role="alert">${escapeHtml(entry.error)}</p>`;
  const report = entry.report;
  const verification = report.gpu_verification;
  const label = {verified:'GPU verified · Android compositor', mismatch:'GPU mismatch', software:'Software rendering · CPU', unknown:'GPU usage not verified'}[verification?.status] || 'GPU usage not verified';
  const statusClass = verification?.status === 'verified' ? 'setup-ready' : 'runtime-blocked';
  return `<div class="runtime-graphics"><p class="${statusClass}"><strong>${label}</strong></p><p class="hint">${escapeHtml(verification?.detail || 'Check again after starting this runtime with the latest worker.')}</p>${report.gpu_selection ? `<p>Saved selection: ${escapeHtml(report.gpu_selection)}</p>` : ''}${report.graphics?.android_node ? `<p>Android GBM device: ${escapeHtml(report.graphics.android_node)}</p>` : ''}${report.graphics?.compositor_nodes?.length ? `<p>Opened by compositor: ${report.graphics.compositor_nodes.map(escapeHtml).join(', ')}</p>` : ''}<strong>Graphics</strong>${report.gpu_node ? `<p>Active render node: ${escapeHtml(report.gpu_node)}</p>` : ''}<p>${report.graphics?.gles ? escapeHtml(report.graphics.gles) : 'Runtime renderer not measured.'}</p>
    ${report.graphics ? `<p class="hint">${escapeHtml(report.graphics.scope || '')}${report.graphics.renderer_kind === 'software' ? ' Software renderer detected.' : ''}</p>` : ''}
    ${(report.gpu_devices || []).map(device => `<p>${escapeHtml(device.node)} · ${escapeHtml(device.driver || 'Unknown driver')} · ${escapeHtml(device.vendor_id || '?')}:${escapeHtml(device.device_id || '?')}${device.available ? '' : ' · Unavailable'}</p>`).join('') || '<p class="hint">No host render nodes detected.</p>'}
    <p class="hint">Host devices are an inventory, not proof of which GPU a game uses.</p></div>` +
    (report.checks || []).filter(check => check.name === 'Graphics').map(check => `<p class="runtime-blocked">${escapeHtml(check.detail)}</p>`).join('') +
    `<p class="hint">Snapshot · ${escapeHtml(new Date(report.checked_at * 1000).toLocaleTimeString())}</p>`;
}

function renderRuntimeHealth(id) {
  const entry = runtimeHealth.get(id);
  if (!entry) return '';
  if (entry.loading) return '<p role="status">Checking graphics, worker and storage…</p>';
  if (entry.error) return `<p class="runtime-feedback error" role="alert">${escapeHtml(entry.error)}</p>`;
  const report = entry.report;
  const gib = value => (value / 1024 ** 3).toFixed(2);
  return `<p class="hint">Health snapshot · ${escapeHtml(new Date(report.checked_at * 1000).toLocaleTimeString())}</p>` +
    (report.free_bytes != null ? `<p>Storage: ${gib(report.free_bytes)} GiB free / ${gib(report.total_bytes)} GiB total</p>` : '') +
    `<p class="runtime-storage-path">${escapeHtml(report.storage_path)}</p>` +
    (report.worker_state ? `<p>Worker: ${escapeHtml(report.worker_state)}</p>` : '') +
    report.checks.map(check => `<p class="${check.status === 'error' ? 'runtime-feedback error' : check.status === 'warning' ? 'runtime-blocked' : 'setup-ready'}"><strong>${escapeHtml(check.name)}:</strong> ${escapeHtml(check.detail)}</p>`).join('');
}
async function checkRuntimeHealth(id) {
  if (controllerMutation || runtimeHealth.get(id)?.loading) return;
  runtimeHealth.set(id, { loading: true });
  renderController();
  try {
    const report = await controllerRequest({ op: 'health', id });
    if (report?.runtime_id !== id || !Array.isArray(report.checks)) throw new Error('Invalid health report');
    runtimeHealth.set(id, { report });
  } catch (error) { runtimeHealth.set(id, { error: `Health check failed: ${error}` }); }
  renderController();
}


function renderManagedLibraryStatus() {
  btnInstallApk.disabled = pendingAppAction || pendingLaunch || runtimeTransition || controllerMutation || runtimeBatchBusy || Boolean(probeInstallBusy);
  btnInstallApk.title = 'Choose APK and runtimes';
  const records = managedLibrary.filter(record => libraryRuntimeSelected(record.id));
  const markup =
    (managedLibraryError ? `<p>${escapeHtml(managedLibraryError)}</p>` : '') + records.map(record => {
      const count = managedSnapshots.get(record.id)?.length;
      const note = record.error || (!['Running', 'Frozen'].includes(record.state) ? 'Select this runtime to start it.' : `${count ?? 0} apps`);
      return `<p><strong>${escapeHtml(record.name)}</strong> · ${escapeHtml(record.state)} · ${escapeHtml(note)}${managedPreferenceErrors.has(record.id) ? ` · ${escapeHtml(managedPreferenceErrors.get(record.id))}` : ''}</p>`;
    }).join('');
  const target = document.getElementById('library-runtime-status');
  if (target.innerHTML !== markup) target.innerHTML = markup;
}
async function refreshManagedLibrary({poll = false} = {}) {
  if (managedLibraryLoading) return;
  managedLibraryLoading = true;
  try {
    const records = await controllerRequest({op:'list'});
    if (!Array.isArray(records)) throw new Error('Invalid runtime list');
    const results = await Promise.all(records.map(async record => {
      let current = record;
      try {
        current = await controllerRequest({op:'refresh', id:record.id});
        if (current?.id !== record.id) throw new Error('Invalid runtime state');
        if (!preferenceBusy && !pendingAppAction) {
          const version = managedPreferenceVersions.get(record.id) || 0;
          try {
            const prefs = await tauriInvoke('get_library', {runtimeId:record.id});
            if ((managedPreferenceVersions.get(record.id) || 0) === version) saveManagedPreferences(record.id, prefs);
          } catch (error) {
            if ((managedPreferenceVersions.get(record.id) || 0) === version) managedPreferenceErrors.set(record.id, `Cannot load favorites/history: ${error}`);
          }
        }
        if (!managedSnapshots.has(record.id)) {
          try {
            const saved = JSON.parse(localStorage.getItem(`anvildroid_managed_apps:${record.id}`) || 'null');
            if (Array.isArray(saved) && saved.every(app => typeof app.package === 'string' && app.launchable === true)) managedSnapshots.set(record.id, saved);
          } catch (_) { /* A corrupt cache is not an app inventory. */ }
        }
        if (['Running', 'Frozen'].includes(current.state) && !pendingAppAction && !pendingLaunch && (!poll || !managedSnapshots.has(record.id) || Date.now() - (managedInventoryChecked.get(record.id) || 0) >= 60000 || managedLibrary.find(r => r.id === record.id)?.state !== current.state)) {
          const report = await controllerRequest({op:'apps', id:record.id});
          if (report?.runtime_id !== record.id || !Array.isArray(report.apps)) throw new Error('Invalid app inventory');
          if (!report.apps.every(app => typeof app.package === 'string' && app.launchable === true)) throw new Error('Stop and start on the Runtime page to update launcher filtering.');
          managedSnapshots.set(record.id, report.apps);
          managedInventoryChecked.set(record.id, Date.now());
          try { localStorage.setItem(`anvildroid_managed_apps:${record.id}`, JSON.stringify(report.apps)); } catch (_) {}
        }
        return current;
      } catch (error) {
        // A launch owns the worker for its startup check after the window maps.
        // BUSY says nothing about runtime health or the cached app inventory.
        return controllerBusy(error) ? current : {...record, error:String(error)};
      }
    }));
    managedLibrary = results;
    managedLibraryError = '';
    if (libraryRuntimeSelection !== null) libraryRuntimeSelection = new Set([...libraryRuntimeSelection].filter(id => id === 'default' || results.some(record => record.id === id)));
    renderRuntimeFilter();
  } catch (error) {
    managedLibraryError = `Cannot load managed runtimes: ${error}. Refresh to retry.`;
    managedLibrary = managedLibrary.map(record => ({...record,error:managedLibraryError}));
  } finally {
    managedLibraryLoading = false;
    if (!appStatesLoading) { renderManagedLibraryStatus(); renderApps(allApps); }
    if(libraryPage&&!document.hidden&&!appStatesLoading) { clearTimeout(appStatesTimer); appStatesTimer=setTimeout(refreshAppStates,5000); }
  }
}
let managedLaunchTail = null;
const queuedManagedLaunches = new Map();
async function managedAppAction(action, pkg, runtimeId) {
  if (action !== 'launch') return runManagedAppAction(action, pkg, runtimeId);
  const key = `${runtimeId}\u0000${pkg}`;
  if (queuedManagedLaunches.has(key)) return queuedManagedLaunches.get(key);
  const previous = managedLaunchTail;
  const task = previous ? previous.then(() => {
    queuedManagedLaunches.delete(key);
    return runManagedAppAction(action, pkg, runtimeId);
  }) : runManagedAppAction(action, pkg, runtimeId);
  if (previous) queuedManagedLaunches.set(key, task);
  managedLaunchTail = task;
  try { return await task; }
  finally { if (managedLaunchTail === task) managedLaunchTail = null; }
}
async function runManagedAppAction(action, pkg, runtimeId) {
  if(probeInstallBusy)return;
  if (pendingLaunch || pendingAppAction) return;
  pendingAppAction = true;
  const record = managedLibrary.find(record => record.id === runtimeId);
  const notify = showAppToast;
  const appName = (managedSnapshots.get(runtimeId) || []).find(app => app.package === pkg)?.label || pkg;
  try {
    if (!record || !['Running', 'Frozen'].includes(record.state) || record.error) throw new Error('Start this runtime on the Runtime page, then refresh its apps.');
    if (!(managedSnapshots.get(runtimeId) || []).some(app => app.package === pkg && app.launchable === true)) throw new Error('Refresh the app list before retrying.');
    if (action === 'uninstall' && !await AppDialog.confirm(`Uninstall ${appName} (${pkg}) from runtime ${record.name}? This removes its data in this runtime.`, {title:'Uninstall app',kind:'warning'})) return;
    if (action === 'force_stop' && !await AppDialog.confirm(`Force-stop ${pkg} in runtime ${record.name}? Unsaved app work may be lost.`, {title:'Force-stop app',kind:'warning'})) return;
    const display = await controllerRequest({op:'display_info',id:runtimeId});
    if (action === 'launch' && (display.effective_mode || display.mode) !== 'desktop') throw new Error('Select Desktop mode on the Runtime page and restart this runtime to open app windows.');
    notify(`${action === 'launch' ? 'Opening' : action === 'uninstall' ? 'Uninstalling' : 'Stopping'} ${appName} · ${record.name}…`, 'info');
    let job = await controllerRequest({op:'app_action',id:runtimeId,action,package:pkg});
    const jobId = job?.id;
    if (!jobId || job.runtime_id !== runtimeId || job.package !== pkg || job.action !== action) throw new Error('Invalid app job');
    for (let attempt = 0; job.status === 'Running' && attempt < 90; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 500));
      job = await controllerRequest({op:'app_job',id:runtimeId});
      if (job?.id !== jobId || job.runtime_id !== runtimeId || job.package !== pkg || job.action !== action) throw new Error('App job changed; check this runtime on the Runtime page.');
    }
    if (job.status !== 'Succeeded') throw new Error(job.error || 'App action is still pending. Check its result on the Runtime page.');
    if (action === 'uninstall') {
      const remaining = (managedSnapshots.get(runtimeId) || []).filter(app => app.package !== pkg);
      managedSnapshots.set(runtimeId, remaining);
      try { localStorage.setItem(`anvildroid_managed_apps:${runtimeId}`, JSON.stringify(remaining)); } catch (_) {}
      renderApps(allApps);
      await refreshManagedLibrary();
    }
    let historyError = '';
    if (action === 'launch') {
      managedPreferenceVersions.set(runtimeId, (managedPreferenceVersions.get(runtimeId) || 0) + 1);
      try {
        saveManagedPreferences(runtimeId, await tauriInvoke('record_runtime_launch', {runtimeId,package:pkg,jobId}));
        renderApps(allApps);
      } catch (error) { historyError = ` App opened, but recent history could not be saved: ${error}`; }
    }
    notify(action === 'launch' ? `Launch requested: ${appName} · ${record.name}${historyError}` : `${record.name} · ${pkg}: ${job.result?.message || 'Done'}${historyError}`, historyError ? 'info' : 'success');
  } catch (error) { notify(`${record?.name || runtimeId} · ${appName}: ${error.message || error}`, 'error'); }
  finally { pendingAppAction = false; }
}


function saveManagedPreferences(id, prefs) {
  if (!prefs || !Array.isArray(prefs.favorites) || !prefs.recent || typeof prefs.recent !== 'object' || Array.isArray(prefs.recent)) throw new Error('Invalid library preferences');
  managedPreferences.set(id,prefs);
  managedPreferenceErrors.delete(id);
}
async function toggleManagedFavorite(pkg, runtimeId) {
  if (preferenceBusy || pendingLaunch || pendingAppAction) return;
  const prefs = managedPreferences.get(runtimeId);
  if (!prefs) { showAppToast('Favorites are unavailable for this runtime. Refresh to retry.', 'error'); return; }
  preferenceBusy = true;
  managedPreferenceVersions.set(runtimeId, (managedPreferenceVersions.get(runtimeId) || 0) + 1);
  try {
    saveManagedPreferences(runtimeId, await tauriInvoke('set_favorite', {runtimeId,package:pkg,enabled:!prefs.favorites.includes(pkg)}));
    renderApps(allApps);
    renderManagedLibraryStatus();
  } catch (error) { showAppToast(`Cannot save favorite for this runtime: ${error}`, 'error'); }
  finally { preferenceBusy = false; }
}

const ARM_BUILTIN_SHA = 'a142d1586c9eafb5edf62277110f2128bc03066179a6783bcaa33ee322e1cbd0';
function runtimeArmSources(arm) {
  const sources = [...(arm.sources || [])];
  if (!sources.some(source => source.sha256 === ARM_BUILTIN_SHA)) sources.unshift({engine:'libndk', version:'0.2.3', sha256:ARM_BUILTIN_SHA, installed:Boolean(arm.available), label:'libndk_translation 0.2.3'});
  return sources;
}
function renderRuntimeArm(record, busy) {
  const arm = record.arm;
  if (!arm) return `<p class="hint">${escapeHtml(record.armError || 'Loading ARM configuration…')}</p>`;
  if (arm.inherited && !arm.supported) return '<p class="hint">ARM translation is provided by this image. Imported system copies retain their installed bridge.</p>';
  if (arm.default_enabled && !arm.supported && ['Allocated', 'Provisioning', 'Error'].includes(record.state)) return '<p class="hint">ARM32 / ARM64 translation will be enabled automatically during Prepare. Choose another engine or version after preparation.</p>';
  const sources = runtimeArmSources(arm);
  const current = sources.find(source => source.sha256 === arm.archive_sha256);
  const draft = runtimeArmDraft.get(record.id) || {engine:arm.enabled ? current?.engine || 'libndk' : 'none', sha256:arm.archive_sha256 || ARM_BUILTIN_SHA};
  const versions = sources.filter(source => (source.engine || 'libndk') === draft.engine);
  const selected = versions.find(source => source.sha256 === draft.sha256) || versions[0];
  const stopped = ['Prepared', 'Stopped', 'Error'].includes(record.state);
  const canConfigure = stopped || ['Running', 'Frozen'].includes(record.state);
  const disabled = busy || !canConfigure || !arm.supported || armInstallBusy;
  const needsDownload = selected && selected.installed === false;
  return `<p class="arm-provider-status">Current: <strong>${escapeHtml(arm.enabled ? arm.provider : 'Disabled')}</strong></p>
    <div class="arm-provider-fields"><label>Engine<select data-arm-engine="${escapeHtml(record.id)}" ${disabled ? 'disabled' : ''}>
      ${[['none','None'],['libndk','libndk_translation'],['libhoudini','libhoudini']].map(([value,label]) => `<option value="${value}" ${draft.engine === value ? 'selected' : ''}>${label}</option>`).join('')}
    </select></label><label>Version<select data-arm-select="${escapeHtml(record.id)}" ${disabled || draft.engine === 'none' || !versions.length ? 'disabled' : ''}>
      ${draft.engine === 'none' ? '<option value="none">No translation</option>' : versions.map(source => `<option value="${escapeHtml(source.sha256)}" ${source === selected ? 'selected' : ''}>${escapeHtml(source.version || source.label)}</option>`).join('')}
    </select></label></div>
    <div class="runtime-actions"><button class="btn btn-primary" data-controller-op="arm_set" data-controller-id="${escapeHtml(record.id)}" ${disabled || (draft.engine !== 'none' && !selected) ? 'disabled' : ''}>${armInstallBusy === record.id ? 'Installing…' : draft.engine === 'none' ? 'Disable translation' : needsDownload ? 'Download & install' : 'Install selected version'}${['Running', 'Frozen'].includes(record.state) ? ' & restart' : ''}</button>
    <button class="btn btn-quiet" data-controller-op="arm_sources" data-controller-id="${escapeHtml(record.id)}">Import another version…</button></div>
    <p class="hint">${!arm.supported ? escapeHtml(arm.detail || 'Prepare an x86_64 Android image first.') : ['Running', 'Frozen'].includes(record.state) ? 'Applying restarts Android; apps will close.' : !stopped ? 'Stop this runtime before changing ARM support.' : 'Applies on next start.'}</p>
    ${selected?.android ? `<details class="arm-compatibility"><summary>Compatibility notes</summary><p class="hint">${escapeHtml(selected.android)}. Compatibility with other Android versions and individual apps is unverified.</p></details>` : ''}
    ${!arm.available && !(arm.sources || []).some(source => source.installed) ? '<p class="hint">ARM translation package is not installed. Download a version to continue.</p>' : ''}
    `;
}
let armInstallBusy = null;
async function installRuntimeArm(id) {
  if (armInstallBusy || controllerMutation) return;
  const record = runtimeRecord(id);
  if (!record?.arm) return;
  const engine = document.querySelector(`[data-arm-engine="${id}"]`)?.value;
  const digest = document.querySelector(`[data-arm-select="${id}"]`)?.value;
  if (!engine || !digest) return;
  if (engine === 'none') { await mutateController({op:'arm_set',id,enabled:false}); if (!controllerError) runtimeArmDraft.delete(id); return; }
  const source = runtimeArmSources(record.arm).find(source => source.sha256 === digest && (source.engine || 'libndk') === engine);
  if (!source) return;
  armInstallBusy = id; renderController();
  try {
    if (source.installed === false) {
      const result = await tauriInvoke('import_arm_translation', {source:digest,sourceKind:'builtin',expectedSha256:''});
      if (result?.sha256 !== digest) throw new Error('Downloaded ARM version does not match selection');
    }
    await mutateController({op:'arm_set',id,enabled:true,archive_sha256:digest});
    if (!controllerError) runtimeArmDraft.delete(id);
  } catch (error) { controllerError = `ARM installation failed: ${error}`; showAppToast(controllerError, 'error'); }
  finally { armInstallBusy = null; renderController(); }
}
document.addEventListener('change', event => {
  const id = event.target.dataset?.armEngine || event.target.dataset?.armSelect;
  if (!id) return;
  const arm = runtimeRecord(id)?.arm;
  if (!arm) return;
  const engine = event.target.dataset.armEngine ? event.target.value : document.querySelector(`[data-arm-engine="${id}"]`)?.value;
  const sources = runtimeArmSources(arm).filter(source => (source.engine || 'libndk') === engine);
  runtimeArmDraft.set(id, {engine, sha256:event.target.dataset.armSelect ? event.target.value : sources[0]?.sha256 || 'none'});
  event.target.blur?.();
  renderController();
});

// Start only after all runtime request helpers have been initialized.
if (!openSettingsOnLaunch && !openControlsOnLaunch) pageLoads.schedule('apps', () => libraryPage, initializeAppLibrary);

// The search shortcut is scoped to the app window and leaves modal dialogs alone.
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !document.querySelector?.('dialog[open]')) {
    event.preventDefault();
    navigatePage('apps');
    document.getElementById('search-apps').focus();
  }
});

// Runtime table selection is independent of the expanded configuration row.
const runtimeBatchRestarting = new Set();
// Lifecycle operations own only their target runtimes; global mutations still
// serialize shared image/controller changes.
const runtimeOperations = new Map();
function runtimeOperationBusy(id) {
  return Boolean(controllerMutation || runtimeBatchBusy || runtimeOperations.has(id) ||
    (id === 'default' && (defaultRuntimeOperation || runtimeTransition || pendingLaunch || pendingAppAction || probeInstallBusy)));
}
function runtimePlanBlocked(op, targets) {
  const lifecycle = ['start', 'stop', 'restart'].includes(op);
  return controllerMutation || runtimeBatchBusy || (!lifecycle && controllerLoading) ||
    (!lifecycle && (runtimeOperations.size || runtimeTransition || pendingLaunch || pendingAppAction || probeInstallBusy)) ||
    targets.some(record => runtimeOperationBusy(record.id));
}
function runtimeBatchRecords() {
  return [...controllerRecords, ...(existingRuntime ? [existingRuntime] : [])];
}
function renderRuntimeBatchSelection() {
  document.getElementById('btn-restart-controller').disabled = controllerRestartBlocked();
  const records = runtimeBatchRecords();
  const ids = new Set(records.map(record => record.id));
  for (const id of runtimeBatchSelection) if (!ids.has(id)) runtimeBatchSelection.delete(id);
  const selected = records.filter(record => runtimeBatchSelection.has(record.id));
  document.getElementById('runtime-batch-toolbar').hidden = !selected.length;
  document.getElementById('runtime-batch-count').textContent = `${selected.length} selected`;
  const all = document.getElementById('runtime-select-all');
  all.checked = records.length > 0 && selected.length === records.length;
  all.indeterminate = selected.length > 0 && selected.length < records.length;
  document.querySelectorAll('[data-controller-op="open_waydroid"]').forEach(button => {
    button.disabled = controllerMutation || controllerLoading || runtimeBatchBusy || runtimeTransition || pendingLaunch || pendingAppAction || probeInstallBusy || !RuntimeBatch.eligible(runtimeRecord(button.dataset.controllerId), 'open');
  });
  all.disabled = runtimeBatchBusy || controllerMutation || !records.length;
  document.querySelectorAll('[data-runtime-check]').forEach(input => {
    input.checked = runtimeBatchSelection.has(input.dataset.runtimeCheck);
    input.disabled = runtimeBatchBusy || controllerMutation || !ids.has(input.dataset.runtimeCheck);
    input.closest?.('tr')?.classList.toggle('batch-selected', input.checked);
  });
  document.querySelectorAll('[data-controller-op="restart"]').forEach(button => {
    button.disabled = runtimeOperationBusy(button.dataset.controllerId) || imageDownloadBusy || imageDownloadActive() || !RuntimeBatch.eligible(runtimeRecord(button.dataset.controllerId), 'restart', controllerRequirements || {});
  });
  document.querySelectorAll('[data-runtime-batch]').forEach(button => {
    const op = button.dataset.runtimeBatch;
    const count = selected.filter(record => !runtimeOperationBusy(record.id) && RuntimeBatch.eligible(record, op, controllerRequirements || {})).length;
    button.textContent = `${op === 'open' ? 'Open Waydroid windows' : op[0].toUpperCase() + op.slice(1)} (${count})`;
    button.disabled = runtimePlanBlocked(op, selected) || imageDownloadBusy || imageDownloadActive() || !count;
    button.title = `${count} of ${selected.length} selected runtimes can ${op}`;
  });
  document.getElementById('runtime-batch-clear').disabled = runtimeBatchBusy;
}
document.getElementById('runtime-table').addEventListener('change', event => {
  if (runtimeBatchBusy || controllerMutation) return;
  const input = event.target;
  if (input.id === 'runtime-select-all') {
    runtimeBatchSelection.clear();
    if (input.checked) runtimeBatchRecords().forEach(record => runtimeBatchSelection.add(record.id));
  } else if (input.dataset.runtimeCheck) {
    input.checked ? runtimeBatchSelection.add(input.dataset.runtimeCheck) : runtimeBatchSelection.delete(input.dataset.runtimeCheck);
  } else return;
  renderRuntimeBatchSelection();
});
document.getElementById('runtime-batch-clear').addEventListener('click', () => {
  if (runtimeBatchBusy) return;
  runtimeBatchSelection.clear();
  renderRuntimeBatchSelection();
});
document.getElementById('runtime-batch-toolbar').addEventListener('click', event => {
  const button = event.target.closest('[data-runtime-batch]');
  if (!button || button.disabled || controllerMutation || runtimeBatchBusy || imageDownloadBusy || imageDownloadActive()) return;
  const op = button.dataset.runtimeBatch;
  const selected = runtimeBatchRecords().filter(record => runtimeBatchSelection.has(record.id));
  showRuntimeBatchDialog(op, selected, button.textContent.replace(/ \(\d+\)$/, ''));
});
function showRuntimeBatchDialog(op, selected, label) {
  if (runtimePlanBlocked(op, selected) || imageDownloadBusy || imageDownloadActive()) return;
  const targets = selected.filter(record => RuntimeBatch.eligible(record, op, controllerRequirements || {}));
  if (!targets.length) return;
  runtimeBatchPlan = {op, targets: targets.map(({id, name}) => ({id, name})), skipped: selected.length - targets.length};
  const destructive = ['delete', 'reinstall'].includes(op);
  document.getElementById('runtime-batch-title').textContent = `${label} ${targets.length} ${targets.length === 1 ? 'runtime' : 'runtimes'}`;
  document.getElementById('runtime-batch-description').textContent = (destructive
    ? op === 'delete' ? 'Permanently remove these runtimes, apps, data and retained storage copies. This cannot be undone.' : 'Replace these runtimes with the selected image. All existing apps, data and settings will be erased. This cannot be undone.'
    : op === 'open' ? 'Open a Waydroid window for each running Desktop runtime below.' : op === 'start' ? 'Start these runtimes in order.' : 'Apps will close; saved data is retained. Runtimes are processed in order.') +
    (runtimeBatchPlan.skipped ? ` ${runtimeBatchPlan.skipped} unavailable runtime(s) will be skipped.` : '');
  document.getElementById('runtime-batch-targets').innerHTML = targets.map(record => `<li>${escapeHtml(record.name)}</li>`).join('');
  document.getElementById('runtime-batch-confirm-field').hidden = !destructive;
  document.getElementById('runtime-batch-confirm-label').textContent = `Type ${op.toUpperCase()} to confirm`;
  document.getElementById('runtime-batch-confirm').value = '';
  document.getElementById('runtime-batch-image-field').hidden = op !== 'reinstall';
  const image = document.getElementById('runtime-batch-image');
  image.innerHTML = '<option value="" disabled selected>Select an image</option>' +
    (imageDownloadState?.available_images || []).map(item => `<option value="${escapeHtml(item.library_id)}">${escapeHtml(item.custom_name || 'Waydroid ' + item.flavor)}</option>`).join('') +
    (Array.from(document.getElementById('controller-image').options).some(option => option.value === 'installed') ? '<option value="installed">Existing Waydroid image</option>' : '');
  document.getElementById('runtime-batch-dialog-error').textContent = '';
  document.getElementById('runtime-batch-dialog').showModal();
}
document.getElementById('runtime-batch-cancel').addEventListener('click', () => document.getElementById('runtime-batch-dialog').close());
document.getElementById('runtime-batch-dialog').addEventListener('close', () => { runtimeBatchPlan = null; });
async function refreshBatchRecord(id, operation) {
  const record = await controllerRequest({op:'refresh', id});
  if (record?.id !== id) throw new Error('Invalid runtime response.');
  if (id !== 'default') record.appJob = await controllerRequest({op:'app_job', id});
  if (id !== 'default') {
    if (operation !== 'open') record.storage = await controllerRequest({op:'storage_info', id});
    record.display = await controllerRequest({op:'display_info', id});
  }
  if (id === 'default') existingRuntime = {...existingRuntime, ...record};
  else {
    const index = controllerRecords.findIndex(item => item.id === id);
    if (index >= 0) controllerRecords[index] = {...controllerRecords[index], ...record};
  }
  renderController();
  return record;
}
async function executeBatchOperation(record, op, imageRequest, activationToken) {
  await lazyFeatures.ensure('runtime');
  const id = record.id;
  if (op === 'open') return launchRuntimeWaydroidWindow(record, activationToken);
  if (id === 'default' && ['start', 'stop'].includes(op)) {
    if (op === 'start' && controllerRecords.some(item => item.import_status === 'pending' && item.state === 'Provisioning')) throw new Error('Wait for Default runtime import to finish.');
    const result = await tauriInvoke(op === 'start' ? 'start_runtime' : 'stop_runtime', {runtimeId:'default'});
    if (!result?.success) throw new Error(result?.message || 'Runtime action failed.');
    const deadline = Date.now() + 360000;
    while (Date.now() < deadline) {
      const updated = await refreshBatchRecord(id);
      if (updated.state === (op === 'start' ? 'Running' : 'Stopped')) return updated;
      if (updated.state === 'Error') throw new Error('Default runtime transition failed.');
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    throw new Error('Timed out waiting for Default runtime.');
  }
  const request = {op, id, ...(['delete', 'reinstall'].includes(op) ? {confirmed_name:record.name} : {}), ...(op === 'reinstall' ? imageRequest : {})};
  await activateRequestImage(request);
  const result = await controllerRequest(request);
  if (op === 'delete') {
    if (result?.runtime_id !== id || !result.id) throw new Error('Invalid deletion response.');
    const deadline = Date.now() + 360000;
    while (Date.now() < deadline) {
      const records = await controllerRequest({op:'list'});
      if (!Array.isArray(records)) throw new Error('Invalid runtime list response.');
      if (!records.some(item => item.id === id)) return;
      let job;
      try { job = await controllerRequest({op:'app_job', id}); }
      catch (error) {
        const remaining = await controllerRequest({op:'list'});
        if (Array.isArray(remaining) && !remaining.some(item => item.id === id)) return;
        throw error;
      }
      if (job?.id !== result.id) throw new Error('Deletion operation changed.');
      if (job.status === 'Failed') throw new Error(job.error || 'Deletion failed.');
      if (job.status === 'Succeeded') return;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    throw new Error('Timed out waiting for deletion; check status before retrying.');
  } else if (op === 'reinstall' && id === 'default') {
    if (result?.id !== id || result.state !== 'Stopped') throw new Error('Could not verify Default runtime reinstall.');
  } else {
    if (result?.id !== id || !result.job?.id) throw new Error('Invalid runtime operation response.');
    Object.assign(runtimeRecord(id), result);
    renderController();
    const updated = await RuntimeSettings.wait(controllerRequest, id, result.job.id, op === 'start' ? 'Running' : op === 'stop' ? 'Stopped' : 'Prepared');
    Object.assign(runtimeRecord(id), updated);
    return updated;
  }
}
document.getElementById('runtime-batch-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!runtimeBatchPlan || runtimePlanBlocked(runtimeBatchPlan.op, runtimeBatchPlan.targets) || imageDownloadBusy || imageDownloadActive()) return;
  const plan = runtimeBatchPlan;
  const error = document.getElementById('runtime-batch-dialog-error');
  if (['delete', 'reinstall'].includes(plan.op) && document.getElementById('runtime-batch-confirm').value !== plan.op.toUpperCase()) {
    error.textContent = `Type exactly: ${plan.op.toUpperCase()}`; return;
  }
  let imageRequest;
  if (plan.op === 'reinstall') {
    const value = document.getElementById('runtime-batch-image').value;
    const image = (imageDownloadState?.available_images || []).find(item => item.library_id === value);
    if (value === 'installed') imageRequest = {flavor:'installed'};
    else if (image) imageRequest = {flavor:image.flavor, system_sha256:image.images.system.sha256, vendor_sha256:image.images.vendor.sha256};
    else { error.textContent = 'Select an available image.'; return; }
  }
  await runRuntimeBatchPlan(plan, imageRequest);
});
async function runRuntimeBatchPlan(plan, imageRequest) {
  if (runtimePlanBlocked(plan.op, plan.targets)) return;
  const lifecycle = ['start', 'stop', 'restart'].includes(plan.op);
  if (lifecycle) plan.targets.forEach(record => runtimeOperations.set(record.id, plan.op));
  else { runtimeBatchBusy = true; controllerMutation = true; }
  clearTimeout(controllerTimer);
  document.getElementById('runtime-batch-dialog').close();
  renderController();
  renderAvailableImages();
  const output = document.getElementById('runtime-batch-result');
  output.textContent = '';
  let results;
  try {
    const activationTokens = new Map();
    if (plan.op === 'open') {
      output.textContent = 'Preparing Waydroid windows…';
      await Promise.all(plan.targets.map(async record => activationTokens.set(record.id, await waydroidActivationToken())));
    }
    await controllerRefreshDone;
    results = await RuntimeBatch.run(plan.targets, plan.op, {
      refresh: id => refreshBatchRecord(id, plan.op), execute: (record, op) => executeBatchOperation(record, op, imageRequest, activationTokens.get(record.id)),
      requirements: controllerRequirements || {},
      progress: (record, index, total) => {
        if (plan.op === 'restart') { runtimeBatchRestarting.add(record.id); renderController(); }
        output.textContent = `${plan.op}: ${index + 1}/${total} · ${record.name}. Keep AnvilDroid open until this finishes.`;
      },
      settled: record => { runtimeBatchRestarting.delete(record.id); renderController(); },
    });
    for (const result of results) if (result.status === 'succeeded' && plan.op !== 'open') runtimeBatchSelection.delete(result.id);
  } catch (error) { output.textContent = `Batch failed: ${error}`; }
  finally {
    if (lifecycle) plan.targets.forEach(record => runtimeOperations.delete(record.id));
    else { controllerMutation = false; runtimeBatchBusy = false; }
    plan.targets.forEach(record => runtimeBatchRestarting.delete(record.id));
    renderController();
    await Promise.all([refreshController(), refreshStatus()]);
    renderRuntimeBatchSelection();
    renderAvailableImages();
    if (results) {
      const succeeded = results.filter(item => item.status === 'succeeded').length;
      const failed = results.filter(item => item.status === 'failed').length;
      const skipped = (plan.skipped || 0) + results.filter(item => item.status === 'skipped').length;
      // Keep per-runtime details available when the user needs to resolve a failure.
      output.innerHTML = failed || skipped ? renderRuntimeBatchResult(plan, results) : '';
      let summary = plan.op === 'open' && results.length === 1 && !failed && !skipped
        ? `${results[0].name}: ${describeWaydroidWindow(results[0].result)}`
        : `${plan.op}: ${succeeded} completed · ${failed} failed · ${skipped} skipped${results.length === 1 ? ' · ' + results[0].name : ''}`;
      if (plan.op === 'open' && (results.length !== 1 || failed || skipped)) {
        const ready = results.filter(item => item.status === 'succeeded' && item.result?.window?.state === 'ready').length;
        summary = 'Waydroid: ' + [ready && `${ready} ready`, succeeded > ready && `${succeeded - ready} request sent`, failed && `${failed} failed`, skipped && `${skipped} skipped`].filter(Boolean).join(' · ');
      }
      const unverifiedWindow = plan.op === 'open' && results.some(item => item.result?.window?.state !== 'ready');
      showAppToast(summary, failed ? 'error' : skipped || unverifiedWindow ? 'info' : 'success');
    }
  }
}

function renderRuntimeBatchResult(plan, results) {
  const succeeded = results.filter(item => item.status === 'succeeded');
  const failed = results.filter(item => item.status === 'failed').length;
  const skipped = (plan.skipped || 0) + results.filter(item => item.status === 'skipped').length;
  if (plan.op !== 'open') return `<strong>${escapeHtml(plan.op)}: ${succeeded.length} completed · ${failed} failed · ${skipped} skipped</strong><ul>${results.map(item => `<li>${escapeHtml(item.name)}: ${escapeHtml(item.status)}${item.detail ? ' — ' + escapeHtml(item.detail) : ''}</li>`).join('')}</ul>`;
  const ready = succeeded.filter(item => item.result?.window?.state === 'ready').length;
  const counts = [];
  if (ready) counts.push(`${ready} ready`);
  if (succeeded.length > ready) counts.push(`${succeeded.length - ready} request sent`);
  if (failed) counts.push(`${failed} failed`);
  if (skipped) counts.push(`${skipped} skipped`);
  const rows = results.map(item => `<li>${escapeHtml(item.name)}: ${escapeHtml(item.status === 'succeeded' ? describeWaydroidWindow(item.result) : item.status)}${item.detail ? ' — ' + escapeHtml(item.detail) : ''}</li>`).join('');
  if (results.length === 1 && !skipped) return `<ul>${rows}</ul>`;
  return `<strong>Waydroid: ${counts.join(' · ')}</strong><ul>${rows}</ul>`;
}

async function launchRuntimeWaydroidWindow(record, activationToken) {
  return RuntimeWindow.measure(record, async stage => {
    stage('requesting focus');
    const token = activationToken === undefined ? await waydroidActivationToken() : activationToken;
    return requestRuntimeWaydroidWindow(record, stage, token);
  }, {
    publish: trace => {
      window.AnvilWaydroidDiagnostics = RuntimeWindow.diagnostics;
      console.debug('[Waydroid window]', trace);
      renderController();
      if (trace.outcome === 'pending') document.getElementById('runtime-batch-result').textContent = `Opening Waydroid · ${record.name} · ${trace.stages.at(-1)?.name || 'request'}…`;
    },
  });
}
async function requestRuntimeWaydroidWindow(record, stage = () => {}, activationToken = null) {
  if (!RuntimeBatch.eligible(record, 'open')) throw new Error('Start this runtime in Desktop mode first.');
  if (record.id === 'default') {
    stage('sending');
    let result;
    for (let attempt = 0; attempt < 15; attempt++) {
      result = await tauriInvoke('open_waydroid', activationToken ? {activationToken} : {});
      if (result?.success || result?.error_code !== 'BUSY') break;
      await new Promise(resolve => setTimeout(resolve, 350));
    }
    if (!result?.success) throw new Error(result?.message || 'Cannot open Waydroid.');
    stage('request accepted');
    return {window:{state:'unverified'}};
  }
  stage('sending');
  let job;
  for (let attempt = 0; attempt < 20; attempt++) {
    try {
      job = await controllerRequest({op:'app_action', id:record.id, action:'full_ui', package:'org.anvildroid.desktop', ...(activationToken ? {activation_token:activationToken} : {})});
      break;
    } catch (error) {
      if (!controllerBusy(error) || attempt === 19) throw error;
      await new Promise(resolve => setTimeout(resolve, 350));
    }
  }
  const token = job?.id;
  const valid = value => value?.id === token && value.runtime_id === record.id && value.action === 'full_ui' && value.package === 'org.anvildroid.desktop';
  if (!token || !valid(job)) throw new Error('Invalid Waydroid launch job.');
  stage('waiting for Android');
  const deadline = Date.now() + 45000;
  while (job.status === 'Running' && Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 300));
    job = await controllerRequest({op:'app_job', id:record.id});
    if (!valid(job)) throw new Error('Waydroid launch operation changed.');
  }
  if (job.status !== 'Succeeded') throw new Error(job.error || 'Timed out waiting for Waydroid window.');
  stage(job.result?.window?.state === 'ready' ? 'window content ready' : 'request completed; window unverified');
  return {...job.result, window:job.result?.window || {state:'unverified', reason:'worker_upgrade_required'}};
}

function describeWaydroidWindow(result) {
  if (result?.window?.state === 'ready') return result.window.activation === 'active'
    ? 'Waydroid ready and focused' : 'Waydroid ready';
  if (result?.window?.state === 'pending') return 'Opening Waydroid…';
  return 'Open request sent';
}

async function waydroidActivationToken() {
  try {
    const token = await tauriInvoke('request_waydroid_activation');
    return typeof token === 'string' && token.length > 0 && token.length <= 512 && /^[!-~]+$/.test(token) ? token : null;
  } catch (_) { return null; } // X11/older GUI/compositors keep the open path.
}
