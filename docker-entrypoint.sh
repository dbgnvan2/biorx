#!/bin/sh
# Make the mounted data volume writable by the unprivileged user, then drop to
# it. Runs as root only for these few lines.
#
# Why this exists: a build-time `chown /data` does not survive a runtime volume
# mount. The platform mounts its own directory over /data — usually owned by
# root — and the first write then fails with PermissionError at startup.
#
# Kept deliberately flat. Three earlier attempts at this file each introduced a
# new defect in the same ten lines: a guard that asked about root instead of the
# app user, so the chown ran only when it was pointless; and an `exec` of a
# shell function, which no shell can do (exit 127). The only indirection left is
# needs_chown, which exists so a test can call it.
#
# Source with ENTRYPOINT_SOURCE_ONLY=1 to get the functions without running.
set -e

DATA_DIR="${DATA_DIR:-/data}"
APP_USER="${APP_USER:-biorx}"

fatal() {
    echo "entrypoint: FATAL $1" >&2
    echo "entrypoint: mount the volume writable by $APP_USER." >&2
    exit 1
}

# True when APP_USER cannot write DATA_DIR.
#
# The test runs AS THE TARGET USER. Asking `[ -O ]`/`[ -w ]` here would ask
# about the current user — root — who owns and can write a root-owned volume,
# which is the one case this whole script exists to fix.
needs_chown() {
    ! gosu "$APP_USER" test -w "$DATA_DIR" 2>/dev/null
}

main() {
    if [ "$(id -u)" != "0" ]; then
        # The platform enforces a uid; nothing can be chowned from here. Fail
        # early and clearly rather than at the first database write.
        [ -w "$DATA_DIR" ] || fatal "$DATA_DIR is not writable by uid $(id -u)."
        exec "$@"
    fi

    mkdir -p "$DATA_DIR"
    if needs_chown; then
        echo "entrypoint: $APP_USER cannot write $DATA_DIR — taking ownership"
        chown -R "$APP_USER" "$DATA_DIR" || fatal "could not chown $DATA_DIR."
        needs_chown && fatal "$DATA_DIR is still not writable by $APP_USER."
    fi

    # A real command, never a shell function: `exec` replaces the process image
    # and cannot call a function.
    exec gosu "$APP_USER" "$@"
}

[ "${ENTRYPOINT_SOURCE_ONLY:-}" = "1" ] || main "$@"
