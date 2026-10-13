#!/usr/bin/env bash
# Focused check for the MLP1 sdl-gl-attributes patch: applies the patches that
# touch gfx/drivers_context/sdl_gl_ctx.c (wayland-swap-interval, then this one,
# the order of the canonical set) to the fetched RetroArch source's copy in a
# scratch directory, builds that on the host against RetroArch's headers and a
# stub SDL whose video start resets the GL attributes, and runs
# tests/sdl-gl-attributes/check.c. It then runs the same check without the
# patch, which must fail: a check that passes either way proves nothing.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWAP_PATCH="$ROOT_DIR/patches/mlp1/0007-wayland-swap-interval.patch"
PATCH="$ROOT_DIR/patches/mlp1/0008-sdl-gl-apply-attributes-after-video-init.patch"
TEST_DIR="$ROOT_DIR/tests/sdl-gl-attributes"
# The SDL and EGL stand-in headers are shared with the swap interval check.
STUB_DIR="$ROOT_DIR/tests/wayland-swap-interval"
RETROARCH_SRC_DIR="${RETROARCH_SRC_DIR:-$ROOT_DIR/workdir/src/RetroArch}"
CC="${CC:-cc}"
FILE=gfx/drivers_context/sdl_gl_ctx.c

fail() {
    printf 'sdl-gl-attributes-patch: %s\n' "$1" >&2
    exit 1
}

[ -d "$RETROARCH_SRC_DIR/.git" ] ||
    fail "no fetched RetroArch source at $RETROARCH_SRC_DIR (run ./fetch-retroarch.sh)"

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

build() { # build NAME: compile the scratch copy named NAME
    "$CC" -std=gnu99 -Wall -Werror -Wno-unused-function \
        -DHAVE_SDL2 -DHAVE_EGL \
        -DSDL_GL_CTX_SOURCE="\"$scratch/$1/$FILE\"" \
        -I "$TEST_DIR" -I "$STUB_DIR" \
        -I "$RETROARCH_SRC_DIR/libretro-common/include" \
        -I "$RETROARCH_SRC_DIR/gfx/drivers_context" \
        -o "$scratch/$1/check" "$TEST_DIR/check.c"
}

# From HEAD, not the working tree: a build in progress has patches applied.
for name in without with; do
    mkdir -p "$scratch/$name/$(dirname "$FILE")"
    git -C "$RETROARCH_SRC_DIR" show "HEAD:$FILE" > "$scratch/$name/$FILE"
    (cd "$scratch/$name" && git apply "$SWAP_PATCH") ||
        fail "0007 does not apply to the fetched RetroArch source"
done
(cd "$scratch/with" && git apply "$PATCH") ||
    fail "patch does not apply after wayland-swap-interval"

build with || fail "check does not build with the patch"
"$scratch/with/check"

build without || fail "check does not build without the patch"
if "$scratch/without/check" >"$scratch/without/out" 2>&1; then
    fail "the check passes without the patch, so it proves nothing"
fi
printf 'ok   the check fails without the patch (%s)\n' "$(grep -c '^FAIL' "$scratch/without/out") failure(s)"
