"""Automated architectural boundary & AST-enforcement tests.

Enforces:
1. Single Responsibility Principle (SRP)
2. Domain-Agnostic Core Invariant
3. Clean Architecture: No domain leaks (AD, WMI, etc.) into generic workflow engine.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE_AUTOMATION_DIR = Path(__file__).resolve().parent.parent / "automation"
API_AUTOPILOT_SERVICE = (
    Path(__file__).resolve().parent.parent.parent
    / "api"
    / "src"
    / "features"
    / "autopilot"
    / "service.py"
)

# Generic Workflow Engine modules that must remain 100% domain-agnostic
GENERIC_ENGINE_FILES = [
    "capabilities.py",
    "clarification.py",
    "compiler.py",
    "persistence.py",
    "policy.py",
    "preflight.py",
    "resolution.py",
    "runner.py",
    "workflows.py",
]

FORBIDDEN_DOMAIN_MODULES = [
    "core.ad",
    "core.directum",
    "worker",
    "api",
]

FORBIDDEN_DOMAIN_LITERALS = {
    "ou_candidates",
    "target_ou_dn",
    "users_root_ou",
}


def _extract_imports(tree: ast.AST) -> set[str]:
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module)
    return imported_modules


def _extract_string_literals(tree: ast.AST) -> set[str]:
    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
    return literals


@pytest.mark.parametrize("filename", GENERIC_ENGINE_FILES)
def test_generic_engine_has_no_domain_imports(filename: str) -> None:
    filepath = CORE_AUTOMATION_DIR / filename
    assert filepath.exists(), f"File {filepath} does not exist"

    tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    imports = _extract_imports(tree)

    violations = []
    for mod in imports:
        for forbidden in FORBIDDEN_DOMAIN_MODULES:
            if mod == forbidden or mod.startswith(f"{forbidden}."):
                violations.append(mod)

    assert not violations, (
        f"Architectural Boundary Violation in {filename}: "
        f"Generic workflow engine must not import domain modules: {violations}"
    )


@pytest.mark.parametrize("filename", GENERIC_ENGINE_FILES)
def test_generic_engine_has_no_domain_literals(filename: str) -> None:
    filepath = CORE_AUTOMATION_DIR / filename
    assert filepath.exists(), f"File {filepath} does not exist"

    tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    literals = _extract_string_literals(tree)

    found_violations = literals.intersection(FORBIDDEN_DOMAIN_LITERALS)
    assert not found_violations, (
        f"Domain Leak Violation in {filename}: "
        f"Generic workflow engine contains domain-specific string literals: {found_violations}"
    )


def test_api_service_has_no_low_level_domain_leak() -> None:
    assert API_AUTOPILOT_SERVICE.exists(), f"File {API_AUTOPILOT_SERVICE} does not exist"

    tree = ast.parse(API_AUTOPILOT_SERVICE.read_text(encoding="utf-8"), filename=str(API_AUTOPILOT_SERVICE))
    imports = _extract_imports(tree)

    forbidden_in_api = ["core.ad.credentials", "core.ad.provisioning"]
    violations = [
        mod for mod in imports
        if any(mod == f or mod.startswith(f"{f}.") for f in forbidden_in_api)
    ]
    assert not violations, (
        f"Architectural Violation in api/autopilot/service.py: "
        f"API layer should not directly import internal AD provisioning utilities: {violations}. "
        "Use CapabilityExecutor polymorphic validation instead."
    )


def test_ast_boundary_guard_catches_synthetic_violation() -> None:
    bad_code = "from core.ad.credentials import AD_USERS_ROOT_OU\nx = 'target_ou_dn'"
    tree = ast.parse(bad_code)
    imports = _extract_imports(tree)
    literals = _extract_string_literals(tree)

    assert "core.ad.credentials" in imports
    assert "target_ou_dn" in literals
