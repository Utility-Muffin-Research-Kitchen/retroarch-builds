#!/usr/bin/env python3
"""Build and validate the deterministic MLP1 RetroArch GLSL shader bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = REPO_ROOT / "shader-sources" / "mlp1-glsl.lock.json"
DEFAULT_SOURCE = REPO_ROOT / "workdir" / "src" / "glsl-shaders"
DEFAULT_OUTPUT = REPO_ROOT / "output" / "mlp1" / "shaders"
DEPENDENCY_KEYS = {"lut", "overlay", "texture"}
DOS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
REFERENCE_RE = re.compile(r'^\s*#reference\s+["\']([^"\']+)["\']', re.MULTILINE)
INCLUDE_RE = re.compile(r'^\s*#include\s+["\']([^"\']+)["\']', re.MULTILINE)
ASSIGNMENT_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^#;\r\n]+))",
    re.MULTILINE,
)
QUALIFICATION_ORDER = {
    "static-only": 0,
    "loads": 1,
    "recommended": 2,
}


class BundleError(RuntimeError):
    """An actionable bundle build or validation failure."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BundleError(f"missing JSON input: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BundleError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BundleError(f"expected a JSON object in {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(path: Path) -> str:
    return sha256_file(path)


def run_git(source: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BundleError(f"git {' '.join(args)} failed in {source}: {detail}")
    return result.stdout.strip()


def validate_lock(lock: dict[str, Any], lock_path: Path) -> Path:
    required = {"schema_version", "source", "commit", "tree", "commit_epoch", "selection"}
    missing = sorted(required - set(lock))
    if missing:
        raise BundleError(f"{lock_path} is missing keys: {', '.join(missing)}")
    if lock["schema_version"] != 1:
        raise BundleError(f"unsupported lock schema: {lock['schema_version']}")
    if not re.fullmatch(r"[0-9a-f]{40}", str(lock["commit"])):
        raise BundleError("lock commit must be a full lowercase SHA-1")
    if not re.fullmatch(r"[0-9a-f]{40}", str(lock["tree"])):
        raise BundleError("lock tree must be a full lowercase SHA-1")
    if not isinstance(lock["commit_epoch"], int) or lock["commit_epoch"] < 0:
        raise BundleError("lock commit_epoch must be a non-negative integer")
    selection_path = REPO_ROOT / str(lock["selection"])
    try:
        selection_path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise BundleError("selection path must remain inside retroarch-builds") from exc
    return selection_path


def validate_selection(selection: dict[str, Any], selection_path: Path) -> None:
    required = {
        "schema_version",
        "bundle_id",
        "platform",
        "policy_version",
        "installed_size_limit_bytes",
        "path_limit_bytes",
        "allowed_extensions",
        "presets",
    }
    missing = sorted(required - set(selection))
    if missing:
        raise BundleError(f"{selection_path} is missing keys: {', '.join(missing)}")
    if selection["schema_version"] != 1:
        raise BundleError(f"unsupported selection schema: {selection['schema_version']}")
    if selection["platform"] != "mlp1":
        raise BundleError("selection platform must be mlp1")
    if not isinstance(selection["presets"], list) or not selection["presets"]:
        raise BundleError("selection must contain at least one preset")
    seen: set[str] = set()
    for index, preset in enumerate(selection["presets"]):
        if not isinstance(preset, dict):
            raise BundleError(f"preset {index} must be an object")
        missing_preset = {
            "path",
            "group",
            "license",
            "license_provenance",
            "license_evidence_path",
            "license_evidence_text",
            "qualification",
        } - set(preset)
        if missing_preset:
            raise BundleError(
                f"preset {index} is missing keys: {', '.join(sorted(missing_preset))}"
            )
        path = normalize_source_path(str(preset["path"]))
        if path.suffix.lower() != ".glslp":
            raise BundleError(f"selected preset is not .glslp: {path}")
        license_id = str(preset["license"]).strip()
        if not license_id or license_id.casefold() in {
            "unknown",
            "noassertion",
            "license-ref-unknown",
        }:
            raise BundleError(f"selected preset has an unknown license: {path}")
        for key in (
            "license_provenance",
            "license_evidence_path",
            "license_evidence_text",
            "qualification",
        ):
            if not str(preset[key]).strip():
                raise BundleError(f"selected preset has an empty {key}: {path}")
        if str(preset["qualification"]) not in QUALIFICATION_ORDER:
            raise BundleError(
                f"selected preset has an invalid qualification "
                f"{preset['qualification']!r}: {path}"
            )
        folded = path.as_posix().casefold()
        if folded in seen:
            raise BundleError(f"duplicate selected preset path: {path}")
        seen.add(folded)

    qualification = aggregate_qualification(selection["presets"])
    report_value = selection.get("qualification_report")
    if qualification != "static-only" and not report_value:
        raise BundleError(
            "device-qualified presets require a qualification_report"
        )
    if report_value:
        report_path, report = load_qualification_report(selection)
        report_presets = {
            str(row.get("path", "")): str(row.get("qualification", ""))
            for row in report.get("presets", [])
            if isinstance(row, dict)
        }
        expected_presets = {
            f"shaders_glsl/{normalize_source_path(str(row['path'])).as_posix()}": str(
                row["qualification"]
            )
            for row in selection["presets"]
        }
        if report_presets != expected_presets:
            raise BundleError(
                f"qualification report preset state does not match selection: "
                f"{report_path}"
            )


def load_qualification_report(
    selection: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    value = selection.get("qualification_report")
    if not value:
        raise BundleError("selection does not declare a qualification report")
    report_path = REPO_ROOT / str(value)
    try:
        report_path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise BundleError(
            "qualification report path must remain inside retroarch-builds"
        ) from exc
    report = load_json(report_path)
    if report.get("schema_version") != 1 or report.get("platform") != "mlp1":
        raise BundleError(f"invalid MLP1 qualification report: {report_path}")
    build = report.get("retroarch_build")
    if not isinstance(build, dict) or not {
        "version",
        "commit",
        "build_profile",
    } <= set(build):
        raise BundleError(
            f"qualification report lacks exact RetroArch build metadata: {report_path}"
        )
    return report_path, report


def load_recommendations(
    selection: dict[str, Any],
    lock: dict[str, Any],
) -> tuple[Path | None, dict[str, Any] | None]:
    value = selection.get("recommendations")
    if not value:
        return None, None
    path = REPO_ROOT / str(value)
    try:
        path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise BundleError(
            "recommendations path must remain inside retroarch-builds"
        ) from exc
    data = load_json(path)
    if data.get("schema_version") != 1 or data.get("platform") != "mlp1":
        raise BundleError(f"invalid MLP1 recommendation metadata: {path}")
    source = data.get("shader_source", {})
    if (
        source.get("url") != lock["source"]
        or source.get("commit") != lock["commit"]
        or source.get("tree") != lock["tree"]
    ):
        raise BundleError("recommendation shader source does not match the source lock")
    rows = data.get("presets")
    if not isinstance(rows, list) or not rows:
        raise BundleError("recommendation metadata must contain presets")

    selected = {
        f"shaders_glsl/{normalize_source_path(str(row['path'])).as_posix()}": row
        for row in selection["presets"]
    }
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BundleError(f"recommendation {index} must be an object")
        required = {
            "path",
            "display_name",
            "description",
            "reference",
            "parameters",
            "intended_systems",
            "qualification",
            "performance",
            "visual_review",
            "constraints",
        }
        missing = required - set(row)
        if missing:
            raise BundleError(
                f"recommendation {index} is missing keys: "
                f"{', '.join(sorted(missing))}"
            )
        alias = normalize_source_path(str(row["path"]))
        if (
            alias.suffix.lower() != ".glslp"
            or not alias.parts
            or alias.parts[0] != "leaf-recommended"
        ):
            raise BundleError(
                f"recommended preset must be under leaf-recommended: {alias}"
            )
        folded = alias.as_posix().casefold()
        if folded in seen:
            raise BundleError(f"duplicate recommended preset path: {alias}")
        seen.add(folded)

        reference = normalize_source_path(str(row["reference"])).as_posix()
        selected_row = selected.get(reference)
        if not selected_row:
            raise BundleError(
                f"recommendation references an unselected preset: {reference}"
            )
        if str(selected_row["qualification"]) != "recommended":
            raise BundleError(
                f"recommendation reference is not qualified recommended: {reference}"
            )
        if row["qualification"] != "recommended":
            raise BundleError(f"recommendation is not qualified: {alias}")
        systems = row["intended_systems"]
        if not isinstance(systems, list) or not systems:
            raise BundleError(f"recommendation has no intended systems: {alias}")
        parameters = row["parameters"]
        if not isinstance(parameters, dict):
            raise BundleError(f"recommendation parameters must be an object: {alias}")
        for key, value in parameters.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                raise BundleError(f"invalid recommendation parameter {key!r}: {alias}")
            if not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", str(value)):
                raise BundleError(
                    f"invalid recommendation parameter value {value!r}: {alias}"
                )
        performance = row["performance"]
        if (
            not isinstance(performance, dict)
            or float(performance.get("minimum_average_fps", 0.0)) < 59.0
        ):
            raise BundleError(
                f"recommendation does not meet the 59 FPS performance gate: {alias}"
            )
        visual = row["visual_review"]
        if not isinstance(visual, dict) or visual.get("status") != "pass":
            raise BundleError(f"recommendation lacks passed visual review: {alias}")
    return path, data


def prepare_source(source: Path, lock: dict[str, Any], fetch: bool) -> None:
    if not source.exists():
        if not fetch:
            raise BundleError(f"shader source checkout does not exist: {source}")
        source.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--no-checkout", str(lock["source"]), str(source)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise BundleError(f"failed to clone shader source: {detail}")
    if not (source / ".git").exists():
        raise BundleError(f"shader source is not a Git checkout: {source}")
    if run_git(source, "status", "--porcelain"):
        raise BundleError(f"shader source checkout has local changes: {source}")

    commit = str(lock["commit"])
    probe = subprocess.run(
        ["git", "-C", str(source), "cat-file", "-e", f"{commit}^{{commit}}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if probe.returncode:
        if not fetch:
            raise BundleError(f"locked shader commit is absent locally: {commit}")
        run_git(source, "fetch", "--depth=1", "origin", commit)
    run_git(source, "checkout", "--detach", "--quiet", commit)
    if run_git(source, "rev-parse", "HEAD") != commit:
        raise BundleError("shader checkout did not resolve to the locked commit")
    if run_git(source, "rev-parse", "HEAD^{tree}") != str(lock["tree"]):
        raise BundleError("shader checkout tree does not match the lock")
    if int(run_git(source, "show", "-s", "--format=%ct", "HEAD")) != lock["commit_epoch"]:
        raise BundleError("shader checkout commit timestamp does not match the lock")


def normalize_source_path(value: str) -> PurePosixPath:
    value = value.strip().replace("\\", "/")
    path = PurePosixPath(value)
    if not value or path.is_absolute():
        raise BundleError(f"dependency path must be relative: {value!r}")
    parts: list[str] = []
    for part in path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise BundleError(f"dependency escapes the shader source: {value}")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise BundleError(f"dependency does not name a file: {value!r}")
    return PurePosixPath(*parts)


def resolve_dependency(base: PurePosixPath, value: str) -> PurePosixPath:
    return normalize_source_path((base.parent / value).as_posix())


def parse_dependencies(path: Path, relative_path: PurePosixPath) -> list[PurePosixPath]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"shader file is not UTF-8: {relative_path}") from exc

    dependencies = []
    for pattern in (REFERENCE_RE, INCLUDE_RE):
        dependencies.extend(
            resolve_dependency(relative_path, match.group(1))
            for match in pattern.finditer(text)
        )
    assignments: dict[str, str] = {}
    for match in ASSIGNMENT_RE.finditer(text):
        value = next(group for group in match.groups()[1:] if group is not None).strip()
        assignments[match.group(1)] = value

    for key, value in assignments.items():
        lowered = key.lower()
        if re.fullmatch(r"shader\d+", lowered):
            dependencies.append(resolve_dependency(relative_path, value))
        elif lowered in DEPENDENCY_KEYS:
            dependencies.append(resolve_dependency(relative_path, value))

    texture_names = assignments.get("textures", "")
    for texture_name in texture_names.split(";"):
        texture_name = texture_name.strip()
        if not texture_name:
            continue
        if texture_name not in assignments:
            raise BundleError(
                f"{relative_path}: textures lists {texture_name!r} without a value"
            )
        dependencies.append(resolve_dependency(relative_path, assignments[texture_name]))

    unique: dict[str, PurePosixPath] = {}
    for dependency in dependencies:
        unique[dependency.as_posix()] = dependency
    return [unique[key] for key in sorted(unique)]


def validate_fat_component(component: str, relative_path: PurePosixPath) -> None:
    if not component or component.endswith((" ", ".")):
        raise BundleError(f"FAT32-unsafe path component: {relative_path}")
    if any(ord(char) < 32 or char in '<>:"\\|?*' for char in component):
        raise BundleError(f"FAT32-unsafe path component: {relative_path}")
    stem = component.split(".", 1)[0].upper()
    if stem in DOS_RESERVED:
        raise BundleError(f"reserved DOS filename in path: {relative_path}")


def validate_bundle_path(
    relative_path: PurePosixPath,
    path_limit_bytes: int,
    allowed_extensions: set[str],
) -> None:
    encoded_length = len(relative_path.as_posix().encode("utf-8"))
    if encoded_length > path_limit_bytes:
        raise BundleError(
            f"path exceeds {path_limit_bytes} UTF-8 bytes ({encoded_length}): {relative_path}"
        )
    for component in relative_path.parts:
        validate_fat_component(component, relative_path)
    suffix = relative_path.suffix.lower()
    if suffix not in allowed_extensions:
        raise BundleError(f"unsupported shader bundle extension {suffix!r}: {relative_path}")


def collect_files(
    source: Path,
    selection: dict[str, Any],
) -> tuple[dict[PurePosixPath, dict[str, str]], list[dict[str, Any]]]:
    path_limit = int(selection["path_limit_bytes"])
    allowed_extensions = {
        str(extension).lower() for extension in selection["allowed_extensions"]
    }
    files: dict[PurePosixPath, dict[str, str]] = {}
    preset_rows: list[dict[str, Any]] = []

    for preset in selection["presets"]:
        preset_path = normalize_source_path(str(preset["path"]))
        preset_rows.append(
            {
                "path": f"shaders_glsl/{preset_path.as_posix()}",
                "group": str(preset["group"]),
                "qualification": str(preset["qualification"]),
                "license": str(preset["license"]),
            }
        )
        pending = [preset_path]
        visited: set[PurePosixPath] = set()
        while pending:
            relative_path = pending.pop()
            if relative_path in visited:
                continue
            visited.add(relative_path)
            source_path = source / relative_path.as_posix()
            try:
                source_path.resolve().relative_to(source.resolve())
            except ValueError as exc:
                raise BundleError(f"source path escapes checkout: {relative_path}") from exc
            if not source_path.is_file():
                raise BundleError(f"missing shader dependency: {relative_path}")
            if source_path.is_symlink():
                raise BundleError(f"symlinks are not allowed in the bundle: {relative_path}")
            if source_path.stat().st_size == 0:
                raise BundleError(f"empty shader dependency: {relative_path}")
            output_relative = PurePosixPath("shaders_glsl") / relative_path
            validate_bundle_path(output_relative, path_limit, allowed_extensions)

            metadata = {
                "license": str(preset["license"]),
                "license_provenance": str(preset["license_provenance"]),
                "license_evidence_path": str(preset["license_evidence_path"]),
            }
            existing = files.get(relative_path)
            if existing and existing != metadata:
                raise BundleError(
                    f"conflicting license metadata for shared dependency: {relative_path}"
                )
            files[relative_path] = metadata
            pending.extend(parse_dependencies(source_path, relative_path))

        evidence_path = normalize_source_path(str(preset["license_evidence_path"]))
        if evidence_path not in visited:
            raise BundleError(
                f"{preset_path}: license evidence is outside its dependency closure: "
                f"{evidence_path}"
            )
        evidence_text = (source / evidence_path.as_posix()).read_text(encoding="utf-8")
        expected_notice = str(preset["license_evidence_text"])
        if expected_notice.casefold() not in evidence_text.casefold():
            raise BundleError(
                f"{preset_path}: expected license evidence is absent from {evidence_path}"
            )

    casefold_paths: dict[str, PurePosixPath] = {}
    for relative_path in files:
        output_relative = PurePosixPath("shaders_glsl") / relative_path
        folded = output_relative.as_posix().casefold()
        existing = casefold_paths.get(folded)
        if existing and existing != output_relative:
            raise BundleError(
                f"case-insensitive path collision: {existing} and {output_relative}"
            )
        casefold_paths[folded] = output_relative
    return files, preset_rows


def write_notice(destination: Path, lock: dict[str, Any], files: dict[PurePosixPath, Any]) -> None:
    lines = [
        "# MLP1 RetroArch GLSL shader bundle notices",
        "",
        "Source: libretro/glsl-shaders",
        f"Source URL: {lock['source']}",
        f"Source commit: {lock['commit']}",
        "",
        "The files selected for this initial bundle identify themselves in their",
        "source headers as public domain. The original per-file notices are retained",
        "inside each shader source file.",
        "",
        "Bundled source paths:",
        "",
    ]
    lines.extend(f"- `{path.as_posix()}`" for path in sorted(files, key=lambda item: item.as_posix()))
    lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_recommendations(
    output: Path,
    recommendations: dict[str, Any],
    generated_epoch: int,
) -> list[dict[str, Any]]:
    rows = []
    readme_lines = [
        "Leaf recommended shaders for MLP1",
        "",
        "These presets are optional and are never enabled automatically.",
        "Each one references a tested preset in ../shaders_glsl/.",
        "",
    ]
    for row in recommendations["presets"]:
        alias = normalize_source_path(str(row["path"]))
        reference = normalize_source_path(str(row["reference"]))
        relative_reference = posixpath.relpath(
            reference.as_posix(),
            start=alias.parent.as_posix(),
        )
        lines = [f'#reference "{relative_reference}"']
        for key, value in sorted(row["parameters"].items()):
            lines.append(f'{key} = "{value}"')
        lines.append("")

        destination = output / alias.as_posix()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        destination.chmod(0o644)
        os.utime(destination, (generated_epoch, generated_epoch))
        rows.append(
            {
                "path": alias.as_posix(),
                "group": "leaf-recommended",
                "qualification": "recommended",
                "license": "LicenseRef-Leaf-Bundle-Metadata",
                "display_name": str(row["display_name"]),
                "reference": reference.as_posix(),
                "intended_systems": list(row["intended_systems"]),
            }
        )
        systems = ", ".join(str(value) for value in row["intended_systems"])
        readme_lines.extend(
            [
                str(row["display_name"]),
                f"  {row['description']}",
                f"  Intended systems: {systems}",
            ]
        )
        for constraint in row["constraints"]:
            readme_lines.append(f"  Note: {constraint}")
        readme_lines.append("")

    readme = output / "leaf-recommended" / "README.txt"
    readme.write_text("\n".join(readme_lines), encoding="utf-8", newline="\n")
    readme.chmod(0o644)
    os.utime(readme, (generated_epoch, generated_epoch))
    return rows


def aggregate_qualification(presets: Iterable[dict[str, Any]]) -> str:
    qualifications = [str(preset["qualification"]) for preset in presets]
    if not qualifications:
        raise BundleError("cannot aggregate qualification for an empty preset list")
    return min(qualifications, key=QUALIFICATION_ORDER.__getitem__)


def write_readme(
    destination: Path,
    qualification: str,
    has_recommendations: bool,
) -> None:
    if qualification == "static-only":
        qualification_note = (
            "The presets are marked static-only until each one is qualified on "
            "MLP1 hardware."
        )
    elif qualification == "loads":
        qualification_note = (
            "Every preset passed the MLP1 load, compile, render, menu, state, "
            "clear/reapply, relaunch, and clean-quit checks."
        )
    else:
        qualification_note = (
            "Every preset passed the MLP1 load checks and recommended-performance "
            "qualification."
        )
    browser_note = (
        "Start with leaf-recommended/, or browse the full shaders_glsl/ tree."
        if has_recommendations
        else "Browse these presets from RetroArch's shader menu under shaders_glsl/."
    )
    destination.write_text(
        "\n".join(
            [
                "Leaf RetroArch GLSL shaders for MLP1",
                "",
                browser_note,
                "No shader is enabled automatically.",
                "",
                "This initial set passed deterministic source, dependency, license,",
                "manifest, and filesystem validation.",
                qualification_note,
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )


def build_manifest(
    output: Path,
    lock: dict[str, Any],
    lock_path: Path,
    selection: dict[str, Any],
    selection_path: Path,
    preset_rows: list[dict[str, Any]],
    recommended_rows: list[dict[str, Any]],
    recommendations_path: Path | None,
    recommendations: dict[str, Any] | None,
    file_metadata: dict[str, dict[str, str]],
) -> dict[str, Any]:
    file_rows = []
    extension_counts: Counter[str] = Counter()
    installed_size = 0
    for path in sorted(
        (candidate for candidate in output.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(output).as_posix(),
    ):
        relative = path.relative_to(output).as_posix()
        source_metadata = file_metadata.get(relative)
        size = path.stat().st_size
        installed_size += size
        extension_counts[path.suffix.lower() or "(none)"] += 1
        row: dict[str, Any] = {
            "path": relative,
            "sha256": sha256_file(path),
            "size": size,
        }
        if source_metadata:
            row.update(source_metadata)
            row["source_path"] = relative.removeprefix("shaders_glsl/")
        else:
            row["license"] = "LicenseRef-Leaf-Bundle-Metadata"
        file_rows.append(row)

    extension_counts[".json"] += 1
    all_preset_rows = preset_rows + recommended_rows
    manifest = {
        "schema_version": 1,
        "bundle_id": selection["bundle_id"],
        "platform": selection["platform"],
        "source": {
            "url": lock["source"],
            "commit": lock["commit"],
            "tree": lock["tree"],
            "commit_epoch": lock["commit_epoch"],
        },
        "lock": {
            "path": lock_path.relative_to(REPO_ROOT).as_posix(),
            "sha256": sha256_json(lock_path),
        },
        "selection": {
            "path": selection_path.relative_to(REPO_ROOT).as_posix(),
            "sha256": sha256_json(selection_path),
            "policy_version": selection["policy_version"],
        },
        "generated_epoch": lock["commit_epoch"],
        "qualification": aggregate_qualification(all_preset_rows),
        "preset_count": len(all_preset_rows),
        "standard_preset_count": len(preset_rows),
        "recommended_preset_count": len(recommended_rows),
        "installed_size_bytes": 0,
        "extension_counts": dict(sorted(extension_counts.items())),
        "presets": sorted(all_preset_rows, key=lambda row: row["path"]),
        "files": file_rows,
    }
    if recommendations_path and recommendations:
        manifest["recommendations"] = {
            "path": recommendations_path.relative_to(REPO_ROOT).as_posix(),
            "sha256": sha256_json(recommendations_path),
            "qualification_id": recommendations["qualification_id"],
        }
    if selection.get("qualification_report"):
        report_path, report = load_qualification_report(selection)
        report_source = report.get("shader_source", {})
        if (
            report_source.get("url") != lock["source"]
            or report_source.get("commit") != lock["commit"]
            or report_source.get("tree") != lock["tree"]
        ):
            raise BundleError(
                "qualification report shader source does not match the source lock"
            )
        manifest["qualification_report"] = {
            "path": report_path.relative_to(REPO_ROOT).as_posix(),
            "sha256": sha256_json(report_path),
        }
        manifest["retroarch_build"] = report["retroarch_build"]
        manifest["qualification_device"] = report["device"]
    return manifest


def serialize_manifest(manifest: dict[str, Any], content_size: int) -> bytes:
    previous_size = -1
    while manifest["installed_size_bytes"] != previous_size:
        previous_size = manifest["installed_size_bytes"]
        payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        manifest["installed_size_bytes"] = content_size + len(payload)
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def atomic_promote(staging: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = output.parent / f".{output.name}.previous"
    if backup.exists():
        shutil.rmtree(backup)
    if output.exists():
        output.rename(backup)
    try:
        staging.rename(output)
    except Exception:
        if backup.exists() and not output.exists():
            backup.rename(output)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def build_bundle(
    lock_path: Path,
    source: Path,
    output: Path,
    fetch: bool,
) -> dict[str, Any]:
    lock = load_json(lock_path)
    selection_path = validate_lock(lock, lock_path)
    selection = load_json(selection_path)
    validate_selection(selection, selection_path)
    recommendations_path, recommendations = load_recommendations(selection, lock)
    prepare_source(source, lock, fetch)
    files, preset_rows = collect_files(source, selection)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.build-", dir=output.parent
    ) as temporary:
        staging = Path(temporary) / output.name
        staging.mkdir()
        metadata_by_output_path: dict[str, dict[str, str]] = {}
        for relative_path in sorted(files, key=lambda item: item.as_posix()):
            source_path = source / relative_path.as_posix()
            destination = staging / "shaders_glsl" / relative_path.as_posix()
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
            destination.chmod(0o644)
            os.utime(destination, (lock["commit_epoch"], lock["commit_epoch"]))
            metadata_by_output_path[
                (PurePosixPath("shaders_glsl") / relative_path).as_posix()
            ] = files[relative_path]

        recommended_rows = (
            write_recommendations(staging, recommendations, lock["commit_epoch"])
            if recommendations
            else []
        )
        write_notice(staging / "NOTICE.md", lock, files)
        write_readme(
            staging / "README.txt",
            aggregate_qualification(selection["presets"] + recommended_rows),
            bool(recommended_rows),
        )
        for metadata_file in (staging / "NOTICE.md", staging / "README.txt"):
            metadata_file.chmod(0o644)
            os.utime(metadata_file, (lock["commit_epoch"], lock["commit_epoch"]))

        manifest = build_manifest(
            staging,
            lock,
            lock_path,
            selection,
            selection_path,
            preset_rows,
            recommended_rows,
            recommendations_path,
            recommendations,
            metadata_by_output_path,
        )
        manifest_path = staging / "manifest.json"
        content_size = sum(row["size"] for row in manifest["files"])
        manifest_payload = serialize_manifest(manifest, content_size)
        if len(manifest_payload) + content_size > int(
            selection["installed_size_limit_bytes"]
        ):
            raise BundleError(
                f"bundle is {len(manifest_payload) + content_size} bytes, above the "
                f"{selection['installed_size_limit_bytes']}-byte limit"
            )
        manifest_path.write_bytes(manifest_payload)
        manifest_path.chmod(0o644)
        os.utime(manifest_path, (lock["commit_epoch"], lock["commit_epoch"]))
        validate_bundle(lock_path, staging)
        atomic_promote(staging, output)
    return manifest


def validate_output_dependencies(output: Path, manifest: dict[str, Any]) -> None:
    listed = {str(row["path"]) for row in manifest["files"]}
    for preset in manifest["presets"]:
        preset_path = PurePosixPath(str(preset["path"]))
        pending = [preset_path]
        visited: set[PurePosixPath] = set()
        while pending:
            relative_path = pending.pop()
            if relative_path in visited:
                continue
            visited.add(relative_path)
            if relative_path.as_posix() not in listed:
                raise BundleError(f"manifest omits shader dependency: {relative_path}")
            path = output / relative_path.as_posix()
            if not path.is_file():
                raise BundleError(f"bundle omits shader dependency: {relative_path}")
            pending.extend(parse_dependencies(path, relative_path))


def validate_bundle(lock_path: Path, output: Path) -> dict[str, Any]:
    lock = load_json(lock_path)
    selection_path = validate_lock(lock, lock_path)
    selection = load_json(selection_path)
    validate_selection(selection, selection_path)
    recommendations_path, recommendations = load_recommendations(selection, lock)
    recommended_rows = recommendations["presets"] if recommendations else []
    manifest_path = output / "manifest.json"
    validate_bundle_path(
        PurePosixPath("manifest.json"),
        int(selection["path_limit_bytes"]),
        {".json"},
    )
    manifest = load_json(manifest_path)

    expected_scalar = {
        "schema_version": 1,
        "bundle_id": selection["bundle_id"],
        "platform": selection["platform"],
        "generated_epoch": lock["commit_epoch"],
        "preset_count": len(selection["presets"]) + len(recommended_rows),
        "standard_preset_count": len(selection["presets"]),
        "recommended_preset_count": len(recommended_rows),
        "qualification": aggregate_qualification(
            selection["presets"] + recommended_rows
        ),
    }
    for key, expected in expected_scalar.items():
        if manifest.get(key) != expected:
            raise BundleError(
                f"manifest {key} is {manifest.get(key)!r}, expected {expected!r}"
            )
    for key, expected in {
        "url": lock["source"],
        "commit": lock["commit"],
        "tree": lock["tree"],
        "commit_epoch": lock["commit_epoch"],
    }.items():
        if manifest.get("source", {}).get(key) != expected:
            raise BundleError(f"manifest source.{key} does not match the lock")
    if manifest.get("lock", {}).get("sha256") != sha256_json(lock_path):
        raise BundleError("manifest lock hash is stale")
    if manifest.get("selection", {}).get("sha256") != sha256_json(selection_path):
        raise BundleError("manifest selection hash is stale")
    expected_recommendations = (
        {
            "path": recommendations_path.relative_to(REPO_ROOT).as_posix(),
            "sha256": sha256_json(recommendations_path),
            "qualification_id": recommendations["qualification_id"],
        }
        if recommendations_path and recommendations
        else None
    )
    if manifest.get("recommendations") != expected_recommendations:
        raise BundleError("manifest recommendation metadata is stale")
    if selection.get("qualification_report"):
        report_path, report = load_qualification_report(selection)
        report_source = report.get("shader_source", {})
        if (
            report_source.get("url") != lock["source"]
            or report_source.get("commit") != lock["commit"]
            or report_source.get("tree") != lock["tree"]
        ):
            raise BundleError(
                "qualification report shader source does not match the source lock"
            )
        if manifest.get("qualification_report") != {
            "path": report_path.relative_to(REPO_ROOT).as_posix(),
            "sha256": sha256_json(report_path),
        }:
            raise BundleError("manifest qualification report metadata is stale")
        if manifest.get("retroarch_build") != report["retroarch_build"]:
            raise BundleError("manifest RetroArch qualification build is stale")
        if manifest.get("qualification_device") != report["device"]:
            raise BundleError("manifest qualification device metadata is stale")

    file_rows = manifest.get("files")
    if not isinstance(file_rows, list) or not file_rows:
        raise BundleError("manifest files must be a non-empty array")
    listed_paths: set[str] = set()
    installed_size = 0
    extension_counts: Counter[str] = Counter()
    casefold_paths: dict[str, str] = {}
    allowed_extensions = {
        str(extension).lower() for extension in selection["allowed_extensions"]
    } | {".json"}
    for row in file_rows:
        if not isinstance(row, dict) or not {"path", "sha256", "size"} <= set(row):
            raise BundleError("manifest contains an invalid file row")
        relative_path = normalize_source_path(str(row["path"]))
        validate_bundle_path(
            relative_path,
            int(selection["path_limit_bytes"]),
            allowed_extensions,
        )
        relative = relative_path.as_posix()
        folded = relative.casefold()
        if folded in casefold_paths:
            raise BundleError(
                f"case-insensitive manifest collision: {casefold_paths[folded]} and {relative}"
            )
        casefold_paths[folded] = relative
        listed_paths.add(relative)
        path = output / relative
        if not path.is_file() or path.is_symlink():
            raise BundleError(f"manifest file is missing or invalid: {relative}")
        if path.stat().st_size == 0:
            raise BundleError(f"manifest file is empty: {relative}")
        if path.stat().st_size != row["size"]:
            raise BundleError(f"size mismatch: {relative}")
        if sha256_file(path) != row["sha256"]:
            raise BundleError(f"hash mismatch: {relative}")
        installed_size += path.stat().st_size
        extension_counts[path.suffix.lower() or "(none)"] += 1

    actual_paths = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    }
    expected_paths = listed_paths | {"manifest.json"}
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        raise BundleError(f"bundle coverage mismatch; missing={missing}, extra={extra}")
    allowed_directories = {
        parent.as_posix()
        for relative in expected_paths
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }
    for path in output.rglob("*"):
        relative = path.relative_to(output).as_posix()
        if path.is_symlink():
            raise BundleError(f"bundle contains a symlink: {relative}")
        if path.is_dir() and relative not in allowed_directories:
            raise BundleError(f"bundle contains an unexpected directory: {relative}")
    installed_size += manifest_path.stat().st_size
    extension_counts[".json"] += 1
    if installed_size != manifest.get("installed_size_bytes"):
        raise BundleError("manifest installed_size_bytes is stale")
    if dict(sorted(extension_counts.items())) != manifest.get("extension_counts"):
        raise BundleError("manifest extension_counts is stale")
    if installed_size > int(selection["installed_size_limit_bytes"]):
        raise BundleError("bundle exceeds its installed size policy")

    expected_standard_presets = {
        f"shaders_glsl/{normalize_source_path(str(row['path'])).as_posix()}"
        for row in selection["presets"]
    }
    expected_recommended_presets = {
        normalize_source_path(str(row["path"])).as_posix()
        for row in recommended_rows
    }
    expected_presets = expected_standard_presets | expected_recommended_presets
    manifest_preset_rows = manifest.get("presets", [])
    manifest_presets = {str(row["path"]) for row in manifest_preset_rows}
    if manifest_presets != expected_presets:
        raise BundleError("manifest preset list does not match selection")
    expected_qualification = {
        f"shaders_glsl/{normalize_source_path(str(row['path'])).as_posix()}": str(
            row["qualification"]
        )
        for row in selection["presets"]
    }
    expected_qualification.update(
        {
            normalize_source_path(str(row["path"])).as_posix(): str(
                row["qualification"]
            )
            for row in recommended_rows
        }
    )
    if {
        str(row["path"]): str(row.get("qualification", ""))
        for row in manifest_preset_rows
    } != expected_qualification:
        raise BundleError("manifest preset qualifications do not match selection")
    validate_output_dependencies(output, manifest)
    return manifest


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build and validate the shader bundle")
    build.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    build.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    build.add_argument(
        "--no-fetch",
        action="store_true",
        help="do not clone or fetch the locked source commit",
    )

    validate = subparsers.add_parser("validate", help="validate an existing bundle")
    validate.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.command == "build":
            manifest = build_bundle(
                args.lock.resolve(),
                args.source.resolve(),
                args.output.resolve(),
                not args.no_fetch,
            )
            print(
                f"built {manifest['preset_count']} presets "
                f"({manifest['installed_size_bytes']} bytes) at {args.output.resolve()}"
            )
        else:
            manifest = validate_bundle(args.lock.resolve(), args.output.resolve())
            print(
                f"validated {manifest['preset_count']} presets "
                f"({manifest['installed_size_bytes']} bytes) at {args.output.resolve()}"
            )
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
