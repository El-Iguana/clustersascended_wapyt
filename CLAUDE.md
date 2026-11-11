# Clusters Ascended — notes for working on this repo

ARK: Survival Ascended cluster manager on pytincture + wapyt. Replaced the
FastAPI webadmin in `~/asa-cluster/webadmin` on 2026-11-11 (the game data stays
in `~/asa-cluster`). Full plan: `~/.claude/plans/tender-meandering-panda.md`.

## Running it (since the cutover, 2026-11-11)

- `clusters-ascended.service` (systemd --user, `deploy/`): the app on
  127.0.0.1:8098, in HTTPS mode (`.env`: `CA_CANONICAL_ORIGIN=https://192.168.1.97:8099`,
  `CA_ALLOWED_HOSTS=192.168.1.97,127.0.0.1`). `KillMode=process` so restarting it
  never kills a map.
- `clusters-ascended-caddy.service`: Caddy (`~/.local/bin/caddy`, `deploy/Caddyfile`)
  serves **https://192.168.1.97:8099** with its internal CA and forwards to 8098.
  Browsers warn until `~/.local/share/caddy/pki/authorities/local/root.crt` is trusted
  (pytincture's service worker also needs that). Sign in as admin / `CA_ADMIN_PASSWORD`.
- All three maps are **adopted**: the app's containers (`--restart no`, labels
  `io.clustersascended.*`). The compose containers are kept as `asa-*.compose-old`;
  rollback is per map on its page. `asa-webadmin.service` is disabled, not deleted.
- **Never run `podman-compose up` in ~/asa-cluster any more**: it would recreate the
  containers behind the app's back.
- Dev copy: `systemctl --user stop clusters-ascended` first (same port and database),
  then `scripts/dev.sh start`, which needs a loopback `.env` (comment out the two CA_
  origin lines). Restart the service afterwards.

## Design rules

- **The cluster is the foundational object.** Host → clusters → servers
  (maps). `models.effective(server, cluster)` merges cluster defaults and
  server overrides; it is the only input for running a map. Mods append to
  the cluster's list unless `mods_mode: replace` (ASA cares about mod order).
- **Every start goes through `engine/supervisor.enqueue`**, which runs
  admission (`engine/admission.py`, pure) against the projected queue, and
  `lifecycle.start` runs it again on a fresh snapshot right before
  `podman start`. Only `engine/lifecycle.py` starts or stops containers
  (tests/test_static.py enforces it).
- **No hard memory caps on containers.** An 18 GiB cgroup cap OOM-killed Lost
  Colony while it loaded (Calagopus trial, 2026-11-11). Admission control
  instead.
- **Sequential gate:** the `lifecycle` lane runs one job at a time host-wide,
  and a start job ends only after: "advertising for join" stamped after this
  start (ShooterGame.log, UTC stamps) → RCON `ListPlayers` answers → settle.
  The old updater matched a *previous* run's line; `logtail.scan(since=)`
  exists to prevent that.
- Stops run on their own `stop` lane and abort a start still loading the
  same map (`supervisor.abort_flag`).
- **Two needs per map** (`memlearn.needs`): running need = margin × the highest recent
  *running* sample (settled after the start, peak after settle, or the log's ready reading;
  `default_need` until measured); startup need = max(startup_factor × running need,
  margin × measured startup peak) — a start is always assumed to take more than running.
  A loading map holds its startup need in admission; it drops to its running need only once
  fully started (`admission.hold`).
- **The start queue** (2026-11-11): starts are not refused for memory at enqueue (only one that
  could never fit with every map stopped: `admission.never_fits`). Each start job, in turn:
  waits for memory (re-check every `memory_recheck_s`, give up after `memory_wait_s` → refused)
  → prepares (container, ini) → loads → verifies (RCON) → settles → measures (records
  `peak_start` and `settled` on the run) → done; then the next one starts. A failed load stops
  the map and retries up to `start_attempts`. `jobs.stage/detail/attempt` track it for the page.
  `supervisor.cancel` drops a queued job or stops a running start (the map is stopped again).

## Game settings and mods (2026-11-11)

- `services/ark_settings.py` (pure, shared with the page) is the catalogue: key, file
  (GUS `[ServerSettings]` / Game.ini `[/Script/ShooterGame.ShooterGameMode]`), group, kind,
  official default, slider range, plain-language label and help, plus presets.
- **The app writes the ini right before a map starts** (`lifecycle.start` →
  `game_settings.apply`): the container is stopped, so ASA's rewrite of GameUserSettings.ini
  on shutdown cannot undo it. Effective = official ← `cluster.game` ← map `overrides["game"]`.
- **Rules of the write:** a key already in the file is rewritten in place (left byte for byte
  when its value is equal); a missing key is added only when it differs from official;
  **nothing is deleted** (wiki defaults are not trusted for ASA); every other line, CRLF/LF and
  repeated keys stay. Section/key matching is case-insensitive (`engine/ark_ini.py`). Files are
  read as bytes (`read_text` turns CRLF into LF) and written through `podman unshare` as 7777.
  Previous files go to `data/ini-backups/<server>/` (last 10).
- `cluster.game is None` means never managed: nothing is written until the user imports
  ("Bring in current settings": equal values → cluster, differing → per map) or saves.
- POK only touches `ServerPassword` and `[MessageOfTheDay]` in GUS at start; SessionName, admin
  password and RCON come from its command line; MaxPlayers is `-WinLiveMaxPlayers`.
- Mods: CurseForge ids in load order; friendly names in the `mods` table (typed by the user).
  A CurseForge API key (search, pictures) is a later option.
- The "wild level" figure uses the wiki's ASE formula (30 × (offset × (difficulty − 0.5) + 0.5)):
  shown as "about"; unverified for ASA.

## Layout

```
service.py                 pytincture wiring; starts the engine once (CA_ENGINE=off skips it)
appcode/clusters_ascended.py   the page: toolbar, hash routes, CSS
appcode/ui/                browser views: dashboard, jobs, host, import_view, common
appcode/services/          BFFs (*_api.py; engine imported inside methods), auth, config,
                           login_page, local_login, fmt (shared, stdlib only)
appcode/engine/            server only: models, admission, memlearn, meminfo, logtail, rcon,
                           podman, store (SQLite), state, poller, supervisor, lifecycle,
                           compose_import, importer, boot, paths
tests/                     unit + orchestrator tests (fake podman in tests/fakes) + static guards
tests/smoke/               Playwright against the running app
```

State: `data/` (git-ignored): `clusters.db`, `config-export.json`, `jobs/<id>.log`,
`import/` (copies of imported compose files), `session.key`.

## pytincture / wapyt traps (from the sibling apps)

1. `APP_ENTRYPOINT` is mandatory (MainWindow detection only knows dhxpyt → HTTP 422).
2. BFF modules are re-executed on every call: threads, caches and state live in `engine/`.
3. Build UI in `load_ui()`, never `__init__`.
4. The browser calls `*_async`; BFF methods are sync `def`s.
5. Hooks are dotted paths in `PytinctureConfig(environment=...)`; custom claims go in
   `AUTH_SESSION_CLAIM_KEYS`.
6. Absolute imports only; the page imports *names* (`from services.x import Y`).
7. A syntax error or missing module in a browser module does not fail the server: the
   page hangs. `tests/test_static.py` compiles everything and checks imports resolve.
8. DOM misses are `JsNull` (falsy, not `None`).
9. **Plain HTTP with sign-in works only on a literal loopback origin.** pytincture refuses
   `http://<lan-ip>` when auth is on ("Production authentication requires an HTTPS
   canonical origin"). LAN access needs HTTPS in front (or a no-auth mode; not built).
10. No CDN (CSP `'self'`); the browser installs wapyt from `appcode/wapyt-99.99.99-*.whl`
    built by `../wa_pytincture_widgetset/scripts/dev_wheel.sh appcode` (needs a
    Python ≥ 3.13 with pip as `PYTHON=`).
11. Never `pkill -f "python service.py"`: use `scripts/dev.sh stop`.

## Host facts (2026-11-11)

31 GiB RAM, 34 GiB swap (`/swapfile2` 32 GiB + 2 GiB), swappiness 60, cgroup v2.
Readiness readings from old logs: Lost Colony 16.91 GB, Astraeos 18.99 GB, Genesis 1
10.35 GB. Podman 4.9.3; Podman Desktop can wedge `podman ps` (every call has a timeout,
three timeouts mark podman degraded).

## Roadmap

1. ✅ Foundation: import, dashboard with memory bar and admission previews, queued
   start/stop/restart/make-room with the sequential gate on the existing compose
   containers, jobs with logs, host rules.
2. ✅ Ownership + safety: app-created containers (spec builder, `--restart=no`, adopt/rollback),
   cluster and map editing, mods editor, watchdog, boot autostart, crashloop guard,
   sequential updates with pre-update backups, world backups. Cutover done 2026-11-11.
3. Live ops: ✅ game settings (sliders, presets, per-map overrides) and named mods; still to do:
   console (log tail + RCON), players, add-map wizard.
4. World backups and restore; files.
5. Schedules (restarts with warnings, backups, update checks), memory history charts.

