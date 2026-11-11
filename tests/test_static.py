"""
Guards the page cannot give us: a broken browser module does not fail the
server, the page just never loads. And the engine's rules (state lives in
plain modules, only lifecycle touches containers) hold by construction.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

APPCODE = Path(__file__).resolve().parents[1] / "appcode"
PAGE = APPCODE / "clusters_ascended.py"
BROWSER = [PAGE, *sorted((APPCODE / "ui").glob("*.py"))]


@pytest.mark.parametrize("path", sorted(APPCODE.rglob("*.py")), ids=lambda p: str(p.relative_to(APPCODE)))
def test_compiles(path):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module, node
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node


def test_every_local_import_in_the_browser_modules_exists():
    missing = []
    for path in BROWSER:
        for module, _node in _imports(path):
            top = module.split(".")[0]
            if top in ("ui", "services"):
                if not (APPCODE / (module.replace(".", "/") + ".py")).exists():
                    missing.append(f"{path.name}: {module}")
    assert not missing, missing


def test_the_browser_never_imports_the_engine():
    bad = [f"{p.name}: {m}" for p in BROWSER for m, _ in _imports(p) if m.split(".")[0] == "engine"]
    assert not bad, bad


def test_browser_modules_import_names_not_modules():
    """`from services import x` boots the browser to a blank page."""
    for path in BROWSER:
        for module, node in _imports(path):
            if module == "services" and isinstance(node, ast.ImportFrom):
                pytest.fail(f"{path.name}: from services import …")


def test_bff_modules_import_the_engine_only_inside_methods():
    """BFF modules are shipped as stubs to the browser and re-executed per call."""
    for path in (APPCODE / "services").glob("*_api.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("engine"):
                pytest.fail(f"{path.name}: module-level engine import")
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(
                    getattr(node, "value", None), (ast.Dict, ast.List, ast.Set, ast.Call)):
                pytest.fail(f"{path.name}: mutable module state is rebuilt on every call")


def test_only_lifecycle_starts_or_stops_containers():
    offenders = []
    for path in (APPCODE / "engine").glob("*.py"):
        if path.name in ("lifecycle.py", "podman.py"):
            continue
        text = path.read_text(encoding="utf-8")
        for call in ("podman.start(", "podman.stop(", "podman.run(["):
            if call in text:
                offenders.append(f"{path.name}: {call}")
    assert not offenders, offenders


def test_relative_imports_are_not_used():
    """pytincture imports BFF modules by path: `from .x import` fails at call time."""
    for path in [*(APPCODE / "services").glob("*.py"), *(APPCODE / "engine").glob("*.py")]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{path.name}: relative import"


def test_csrf_cookie_names_follow_the_namespace():
    import re

    service = (APPCODE.parent / "service.py").read_text(encoding="utf-8")
    namespace = re.search(r'COOKIE_NAMESPACE = "([^"]+)"', service).group(1)
    page = PAGE.read_text(encoding="utf-8")
    assert f'"__Host-{namespace}-csrf"' in page and f'"{namespace}-dev-csrf"' in page


def test_engine_state_survives_bff_reexecution(data_dir):
    loading = pytest.importorskip("pytincture.backend.source_loading")
    from engine import state

    state.set_override("probe", "starting")
    for path in (APPCODE / "services").glob("*_api.py"):
        first = loading.load_source_module(str(path), path.stem, str(APPCODE))
        second = loading.load_source_module(str(path), path.stem, str(APPCODE))
        assert first is not second
    import engine.state as again

    assert again.override("probe") == "starting"
    state.set_override("probe", None)
