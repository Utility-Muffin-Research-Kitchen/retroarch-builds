#!/usr/bin/env bash
# Focused check for the MLP1 hotkey-poll-block patch: applies the patches that
# touch input/input_driver.c (jawaka-load-content, controller-bindings, then
# this one, the order of the canonical set) to the fetched RetroArch source's
# copy in a scratch directory, builds that on the host against RetroArch's
# headers with a stub joypad, and runs tests/hotkey-poll-block/check.c, which
# replays RetroArch's frame order around a Menu + R2 chord. It then runs the
# same check without the patch, which must fail: a check that passes either
# way proves nothing.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH="$ROOT_DIR/patches/mlp1/0009-hotkey-block-at-poll.patch"
EARLIER_PATCHES=(
    "$ROOT_DIR/patches/mlp1/0002-jawaka-load-content-command.patch"
    "$ROOT_DIR/patches/mlp1/0003-controller-only-bindings-ui.patch"
)
TEST_DIR="$ROOT_DIR/tests/hotkey-poll-block"
RETROARCH_SRC_DIR="${RETROARCH_SRC_DIR:-$ROOT_DIR/workdir/src/RetroArch}"
CC="${CC:-cc}"
FILE=input/input_driver.c

fail() {
    printf 'hotkey-poll-block-patch: %s\n' "$1" >&2
    exit 1
}

[ -d "$RETROARCH_SRC_DIR/.git" ] ||
    fail "no fetched RetroArch source at $RETROARCH_SRC_DIR (run ./fetch-retroarch.sh)"

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

build() { # build NAME: compile the scratch copy named NAME
    # The copy's relative includes miss next to it and fall through to the
    # header tree via -I.
    "$CC" -std=gnu99 -Wall -Werror -Wno-unused-function -Wno-unused-variable \
        -DINPUT_DRIVER_SOURCE="\"$scratch/$1/$FILE\"" \
        -I "$tree/input" \
        -I "$tree" \
        -I "$tree/libretro-common/include" \
        -o "$scratch/$1/check" "$TEST_DIR/check.c"
}

# From HEAD, not the working tree: a build in progress has patches applied.
# Every header comes along because the earlier patches change some of them
# (controller-bindings adds a setting that its input_driver.c hunk reads).
tree="$scratch/tree"
mkdir -p "$tree"
git -C "$RETROARCH_SRC_DIR" archive HEAD -- \
    $(git -C "$RETROARCH_SRC_DIR" ls-files '*.h') "$FILE" | tar -x -C "$tree"
for earlier in "${EARLIER_PATCHES[@]}"; do
    (cd "$tree" && git apply --include='*.h' --include="$FILE" "$earlier") ||
        fail "$(basename "$earlier") does not apply to the fetched RetroArch source"
done
for name in without with; do
    mkdir -p "$scratch/$name/$(dirname "$FILE")"
    cp "$tree/$FILE" "$scratch/$name/$FILE"
done
(cd "$scratch/with" && git apply "$PATCH") ||
    fail "patch does not apply after the earlier input_driver.c patches"

build with || fail "check does not build with the patch"
"$scratch/with/check"

build without || fail "check does not build without the patch"
if "$scratch/without/check" >"$scratch/without/out" 2>&1; then
    fail "the check passes without the patch, so it proves nothing"
fi
printf 'ok   the check fails without the patch (%s)\n' "$(grep -c '^FAIL' "$scratch/without/out")"
