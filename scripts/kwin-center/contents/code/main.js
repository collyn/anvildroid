// Plasma 6: center only newly opened Waydroid app windows/dialogs.
// Place synchronously at mapping; timers only disconnect initial-size tracking.
var placements = [];

function centerWaydroidWindow(w) {
    if (!w || !(w.normalWindow || w.dialog) || !/^(waydroid\.|anvildroid\.r-[a-f0-9]{32}\.waydroid\.)/i.test(String(w.resourceClass))) return;
    if (w.fullScreen || w.tile) return;
    // Waydroid maps app surfaces as maximized even when their Android task
    // occupies only part of the display. Moving that frame alone leaves KWin
    // in maximized mode, hiding its resize borders. Normalize only at mapping;
    // later user maximize/restore operations must remain untouched.
    if (w.maximizeMode) w.setMaximize(false, false);
    var output = (w.transientFor && w.transientFor.output)
        || workspace.screenAt(workspace.cursorPos) || workspace.activeScreen;
    var settle = new QTimer();
    settle.interval = 350;
    settle.singleShot = true;
    var deadline = new QTimer();
    deadline.interval = 2000;
    deadline.singleShot = true;
    var record = { window: w, settle: settle, deadline: deadline };
    placements.push(record);
    var stopped = false;
    var placing = false;
    var width = -1, height = -1;

    function center() {
        if (stopped || placing || w.fullScreen || w.tile || w.minimized) return;
        var area = workspace.clientArea(KWin.MaximizeArea, output, workspace.currentDesktop);
        var g = w.frameGeometry;
        if (g.width <= 0 || g.height <= 0) return;
        width = g.width; height = g.height;
        // Preserve dimensions; oversized windows keep their titlebar reachable.
        var x = area.x + Math.max(0, Math.round((area.width - g.width) / 2));
        var y = area.y + Math.max(0, Math.round((area.height - g.height) / 2));
        if (g.x !== x || g.y !== y) {
            placing = true;
            try { w.frameGeometry = { x: x, y: y, width: g.width, height: g.height }; }
            finally { placing = false; }
        }
    }
    function sizeChanged() {
        if (stopped || placing) return;
        var g = w.frameGeometry;
        // A move is not an Android size update. Never pull a moving/closing
        // window back to the center, including our own move notifications.
        if (g.width === width && g.height === height) return;
        center();
        settle.start();
    }
    function maximizeChanged() {
        // KWin can deliver our initial restore notification asynchronously.
        // It must not cancel centering; a later maximize by the user must.
        if (w.maximizeMode) finish();
    }
    function finish() {
        if (stopped) return;
        stopped = true;
        settle.stop();
        deadline.stop();
        w.frameGeometryChanged.disconnect(sizeChanged);
        w.interactiveMoveResizeStarted.disconnect(finish);
        w.maximizedChanged.disconnect(maximizeChanged);
        w.fullScreenChanged.disconnect(finish);
        w.closed.disconnect(finish);
        placements.splice(placements.indexOf(record), 1);
    }
    settle.timeout.connect(finish);
    deadline.timeout.connect(finish);
    w.frameGeometryChanged.connect(sizeChanged);
    w.interactiveMoveResizeStarted.connect(finish);
    w.maximizedChanged.connect(maximizeChanged);
    w.fullScreenChanged.connect(finish);
    w.closed.connect(finish);
    center();
    settle.start();
    deadline.start();
}

// Existing windows and later user moves are deliberately left in place.
workspace.windowAdded.connect(centerWaydroidWindow);
