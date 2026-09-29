#!/usr/bin/env bash
set -euo pipefail

out="${1:?usage: verify-mlp1-ffmpeg.sh OUTPUT_DIR}"
readelf_bin="${READELF:-aarch64-buildroot-linux-gnu-readelf}"
[[ -x "$out/bin/ffmpeg" ]] || { echo "missing FFmpeg CLI" >&2; exit 1; }
[[ -f "$out/configure-inputs.txt" ]] || { echo "missing configure inputs" >&2; exit 1; }
[[ "$(patchelf --print-rpath "$out/bin/ffmpeg")" == '$ORIGIN/../lib/ffmpeg' ]] || {
    echo "FFmpeg CLI runpath is wrong" >&2; exit 1;
}

required=(libavcodec.so.60 libavdevice.so.60 libavfilter.so.9
    libavformat.so.60 libavutil.so.58 libpostproc.so.57
    librockchip_mpp.so.1 libswresample.so.4 libswscale.so.7)
for name in "${required[@]}"; do
    file="$out/flat/$name"
    [[ -f "$file" && ! -L "$file" ]] || { echo "missing flattened library: $name" >&2; exit 1; }
    [[ "$(patchelf --print-rpath "$file")" == '$ORIGIN' ]] || {
        echo "wrong runpath: $name" >&2; exit 1;
    }
    soname="$("$readelf_bin" -d "$file" | sed -n 's/.*SONAME.*\[\(.*\)\]/\1/p')"
    [[ "$soname" == "$name" ]] || { echo "wrong SONAME: $name -> $soname" >&2; exit 1; }
done
if ! aarch64-buildroot-linux-gnu-strings "$out/flat/libavcodec.so.60" | grep -x h264_rkmpp >/dev/null; then
    echo "FFmpeg has no h264_rkmpp encoder" >&2
    exit 1
fi
echo "verified MLP1 FFmpeg CLI, h264_rkmpp, flattened SONAMEs and runpaths"
