#!/usr/bin/env bash
# Focused wiring check for Jawaka's power-hold save and boot resume: the
# synchronous temporary-file save, its capability probe, and the synchronous
# load in the MLP1 command patch.
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

# LOAD_STATE_SYNC: Jawaka's boot resume accepts only this reply as proof that
# the recorded state is the running state.
require '{ "LOAD_STATE_SYNC", command_load_state_sync,' \
    "LOAD_STATE_SYNC is not registered"
forbid '"LOAD_STATE_SLOT_SYNC"' \
    "a LOAD_STATE_SLOT_* name is shadowed by the LOAD_STATE_SLOT prefix match"
if ! awk '/"LOAD_STATE_SYNC", command_load_state_sync,/ { seen = 1 }
          /"LOAD_STATE_SLOT",command_load_state_slot,/ { exit !seen }
          END { if (!seen) exit 1 }' "$PATCH"; then
    fail "LOAD_STATE_SYNC must be listed before LOAD_STATE_SLOT"
fi
require '"LOAD_STATE_SYNC %s OK %llu"' \
    "sync load no longer replies OK with id and bytes"
require '"LOAD_STATE_SYNC %s ERROR %s"' \
    "sync load no longer replies ERROR with id and code"
require '"LOAD_STATE_SYNC - ERROR BAD_ARGS"' \
    "sync load no longer rejects an unparseable line without echoing it"
require 'strcspn(arg, " ") > 32' \
    "sync load no longer bounds the request id before sscanf"
for code in UNSUPPORTED HARDCORE BUSY OPEN READ TOO_LARGE UNSERIALIZE; do
    require "case CONTENT_SYNC_LOAD_${code}: " \
        "sync load no longer reports $code"
done
require 'return CONTENT_SYNC_LOAD_HARDCORE;' \
    "sync load no longer refuses while hardcore is active"
require 'memcmp(data, "#RZIPv", 6) == 0' \
    "sync load no longer refuses a compressed state"
require 'content_sync_load_rastate_bounded(data, _len)' \
    "sync load no longer bounds RASTATE blocks before the core sees them"
require 'CONTENT_SYNC_LOAD_HEADROOM' \
    "sync load no longer bounds the file size before reading it"
# One reply, no task: a queued load would answer before the state applied.
if awk '/^\+bool command_load_state_sync\(/,/^\+}$/' "$PATCH" |
        grep -Eq 'content_load_state\(|task_push'; then
    fail "LOAD_STATE_SYNC must apply the state itself, not queue a load task"
fi
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
