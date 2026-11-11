"""
Game settings: what the cluster and each map ask for, and writing it into
the map's ini files right before the map starts.

    effective = official defaults ← cluster.game ← map overrides["game"]

Written only while the map is stopped (ASA rewrites GameUserSettings.ini on
shutdown, which would undo an edit made while it runs), and only the keys in
``services/ark_settings.py``: a key at its official value is removed, every
other line of the file stays as it is. A cluster that never had its
settings saved (``game is None``) is not written at all.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path

from engine import ark_ini, paths, podman, store
from services.ark_settings import GAME_SECTION, GUS_SECTION, ORDER, SETTINGS, ini_value, is_default, normalize

FILES = {"gus": "GameUserSettings.ini", "game": "Game.ini"}
STACK_KEY = "ConfigOverrideItemMaxQuantity"
_STACK = re.compile(r'ItemClassString\s*=\s*"([^"]+)".*?MaxItemQuantity\s*=\s*(\d+)', re.I)


def stack_line(item_class: str, quantity: int) -> str:
    """One Game.ini override; bIgnoreMultiplier makes the number the exact stack size."""
    return (f'(ItemClassString="{item_class}",Quantity=(MaxItemQuantity={int(quantity)},'
            f'bIgnoreMultiplier=true))')


def parse_stack(value: str):
    m = _STACK.search(value or "")
    return (m.group(1), int(m.group(2))) if m else None


def read_stacks(text: str) -> dict:
    out = {}
    for value in ark_ini.read_all(text, GAME_SECTION, STACK_KEY):
        parsed = parse_stack(value)
        if parsed:
            out[parsed[0]] = parsed[1]
    return out
KEEP_BACKUPS = 10


def official() -> dict:
    return {k: SETTINGS[k]["default"] for k in ORDER}


def effective(cluster, server=None) -> dict:
    values = official()
    values.update(cluster.game or {})
    if server is not None:
        values.update((server.overrides or {}).get("game") or {})
    return values


def config_dir(server) -> Path:
    return Path(server.saved_dir) / "Config" / "WindowsServer"


def read_map_text(text: str, file: str) -> dict:
    """The catalogue keys one ini file's text sets, as typed values."""
    found = {}
    keys = [k for k in ORDER if SETTINGS[k]["file"] == file]
    section = GUS_SECTION if file == "gus" else GAME_SECTION
    for key, raw in ark_ini.read(text, section, keys).items():
        try:
            found[key] = normalize(key, raw.strip('"'))
        except ValueError:
            continue
    return found


def read_map(server) -> dict:
    """The catalogue keys a map's ini files set now, as typed values."""
    found = {}
    for file, name in FILES.items():
        try:
            text = _read(config_dir(server) / name)
        except OSError:
            continue
        found.update(read_map_text(text, file))
    return found


def import_from_maps(cluster, servers) -> tuple[dict, dict]:
    """
    What the maps use now, split into the cluster's settings (equal on every
    map) and each map's own (where they differ). Nothing is lost.
    """
    readings = {s.id: {**official(), **read_map(s)} for s in servers}
    shared, own = {}, {s.id: {} for s in servers}
    for key in ORDER:
        values = [readings[s.id][key] for s in servers]
        if values and all(_same(v, values[0]) for v in values):
            if not is_default(key, values[0]):
                shared[key] = values[0]
        else:
            for s in servers:
                own[s.id][key] = readings[s.id][key]
    return shared, own


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    return abs(float(a) - float(b)) < 1e-9


def render(text: str, file: str, values: dict, stacks: dict | None = None) -> str:
    """
    ``text`` with this file's managed keys set. A key already in the file is
    always rewritten with its value; a missing one is only added when it
    differs from official. Nothing is deleted, so a wiki default that is
    wrong for ASA can never change a server's behaviour.
    """
    section = GUS_SECTION if file == "gus" else GAME_SECTION
    keys = [k for k in ORDER if SETTINGS[k]["file"] == file]
    present = ark_ini.read(text, section, keys)
    changes = {}
    for key in keys:
        if key in present or not is_default(key, values[key]):
            new = ini_value(key, values[key])
            old = present.get(key)
            if old is not None and _equal_text(key, old, new):
                continue          # same value: leave the line exactly as it is
            changes[key] = new
    text = ark_ini.write(text, section, changes)
    if file == "game" and stacks is not None:
        # Managed: the app owns every override line (unchanged lines are left as they are).
        wanted = [stack_line(c, q) for c, q in sorted(stacks.items())]
        if read_stacks(text) != dict(stacks) or len(ark_ini.read_all(text, section, STACK_KEY)) != len(wanted):
            text = ark_ini.replace_all(text, section, STACK_KEY, wanted)
    return text


def _equal_text(key: str, old: str, new: str) -> bool:
    try:
        return _same(normalize(key, old.strip('"')), normalize(key, new))
    except ValueError:
        return False


def apply(server, cluster, log) -> bool:
    """Write the map's ini files for its effective settings. True if anything changed."""
    if cluster.game is None and cluster.stacks is None:
        return False
    values = effective(cluster, server)
    target_dir = config_dir(server)
    changed = False
    for file, name in FILES.items():
        path = target_dir / name
        try:
            old = _read(path) if path.exists() else ""
        except OSError as exc:
            raise RuntimeError(f"Cannot read {path}: {exc}")
        new = render(old, file, values, cluster.stacks)
        if new == old:
            continue
        if old:
            _backup(server, name, old)
        _write(path, new)
        changed = True
        log(f"Game settings written to {name}.")
    if not changed:
        log("Game settings already up to date.")
    return changed


def _read(path: Path) -> str:
    """The file's text with its line endings untouched (read_text would turn CRLF into LF)."""
    return path.read_bytes().decode("utf-8", errors="replace")


def _backup(server, name: str, text: str) -> None:
    d = paths.data_dir() / "ini-backups" / server.id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.{time.strftime('%Y%m%d-%H%M%S')}").write_bytes(text.encode("utf-8"))
    old = sorted(d.glob(f"{name}.*"))
    for extra in old[:-KEEP_BACKUPS]:
        extra.unlink()


def _write(path: Path, text: str) -> None:
    """The files belong to the container's user: write through podman's user namespace."""
    settings = store.host_settings()
    owner = f"{settings.puid}:{settings.pgid}"
    tmp_dir = paths.data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=tmp_dir, delete=False, newline="") as fh:
        fh.write(text)
        tmp = fh.name
    try:
        script = (f'mkdir -p "{path.parent}" && chown {owner} "{path.parent}" && '
                  f'cp "{tmp}" "{path}.ca-new" && chown {owner} "{path}.ca-new" && '
                  f'chmod 644 "{path}.ca-new" && mv -f "{path}.ca-new" "{path}"')
        cp = subprocess.run([podman.binary(), "unshare", "sh", "-c", script],
                            capture_output=True, text=True, timeout=60)
        if cp.returncode != 0:
            raise RuntimeError(f"Could not write {path.name}: {(cp.stderr or cp.stdout).strip()[:300]}")
    finally:
        Path(tmp).unlink(missing_ok=True)
