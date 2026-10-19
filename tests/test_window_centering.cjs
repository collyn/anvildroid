const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../scripts/kwin-center/contents/code/main.js'), 'utf8');

function signal() {
    const handlers = new Set();
    return { connect: f => handlers.add(f), disconnect: f => handlers.delete(f),
        emit: (...args) => [...handlers].forEach(f => f(...args)) };
}
function setup(overrides = {}, savedPosition = null) {
    const timers = [];
    const output = { name: 'test-output', area: { x: 1920, y: 32, width: 1920, height: 1048 } };
    const workspace = { screens: [output, ...(overrides.transientFor ? [overrides.transientFor.output] : [])], windowAdded: signal(), screenAt: () => output,
        activeScreen: output, currentDesktop: {}, cursorPos: {},
        clientArea: (_, screen) => screen.area };
    function QTimer() {
        this.timeout = signal(); this.running = false;
        this.start = () => { this.running = true; };
        this.stop = () => { this.running = false; };
        this.fire = () => { if (this.running) { this.running = false; this.timeout.emit(); } };
        timers.push(this);
    }
    const context = vm.createContext({ workspace, QTimer, KWin: { MaximizeArea: 1 },
        callDBus: savedPosition ? (...args) => args.at(-1)(JSON.stringify(savedPosition)) : undefined });
    vm.runInContext(source, context);
    const w = { normalWindow: true, dialog: false, resourceClass: 'waydroid.test',
        maximizeMode: 0, restoreCalls: 0,
        setMaximize(horizontal, vertical) {
            assert.equal(horizontal, false); assert.equal(vertical, false);
            this.restoreCalls++; this.maximizeMode = 0; this.maximizedChanged.emit();
        },
        frameGeometry: { x: 0, y: 0, width: 400, height: 600 },
        frameGeometryChanged: signal(), interactiveMoveResizeStarted: signal(),
        maximizedChanged: signal(), fullScreenChanged: signal(), closed: signal(), ...overrides };
    workspace.windowAdded.emit(w);
    return { w, timers, context };
}
test('new app is centered in cursor display work area, preserving size', () => {
    const { w, timers } = setup();
    assert.equal(JSON.stringify(w.frameGeometry), JSON.stringify({ x: 2680, y: 256, width: 400, height: 600 }));
});
test('dialog follows parent display', () => {
    const { w, timers } = setup({ normalWindow: false, dialog: true,
        transientFor: { output: { area: { x: 0, y: 0, width: 1920, height: 1036 } } } });
    assert.equal(w.frameGeometry.x, 760); assert.equal(w.frameGeometry.y, 218);
});
test('initial Waydroid maximize flag is cleared before centering; user maximize is preserved', () => {
    const { w, timers, context } = setup({ maximizeMode: 3 });
    assert.equal(w.maximizeMode, 0); assert.equal(w.restoreCalls, 1);
    w.maximizedChanged.emit(); // delayed notification from initial restore
    assert.equal(w.frameGeometry.x, 2680);
    w.maximizeMode = 3; w.maximizedChanged.emit(); timers.forEach(t => t.fire());
    assert.equal(w.maximizeMode, 3); assert.equal(w.restoreCalls, 1);
    assert.equal(context.placements.length, 0);
});
test('initial Android resize is recentered; later movement is preserved', () => {
    const { w, timers, context } = setup();
    w.frameGeometry.width = 800; w.frameGeometryChanged.emit();
    assert.equal(w.frameGeometry.x, 2480);
    timers[1].fire(); assert.equal(context.placements.length, 0);
    w.frameGeometry.x = 99; w.frameGeometryChanged.emit(); timers[0].fire();
    assert.equal(w.frameGeometry.x, 99);
});
test('user movement, maximization and closing immediately stop placement', () => {
    for (const name of ['interactiveMoveResizeStarted', 'fullScreenChanged', 'closed']) {
        const { w, timers, context } = setup(); w[name].emit();
        w.frameGeometry.x = 91; timers.forEach(t => t.fire()); assert.equal(w.frameGeometry.x, 91);
        assert.equal(context.placements.length, 0);
    }
});
test('unrelated windows, fullscreen and tiles are untouched', () => {
    for (const props of [{ resourceClass: 'firefox' }, { fullScreen: true }, { tile: {} }, { normalWindow: false }]) {
        assert.equal(setup(props).timers.length, 0);
    }
});
test('oversized windows keep top left/titlebar inside work area', () => {
    const { w, timers } = setup({ frameGeometry: { x: 0, y: 0, width: 2200, height: 1200 } });
    timers[0].fire(); assert.equal(w.frameGeometry.x, 1920); assert.equal(w.frameGeometry.y, 32);
});

test('runtime-scoped Waydroid windows are centered without changing unrelated app IDs', () => {
    const {w,timers}=setup({resourceClass:'anvildroid.r-11111111111111111111111111111111.waydroid.com.android.settings',maximizeMode:3});
    assert.equal(w.maximizeMode,0);
    timers[0].fire(); assert.equal(w.frameGeometry.x,2680);
    assert.equal(setup({resourceClass:'anvildroid.r-bad.waydroid.com.android.settings'}).timers.length,0);
});

test('position-only changes and deadline do not pull a closing window back', () => {
    const {w,timers,context}=setup();
    assert.equal(w.frameGeometry.x,2680); // no delayed first placement
    w.frameGeometry.x=12;w.frameGeometryChanged.emit();
    timers.forEach(t=>t.fire());
    assert.equal(w.frameGeometry.x,12);
    assert.equal(context.placements.length,0);
});
test('quiet initial size ends tracking before a later close animation resize',()=>{
    const {w,timers,context}=setup();
    timers[0].fire();
    assert.equal(context.placements.length,0);
    w.frameGeometry={x:70,y:40,width:300,height:500};
    w.frameGeometryChanged.emit();timers[1].fire();
    assert.equal(w.frameGeometry.x,70);
});

test('saved small-window position cannot displace a recreated maximized task',()=>{
    const saved={x:2200,y:150,output:'test-output'};
    for(const [width,height,x,y] of [[1920,1048,1920,32],[1920,600,1920,150],[400,1048,2200,32],[400,600,2200,150]]) {
        const {w,timers}=setup({frameGeometry:{x:0,y:0,width,height}},saved);
        assert.equal(w.frameGeometry.x,x);assert.equal(w.frameGeometry.y,y);
        if(width===1920&&height===1048) {
            w.maximizeMode=3;w.maximizedChanged.emit();timers.forEach(t=>t.fire());
            assert.equal(w.frameGeometry.x,1920);assert.equal(w.frameGeometry.y,32);
        }
    }
});
