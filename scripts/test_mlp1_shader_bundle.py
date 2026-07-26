#!/usr/bin/env python3
"""Unit tests for the deterministic MLP1 shader bundle builder."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path, PurePosixPath


MODULE_PATH = Path(__file__).with_name("mlp1_shader_bundle.py")
SPEC = importlib.util.spec_from_file_location("mlp1_shader_bundle", MODULE_PATH)
assert SPEC and SPEC.loader
shader_bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shader_bundle)


class ShaderBundleTests(unittest.TestCase):
    @staticmethod
    def selection(path: str, evidence: str) -> dict:
        return {
            "path_limit_bytes": 240,
            "allowed_extensions": [".glsl", ".glslp"],
            "presets": [
                {
                    "path": path,
                    "group": "test",
                    "license": "LicenseRef-Public-Domain",
                    "license_provenance": "embedded",
                    "license_evidence_path": evidence,
                    "license_evidence_text": "public domain",
                    "qualification": "static-only",
                }
            ],
        }

    def test_parses_shader_and_texture_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shader = Path(temporary) / "preset.glslp"
            shader.write_text(
                'shaders = "1"\n'
                'shader0 = "pass.glsl"\n'
                'textures = "LUT"\n'
                'LUT = "images/lut.png"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                shader_bundle.parse_dependencies(
                    shader, PurePosixPath("group/preset.glslp")
                ),
                [
                    PurePosixPath("group/images/lut.png"),
                    PurePosixPath("group/pass.glsl"),
                ],
            )

    def test_resolves_parent_path_within_source(self) -> None:
        self.assertEqual(
            shader_bundle.resolve_dependency(
                PurePosixPath("group/preset.glslp"), "../shared/pass.glsl"
            ),
            PurePosixPath("shared/pass.glsl"),
        )

    def test_rejects_dependency_escape(self) -> None:
        with self.assertRaises(shader_bundle.BundleError):
            shader_bundle.resolve_dependency(
                PurePosixPath("preset.glslp"), "../outside.glsl"
            )

    def test_rejects_fat32_reserved_filename(self) -> None:
        with self.assertRaises(shader_bundle.BundleError):
            shader_bundle.validate_bundle_path(
                PurePosixPath("shaders_glsl/CON.glsl"),
                240,
                {".glsl"},
            )

    def test_rejects_missing_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "preset.glslp").write_text(
                'shaders = "1"\nshader0 = "missing.glsl"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                shader_bundle.BundleError, "missing shader dependency"
            ):
                shader_bundle.collect_files(
                    source, self.selection("preset.glslp", "missing.glsl")
                )

    def test_rejects_missing_license_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "preset.glslp").write_text(
                'shaders = "1"\nshader0 = "pass.glsl"\n',
                encoding="utf-8",
            )
            (source / "pass.glsl").write_text("// no notice\n", encoding="utf-8")
            with self.assertRaisesRegex(
                shader_bundle.BundleError, "expected license evidence is absent"
            ):
                shader_bundle.collect_files(
                    source, self.selection("preset.glslp", "pass.glsl")
                )

    def test_rejects_case_insensitive_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "group").mkdir()
            (source / "group" / "Preset.glslp").write_text(
                'shaders = "1"\nshader0 = "pass.glsl"\n',
                encoding="utf-8",
            )
            (source / "group" / "preset.glslp").write_text(
                'shaders = "1"\nshader0 = "pass.glsl"\n',
                encoding="utf-8",
            )
            (source / "group" / "pass.glsl").write_text(
                "// License: Public domain\n", encoding="utf-8"
            )
            selection = {
                "path_limit_bytes": 240,
                "allowed_extensions": [".glsl", ".glslp"],
                "presets": [
                    {
                        "path": "group/Preset.glslp",
                        "group": "one",
                        "license": "LicenseRef-Public-Domain",
                        "license_provenance": "embedded",
                        "license_evidence_path": "group/pass.glsl",
                        "license_evidence_text": "public domain",
                        "qualification": "static-only",
                    },
                    {
                        "path": "group/preset.glslp",
                        "group": "two",
                        "license": "LicenseRef-Public-Domain",
                        "license_provenance": "embedded",
                        "license_evidence_path": "group/pass.glsl",
                        "license_evidence_text": "public domain",
                        "qualification": "static-only",
                    },
                ],
            }
            with self.assertRaises(shader_bundle.BundleError):
                shader_bundle.collect_files(source, selection)

    def test_aggregate_qualification_uses_weakest_preset(self) -> None:
        self.assertEqual(
            shader_bundle.aggregate_qualification(
                [
                    {"qualification": "recommended"},
                    {"qualification": "loads"},
                ]
            ),
            "loads",
        )

    def test_aggregate_qualification_rejects_empty_selection(self) -> None:
        with self.assertRaisesRegex(shader_bundle.BundleError, "empty preset list"):
            shader_bundle.aggregate_qualification([])

    def test_writes_thin_recommendation_with_parameter_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            rows = shader_bundle.write_recommendations(
                output,
                {
                    "presets": [
                        {
                            "path": "leaf-recommended/subtle.glslp",
                            "display_name": "Subtle",
                            "description": "A tuned test preset.",
                            "reference": "shaders_glsl/scanlines/base.glslp",
                            "parameters": {"DARKNESS": "0.25"},
                            "intended_systems": ["FC"],
                            "constraints": ["Test note."],
                        }
                    ]
                },
                123,
            )
            self.assertEqual(rows[0]["qualification"], "recommended")
            self.assertEqual(
                (output / "leaf-recommended" / "subtle.glslp").read_text(
                    encoding="utf-8"
                ),
                '#reference "../shaders_glsl/scanlines/base.glslp"\n'
                'DARKNESS = "0.25"\n',
            )


if __name__ == "__main__":
    unittest.main()
