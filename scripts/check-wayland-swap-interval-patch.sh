#!/usr/bin/env bash
# Focused check for the MLP1 wayland-swap-interval patch: applies it to the
# fetched RetroArch source's sdl_gl_ctx.c in a scratch directory, builds that
# on the host against RetroArch's headers and a stub SDL/EGL layer, and runs
# tests/wayland-swap-interval/check.c.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH="$ROOT_DIR/patches/mlp1/0007-wayland-swap-interval.patch"
TEST_DIR="$ROOT_DIR/tests/wayland-swap-interval"
RETROARCH_SRC_DIR="${RETROARCH_SRC_DIR:-$ROOT_DIR/workdir/src/RetroArch}"
CC="${CC:-cc}"
FILE=gfx/drivers_context/sdl_gl_ctx.c

fail() {
    printf 'wayland-swap-interval-patch: %s\n' "$1" >&2
    exit 1
}

[ -d "$RETROARCH_SRC_DIR/.git" ] ||
    fail "no fetched RetroArch source at $RETROARCH_SRC_DIR (run ./fetch-retroarch.sh)"

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

# From HEAD, not the working tree: a build in progress has patches applied.
mkdir -p "$scratch/src/$(dirname "$FILE")"
git -C "$RETROARCH_SRC_DIR" show "HEAD:$FILE" > "$scratch/src/$FILE"
(cd "$scratch/src" && git apply "$PATCH") ||
    fail "patch does not apply to the fetched RetroArch source"

# The patched file's "../../" includes fall through to the -I of the real
# drivers_context directory, so they resolve to the fetched headers.
"$CC" -std=gnu99 -Wall -Werror -Wno-unused-function \
    -DHAVE_SDL2 -DHAVE_EGL \
    -DSDL_GL_CTX_SOURCE="\"$scratch/src/$FILE\"" \
    -I "$TEST_DIR" \
    -I "$RETROARCH_SRC_DIR/libretro-common/include" \
    -I "$RETROARCH_SRC_DIR/gfx/drivers_context" \
    -o "$scratch/check" "$TEST_DIR/check.c" ||
    fail "check does not build"

"$scratch/check"
