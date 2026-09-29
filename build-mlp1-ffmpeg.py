#!/usr/bin/env python3
"""Fetch locked FFmpeg sources and build or reuse the MLP1 recording runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
LOCK = ROOT / "config/mlp1-ffmpeg-source-lock.json"
BUILD = ROOT / "build-mlp1-ffmpeg.sh"
VERIFY = ROOT / "scripts/verify-mlp1-ffmpeg.sh"
OUTPUT = ROOT / "output/mlp1/ffmpeg"
STAMP = OUTPUT / "input-stamp.json"
IMAGE = os.environ.get(
    "TOOLCHAIN_IMAGE", "ghcr.io/utility-muffin-research-kitchen/mlp1-toolchain:local"
)


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_checkouts() -> dict[str, Path]:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if (not isinstance(lock, dict) or lock.get("version") != 1
            or set(lock) != {"version", "mpp", "ffmpeg-rockchip"}):
        raise ValueError("invalid MLP1 FFmpeg source lock")
    paths = {}
    for name in ("mpp", "ffmpeg-rockchip"):
        entry = lock[name]
        if not isinstance(entry, dict):
            raise ValueError(f"invalid {name} source lock entry")
        url, commit, checkout = (entry.get(key) for key in ("url", "commit", "checkout"))
        if (not isinstance(url, str) or not url.startswith("https://github.com/")
                or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit)
                or not isinstance(checkout, str) or Path(checkout).name != checkout):
            raise ValueError(f"invalid {name} source lock entry")
        path = ROOT / "workdir" / checkout
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            run("git", "clone", "--no-checkout", url, str(path))
            run("git", "-C", str(path), "checkout", "--detach", commit)
        if not (path / ".git").exists():
            raise ValueError(f"{name} is not a Git checkout: {path}")
        if run("git", "-C", str(path), "remote", "get-url", "origin", capture=True) != url:
            raise ValueError(f"{name} source URL differs from lock: {path}")
        if run("git", "-C", str(path), "rev-parse", "HEAD", capture=True) != commit:
            raise ValueError(f"{name} checkout is at the wrong revision: {path}")
        if run("git", "-C", str(path), "status", "--porcelain", capture=True):
            raise ValueError(f"{name} checkout is dirty: {path}")
        paths[name] = path
    return paths


def verify_output(image_id: str) -> None:
    run("docker", "run", "--rm", "--platform", "linux/arm64",
        "-v", f"{ROOT}:/workspace:ro", "-v", f"{OUTPUT}:/ffmpeg:ro",
        image_id, "bash", "/workspace/scripts/verify-mlp1-ffmpeg.sh", "/ffmpeg")


def output_hashes() -> dict[str, str]:
    files = [OUTPUT / "bin/ffmpeg", *(OUTPUT / "flat").glob("*.so.*")]
    return {str(path.relative_to(OUTPUT)): sha(path) for path in sorted(files)}


def stamp_inputs(image_id: str) -> dict:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    return {
        "version": 1,
        "source_lock_sha256": sha(LOCK),
        "sources": {name: {"url": lock[name]["url"], "commit": lock[name]["commit"]}
                    for name in ("mpp", "ffmpeg-rockchip")},
        "toolchain_image": IMAGE,
        "toolchain_image_id": image_id,
        "build_script_sha256": sha(BUILD),
    }


def main() -> None:
    paths = source_checkouts()
    image_id = run("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE, capture=True)
    expected = stamp_inputs(image_id)
    if STAMP.is_file():
        try:
            stamp = json.loads(STAMP.read_text(encoding="utf-8"))
            if (all(stamp.get(key) == value for key, value in expected.items())
                    and stamp.get("configure_inputs_sha256") == sha(OUTPUT / "configure-inputs.txt")
                    and stamp.get("output_sha256") == output_hashes()):
                verify_output(image_id)
                print(f"reusing checksum-validated MLP1 FFmpeg: {STAMP}")
                return
        except (OSError, ValueError, subprocess.CalledProcessError):
            pass

    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    run("docker", "run", "--rm", "--platform", "linux/arm64",
        "-v", f"{ROOT}:/workspace:ro",
        "-v", f"{paths['mpp']}:/work/mpp",
        "-v", f"{paths['ffmpeg-rockchip']}:/work/ffmpeg-rockchip",
        "-v", f"{OUTPUT}:/work/output/mlp1/ffmpeg",
        image_id, "bash", "/workspace/build-mlp1-ffmpeg.sh")
    verify_output(image_id)
    expected["configure_inputs"] = (OUTPUT / "configure-inputs.txt").read_text(encoding="utf-8").splitlines()
    expected["configure_inputs_sha256"] = sha(OUTPUT / "configure-inputs.txt")
    expected["output_sha256"] = output_hashes()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=OUTPUT,
                                     prefix=".input-stamp.", delete=False) as stream:
        temp = Path(stream.name)
        json.dump(expected, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temp, STAMP)
    print(f"built and stamped MLP1 FFmpeg: {STAMP}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"MLP1 FFmpeg: {error}", file=sys.stderr)
        raise SystemExit(1) from error
