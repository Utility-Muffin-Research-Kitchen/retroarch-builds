#!/usr/bin/env bash
# Focused wiring check for Jawaka's power-hold save: the synchronous
# temporary-file save and its capability probe in the MLP1 command patch.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH="$ROOT_DIR/patches/mlp1/0001-command-menu-commands.patch"

fail() {
    printf 'power-hold-sync-save-patch: %s\n' "$1" >&2
    exit 1
}

require() {
    local needle="$1" message="$2"
    grep -Fq -- "$needle" "$PATCH" || fail "$message"
}

forbid() {
    local needle="$1" message="$2"
    if grep -Fq -- "$needle" "$PATCH"; then
        fail "$message"
    fi
}

require '{ "GET_STATE_SAVE_INFO", command_get_state_save_info,' \
    "GET_STATE_SAVE_INFO is not registered"
require '{ "SAVE_STATE_SYNC", command_save_state_sync,' \
    "SAVE_STATE_SYNC is not registered"
# RetroArch matches action commands by prefix and stops at the first
# mismatch, so SAVE_STATE_SLOT_SYNC would be swallowed by SAVE_STATE_SLOT.
forbid '"SAVE_STATE_SLOT_SYNC"' \
    "a SAVE_STATE_SLOT_* name is shadowed by the SAVE_STATE_SLOT prefix match"
require '"GET_STATE_SAVE_INFO 1 %llu %d"' \
    "capability reply no longer carries protocol version, size and compression"
require '"SAVE_STATE_SYNC %s TMP_READY %llu %s"' \
    "sync save no longer replies TMP_READY with id, bytes and path"
require '"%s.tmp-%s"' \
    "sync save no longer writes a request-specific temporary name"
require 'O_WRONLY | O_CREAT | O_EXCL' \
    "sync save may overwrite an existing file"
require 'fsync(fd)' \
    "sync save no longer fsyncs before replying"
require 'if (close(fd) != 0)' \
    "sync save ignores close errors"
require 'return CONTENT_SYNC_SAVE_LATE;' \
    "sync save no longer rechecks the start-by bound"
require 'return CONTENT_SYNC_SAVE_COMPRESSED;' \
    "sync save no longer refuses compressed states"
forbid 'take_screenshot' \
    "the command patch now captures thumbnails"
# The definition's parameter list ends in ")" where the header prototype ends in ");".
if awk '/uint64_t max_bytes, int64_t start_by_ms, size_t \*out_len\)$/,/^\+}$/' "$PATCH" |
        grep -Eq 'rename|filestream_rename'; then
    fail "RetroArch must never publish the temporary file itself"
fi

if [ -d "$ROOT_DIR/workdir/src/RetroArch/.git" ] &&
   [ -z "$(git -C "$ROOT_DIR/workdir/src/RetroArch" status --porcelain)" ]; then
    git -C "$ROOT_DIR/workdir/src/RetroArch" apply --check \
        "$ROOT_DIR/patches/common/0002-portrait-panel-landscape-rotation.patch" "$PATCH" ||
        fail "command patch does not apply to the fetched RetroArch source"
fi

echo "PASS power-hold-sync-save-patch-test"
