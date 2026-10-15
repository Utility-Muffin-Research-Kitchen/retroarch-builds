#!/usr/bin/env bash
# Build the SDL2 that Leaf's MLP1 RetroArch loads instead of the rootfs copy.
#
# RetroArch is built --disable-wayland, so everything it draws on the MLP1 goes
# through SDL's Wayland backend, and the rootfs SDL is 2.28.5. When HDMI is
# unplugged in HDMI mode, Weston loses its only output, SDL_GetDisplayForWindow
# returns NULL on the next vsynced swap, and Wayland_GLES_SwapWindow
# dereferences it: RetroArch dies with SIGSEGV before it writes SRAM. Upstream
# fixed that in 2.30.2. This builds the same 2.28.5 with that null check
# backported (patches/mlp1/sdl2/), so nothing else about SDL changes. 2.30 also
# changes Wayland frame throttling and scaling, which is not worth taking for
# a null check.
#
# 0002 is ours: with only 0001, an unplug can instead leave RetroArch spinning
# forever in the same function's frame-callback wait once the connection to
# Weston fails, which loses SRAM the same way. See the patch for the details.
#
# The result is one regular file named by its SONAME, because the SD card is
# FAT32 and cannot carry a symlink chain. build-mlp1.sh gives RetroArch a
# RUNPATH of $ORIGIN/../lib/retroarch, so RetroArch loads this copy and nothing
# else on the device sees it.
#
# Runs INSIDE the mlp1-toolchain image. build-mlp1.sh sources this file and
# calls mlp1_sdl2_build; running it directly builds just the library.

MLP1_SDL2_VERSION="2.28.5"
# The same tarball and hash as Buildroot 2024.02's package/sdl2/sdl2.hash.
MLP1_SDL2_SHA256="332cb37d0be20cb9541739c61f79bae5a477427d79ae85e352089afdaf6666e4"
MLP1_SDL2_URLS=(
    "https://github.com/libsdl-org/SDL/releases/download/release-$MLP1_SDL2_VERSION/SDL2-$MLP1_SDL2_VERSION.tar.gz"
    "https://www.libsdl.org/release/SDL2-$MLP1_SDL2_VERSION.tar.gz"
)
# Relative to patches/, like build-mlp1.sh's RetroArch patch labels.
MLP1_SDL2_PATCHES=(
    "mlp1/sdl2/0001-wayland-check-display-before-dereferencing.patch"
    "mlp1/sdl2/0002-wayland-stop-swap-wait-on-connection-error.patch"
)

# SDL's configure refuses Wayland without xkbcommon, and the toolchain sysroot
# has none. SDL dlopens libxkbcommon at runtime like the rest of Wayland, so
# the build needs only its headers and a name to dlopen. 1.4.0 is Buildroot
# 2024.02's version, and the device's library exports nothing newer than its
# V_1.0.0 symbol set; the hash is Buildroot's package/libxkbcommon one.
MLP1_XKBCOMMON_VERSION="1.4.0"
MLP1_XKBCOMMON_SHA256="106cec5263f9100a7e79b5f7220f889bc78e7d7ffc55d2b6fdb1efefb8024031"
MLP1_XKBCOMMON_URLS=(
    "https://xkbcommon.org/download/libxkbcommon-$MLP1_XKBCOMMON_VERSION.tar.xz"
    "https://sources.buildroot.net/libxkbcommon/libxkbcommon-$MLP1_XKBCOMMON_VERSION.tar.xz"
)

# The device's SDL dlopens libxkbcommon.so.0. configure picks the shortest
# libxkbcommon.so.* it can see, so the build-only stub carries exactly that name.
MLP1_SDL2_CONFIGURE_FLAGS=(
    "--prefix=/usr"
    "--disable-static"
    "--enable-video-wayland"
    "--disable-video-x11"
    "--enable-video-kmsdrm"
    "--disable-video-vulkan"
    "--disable-esd"
    "--disable-arts"
    "--disable-nas"
    "--disable-jack"
)
MLP1_SDL2_SONAME="libSDL2-2.0.so.0"
# Same as the device's /usr/lib/libSDL2-2.0.so.0.2800.5: everything else,
# Wayland, EGL, KMSDRM and audio included, is loaded at runtime.
MLP1_SDL2_EXPECTED_NEEDED="ld-linux-aarch64.so.1 libc.so.6 libm.so.6"
# What RetroArch relies on, checked against the generated SDL_config.h.
MLP1_SDL2_REQUIRED_CONFIG=(
    'SDL_VIDEO_DRIVER_WAYLAND 1'
    'SDL_VIDEO_DRIVER_WAYLAND_DYNAMIC "libwayland-client.so.0"'
    'SDL_VIDEO_DRIVER_WAYLAND_DYNAMIC_EGL "libwayland-egl.so.1"'
    'SDL_VIDEO_DRIVER_WAYLAND_DYNAMIC_CURSOR "libwayland-cursor.so.0"'
    'SDL_VIDEO_DRIVER_WAYLAND_DYNAMIC_XKBCOMMON "libxkbcommon.so.0"'
    'SDL_VIDEO_OPENGL_EGL 1'
    'SDL_VIDEO_OPENGL_ES2 1'
    'SDL_JOYSTICK_LINUX 1'
    'SDL_HAPTIC_LINUX 1'
)

# Every SDL_*_DYNAMIC* define and the library it dlopens, as alternating
# names and values, for the build manifest.
MLP1_SDL2_DYNAMIC_CONFIG=()
MLP1_SDL2_OUTPUT_SHA256=""

mlp1_sdl2_fail() {
    echo "MLP1 SDL2: $*" >&2
    return 1
}

# mlp1_sdl2_fetch DIR NAME SHA256 URL... -- leaves DIR/NAME matching SHA256.
mlp1_sdl2_fetch() {
    local dir="$1" name="$2" sha="$3"
    local url partial
    shift 3

    mkdir -p "$dir"
    if [[ -f "$dir/$name" ]] && echo "$sha  $dir/$name" | sha256sum -c --status; then
        return 0
    fi
    partial="$dir/.$name.partial"
    for url in "$@"; do
        rm -f "$partial"
        echo "fetching $url"
        if curl -fsSL --retry 2 -o "$partial" "$url" &&
            echo "$sha  $partial" | sha256sum -c --status; then
            mv -f "$partial" "$dir/$name"
            return 0
        fi
        echo "  rejected: download failed or SHA-256 differs" >&2
    done
    rm -f "$partial"
    mlp1_sdl2_fail "no source matched $sha for $name"
}

# mlp1_sdl2_build OUTPUT_DIR -- writes OUTPUT_DIR/libSDL2-2.0.so.0.
mlp1_sdl2_build() {
    local output_dir="$1"
    local repo_root download_dir work overlay src label config needed
    local cflags ldflags strip entry built
    repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    download_dir="${MLP1_SDL2_DOWNLOAD_DIR:-$repo_root/workdir/downloads}"
    work="${MLP1_SDL2_WORK_DIR:-/tmp/mlp1-sdl2}"
    strip="${STRIP:-$CROSS_TRIPLE-strip}"

    mlp1_sdl2_fetch "$download_dir" "SDL2-$MLP1_SDL2_VERSION.tar.gz" \
        "$MLP1_SDL2_SHA256" "${MLP1_SDL2_URLS[@]}" || return 1
    mlp1_sdl2_fetch "$download_dir" "libxkbcommon-$MLP1_XKBCOMMON_VERSION.tar.xz" \
        "$MLP1_XKBCOMMON_SHA256" "${MLP1_XKBCOMMON_URLS[@]}" || return 1

    # The container's own filesystem: ephemeral and case-sensitive, unlike a
    # bind mount from a macOS host.
    rm -rf "$work"
    mkdir -p "$work"
    tar -xzf "$download_dir/SDL2-$MLP1_SDL2_VERSION.tar.gz" -C "$work"
    tar -xJf "$download_dir/libxkbcommon-$MLP1_XKBCOMMON_VERSION.tar.xz" -C "$work"
    src="$work/SDL2-$MLP1_SDL2_VERSION"

    for label in "${MLP1_SDL2_PATCHES[@]}"; do
        echo "Applying SDL2 patch: $label"
        patch -d "$src" -p1 --forward --batch < "$repo_root/patches/$label" ||
            { mlp1_sdl2_fail "patch did not apply: $label"; return 1; }
    done

    # The xkbcommon overlay stays out of the sysroot. build-mlp1.sh configures
    # RetroArch in this same container afterwards, and RetroArch's configure
    # would find an xkbcommon there and link the stub below.
    overlay="$work/xkbcommon-overlay"
    mkdir -p "$overlay/include/xkbcommon" "$overlay/lib/pkgconfig"
    cp "$work/libxkbcommon-$MLP1_XKBCOMMON_VERSION"/include/xkbcommon/xkbcommon.h \
        "$work/libxkbcommon-$MLP1_XKBCOMMON_VERSION"/include/xkbcommon/xkbcommon-compat.h \
        "$work/libxkbcommon-$MLP1_XKBCOMMON_VERSION"/include/xkbcommon/xkbcommon-compose.h \
        "$work/libxkbcommon-$MLP1_XKBCOMMON_VERSION"/include/xkbcommon/xkbcommon-keysyms.h \
        "$work/libxkbcommon-$MLP1_XKBCOMMON_VERSION"/include/xkbcommon/xkbcommon-names.h \
        "$overlay/include/xkbcommon/"
    # Only configure's library-name probe ever sees this. Wayland is loaded at
    # runtime, so nothing links it, and the NEEDED check below proves that.
    printf '' | "$CROSS_TRIPLE-gcc" -shared -nostdlib -x c - \
        -Wl,-soname,libxkbcommon.so.0 -o "$overlay/lib/libxkbcommon.so.0"
    # Empty Cflags and Libs: the toolchain's pkg-config prefixes every path
    # with the sysroot, so the overlay is passed in CFLAGS and LDFLAGS instead.
    cat > "$overlay/lib/pkgconfig/xkbcommon.pc" <<EOF
Name: xkbcommon
Description: libxkbcommon $MLP1_XKBCOMMON_VERSION headers for SDL's runtime-loaded Wayland
Version: $MLP1_XKBCOMMON_VERSION
Cflags:
Libs:
EOF

    cflags="$UMRK_MLP1_PROFILE_CFLAGS"
    ldflags="$UMRK_MLP1_PROFILE_LDFLAGS"
    MLP1_SDL2_CFLAGS="$cflags"
    MLP1_SDL2_LDFLAGS="$ldflags"
    (
        cd "$src"
        export CFLAGS="$cflags -I$overlay/include"
        export LDFLAGS="$ldflags -L$overlay/lib"
        export PKG_CONFIG_SYSROOT_DIR="$SYSROOT"
        export PKG_CONFIG_LIBDIR="$overlay/lib/pkgconfig:$SYSROOT/usr/lib/pkgconfig:$SYSROOT/usr/share/pkgconfig"
        export PKG_CONFIG_PATH="$PKG_CONFIG_LIBDIR"
        ./configure "--host=$CROSS_TRIPLE" "${MLP1_SDL2_CONFIGURE_FLAGS[@]}" \
            >"$work/configure.log" 2>&1 ||
            { tail -40 "$work/configure.log"; exit 1; }
        sed -n '/^SDL2 Configure Summary:/,$p' "$work/configure.log"
        # The sysroot's wayland-scanner.pc names /usr/bin, which is the
        # container's, not the toolchain's.
        make -j"${JOBS:-$(nproc)}" WAYLAND_SCANNER=/opt/mlp1-toolchain/bin/wayland-scanner \
            >"$work/make.log" 2>&1 ||
            { tail -40 "$work/make.log"; exit 1; }
    ) || { mlp1_sdl2_fail "configure or make failed"; return 1; }

    config="$src/include/SDL_config.h"
    for entry in "${MLP1_SDL2_REQUIRED_CONFIG[@]}"; do
        grep -qxF "#define $entry" "$config" ||
            { mlp1_sdl2_fail "SDL_config.h lacks: #define $entry"; return 1; }
    done
    MLP1_SDL2_DYNAMIC_CONFIG=()
    while read -r entry built; do
        MLP1_SDL2_DYNAMIC_CONFIG+=("$entry" "$built")
    done < <(sed -n 's/^#define \(SDL_[A-Z0-9_]*_DYNAMIC[A-Z0-9_]*\) "\(.*\)"$/\1 \2/p' "$config" | sort)

    built="$(find "$src/build/.libs" -maxdepth 1 -type f -name "$MLP1_SDL2_SONAME.*" | head -1)"
    [[ -n "$built" ]] || { mlp1_sdl2_fail "no $MLP1_SDL2_SONAME.* in build/.libs"; return 1; }
    mkdir -p "$output_dir"
    rm -f "$output_dir/$MLP1_SDL2_SONAME"
    cp "$built" "$output_dir/$MLP1_SDL2_SONAME"
    "$strip" --strip-unneeded "$output_dir/$MLP1_SDL2_SONAME"
    chmod 755 "$output_dir/$MLP1_SDL2_SONAME"

    mlp1_sdl2_verify "$output_dir/$MLP1_SDL2_SONAME" || return 1
    MLP1_SDL2_OUTPUT_SHA256="$(sha256sum "$output_dir/$MLP1_SDL2_SONAME" | cut -d' ' -f1)"
    echo "MLP1 SDL2 built: $output_dir/$MLP1_SDL2_SONAME ($MLP1_SDL2_OUTPUT_SHA256)"
}

# mlp1_sdl2_verify LIBRARY -- the shipped file, whatever built it.
mlp1_sdl2_verify() {
    local library="$1"
    local readelf="$CROSS_TRIPLE-readelf"
    local soname needed

    [[ -f "$library" && ! -L "$library" ]] ||
        { mlp1_sdl2_fail "missing or not a regular file: $library"; return 1; }
    soname="$("$readelf" -d "$library" | sed -n 's/.*(SONAME).*\[\(.*\)\]/\1/p')"
    [[ "$soname" == "$MLP1_SDL2_SONAME" ]] ||
        { mlp1_sdl2_fail "SONAME is '$soname', expected $MLP1_SDL2_SONAME"; return 1; }
    needed="$("$readelf" -d "$library" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p' | sort | tr '\n' ' ')"
    [[ "${needed% }" == "$MLP1_SDL2_EXPECTED_NEEDED" ]] ||
        { mlp1_sdl2_fail "NEEDED is '${needed% }', expected '$MLP1_SDL2_EXPECTED_NEEDED'"; return 1; }
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
    if [[ -f /opt/mlp1-toolchain/umrk/mlp1-build-flags.env ]]; then
        . /opt/mlp1-toolchain/umrk/mlp1-build-flags.env
    fi
    : "${UMRK_MLP1_PROFILE_CFLAGS:?run inside the mlp1-toolchain image}"
    mlp1_sdl2_build "${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/output/mlp1/lib/retroarch}"
fi
