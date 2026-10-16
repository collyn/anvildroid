#!/system/bin/sh
# Narrow app-management queue shared with the logged-in host user (Android
# system group), never an arbitrary command interpreter. No eval/shell payloads.
umask 007
base=/data/waydroid_tmp/anvildroid
cd "$base" || exit 1
mkdir -p apps
chmod 2770 apps
while true; do
    for request in apps/job-*.ready; do
        [ -d "$request" ] && [ ! -L "$request" ] || continue
        work=${request%.ready}.working
        mv "$request" "$work" || continue
        (
            cd "$work" || exit 1
            exec > output.tmp 2>&1
            read -r action < action
            read -r package < package
            case "$package" in
                ''|*[!a-zA-Z0-9_.]*|.*|*.|*..*) echo 'Invalid package'; exit 2;;
            esac
            case "$action" in
                details) dumpsys package "$package" ;;
                install)
                    [ -f input.apk ] && [ ! -L input.apk ] || exit 2
                    pm install -r --user 0 "$base/$work/input.apk" ;;
                force-stop)
                    case "$package" in
                        android|com.android.*|org.lineageos.*|org.anvildroid.*|id.waydro.*)
                            echo 'Protected component'; exit 2;;
                    esac
                    am force-stop --user 0 "$package" ;;
                uninstall)
                    case "$package" in
                        android|com.android.*|org.lineageos.*|org.anvildroid.*|id.waydro.*|cu.axel.smartdock)
                            echo 'Protected component'; exit 2;;
                    esac
                    third_party=$(pm list packages -3 --user 0 "$package") || exit 1
                    printf '%s\n' "$third_party" | grep -Fxq "package:$package" || {
                        echo 'Cannot uninstall a system or protected app'; exit 2;
                    }
                    response=$(timeout 15 pm uninstall --user 0 "$package")
                    result=$?
                    printf '%s\n' "$response"
                    if [ "$result" -ne 0 ] || [ "$response" != Success ]; then
                        echo "Android uninstall did not complete (exit $result). Refresh before retrying."
                        exit 1
                    fi
                    remaining=$(pm list packages --user 0 "$package") || exit 1
                    if printf '%s\n' "$remaining" | grep -Fxq "package:$package"; then
                        echo 'Android still reports the app installed; refresh before retrying'
                        exit 1
                    fi ;;
                *) echo 'Unsupported operation'; exit 2;;
            esac
        )
        result=$?
        mv "$work/output.tmp" "$work/output"
        echo "$result" > "$work/status.tmp"
        mv "$work/status.tmp" "$work/status"
        # Host owns cleanup after consuming the atomic status marker.
    done
    sleep 0.1
done
