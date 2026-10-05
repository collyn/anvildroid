#!/system/bin/sh
# No host root daemon. Android's shell UID reads ActivityManager state.
umask 007
cd /data/waydroid_tmp/anvildroid || exit 1
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
snapshot_ticks=0
while true; do
    if [ -f close ] && [ ! -L close ]; then
        mv close close.processing
        read -r epoch task package extra < close.processing
        result=2
        if [ -n "$close_epoch" ] && [ "$epoch" = "$close_epoch" ] && [ -z "$extra" ] && [ "$(pidof system_server 2>/dev/null)" = "$close_server" ]; then
            close_one_task "$task" "$package"
            result=$?
        fi
        printf '%s %s %s\n' "$task" "$package" "$result" > close-result.tmp
        mv close-result.tmp close-result
        rm -f close.processing
        snapshot_ticks=0
    fi
    if [ -f resize ]; then
        mv resize resize.processing
        read -r task width height extra < resize.processing
        case "$task:$width:$height" in
            *[!0-9:]*|::*|:*|*:) ;;
            *)
                if [ -z "$extra" ] && [ "$width" -ge 64 ] && [ "$height" -ge 64 ] &&
                   [ "$width" -le 8192 ] && [ "$height" -le 8192 ]; then
                    cmd activity task resize "$task" 0 0 "$width" "$height"
                    snapshot_ticks=0
                fi
                ;;
        esac
        rm -f resize.processing
    fi
    # Check resize requests at 50 ms, but keep the heavier ActivityManager
    # snapshot at about 250 ms when idle. Refresh immediately after a resize.
    if [ "$snapshot_ticks" -eq 0 ]; then
        refresh_close_epoch
        if cmd activity stack list > tasks.tmp; then
            mv tasks.tmp tasks
        fi
        snapshot_ticks=5
    fi
    snapshot_ticks=$((snapshot_ticks - 1))
    sleep 0.05
done
