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
- **How a stop works (2026-11-13):** POK's SIGTERM handler can't verify `-usestore` saves
  ("No unambiguous main .ark file") and then holds PID 1 for the whole grace period (Lost
  Colony sat 300 s, then SIGKILL). So `lifecycle.stop` does its own: RCON SaveWorld → wait
  for the file → touch `/home/pok/shutdown.flag` (otherwise POK's monitor restarts a game
  that exits) → RCON `DoExit` → wait up to `exit_wait_s` (90) for `pgrep ArkAscendedServer.exe`
  to come back empty → `podman stop -t 10`. The long `stop_timeout_s` grace is used only when
  the save could not be confirmed.
- **Update all** (`update_cluster` job, lifecycle lane, `server` NULL, params carry the
  cluster and its map ids): if any map is behind, save and stop every running map in parallel,
  set them `desired=stopped`, then `update()` each map that is behind, one at a time. Nothing
  is started again afterwards. Clears each map's stale abort flag before its boot. Update
  boots skip the settle wait.
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
- **Per-item stack sizes** (`cluster.stacks`, `services/ark_items.py`, `ui/stacks_card.py`): Game.ini
  `ConfigOverrideItemMaxQuantity=(ItemClassString="…_C",Quantity=(MaxItemQuantity=N,bIgnoreMultiplier=true))`,
  one line per item (the number is the exact stack). `None` = the app leaves those lines alone;
  once saved the app owns all of them (`ark_ini.replace_all`). Items that stack to 1 (prime meat,
  mutton, fertilizer) ignore the global multiplier. Cryopods are left out (stacking breaks them);
  mutton overrides reportedly may not apply on servers; fertilizer is used a whole stack at a time.
  Class names and ASA defaults were checked against the ARK wiki and wikily.gg (2026-11-11).
- The "wild level" figure (2026-11-19, ark.wiki.gg/wiki/Difficulty + ASA players' reports): when
  `OverrideOfficialDifficulty` > 0 it *is* the difficulty (level 30 × it) and `DifficultyOffset` is
  ignored; at 0, difficulty = offset × (4 − 0.5) + 0.5. Common creatures only (wyverns etc. have 38 steps).

## Backups, restore and schedules (2026-11-11)

- `engine/backups.py`: tar + zstd of `SavedArks/<MAP>` + `Config` under `podman unshare`,
  verified (`zstd -t`), into `<cluster root>/Backups/<server>/`. **ASA's own history is left
  out** (`*.arkrbf` every ~15 min, date-stamped `<MAP>_DD.MM.YYYY_HH.MM.SS.ark` every ~2 h,
  `*_AntiCorruptionBackup.bak`): ASA rotates them while it runs, which failed tar ("File removed
  before we read it"). The live `<MAP>.ark` (~95 MB → ~12 MB compressed) plus tribe files is
  what a restore needs (with -usestore the survivors are in the .ark).
- **ASA answers "World Saved" about a second before the file is written.** `save_world` waits
  until the world file is newer than the request, then for the folder to be quiet, and tar
  retries if a file changed while it read (exit 1).
- Pruning per kind (`KEEP`; nightly keep comes from the schedule), pinned never pruned.
- Restore (`lifecycle.restore`, lifecycle lane): save+stop if running → pre-restore backup →
  `restore_files` (archive checked: only this map's world/Config, no `..`/absolute) moves the
  current world to `Saved/.pre-restore-<ts>` (newest one kept) and unpacks → start again.
  The app's game settings are rewritten into the restored ini at the next start, as always.
- `engine/scheduler.py` (ticked by the poller): per cluster a nightly backup (default on,
  04:00, keep 7, stopped maps unchanged since their last backup skipped) and a daily restart
  (default off, 05:00, chat countdown 15/5/1 → `warned_restart` jobs through the start queue).
  A restart missed by over an hour is skipped; a missed backup runs late.

## Players and console (2026-11-11)

- `engine/players.py`: the poller's 30 s `ListPlayers` feeds `player_sessions` (join/leave;
  a silent RCON is "no change", a stopped map closes all). The old webadmin's
  `<root>/webadmin/var/logins.jsonl` is imported once at boot (source `webadmin`).
- Bans belong to the cluster (`bans` table): `BanPlayer` to every running map now, and to a
  stopped map right after RCON answers on its next start (`lifecycle._apply_bans`); `applied`
  lists the maps that got it. Unban sends `UnbanPlayer` to running maps only (a stopped map
  keeps ASA's own ban until it is unbanned while running).
- Messages: `ServerChat` (chat) or `Broadcast` (big on-screen), to a map or the whole cluster.
- `engine/console.py`: the page polls ShooterGame.log by (inode, offset) every 2 s; a new inode
  (ASA starts a new log each boot) starts over. Commands go over RCON; replies show in blue.
- Map page tabs: Overview · Players · Console · Settings · Backups (`#/map/<id>/<tab>`).

## Adding a map (2026-11-11)

- Wizard `#/cluster/<id>/add` (`ui/add_map.py`) → `supervisor.enqueue_add_map` →
  `lifecycle.add_map` (lifecycle lane). **Only with every map on the host stopped and no other
  lifecycle/stop job** (`addmap.busy_maps`), checked at enqueue and again in the job; the wizard
  offers "Save and stop every map".
- `engine/addmap.py`: suggestions (next session name in the cluster's pattern, next free ports
  host-wide + `ss`, last priority, installs to copy), `plan` (map not already in the cluster,
  ports free, target folders absent or empty, disk space = source size + 5 GiB).
- Layout like the cluster's: `<root>/ServerFiles_<Folder>/arkserver`, `<root>/Instance_<Folder>/Saved`
  (`services/ark_maps.py` names the folders), made and owned 7777 inside `podman unshare`.
  Install by `podman unshare cp -a` of an existing map's install (progress in the job's stage),
  or "download": an update-pass boot (UPDATE_SERVER=TRUE) on an empty install, then stop —
  **unverified against POK** (copy is the default).
- New map: cluster settings and mods (inherit), autostart off, last priority, container created;
  optional start through the queue. Any failure removes the server row, its container and the
  folders it made.

## Several clusters (2026-11-18)

- **New cluster** (`#/new-cluster`, `ui/new_cluster.py` → `engine/clusters.py`): name → id, ClusterId,
  `~/asa-clusters/<id>` with `Cluster/` (owned 7777). Starts empty with `game={}`; the wizard adds maps.
  ClusterId and folders may not overlap another cluster's or any map's files.
- **Import never changes what is already here** (`importer._reconcile`): the same ClusterId + transfer
  folder means the same cluster, and only maps not yet known are added. A collision with anything else
  (cluster name, ClusterId, transfer folder, service id, container, port, Saved folder) refuses the whole
  import, which is written in one transaction.
- **Remove a map** (`remove_map` job, lifecycle lane, `lifecycle.remove_map`): only stopped and only the
  app's container (a compose container is refused; `.compose-old` is kept). Rows go (runs, sessions,
  backup list); files stay unless `delete_files`, and even then a folder another map or the transfer
  folder uses is kept. **Delete cluster** only when it has no maps; it removes empty folders only.

## Files tab and retiring compose containers (2026-11-19)

- `#/map/<id>/files` (`ui/files_tab.py` → `services/files_api.py` → `engine/files.py`): three roots —
  the map's Saved (world and settings), its install and the cluster's transfer folder (both read-only).
  Paths are resolved and must stay under the root (`..` and symlinks out are refused). Text files show
  (last 2 MB of a big log); binary ones are not shown.
- Editing: existing `.ini/.txt/.json/.cfg` in Saved only, map stopped, ≤ 2 MB UTF-8, safe names (the
  write goes through `podman unshare` via `game_settings._write`), refused if the file changed since it
  was opened (mtime). CRLF is kept (a textarea sends LF). The old copy goes to
  `data/file-backups/<server>/` (last 10). GameUserSettings.ini / Game.ini carry a note: the app rewrites
  the keys it manages at each start.
- `retire_old` job (map page button once the app's own container exists): removes `<name>.compose-old`,
  never a running one, touches no file. Rollback to compose is gone after it.

## Auto-update and restart after updates (2026-11-19)

- `update_cluster` now starts the maps that were running again afterwards (one at a time through
  `start`, so admission and the start gate apply); maps that were stopped stay stopped. Optional
  `warn`/`message` params count down in game chat on every running map first (`_count_down`, uses
  `WARN_UNIT`); cancelling during the countdown stops nothing and tells players it's called off.
- The dashboard's "Update all" passes no countdown (immediate), but does restart.
- Third schedule kind `update` ("Auto-update", default 06:00, off, warn 15/5/1, "Server update"):
  uses the poller's cached Steam build; if every map is on it, records "Up to date" and queues
  nothing; otherwise queues one `update_cluster` with the countdown. If the cache is empty or errored
  it queues anyway (the job checks Steam itself). Missed by >1 h → skipped, like restarts.
- The cluster tab is now "Backups & schedules".

## Memory history (2026-11-19)

- `engine/history.py`: the poller records once a minute into `mem_samples` (migration 7): each running
  map's total (anon + swap, the dashboard's figure), `_other` (host used − maps) and `_host`. 14 days
  kept, pruned hourly; removing a map drops its readings.
- Charts show the **highest** reading per slice (6 h: 2 min, 24 h: 5 min, 7 d: 30 min, 14 d: 1 h). A
  slice with no `_host` reading is a gap (app down); a map missing from a covered slice is 0.
- `#/memory` (linked from the dashboard's memory card): maps stacked + "Everything else", RAM and
  budget lines. Each map page's Overview: its own line + its running need. wapyt `Chart` (Chart.js),
  container needs `data-wapyt-theme="dark"`.
- One colour per map everywhere: `ui/common.MAP_COLOURS` (wapyt's dark palette, validated for CVD and
  contrast on #111827) in the dashboard's order (clusters, then maps); `history` sends each map's
  position as `colour`.

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
**RAM is 62.6 GiB as of 2026-11-19** (MemTotal 65613136 kB); the readings below predate that.
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
3. Live ops: ✅ game settings, named mods, players (kick, cluster bans, messages, history) and
   console, add-map wizard (2026-11-11).
4. ✅ World backups, restore, nightly backups and daily restarts (2026-11-11); files still to do.
5. ✅ Schedules (restarts with warnings, backups, auto-update with restart afterwards), ✅ memory history
   charts (2026-11-19).

