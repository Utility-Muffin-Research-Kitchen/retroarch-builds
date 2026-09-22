"""Focused checks for locked FFmpeg source validation."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "build-mlp1-ffmpeg.py"
SPEC = importlib.util.spec_from_file_location("mlp1_ffmpeg_build", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SourceLockTests(unittest.TestCase):
    def test_clean_pinned_sources_required(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "workdir").mkdir()
            lock = {"version": 1}
            for name, checkout in (("mpp", "mpp-full"),
                                   ("ffmpeg-rockchip", "ffmpeg-rockchip")):
                path = root / "workdir" / checkout
                subprocess.run(["git", "init", "-q", str(path)], check=True)
                subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
                subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.invalid"], check=True)
                (path / "source.c").write_text("int x;\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(path), "add", "source.c"], check=True)
                subprocess.run(["git", "-C", str(path), "commit", "-qm", "source"], check=True)
                url = f"https://github.com/example/{name}.git"
                subprocess.run(["git", "-C", str(path), "remote", "add", "origin", url], check=True)
                commit = subprocess.check_output(
                    ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
                ).strip()
                lock[name] = {"url": url, "commit": commit, "checkout": checkout}
            lock_path = root / "lock.json"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            old_root, old_lock = MODULE.ROOT, MODULE.LOCK
            MODULE.ROOT, MODULE.LOCK = root, lock_path
            try:
                self.assertEqual(set(MODULE.source_checkouts()), {"mpp", "ffmpeg-rockchip"})
                (root / "workdir/mpp-full/source.c").write_text("int y;\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "dirty"):
                    MODULE.source_checkouts()
                (root / "workdir/mpp-full/source.c").write_text("int x;\n", encoding="utf-8")
                lock["mpp"]["commit"] = "0" * 40
                lock_path.write_text(json.dumps(lock), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "wrong revision"):
                    MODULE.source_checkouts()
            finally:
                MODULE.ROOT, MODULE.LOCK = old_root, old_lock


if __name__ == "__main__":
    unittest.main()
