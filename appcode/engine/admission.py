"""
Memory admission control. Pure: a snapshot in, a decision out, no I/O.

ASA needs ~16 GiB per map. A third map on a 31 GiB host OOMs, and a hard
per-container cap kills a map while it loads (tried: 18 GiB, killed). So
instead of capping, nothing starts unless the host can hold it:

    budget    = RAM + swap allowance − reserve
    external  = everything in use that is not a managed server
    projected = Σ hold(committed servers) + external + startup need(candidate)

Loading a map takes more memory than running it. So a map that is starting
(or updating) holds its *startup* need, and a start always reserves the
startup need; only once it is fully started does it count at its *running*
need (measured after the start: memlearn.py). A map never counts below what
it actually holds.

    R1  projected ≤ budget
    R2  committed + 1 ≤ host max_running
    R3  the candidate's cluster rules (max_running, memory_cap) hold
    R4  the memory is physically there: free RAM + free swap − reserve
        covers the candidate plus what committed servers have yet to grow

"Committed" servers are starting, ready, stopping (still holding memory) or
updating. A restart or update leaves the candidate out of committed, since
it replaces itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from engine.models import COMMITTED, GiB, Cluster, HostSettings

START_KINDS = frozenset({"start", "restart", "update", "make_room_start"})


@dataclass
class HostMem:
    mem_total: int
    mem_available: int
    swap_total: int
    swap_free: int
    psi_some10: float = 0.0
    psi_full10: float = 0.0

    @property
    def used_total(self) -> int:
        return (self.mem_total - self.mem_available) + (self.swap_total - self.swap_free)


@dataclass
class ServerView:
    id: str
    name: str
    cluster: str
    priority: int
    phase: str
    need: int                        # running need
    actual: Optional[int] = None     # anon + swap of its container, if running
    players: int = 0
    started_at: float = 0.0
    need_start: Optional[int] = None  # startup need (None: same as running)

    @property
    def start_need(self) -> int:
        return max(self.need, self.need_start or 0)


LOADING = frozenset({"starting", "updating"})


def hold(v: ServerView, phase: str) -> int:
    """What a committed map counts for: startup need while loading, else running need."""
    base = v.start_need if phase in LOADING else v.need
    return max(base, v.actual or 0)


@dataclass
class Snapshot:
    host: HostMem
    settings: HostSettings
    servers: dict                       # id -> ServerView
    clusters: dict = field(default_factory=dict)   # id -> Cluster


@dataclass
class Decision:
    ok: bool
    reason: str
    lines: list = field(default_factory=list)
    victims: list = field(default_factory=list)    # who "make room" would stop
    numbers: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "lines": list(self.lines),
                "victims": list(self.victims), "numbers": dict(self.numbers)}


def gib(n: float) -> str:
    return f"{n / GiB:.1f} GiB"


def project(snap: Snapshot, pending: Iterable[tuple[str, str]]) -> dict:
    """Phases after the queued jobs run: starts commit, stops release."""
    phases = {sid: v.phase for sid, v in snap.servers.items()}
    for kind, sid in pending:
        if sid not in phases:
            continue
        if kind in START_KINDS:
            phases[sid] = "starting"
        elif kind == "stop":
            phases[sid] = "stopped"
    return phases


def admit(snap: Snapshot, kind: str, server_id: str,
          pending: Iterable[tuple[str, str]] = ()) -> Decision:
    """May ``server_id`` be started (``kind``: start, restart, update)?"""
    cand = snap.servers.get(server_id)
    if cand is None:
        return Decision(False, "Unknown server.")
    s = snap.settings
    phases = project(snap, pending)
    others = [v for sid, v in snap.servers.items()
              if sid != server_id and phases.get(sid) in COMMITTED]

    if kind == "start" and phases.get(server_id) in COMMITTED:
        return Decision(False, f"{cand.name} is already {phases[server_id]}.")

    budget = snap.host.mem_total + s.swap_allowance - s.reserve
    managed_actual = sum(v.actual or 0 for v in snap.servers.values())
    external = max(0, snap.host.used_total - managed_actual)
    committed = sum(hold(v, phases.get(v.id)) for v in others)
    need = cand.start_need
    projected = committed + external + need
    numbers = {"budget": budget, "external": external, "committed": committed,
               "need": need, "need_running": cand.need, "projected": projected}

    held = ", ".join(f"{v.name} {gib(hold(v, phases.get(v.id)))} {phases.get(v.id)}" for v in others) or "nothing"
    lines = [
        f"{cand.name} needs {gib(need)} to start ({gib(cand.need)} once it is running).",
        f"Committed: {gib(committed)} ({held}); other use on the host: {gib(external)}.",
        f"Projected {gib(projected)} of a {gib(budget)} budget "
        f"(RAM {gib(snap.host.mem_total)} + swap allowance {gib(s.swap_allowance)} − reserve {gib(s.reserve)}).",
    ]

    problems = []
    if projected > budget:
        problems.append(f"it would need {gib(projected - budget)} more than the budget")
    if len(others) + 1 > s.max_running:
        problems.append(f"the host runs at most {s.max_running} maps at once")

    cluster = snap.clusters.get(cand.cluster)
    if cluster is not None:
        same = [v for v in others if v.cluster == cand.cluster]
        if cluster.max_running and len(same) + 1 > cluster.max_running:
            problems.append(f"cluster {cluster.name} runs at most {cluster.max_running} at once")
        if cluster.memory_cap and sum(hold(v, phases.get(v.id)) for v in same) + need > cluster.memory_cap:
            problems.append(f"cluster {cluster.name} is capped at {gib(cluster.memory_cap)}")

    # R4: the bytes must exist now. Committed servers still growing toward
    # their need will take memory first.
    growth = sum(max(0, hold(v, phases.get(v.id)) - (v.actual or 0)) for v in others)
    physical = snap.host.mem_available + snap.host.swap_free - s.reserve - growth
    numbers["physical"] = physical
    if physical < need and not any("budget" in p for p in problems):
        problems.append(f"only {gib(max(0, physical))} of RAM and swap is free right now")

    if problems:
        victims = pick_victims(snap, others, cand, phases)
        reason = f"Not enough memory to start {cand.name}: " + "; ".join(problems) + "."
        return Decision(False, reason, lines, victims, numbers)
    return Decision(True, f"{cand.name} fits.", lines, [], numbers)


def never_fits(snap: Snapshot, server_id: str) -> Optional[str]:
    """Why this map could not start even with every other map stopped, or None."""
    cand = snap.servers.get(server_id)
    if cand is None:
        return "Unknown server."
    s = snap.settings
    budget = snap.host.mem_total + s.swap_allowance - s.reserve
    managed_actual = sum(v.actual or 0 for v in snap.servers.values())
    external = max(0, snap.host.used_total - managed_actual)
    if cand.start_need + external > budget:
        return (f"{cand.name} needs {gib(cand.start_need)} to start, but even with every map stopped only "
                f"{gib(max(0, budget - external))} of the {gib(budget)} budget is free.")
    return None


def pick_victims(snap: Snapshot, others: list, cand: ServerView, phases: Optional[dict] = None) -> list:
    """
    Who to stop so ``cand`` fits: least important first (highest priority
    number), then fewest players, then most recently started. Stops until
    both the count and the budget rules would hold.
    """
    s = snap.settings
    budget = snap.host.mem_total + s.swap_allowance - s.reserve
    managed_actual = sum(v.actual or 0 for v in snap.servers.values())
    external = max(0, snap.host.used_total - managed_actual)
    phases = phases or {v.id: v.phase for v in others}
    order = sorted(others, key=lambda v: (-v.priority, v.players, -v.started_at))
    remaining = list(others)
    victims = []
    for v in order:
        fits_count = len(remaining) + 1 <= s.max_running
        fits_memory = sum(hold(r, phases.get(r.id)) for r in remaining) + external + cand.start_need <= budget
        if fits_count and fits_memory:
            break
        victims.append(v.id)
        remaining.remove(v)
    fits = (len(remaining) + 1 <= s.max_running
            and sum(hold(r, phases.get(r.id)) for r in remaining) + external + cand.start_need <= budget)
    return victims if fits else []
