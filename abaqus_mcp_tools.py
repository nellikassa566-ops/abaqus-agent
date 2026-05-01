#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure-Python helpers for structured Abaqus MCP tools.

This module intentionally avoids importing Abaqus or the MCP runtime so it can
be unit-tested with normal Python before commands are sent to Abaqus/CAE.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping


MCP_HOME = Path(__file__).resolve().parent
TEMPLATES_DIR = MCP_HOME / "templates"

SUPPORTED_PART_TYPES = {"block", "cylinder"}
SUPPORTED_STEP_TYPES = {"static", "visco", "heat_transfer", "coupled_temp_displacement"}
SUPPORTED_BC_TYPES = {
    "encastre",
    "displacement",
    "temperature",
    "xsymm",
    "ysymm",
    "zsymm",
}
DEFAULT_ELEMENT_TYPE = "C3D8R"


def _as_dict(value: Any, field: str, errors: List[str]) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    errors.append(f"{field} must be an object")
    return {}


def _as_list(value: Any, field: str, errors: List[str]) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    errors.append(f"{field} must be a list")
    return []


def _require_name(item: Mapping[str, Any], field: str, errors: List[str]) -> str:
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{field} requires non-empty name")
        return ""
    return name.strip()


def _number_list(value: Any, length: int, field: str, errors: List[str]) -> List[float]:
    if not isinstance(value, list) or len(value) != length:
        errors.append(f"{field} must be a list of {length} numbers")
        return []
    result = []
    for item in value:
        try:
            result.append(float(item))
        except Exception:
            errors.append(f"{field} contains non-numeric value: {item!r}")
            return []
    return result


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, Mapping):
        merged = copy.deepcopy(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    return copy.deepcopy(override)


def validate_model_spec(spec: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and normalize the structured model spec.

    The schema is intentionally small for v1: block/cylinder parts, simple
    elastic materials, homogeneous sections, common steps, BCs, loads, mesh,
    and jobs. Unsupported keys are preserved so advanced builders can still
    inspect them in Abaqus.
    """
    errors: List[str] = []
    warnings: List[str] = []
    spec = _as_dict(spec, "spec", errors)
    normalized: Dict[str, Any] = copy.deepcopy(spec)

    model_name = normalized.get("model_name", "Model-1")
    if not isinstance(model_name, str) or not model_name.strip():
        errors.append("model_name must be a non-empty string")
        model_name = "Model-1"
    normalized["model_name"] = model_name.strip()

    parts = _as_list(normalized.get("parts"), "parts", errors)
    part_names = set()
    for index, part in enumerate(parts):
        part = _as_dict(part, f"parts[{index}]", errors)
        name = _require_name(part, f"parts[{index}]", errors)
        if name:
            if name in part_names:
                errors.append(f"duplicate part name: {name}")
            part_names.add(name)
        part_type = part.get("type", "block")
        if part_type not in SUPPORTED_PART_TYPES:
            errors.append(f"unsupported part type: {name or index} -> {part_type}")
        if part_type == "block":
            dims = _number_list(part.get("dimensions"), 3, f"part dimensions: {name}", errors)
            if dims and any(v <= 0 for v in dims):
                errors.append(f"part dimensions must be positive: {name}")
        elif part_type == "cylinder":
            try:
                radius = float(part.get("radius"))
                height = float(part.get("height"))
                if radius <= 0 or height <= 0:
                    errors.append(f"cylinder radius/height must be positive: {name}")
            except Exception:
                errors.append(f"cylinder requires numeric radius and height: {name}")
        if "origin" in part:
            _number_list(part.get("origin"), 3, f"part origin: {name}", errors)

    materials = _as_list(normalized.get("materials"), "materials", errors)
    material_names = set()
    for index, material in enumerate(materials):
        material = _as_dict(material, f"materials[{index}]", errors)
        name = _require_name(material, f"materials[{index}]", errors)
        if name:
            if name in material_names:
                errors.append(f"duplicate material name: {name}")
            material_names.add(name)
        elastic = material.get("elastic")
        if elastic is not None:
            elastic = _as_dict(elastic, f"material elastic: {name}", errors)
            if "table" not in elastic:
                for key in ("youngs_modulus", "poisson_ratio"):
                    try:
                        float(elastic[key])
                    except Exception:
                        errors.append(f"material elastic requires {key}: {name}")

    sections = _as_list(normalized.get("sections"), "sections", errors)
    assigned_parts = set()
    for index, section in enumerate(sections):
        section = _as_dict(section, f"sections[{index}]", errors)
        section_name = _require_name(section, f"sections[{index}]", errors)
        material_name = section.get("material")
        if material_name and material_name not in material_names:
            errors.append(f"section material not found: {section_name} -> {material_name}")
        target_parts = _as_list(section.get("parts"), f"section parts: {section_name}", errors)
        for part_name in target_parts:
            if part_name not in part_names:
                errors.append(f"section part not found: {section_name} -> {part_name}")
            assigned_parts.add(part_name)
    for part_name in sorted(part_names - assigned_parts):
        warnings.append(f"part has no section assignment: {part_name}")

    steps = _as_list(normalized.get("steps"), "steps", errors)
    for index, step in enumerate(steps):
        step = _as_dict(step, f"steps[{index}]", errors)
        step_name = _require_name(step, f"steps[{index}]", errors)
        step_type = step.get("type", "static")
        if step_type not in SUPPORTED_STEP_TYPES:
            errors.append(f"unsupported step type: {step_name} -> {step_type}")

    bcs = _as_list(normalized.get("boundary_conditions"), "boundary_conditions", errors)
    for index, bc in enumerate(bcs):
        bc = _as_dict(bc, f"boundary_conditions[{index}]", errors)
        bc_name = _require_name(bc, f"boundary_conditions[{index}]", errors)
        bc_type = bc.get("type", "displacement")
        if bc_type not in SUPPORTED_BC_TYPES:
            errors.append(f"unsupported boundary condition type: {bc_name} -> {bc_type}")

    mesh = normalized.get("mesh") or {}
    mesh = _as_dict(mesh, "mesh", errors)
    if "global_size" in mesh:
        try:
            if float(mesh["global_size"]) <= 0:
                errors.append("mesh.global_size must be positive")
        except Exception:
            errors.append("mesh.global_size must be numeric")
    else:
        mesh["global_size"] = 1.0
    mesh.setdefault("element_type", DEFAULT_ELEMENT_TYPE)
    normalized["mesh"] = mesh

    jobs = _as_list(normalized.get("jobs"), "jobs", errors)
    for index, job in enumerate(jobs):
        job = _as_dict(job, f"jobs[{index}]", errors)
        _require_name(job, f"jobs[{index}]", errors)
        job.setdefault("model", normalized["model_name"])

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized": normalized,
    }


def build_command_payload(command_type: str, **kwargs: Any) -> Dict[str, Any]:
    """Build a validated command payload for Abaqus-side execution."""
    payload: Dict[str, Any] = {"type": command_type}
    payload.update(kwargs)
    if "spec" in payload:
        validation = validate_model_spec(payload["spec"])
        payload["validation"] = validation
        payload["spec"] = validation["normalized"]
        if "dry_run" not in payload:
            payload["dry_run"] = True
    return payload


def _template_dirs() -> Iterable[Path]:
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(path for path in TEMPLATES_DIR.iterdir() if path.is_dir())


def _read_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_template_metadata() -> List[Dict[str, Any]]:
    """Return metadata for all installed simulation templates."""
    templates = []
    for template_dir in _template_dirs():
        defaults_path = template_dir / "defaults.json"
        schema_path = template_dir / "schema.json"
        if not defaults_path.exists():
            continue
        defaults = _read_json(defaults_path)
        schema = _read_json(schema_path) if schema_path.exists() else {}
        templates.append({
            "id": template_dir.name,
            "title": schema.get("title", defaults.get("template", template_dir.name)),
            "description": schema.get("description", ""),
            "parameters": schema.get("properties", {}),
        })
    return templates


def instantiate_template(template_id: str, parameters: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Instantiate a template by merging user parameters into defaults."""
    template_dir = TEMPLATES_DIR / template_id
    defaults_path = template_dir / "defaults.json"
    if not defaults_path.exists():
        raise ValueError(f"template not found: {template_id}")
    defaults = _read_json(defaults_path)
    spec = _deep_merge(defaults.get("spec", defaults), parameters or {})
    validation = validate_model_spec(spec)
    if not validation["valid"]:
        raise ValueError("template produced invalid spec: " + "; ".join(validation["errors"]))
    return validation["normalized"]


DIAGNOSTIC_PATTERNS = [
    (
        "increment_cutback_failure",
        ("TOO MANY ATTEMPTS", "TIME INCREMENT REQUIRED IS LESS THAN"),
        "Reduce initial increment, add stabilization, improve contact/mesh, or simplify nonlinearities.",
        "error",
    ),
    (
        "negative_eigenvalues",
        ("NEGATIVE EIGENVALUE", "NEGATIVE EIGENVALUES"),
        "Check constraints, contact stability, material stiffness, and unconstrained rigid body motion.",
        "warning",
    ),
    (
        "distorted_elements",
        ("DISTORTED", "EXCESSIVE DISTORTION"),
        "Refine or repair the mesh near reported elements and check geometry quality.",
        "warning",
    ),
    (
        "contact_penetration",
        ("CONTACT", "PENETRATION"),
        "Review contact pair orientation, surface density, clearance, and contact controls.",
        "warning",
    ),
    (
        "creep_subroutine",
        ("USER SUBROUTINE CREEP", "CREEP WILL CAUSE CODE EXECUTION ERRORS"),
        "Verify creep law constants, material naming, and user subroutine availability.",
        "warning",
    ),
    (
        "license_or_solver_error",
        ("Abaqus Error", "LICENSE", "ERROR MESSAGES"),
        "Inspect solver logs and license availability.",
        "error",
    ),
]


def parse_job_diagnostics_text(text: str) -> Dict[str, Any]:
    """Classify common Abaqus solver diagnostics from .msg/.dat/.sta text."""
    text = text or ""
    upper = text.upper()
    issues = []
    for code, patterns, fix_hint, severity in DIAGNOSTIC_PATTERNS:
        if any(pattern.upper() in upper for pattern in patterns):
            issues.append({
                "code": code,
                "severity": severity,
                "fix_hint": fix_hint,
            })
    explicit_error = "***ERROR" in upper or " ERROR MESSAGES" in upper and " 0  ERROR MESSAGES" not in upper
    completed = "THE ANALYSIS HAS BEEN COMPLETED" in upper or "JOB" in upper and "COMPLETED" in upper
    ok = completed and not explicit_error and not any(item["severity"] == "error" for item in issues)
    return {"ok": ok, "completed": completed, "issues": issues}


def parse_job_diagnostics_files(paths: Iterable[str]) -> Dict[str, Any]:
    combined = []
    existing = []
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        existing.append(str(p))
        try:
            combined.append(p.read_text(encoding="utf-8", errors="replace"))
        except TypeError:
            combined.append(p.read_text(encoding="utf-8"))
    diagnostics = parse_job_diagnostics_text("\n".join(combined))
    diagnostics["files"] = existing
    return diagnostics
