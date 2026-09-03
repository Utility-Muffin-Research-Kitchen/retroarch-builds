#!/usr/bin/env bash
# Focused wiring check for Leaf's direct RetroArch shader-menu handoff.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH="$ROOT_DIR/patches/mlp1/0001-command-menu-commands.patch"

fail() {
    printf 'advanced-shader-menu-patch: %s\n' "$1" >&2
    exit 1
}

require() {
    local needle="$1" message="$2"
    grep -Fq -- "$needle" "$PATCH" || fail "$message"
}

require '#include "menu/menu_displaylist.h"' \
    "command patch cannot build a menu display list"
require 'if (string_is_equal(arg, "SHADERS"))' \
    "OPEN_MENU no longer recognizes the Shaders destination"
require 'menu_entries_flush_stack(label, 0);' \
    "reopening Advanced can accumulate duplicate shader stack entries"
require 'DISPLAYLIST_OPTIONS_SHADERS' \
    "OPEN_MENU SHADERS no longer builds RetroArch's native shader screen"
require 'retroarch_menu_running();' \
    "direct menu handoff no longer uses RetroArch's input-flushing menu open"
require 'menu_state_get_ptr()->flags & MENU_ST_FLAG_ALIVE' \
    "GET_STATUS no longer distinguishes a foreground RetroArch menu"
require 'strlcpy(reply + _len, "MENU"' \
    "GET_STATUS no longer reports the menu state to Jawaka"

if [ -d "$ROOT_DIR/workdir/src/RetroArch/.git" ]; then
    git -C "$ROOT_DIR/workdir/src/RetroArch" apply --check "$PATCH" ||
        fail "command patch does not apply to the fetched RetroArch source"
fi

echo "PASS advanced-shader-menu-patch-test"
