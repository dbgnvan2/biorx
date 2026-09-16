#!/bin/sh
# Make the mounted data volume writable by the unprivileged user, then drop to
# it. Runs as root only for these few lines.
#
# This exists because a build-time `chown /data` does not survive a runtime
# volume mount: the platform mounts its own directory over /data, usually owned
# by root, and the first write then fails with PermissionError at startup.
set -e

DATA_DIR="${DATA_DIR:-/data}"
APP_USER="${APP_USER:-biorx}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    if [ ! -O "$DATA_DIR" ] || [ ! -w "$DATA_DIR" ]; then
        echo "entrypoint: taking ownership of $DATA_DIR for $APP_USER"
        chown -R "$APP_USER" "$DATA_DIR" 2>/dev/null || {
            echo "entrypoint: WARNING could not chown $DATA_DIR." >&2
            echo "entrypoint: the volume must be writable by uid $(id -u "$APP_USER")." >&2
        }
    fi
    exec gosu "$APP_USER" "$@"
fi

# Already unprivileged (some platforms enforce a uid): carry on and let the
# app's own startup check report clearly if the volume is not writable.
exec "$@"
