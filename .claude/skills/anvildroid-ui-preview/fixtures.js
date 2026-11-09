// Fake backend data for the UI preview. Keys: Tauri command name, or
// `controller:<op>` / `controller:<op>:<id>` for runtime_controller requests.
// A value may be a function (args) => result. Scenarios override `default`;
// pick one with #scenario=<name>.
(() => {
  const managed = {
    id: 'r-0123456789abcdef0123456789abcdef', owner_uid: 1000, name: 'Gaming', state: 'Running',
    start_on_boot: false, job: null,
    image_selection: {
      flavor: 'VANILLA', library_id: 'img-vanilla', image_bytes: 1.4 * 1024 ** 3,
      images: {
        system: { sha256: 'a'.repeat(64), filename: 'lineage-20.0-system.zip', version: '20.0' },
        vendor: { sha256: 'b'.repeat(64), filename: 'lineage-20.0-vendor.zip', version: '20.0' },
      },
    },
  };
  const stopped = { ...managed, id: 'r-fedcba9876543210fedcba9876543210', name: 'Work profile', state: 'Stopped' };
  const system = { id: 'default', name: 'System Waydroid', state: 'Running', job: null };
  const apps = [
    ['org.lineageos.jelly', 'Browser'], ['com.android.settings', 'Settings'],
    ['org.lineageos.etar', 'Calendar'], ['com.android.camera2', 'Camera'],
    ['org.lineageos.recorder', 'Recorder'], ['com.android.contacts', 'Contacts'],
    ['org.fdroid.fdroid', 'F-Droid'], ['net.minetest.minetest', 'Luanti'],
  ].map(([pkg, label]) => ({ package: pkg, label, activity: `${pkg}/.Main`, icon_path: null, uninstall_block_reason: null, launchable: true }));

  window.PREVIEW_FIXTURES = {
    default: {
      get_app_version: '0.1.10-preview',
      get_application_preferences: {},
      get_shortcut_preferences: {},
      get_library: { favorites: ['org.fdroid.fdroid'], recent: {}, tags: {}, sort: 'name' },
      get_apps: apps,
      get_health_report: { status: { state: 'Running' } },
      get_runtime_catalog: { configured: { settings: { image_kind: 'VANILLA' } } },
      waydroid_init_status: { status: 'Idle' },
      update_status: null,
      adb_host_status: { running: false },
      set_window_theme: null,
      set_login_start: null,
      check_update: { available: false },
      'controller:image_download_status': { status: 'Idle', available_images: [managed.image_selection] },
      'controller:image_catalog': {
        status: 'Ready',
        report: {
          checked_at: 1762650000, architecture: 'x86_64',
          variants: ['VANILLA', 'GAPPS'].map((flavor, i) => ({
            flavor, download_bytes: (1.1 + i * 0.4) * 1024 ** 3, matching_versions: true,
            system: { sha256: String(i + 1).repeat(64), version: '20.0-20251101' },
            vendor: { sha256: String(i + 3).repeat(64), version: '20.0-20251101' },
          })),
        },
      },
      'controller:list': [managed, stopped],
      'controller:refresh': args => args.id === 'default' ? system : [managed, stopped].find(r => r.id === args.id),
      // Game Controls: saved keymaps per device (one app unknown to the library).
      'controller:keymaps': args => ({ runtime_id: args.id, invalid_count: 0, profiles: [
        { runtime: args.id, package: 'net.minetest.minetest', bindings: [{ evdev: 17, kind: 'tap', nx: 30000, ny: 40000 }, { evdev: 57, kind: 'hold', nx: 50000, ny: 50000 }] },
        { runtime: args.id, package: 'com.example.unlisted', bindings: [] },
      ] }),
      'controller:requirements': { can_prepare: true, can_start: true, checks: [] },
      'controller:arm_sources': { sources: [] },
      'controller:app_states': args => ({ runtime_id: args.id, state: 'Running', states: {} }),
      'controller:apps': args => ({ runtime_id: args.id, apps: apps.slice(0, 4) }),
      'controller:gpu_info': args => ({
        runtime_id: args.id, selection: 'auto', software_supported: true, system_nvidia_supported: true,
        devices: [
          { node: '/dev/dri/renderD128', driver: 'i915', vendor_id: '0x8086', device_id: '0x9a49', name: 'Intel TigerLake-LP GT2 [Iris Xe Graphics]', selectable: true, experimental: false },
          { node: '/dev/dri/renderD129', driver: 'amdgpu', vendor_id: '0x1002', device_id: '0x73ff', name: 'AMD Navi 23 [Radeon RX 6600/6600 XT/6600M]', selectable: true, experimental: false },
        ],
      }),
      'controller:resources_info': args => ({
        runtime_id: args.id,
        host: { supported: true, memory_total_mib: 32768, memory_available_mib: 21504, cpu_count: 16 },
        configured: { memory_mib: 8192, cpu_count: 8 },
        presets: [
          { name: 'Light', memory_mib: 4096, cpu_count: 4 },
          { name: 'Balanced', memory_mib: 8192, cpu_count: 8 },
          { name: 'Performance', memory_mib: 16384, cpu_count: 12 },
          { name: 'Unlimited', memory_mib: 0, cpu_count: 0 },
        ],
        budget: { active_count: 0, memory_limit_mib: 0, cpu_quota_count: 0, available_for_growth_mib: 14336, host_reserve_mib: 2048, unused_memory_mib: 0, unbounded_memory_count: 0, unbounded_cpu_count: 0, unknown_count: 0, target_current_mib: 0 },
        effective: {
          scope: 'anvildroid-runtime.scope', memory_current_bytes: 2.6 * 1024 ** 3, memory_max_bytes: 8192 * 1024 ** 2,
          cpu_quota_count: 8, cpu_usage_usec: Math.floor(performance.now() * 1000 * 0.9), cpu_throttled_usec: 120000,
          swap_current_bytes: 0, swap_max_bytes: 0, oom_kills: 0,
        },
      }),
      'controller:notifications_get': args => ({
        runtime_id: args.id, enabled: true, status: { state: 'Connected' },
        blocked: ['org.lineageos.etar', 'org.fdroid.fdroid'],
      }),
      'controller:camera_get': args => ({
        runtime_id: args.id, enabled: false, devices: [], active: [],
        available: [{ id: 'usb-046d_HD_Pro_Webcam_C920', name: 'HD Pro Webcam C920', node: 'video0' }],
      }),
      'controller:device_info': args => {
        const row = (id, name, brand, manufacturer, model, device) => ({ id, name, values: { brand, manufacturer, model, device, product: device, fingerprint: '' } });
        return {
          runtime_id: args.id, profile: 'default', custom: null, values: null, revision: 'c'.repeat(64), activation: 'runtime_restart',
          profiles: [
            row('pixel-8-pro', 'Pixel 8 Pro', 'google', 'Google', 'Pixel 8 Pro', 'husky'),
            row('galaxy-s24-ultra', 'Galaxy S24 Ultra', 'samsung', 'samsung', 'SM-S928B', 'e3q'),
            row('rog-phone-8', 'ROG Phone 8 Pro', 'asus', 'asus', 'ASUS_AI2401_A', 'AI2401'),
            row('redmagic-9', 'RedMagic 9 Pro', 'nubia', 'nubia', 'NX769J', 'NX769J'),
          ],
        };
      },
      'controller:arm_info': args => ({
        runtime_id: args.id, provider: 'libhoudini 11.0.1b', archive_sha256: 'd'.repeat(64), enabled: true, inherited: false,
        supported: true, available: true, restore_available: true, android_sdk: 33, experimental: true,
        abis: ['armeabi-v7a', 'arm64-v8a'], default_enabled: false,
        sources: [
          { engine: 'libhoudini', version: '11.0.1b', android: 'Android 13', sha256: 'd'.repeat(64), installed: true, label: 'libhoudini 11.0.1b', android_sdks: [33] },
          { engine: 'libndk', version: '0.2.3', android: 'Android 13', sha256: 'a142d1586c9eafb5edf62277110f2128bc03066179a6783bcaa33ee322e1cbd0', installed: true, label: 'libndk_translation 0.2.3', android_sdks: [33] },
        ],
      }),
      'controller:display_info': args => ({ runtime_id: args.id, mode: 'desktop', effective_mode: 'desktop', backend: 'auto', presenter: 'amd' }),
      'controller:storage_info': args => ({
        runtime_id: args.id, path: '/home/player/.local/share/anvildroid/runtimes/' + args.id,
        used_bytes: 9.4 * 1024 ** 3, free_bytes: 412 * 1024 ** 3, total_bytes: 932 * 1024 ** 3,
        limit_bytes: null, limit_status: 'none', previous_path: null, job: null, error: null,
      }),
    },
    // Nothing installed yet: first-run / empty states.
    // Click through the whole first-run flow: download progresses over a few
    // polls, then create_image adds a running device.
    empty: (() => {
      let polls = -1, created = null;
      const image = managed.image_selection;
      const download = () => polls < 0 ? { status: 'Idle', available_images: [] }
        : polls < 4 ? { id: 'dl-1', status: 'Downloading', flavor: 'VANILLA', phase: 'Downloading', download_bytes: 1e9, received_bytes: (++polls) * 2.4e8, can_cancel: true, available_images: [], cache_path: '/var/lib/anvildroid-controller/downloads', free_bytes: 5e10 }
        : { id: 'dl-1', status: 'Ready', flavor: 'VANILLA', images: image.images, available_images: [image], cache_path: '/var/lib/anvildroid-controller/downloads', free_bytes: 5e10 };
      return {
        get_apps: [],
        'controller:image_download_status': download,
        'controller:image_download': () => { polls = 0; return download(); },
        // create_image -> display_set -> prepare -> start, each job finishing after 3 refreshes.
        'controller:create_image': args => (created = { ...managed, name: args.name, state: 'Allocated', job: null }, { ...created }),
        'controller:image_select': () => ({ status: 'Ready', flavor: image.flavor, images: image.images }),
        'controller:display_set': () => ({ mode: 'desktop' }),
        'controller:prepare': args => (created = { ...created, job: { id: 'j-prepare', operation: 'prepare', status: 'Running', ticks: 0, target: 'Prepared' } }, { ...created }),
        'controller:start': args => (created = { ...created, job: { id: 'j-start', operation: 'start', status: 'Running', ticks: 0, target: 'Running' } }, { ...created }),
        'controller:list': () => created ? [created] : [],
        'controller:refresh': args => {
          if (args.id === 'default') return { id: 'default', state: 'Uninitialized', job: null };
          const job = created?.job;
          if (job?.status === 'Running' && ++job.ticks >= 3) { job.status = 'Succeeded'; created.state = job.target; }
          return created && JSON.parse(JSON.stringify(created));
        },
        'controller:apps': args => ({ runtime_id: args.id, apps: apps.slice(0, 6) }),
      };
    })(),
    // Backend down: error states.
    offline: {
      'controller:list': undefined,
      'controller:refresh': undefined,
      'controller:requirements': undefined,
    },
  };
  // `undefined` in a scenario removes that fixture (forces the error path).
})();
