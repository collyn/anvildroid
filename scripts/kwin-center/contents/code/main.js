// Host positions belong to KWin, not Android task bounds or xdg window geometry.
var placements = [];
var activeWindows = [];
var positionBus = 'org.anvildroid.WindowPositions';

function appKey(w) {
    var key = String(w.resourceClass);
    return /^(waydroid(?:\.|$)|anvildroid\.r-[a-f0-9]{32}\.waydroid\.)[A-Za-z0-9_.-]*$/i.test(key) ? key : '';
}

function normal(w) {
    return !w.fullScreen && !w.maximizeMode && !w.tile && !w.minimized;
}

function areaFor(output) {
    return workspace.clientArea(KWin.MaximizeArea, output, workspace.currentDesktop);
}

function connect(signal, handler) {
    if (signal && signal.connect) signal.connect(handler);
}
function disconnect(signal, handler) {
    if (signal && signal.disconnect) signal.disconnect(handler);
}

function validPosition(saved, g) {
    if (!saved || !Number.isFinite(saved.x) || !Number.isFinite(saved.y)) return false;
    var outputs = workspace.screens || workspace.outputs || [];
    for (var i = 0; i < outputs.length; ++i) {
        if (String(outputs[i].name) !== saved.output) continue;
        var a = areaFor(outputs[i]);
        var visibleWidth = Math.min(saved.x + g.width, a.x + a.width) - Math.max(saved.x, a.x);
        // Keep a usable titlebar on the original output. Merely intersecting
        // the bottom of a disconnected screen's window is not enough.
        return visibleWidth >= Math.min(160, g.width) && saved.y >= a.y &&
            saved.y + Math.min(64, g.height) <= a.y + a.height;
    }
    return false;
}

function watchWaydroidWindow(w, existing) {
    var key = w && appKey(w);
    if (!key || !(w.normalWindow || w.dialog) || w.fullScreen || w.tile) return;
    // Dialogs must not replace their parent app's last normal position.
    var persistent = w.normalWindow && !w.dialog && !w.transientFor;
    var settle = new QTimer(), deadline = new QTimer(), saveTimer = new QTimer();
    settle.interval = 350; deadline.interval = 2000; saveTimer.interval = 500;
    settle.singleShot = deadline.singleShot = saveTimer.singleShot = true;
    var record = { window: w, settle: settle, deadline: deadline, save: saveTimer };
    placements.push(record);
    activeWindows.push(w);
    var alive = true, initial = !existing, placing = false, interactive = false;
    var loaded = !persistent, saved = null, lastNormal = null, lastWritten = '';
    var width = -1, height = -1;
    var output = (w.transientFor && w.transientFor.output)
        || workspace.screenAt(workspace.cursorPos) || workspace.activeScreen;

    function unique() {
        return activeWindows.filter(function(candidate) {
            return appKey(candidate) === key && candidate.normalWindow &&
                !candidate.dialog && !candidate.transientFor;
        }).length === 1;
    }
    function persist() {
        if (!persistent || !loaded || !lastNormal || !unique()) return;
        var value = JSON.stringify(lastNormal);
        if (value === lastWritten) return;
        lastWritten = value;
        if (typeof callDBus !== 'function') return;
        callDBus(positionBus, '/Positions', positionBus, 'Save', key, value, function(ok) {
            if (!ok) { lastWritten = ''; print('AnvilDroid: position save failed for ' + key); }
        });
    }
    function remember() {
        if (!alive || initial || placing || interactive || !normal(w)) return;
        var g = w.frameGeometry;
        if (g.width <= 0 || g.height <= 0 || !w.output) return;
        lastNormal = { x: g.x, y: g.y, output: String(w.output.name) };
        persist();
    }
    function place() {
        if (!alive || !initial || !loaded || placing || !normal(w)) return;
        var g = w.frameGeometry;
        if (g.width <= 0 || g.height <= 0) return;
        width = g.width; height = g.height;
        var x, y;
        if (persistent && unique() && validPosition(saved, g)) {
            x = saved.x; y = saved.y;
        } else {
            // Re-select if the initial output disappeared while Android mapped.
            var outputs = workspace.screens || workspace.outputs || [];
            if (outputs.length && outputs.indexOf(output) < 0)
                output = workspace.screenAt(workspace.cursorPos) || workspace.activeScreen;
            if (!output) return;
            var a = areaFor(output);
            x = a.x + Math.max(0, Math.round((a.width - g.width) / 2));
            y = a.y + Math.max(0, Math.round((a.height - g.height) / 2));
        }
        if (g.x !== x || g.y !== y) {
            placing = true;
            try { w.frameGeometry = { x: x, y: y, width: g.width, height: g.height }; }
            finally { placing = false; }
        }
    }
    function finishInitial() {
        var index = placements.indexOf(record);
        if (index >= 0) placements.splice(index, 1);
        initial = false;
        settle.stop(); deadline.stop();
        saveTimer.start();
    }
    function geometryChanged() {
        if (placing || !alive) return;
        var g = w.frameGeometry;
        if (initial) {
            if (g.width === width && g.height === height) return;
            place();
            if (loaded) settle.start();
        } else saveTimer.start();
    }
    function moveStarted() {
        interactive = true;
        finishInitial(); // User input wins even if the D-Bus read is still pending.
        saveTimer.stop();
    }
    function moveFinished() {
        interactive = false;
        remember();
    }
    function stateChanged() {
        if (!normal(w)) {
            finishInitial(); saveTimer.stop();
        } else saveTimer.start();
    }
    function closed() {
        // Use the last settled normal frame, never the closing/maximized frame.
        persist();
        alive = false;
        settle.stop(); deadline.stop(); saveTimer.stop();
        disconnect(w.frameGeometryChanged, geometryChanged);
        disconnect(w.interactiveMoveResizeStarted, moveStarted);
        disconnect(w.interactiveMoveResizeFinished, moveFinished);
        disconnect(w.maximizedChanged, stateChanged);
        disconnect(w.fullScreenChanged, stateChanged);
        disconnect(w.minimizedChanged, stateChanged);
        disconnect(w.closed, closed);
        var index = activeWindows.indexOf(w);
        if (index >= 0) activeWindows.splice(index, 1);
        index = placements.indexOf(record);
        if (index >= 0) placements.splice(index, 1);
    }
    // Normalize only newly mapped Android surfaces, not existing user windows.
    if (initial && w.maximizeMode) {
        w.setMaximize(false, false);
        if (!normal(w)) return;
    }
    settle.timeout.connect(finishInitial);
    deadline.timeout.connect(finishInitial);
    saveTimer.timeout.connect(remember);
    connect(w.frameGeometryChanged, geometryChanged);
    connect(w.interactiveMoveResizeStarted, moveStarted);
    connect(w.interactiveMoveResizeFinished, moveFinished);
    connect(w.maximizedChanged, stateChanged);
    connect(w.fullScreenChanged, stateChanged);
    connect(w.minimizedChanged, stateChanged);
    connect(w.closed, closed);
    if (persistent) {
        if (typeof callDBus !== 'function') {
            loaded = true; place(); settle.start();
        } else callDBus(positionBus, '/Positions', positionBus, 'Load', key, function(value) {
            if (!alive) return;
            try { saved = JSON.parse(String(value)); loaded = true; }
            catch (e) { print('AnvilDroid: position load failed for ' + key); return; }
            place();
            if (initial) settle.start();
            else remember();
        });
    } else { place(); settle.start(); }
    if (initial) deadline.start();
    else saveTimer.start();
}

workspace.windowAdded.connect(function(w) { watchWaydroidWindow(w, false); });
// Reloading this script must not move windows already on the desktop.
if (typeof workspace.windowList === 'function')
    workspace.windowList().forEach(function(w) { watchWaydroidWindow(w, true); });
