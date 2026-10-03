#!/usr/bin/env bash
# Runtime smoke for LOAD_STATE_SYNC against the built MLP1 RetroArch.
#
# The MLP1 toolchain image is arm64, as is the binary, so on an arm64 host
# Docker runs RetroArch natively with the sysroot's loader and libraries. A
# test core (tests/load-state-sync/testcore.c) is cross-built in the same
# container; RetroArch runs it with null video/audio/input drivers and the UDP
# command port, and tests/load-state-sync/drive.py exercises OK, a missing
# file, truncated files, and the other refusals.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/output/mlp1}"
BINARY="${BINARY:-$OUTPUT_DIR/bin/retroarch}"
TOOLCHAIN_IMAGE="${TOOLCHAIN_IMAGE:-ghcr.io/utility-muffin-research-kitchen/mlp1-toolchain:latest}"
RETROARCH_SRC_DIR="${RETROARCH_SRC_DIR:-$SCRIPT_DIR/workdir/src/RetroArch}"

if [[ "${IN_MLP1_CONTAINER:-0}" != "1" ]]; then
    if [[ ! -x "$BINARY" ]]; then
        echo "missing executable MLP1 RetroArch binary: $BINARY" >&2
        exit 1
    fi
    if [[ ! -f "$RETROARCH_SRC_DIR/libretro-common/include/libretro.h" ]]; then
        echo "missing libretro.h; run ./fetch-retroarch.sh first" >&2
        exit 1
    fi
    if ! docker image inspect "$TOOLCHAIN_IMAGE" >/dev/null 2>&1; then
        echo "missing Docker image: $TOOLCHAIN_IMAGE" >&2
        exit 1
    fi
    exec docker run --rm --init \
        -e IN_MLP1_CONTAINER=1 \
        -v "$SCRIPT_DIR":/workspace:ro \
        -v "$(cd "$(dirname "$BINARY")" && pwd)":/ra-bin:ro \
        -v "$RETROARCH_SRC_DIR/libretro-common/include":/ra-include:ro \
        -w /workspace \
        "$TOOLCHAIN_IMAGE" \
        /workspace/smoke-mlp1-load-state-sync.sh
fi

WORK="$(mktemp -d)"
RA_PID=""
cleanup() {
    if [[ -n "$RA_PID" ]] && kill -0 "$RA_PID" 2>/dev/null; then
        kill "$RA_PID" 2>/dev/null || true
        wait "$RA_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/home" "$WORK/states" "$WORK/saves" "$WORK/system" "$WORK/info"
"${CROSS_TRIPLE:-aarch64-buildroot-linux-gnu}-gcc" -O2 -Wall -Wextra -Werror \
    -shared -fPIC -I/ra-include \
    -o "$WORK/testcore_libretro.so" /workspace/tests/load-state-sync/testcore.c
printf 'not a real game\n' > "$WORK/game.bin"
cp /workspace/tests/load-state-sync/testcore_libretro.info "$WORK/info/"

cat > "$WORK/retroarch.cfg" <<EOF
video_driver = "null"
audio_driver = "null"
input_driver = "null"
input_joypad_driver = "null"
menu_driver = "rgui"
midi_driver = "null"
microphone_driver = "null"
camera_driver = "null"
location_driver = "null"
record_driver = "null"
bluetooth_driver = "null"
wifi_driver = "null"
config_save_on_exit = "false"
quit_press_twice = "false"
network_cmd_enable = "true"
network_cmd_port = "55355"
savestate_directory = "$WORK/states"
savefile_directory = "$WORK/saves"
system_directory = "$WORK/system"
libretro_directory = "$WORK"
libretro_info_path = "$WORK/info"
sort_savestates_enable = "false"
sort_savestates_by_content_enable = "false"
savestate_file_compression = "false"
savestate_thumbnail_enable = "false"
savestate_auto_load = "false"
savestate_auto_save = "false"
cheevos_enable = "false"
pause_nonactive = "false"
video_threaded = "false"
EOF

SYSROOT="${SYSROOT:?toolchain image did not set SYSROOT}"
HOME="$WORK/home" "$SYSROOT/lib/ld-linux-aarch64.so.1" \
    --library-path "$SYSROOT/lib:$SYSROOT/usr/lib:$SYSROOT/usr/lib/pulseaudio" \
    /ra-bin/retroarch --verbose --config "$WORK/retroarch.cfg" \
    -L "$WORK/testcore_libretro.so" "$WORK/game.bin" \
    > "$WORK/retroarch.log" 2>&1 &
RA_PID=$!

if ! python3 /workspace/tests/load-state-sync/drive.py "$WORK/states" "$WORK/retroarch.log"; then
    echo "--- retroarch.log (tail) ---" >&2
    tail -40 "$WORK/retroarch.log" >&2
    exit 1
fi

for _ in $(seq 1 50); do
    kill -0 "$RA_PID" 2>/dev/null || break
    sleep 0.1
done
if kill -0 "$RA_PID" 2>/dev/null; then
    echo "RetroArch did not exit after QUIT" >&2
    exit 1
fi
wait "$RA_PID" || { echo "RetroArch exited non-zero after QUIT" >&2; exit 1; }
RA_PID=""
echo "PASS smoke-mlp1-load-state-sync"
