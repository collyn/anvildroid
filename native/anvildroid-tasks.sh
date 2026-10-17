#!/system/bin/sh
# No host root daemon. Android's shell UID reads ActivityManager state.
umask 007
cd /data/waydroid_tmp/anvildroid || exit 1
# Android mksh can wait on a private FIFO without launching toybox sleep on
# every 50 ms tick. Keep a portable fallback for alternate shells.
tick_ready=0
if [ -n "${KSH_VERSION:-}" ]; then
    tick_dir=$(mktemp -d /data/local/tmp/anvil-task-tick-XXXXXX)
    if [ -n "$tick_dir" ] && mkfifo "$tick_dir/tick" && { exec 9<>"$tick_dir/tick"; }; then
        tick_ready=1
    fi
    # The open descriptor holds the FIFO alive; no pathname or exit trap is
    # needed while processing queue jobs in subshells.
    if [ -n "$tick_dir" ]; then rm -f "$tick_dir/tick"; rmdir "$tick_dir"; fi
fi
wait_tick() {
    if [ "$tick_ready" -eq 1 ]; then IFS= read -r -t "$1" tick <&9 || :
    else sleep "$1"; fi
}
# Close only a root stack containing exactly the requested task/package. Never
# force-stop a package or remove a shared/home stack to emulate a window close.
close_one_task() {
    wanted=$1 package=$2
    case "$wanted" in ''|*[!0-9]*|0) return 2;; esac
    case "$package" in ''|*[!a-zA-Z0-9_.]*|.*|*.|*..*|android|com.android.systemui|com.android.launcher3) return 2;; esac
    # Android 16 groups freeform apps under one desktop root. Remove only the
    # verified leaf task; removing its root would close the other apps too.
    if [ "$(getprop ro.build.version.sdk)" -ge 36 ] 2>/dev/null; then
        CLASSPATH=/data/local/tmp/anvildroid-shutdown.jar app_process /system/bin org.anvildroid.runtime.RuntimeTask "$wanted" "$package"
        return $?
    fi
    cmd activity stack list > close-check.tmp || return 3
    root= count=0 matched=0 selected= candidates=0 home=0
    while IFS= read -r line; do
        case "$line" in
            'RootTask id='*|'Stack id='*)
                if [ "$matched" -eq 1 ] && [ "$count" -eq 1 ] && [ "$home" -eq 0 ]; then selected=$root; candidates=$((candidates+1)); fi
                root=${line#*id=}; root=${root%% *}; count=0 matched=0 home=0 ;;
            *mActivityType=home*) home=1 ;;
            *taskId=*)
                entry=${line#*taskId=}; task_id=${entry%%:*}; component=${entry#*: }; component=${component%% *}
                count=$((count+1))
                if [ "$task_id" = "$wanted" ] && [ "${component%%/*}" = "$package" ]; then matched=$((matched+1)); fi ;;
        esac
    done < close-check.tmp
    if [ "$matched" -eq 1 ] && [ "$count" -eq 1 ] && [ "$home" -eq 0 ]; then selected=$root; candidates=$((candidates+1)); fi
    rm -f close-check.tmp
    case "$selected" in ''|*[!0-9]*|0) return 2;; esac
    [ "$candidates" -eq 1 ] || return 2
    cmd activity stack remove "$selected"
}
if [ "${1:-}" = '--close-one' ]; then close_one_task "${2:-}" "${3:-}"; exit $?; fi

# Only this shell-UID service changes Android's logical display. The adapter
# crops the acknowledged physical projection, retaining HWC's physical mode.
display_full=0
[ -f display-restore ] && display_full=1
rm -f display-size display-size.processing
full_ui_active() {
    [ -n "$(getprop waydroid.anvil_full_ui)" ] || [ "$(getprop waydroid.active_apps)" = Waydroid ]
}
publish_display_state() {
    dumpsys display > display-dump.tmp || return 1
    viewport=$(sed -n '/mViewports=/{p;q;}' display-dump.tmp)
    values=$(printf '%s\n' "$viewport" | sed -n 's/.*orientation=0, .*logicalFrame=Rect(0, 0 - \([0-9]*\), \([0-9]*\)), physicalFrame=Rect(\([0-9]*\), \([0-9]*\) - \([0-9]*\), \([0-9]*\)), deviceWidth=\([0-9]*\), deviceHeight=\([0-9]*\).*/\1 \2 \3 \4 \5 \6 \7 \8/p')
    [ -n "$values" ] || return 1
    requested_w=$1 requested_h=$2
    set -- $values
    [ "$#" -eq 8 ] || return 1
    lw=$1 lh=$2 px=$3 py=$4 right=$5 bottom=$6 pw=$7 ph=$8
    [ "$lw" = "$requested_w" ] && [ "$lh" = "$requested_h" ] || return 1
    printf '%s %s %s %s %s %s %s %s\n' "$lw" "$lh" "$pw" "$ph" "$px" "$py" "$((right-px))" "$((bottom-py))" > display-state.tmp && mv display-state.tmp display-state
}
resize_display() {
    case "$1:$2" in *[!0-9:]*|:*|*:) return 2;; esac
    [ "$1" -ge 160 ] && [ "$2" -ge 160 ] && [ "$1" -le 8192 ] && [ "$2" -le 8192 ] || return 2
    case "$3" in full) full_ui_active || return 2;; canvas) ;; *) return 2;; esac
    if [ "$3" = full ] && [ ! -f display-restore ]; then
        original_size=$(wm size | sed -n 's/^Override size: //p')
        [ -n "$original_size" ] || original_size=reset
        printf '%s %s %s %s %s\n' "$original_size" "$(settings get system accelerometer_rotation)" "$(settings get system user_rotation)" "$(wm fixed-to-user-rotation)" "$(dumpsys window displays | sed -n '/init=.* noscale/{s/.*/1/p;q;}')" > display-restore.tmp && mv display-restore.tmp display-restore || return 1
    fi
    [ "$3" = full ] && display_full=1
    # Request pixel-for-pixel output where Android supports it. Some Android 15
    # builds drop FLAG_SCALING_DISABLED between WM and DMS: acknowledge their
    # actual projection too, rather than silently ignoring valid size requests.
    # The reserved canvas avoids downsampling the largest portrait preset.
    physical=$(wm size | sed -n 's/^Physical size: //p')
    physical_w=${physical%x*} physical_h=${physical#*x}
    case "$physical_w:$physical_h" in *[!0-9:]*|:*|*:) return 1;; esac
    [ "$1" -le "$physical_w" ] && [ "$2" -le "$physical_h" ] || return 3
    wm fixed-to-user-rotation enabled && wm user-rotation lock 0 && wm size "$1"x"$2" && wm scaling off || return 1
    tries=0
    while [ "$tries" -lt 20 ]; do
        if publish_display_state "$1" "$2"; then return 0; fi
        tries=$((tries+1)); sleep 0.05
    done
    return 1
}

bootstrap_display() {
    if [ "$(getprop waydroid.anvil_single_display)" = true ]; then
        if [ ! -f display-state ] && [ ! -f display-size ]; then resize_display 1280 720 canvas; fi
    elif ! full_ui_active; then
        # wm overrides survive restarts in /data. Per-task host frames use
        # Android task coordinates, so an old single-display projection
        # must not scale the content independently of those frames.
        wm size reset && wm scaling auto || return 1
        rm -f display-state
    fi
}

# Android 16 applies desktop launch placement when bounds overlap the status
# bar. In particular a portrait -> landscape resize at (0,0) can restore the
# old portrait bounds. Keep the task's acknowledged origin; host placement is
# independent of these Android canvas coordinates.
resize_app_task() {
    task=$1 width=$2 height=$3
    left=0 top=0
    if [ "$(getprop ro.build.version.sdk)" -ge 36 ] 2>/dev/null; then
        origin=$(sed -n "s/^  taskId=$task: .* bounds=\[\([0-9]*\),\([0-9]*\)\].*/\1 \2/p" tasks)
        set -- $origin
        [ "$#" -eq 2 ] || return 1
        left=$1 top=$2
    fi
    cmd activity task resize "$task" "$left" "$top" "$((left+width))" "$((top+height))"
}

publish_task_snapshot() {
    snapshot=$(cmd activity stack list) || return 1
    # Keep the same inode when bounds are unchanged, allowing the bridge to
    # consume notifications without reparsing the task list on every tick.
    if [ "$snapshot_published" -eq 0 ] || [ "$snapshot" != "$last_snapshot" ] || [ ! -f tasks ]; then
        printf '%s\n' "$snapshot" > tasks.tmp && mv tasks.tmp tasks || return 1
        last_snapshot=$snapshot
        snapshot_published=1
    fi
}

# Fresh capability token on each helper/framework lifetime. Old requests must
# not close a recycled task ID after Android restarts.
close_epoch= close_server=
refresh_close_epoch() {
    server=$(pidof system_server 2>/dev/null)
    [ -n "$server" ] || { rm -f close-epoch; close_epoch=; return; }
    if [ "$server" != "$close_server" ] || [ -z "$close_epoch" ]; then
        close_server=$server
        IFS= read -r close_epoch < /proc/sys/kernel/random/uuid || close_epoch=
        case "$close_epoch" in ''|*[!a-f0-9-]*) close_epoch=; rm -f close-epoch; return;; esac
        rm -f close close.processing
        printf '%s\n' "$close_epoch" > close-epoch.tmp && mv close-epoch.tmp close-epoch
    fi
}
refresh_close_epoch
display_bootstrap=0
snapshot_ticks=0
last_snapshot= snapshot_published=0
while true; do
    if [ -f close ] && [ ! -L close ]; then
        mv close close.processing
        read -r epoch task package extra < close.processing
        result=2
        if [ -n "$close_epoch" ] && [ "$epoch" = "$close_epoch" ] && [ -z "$extra" ] && [ "$(pidof system_server 2>/dev/null)" = "$close_server" ]; then
            close_one_task "$task" "$package"
            result=$?
        fi
        if [ "$task" = '-1' ] && [ "$package" = Waydroid ] && [ -z "$extra" ]; then
            case "$epoch" in
                ''|*[!a-f0-9]*) ;;
                *) if [ "${#epoch}" -eq 32 ]; then
                    CLASSPATH=/data/local/tmp/anvildroid-shutdown.jar timeout -s KILL 8 app_process /system/bin org.anvildroid.runtime.RuntimePresentation leave "$epoch"
                    result=$?
                fi ;;
            esac
        fi
        printf '%s %s %s\n' "$task" "$package" "$result" > close-result.tmp
        mv close-result.tmp close-result
        rm -f close.processing
        snapshot_ticks=0
    fi
    if [ -f resize ] && [ ! -L resize ]; then
        mv resize resize.processing
        read -r task width height extra < resize.processing
        case "$task:$width:$height" in
            *[!0-9:]*|*::*|:*|*:|0:*) ;;
            *)
                if [ -z "$extra" ] && [ -z "$(getprop waydroid.anvil_full_ui)" ] && [ "$width" -ge 64 ] && [ "$height" -ge 64 ] &&
                   [ "$width" -le 8192 ] && [ "$height" -le 8192 ]; then
                    resize_app_task "$task" "$width" "$height"
                    snapshot_ticks=0
                fi
                ;;
        esac
        rm -f resize.processing
    fi
    if [ "$display_bootstrap" -eq 0 ] && [ "$(getprop sys.boot_completed)" = 1 ]; then
        bootstrap_display && display_bootstrap=1
    fi
    if [ -f display-size ] && [ ! -L display-size ]; then
        mv display-size display-size.processing
        read -r width height mode extra < display-size.processing
        if [ -z "$extra" ] && resize_display "$width" "$height" "$mode"; then
            [ "$mode" = full ] && display_full=1
        fi
        rm -f display-size.processing
    fi
    # Full UI and separate app windows share a display. Restore its reserved
    # capacity before returning to app-window mode.
    if [ "$snapshot_ticks" -eq 0 ] && [ "$display_full" -eq 1 ] && ! full_ui_active; then
        read -r original_size original_auto original_rotation original_fixed original_scaling < display-restore
        case "$original_size" in reset|*[!0-9x]*|'') wm size reset;; *) wm size "$original_size";; esac
        case "$original_rotation" in 0|1|2|3) ;; *) original_rotation=0;; esac
        if [ "$original_auto" = 1 ]; then wm user-rotation free; else wm user-rotation lock "$original_rotation"; fi
        case "$original_fixed" in enabled|disabled|default) wm fixed-to-user-rotation "$original_fixed";; *) wm fixed-to-user-rotation default;; esac
        if [ "$original_scaling" = 1 ]; then wm scaling off; else wm scaling auto; fi
        rm -f display-state display-restore
        display_full=0
    fi
    # Check resize requests at 50 ms, but keep the heavier ActivityManager
    # snapshot at about 250 ms when idle. Refresh immediately after a resize.
    if [ "$snapshot_ticks" -eq 0 ]; then
        refresh_close_epoch
        publish_task_snapshot
        snapshot_ticks=5
    fi
    snapshot_ticks=$((snapshot_ticks - 1))
    wait_tick 0.05
done
