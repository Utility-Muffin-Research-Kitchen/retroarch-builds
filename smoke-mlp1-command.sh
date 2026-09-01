#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/output/mlp1}"
BINARY="${BINARY:-$OUTPUT_DIR/bin/retroarch}"
BUILD_MANIFEST="${BUILD_MANIFEST:-$OUTPUT_DIR/build-manifest.json}"

if [[ ! -x "$BINARY" ]]; then
    echo "missing executable MLP1 RetroArch binary: $BINARY" >&2
    exit 1
fi

if [[ ! -f "$BUILD_MANIFEST" ]]; then
    echo "missing MLP1 build manifest: $BUILD_MANIFEST" >&2
    echo "run ./build-mlp1.sh before the command smoke check" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required to inspect $BUILD_MANIFEST" >&2
    exit 1
fi

python3 - "$BUILD_MANIFEST" "$BINARY" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
manifest = json.loads(manifest_path.read_text())
flags = set(manifest.get("configure_flags", []))
missing = sorted({"--enable-networking", "--enable-command"} - flags)

if missing:
    print(
        f"manifest is missing command-capable configure flags: {', '.join(missing)}",
        file=sys.stderr,
    )
    sys.exit(1)

patch_controls = manifest.get("patch_controls", {})
patch_set = patch_controls.get("MLP1_PATCH_SET", "")
patches_applied = manifest.get("patches_applied", [])

# Derived from the set rather than enumerated, so this check does not have to be
# updated every time the canonical patch set gains an entry. It previously knew
# only "" and "command-menu" and rejected the real six-entry set outright.
PATCH_PATHS = {
    "portrait-rotation": "common/0002-portrait-panel-landscape-rotation.patch",
    "command-menu": "mlp1/0001-command-menu-commands.patch",
    "jawaka-load-content": "mlp1/0002-jawaka-load-content-command.patch",
    "controller-bindings": "mlp1/0003-controller-only-bindings-ui.patch",
    "record-scale-clamp": "mlp1/0005-record-scale-clamp.patch",
    "record-indicator": "mlp1/0006-recording-indicator.patch",
}

entries = [name for name in patch_set.split(",") if name]
unknown = sorted(set(entries) - set(PATCH_PATHS))
if unknown:
    print(f"manifest has unknown MLP1_PATCH_SET entries: {', '.join(unknown)}",
          file=sys.stderr)
    sys.exit(1)

expected_patches = [PATCH_PATHS[name] for name in entries]
if patches_applied != expected_patches:
    print(
        "manifest patch list mismatch: "
        f"expected {expected_patches}, got {patches_applied}",
        file=sys.stderr,
    )
    sys.exit(1)

print("manifest_ok: command-capable MLP1 build flags are present")
if "command-menu" in entries:
    print("manifest_ok: command-menu patch set is present")

    # A build must not be able to claim command-menu while omitting the
    # namespaced shader replies. The prefix is the capability probe Jawaka
    # uses, so its absence has to fail here rather than at runtime.
    binary = Path(sys.argv[2])
    blob = binary.read_bytes()
    required = [
        b"JAWAKA_GET_SHADER",
        b"JAWAKA_SET_SHADER",
        b"JAWAKA_CLEAR_SHADER",
        b"JAWAKA_SAVE_SHADER_PRESET",
        b"JAWAKA_REMOVE_SHADER_PRESET",
        b"JAWAKA_SHADER ",
    ]
    absent = [token.decode().strip() for token in required if token not in blob]
    if absent:
        print(
            "binary claims command-menu but is missing the namespaced shader "
            f"commands: {', '.join(absent)}",
            file=sys.stderr,
        )
        sys.exit(1)
    if b"SET_SHADER" not in blob:
        print("binary is missing upstream SET_SHADER", file=sys.stderr)
        sys.exit(1)
    print("manifest_ok: namespaced shader commands and upstream SET_SHADER present")
    print(
        "device_required: launch this binary on MLP1 and send "
        "GET_INFO, GET_STATE_SLOT, SET_STATE_SLOT, SAVE_STATE_SLOT, "
        "GET_DISK_COUNT, GET_DISK_SLOT, SET_DISK_SLOT, GET_PATH savestate, "
        "PAUSE, UNPAUSE, MENU_TOGGLE, RESET, QUIT over the RetroArch command interface"
    )
    print(
        "device_required: also exercise JAWAKA_GET_SHADER, JAWAKA_SET_SHADER "
        "(valid, missing, unsupported and unlinkable presets), "
        "JAWAKA_CLEAR_SHADER, and JAWAKA_SAVE/REMOVE_SHADER_PRESET for each of "
        "GAME|PARENT|CORE|GLOBAL plus one unknown scope"
    )
else:
    print("manifest_ok: clean upstream command build has no MLP1 patches")
    print("device_required: launch this binary on MLP1 and send GET_STATUS, PAUSE_TOGGLE, MENU_TOGGLE, QUIT over the RetroArch command interface")
PY
