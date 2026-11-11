"""
Edit ARK's ini files in place (from calagopus_wapyt, now case-insensitive).

ARK's ini files are not quite INI: keys repeat on purpose
(``ConfigOverrideItemMaxQuantity=…``), and configparser would reorder and
lowercase. So this works on lines: it changes the first ``key=`` line of a
section, or appends to the section (creating it at the end of the file if
needed), and leaves every other line byte for byte, line endings included.

Section and key names match case-insensitively (Game.ini says
``[/Script/ShooterGame.ShooterGameMode]``, the wiki writes it lowercase); a
rewritten line keeps the spelling the file already used.
"""
from __future__ import annotations


def _header(stripped: str):
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped[1:-1].strip().lower()
    return None


def read(text: str, section: str, keys) -> dict:
    """The current values of ``keys`` in ``[section]`` (first occurrence each), by the given key."""
    wanted = {k.lower(): k for k in keys}
    target = section.lower()
    found: dict = {}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        head = _header(stripped)
        if head is not None:
            current = head
            continue
        if current != target or "=" not in stripped or stripped.startswith((";", "#")):
            continue
        key, _, value = stripped.partition("=")
        name = wanted.get(key.strip().lower())
        if name and name not in found:
            found[name] = value.strip()
    return found


def write(text: str, section: str, changes: dict, remove=()) -> str:
    """
    ``text`` with ``changes`` (key -> value) applied in ``[section]`` and the
    keys in ``remove`` deleted from it.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    pending = {k.lower(): (k, v) for k, v in changes.items()}
    removing = {k.lower() for k in remove}
    target = section.lower()
    out: list[str] = []
    current = None
    seen_section = False
    section_end = None  # index in `out` after the last line of the section

    for line in lines:
        stripped = line.strip()
        head = _header(stripped)
        if head is not None:
            if current == target:
                section_end = _trim_blank(out)
            current = head
            out.append(line)
            if current == target and not seen_section:
                seen_section = True
                section_end = len(out)
            continue
        if current == target and "=" in stripped and not stripped.startswith((";", "#")):
            key = stripped.partition("=")[0].strip()
            low = key.lower()
            if low in removing:
                continue
            if low in pending:
                _name, value = pending.pop(low)
                out.append(f"{key}={value}")
                section_end = len(out)
                continue
        out.append(line)
        if current == target and stripped:
            section_end = len(out)

    if pending:
        additions = [f"{name}={value}" for name, value in pending.values()]
        if section_end is None:
            if out and out[-1].strip():
                out.append("")
            out.append(f"[{section}]")
            out.extend(additions)
        else:
            out[section_end:section_end] = additions
    result = newline.join(out)
    return result + newline if text.endswith(("\n", "\r\n")) or not text else result


def _trim_blank(out: list) -> int:
    """Index just after the last non-blank line (blank lines before the next header stay after it)."""
    end = len(out)
    while end > 0 and not out[end - 1].strip():
        end -= 1
    return end
