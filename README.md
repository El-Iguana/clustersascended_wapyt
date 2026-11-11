# Clusters Ascended

Define ARK: Survival Ascended clusters, add maps to them, and run them on one
host without running it out of memory. Built on
[pytincture](https://pypi.org/project/pytincture/) and wapyt (Python in the
browser).

- **The cluster comes first.** A cluster carries the ClusterId, the shared
  transfer folder and the settings its maps share (mods, launch flags,
  passwords). Maps are added to a cluster; a single map is a one-map cluster.
- **Memory admission control.** Nothing starts unless the host can hold it:
  `projected = Σ needs of running maps + other use on the host + this map's
  need` must fit `RAM + swap allowance − reserve`, at most N maps run at once,
  and the bytes must physically be free. A refusal says why and offers to make
  room by saving and stopping the least important map.
- **One start at a time.** A map counts as started only once ASA advertises
  for join (a log line stamped after this start), RCON answers and a settle
  period passes. Then the next one may start.
- **Learned needs.** Each map's need is its recent peak memory (anon + swap of
  its container) × a margin, never below a floor. Imported maps are seeded
  from the "(xx GB Mem)" their old logs printed.
- **Graceful stops.** `SaveWorld` over RCON, then a long stop timeout.

## Run

```bash
uv sync --group dev
../wa_pytincture_widgetset/scripts/dev_wheel.sh appcode   # needs PYTHON=<python ≥3.13 with pip>
echo 'CA_ADMIN_PASSWORD=changeme' > .env
scripts/dev.sh start          # http://127.0.0.1:8098/clusters_ascended/
uv run pytest -q
uv run --with playwright python tests/smoke/page_smoke.py
```

Opening the app on the same machine signs you in without the form
(`CA_LOCAL_LOGIN=false` turns that off).

Status: phase 1 (import, dashboard, admission-controlled start/stop on the
existing containers, jobs). See `CLAUDE.md` for the design and the roadmap.
