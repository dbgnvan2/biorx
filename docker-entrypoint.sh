#!/bin/sh
# Make the mounted data volume writable by the unprivileged user, then drop to
# it. Runs as root only for these few lines.
#
# Why this exists: a build-time `chown /data` does not survive a runtime volume
# mount. The platform mounts its own directory over /data — usually owned by
# root — and the first write then fails with PermissionError at startup.
#
# The decision is a function so it can be exercised by a test rather than
# inferred from reading it: source this file with ENTRYPOINT_SOURCE_ONLY=1 and
# call needs_chown yourself.
set -e

DATA_DIR="${DATA_DIR:-/data}"
APP_USER="${APP_USER:-biorx}"

run_as_app() {
    gosu "$APP_USER" "$@"
}

# True when APP_USER cannot write DATA_DIR.
#
# The test must run AS THE TARGET USER. An earlier version asked `[ -O dir ] &&
# [ -w dir ]`, which tests the *current* user — root — who owns and can write a
# root-owned volume, so the chown was skipped in exactly the case it existed
# for, and ran only when it was already unnecessary.
needs_chown() {
    ! run_as_app test -w "$DATA_DIR" 2>/dev/null
}

main() {
    if [ "$(id -u)" = "0" ]; then
        mkdir -p "$DATA_DIR"
        if needs_chown; then
            echo "entrypoint: $APP_USER cannot write $DATA_DIR — taking ownership"
            if ! chown -R "$APP_USER" "$DATA_DIR"; then
                echo "entrypoint: FATAL could not give $APP_USER ownership of $DATA_DIR." >&2
                echo "entrypoint: mount the volume writable by uid $(id -u "$APP_USER")." >&2
                exit 1
            fi
            if needs_chown; then
                echo "entrypoint: FATAL $DATA_DIR is still not writable by $APP_USER." >&2
                exit 1
            fi
        fi
        exec run_as_app "$@"
    fi

    # Already unprivileged, because the platform enforces a uid. Nothing can be
    # chowned from here, so fail early and clearly rather than at the first write.
    if [ ! -w "$DATA_DIR" ]; then
        echo "entrypoint: FATAL $DATA_DIR is not writable by uid $(id -u)." >&2
        echo "entrypoint: mount the volume writable by that uid." >&2
        exit 1
    fi
    exec "$@"
}

[ "${ENTRYPOINT_SOURCE_ONLY:-}" = "1" ] || main "$@"
