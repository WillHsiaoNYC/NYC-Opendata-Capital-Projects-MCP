# NYC Open Data — Capital Projects MCP Server

A local [MCP](https://modelcontextprotocol.io) server over the NYC Capital
Projects Dashboard (CPD) datasets on [NYC Open Data](https://opendata.cityofnewyork.us/).
It ingests four public Socrata datasets into a single local DuckDB and exposes
**16 tools** so an AI assistant can answer schedule, budget, and lifecycle
questions about NYC capital projects — with the domain rules (PID↔FMS
many-to-many, role-aware agency attribution, signed variance reporting) baked
into the tools instead of left for the caller to rediscover.

## Source datasets (Socrata)

| ID | Dataset |
|----|---------|
| `fb86-vt7u` | Citywide Capital Project List Detail (the schedule↔budget edge) |
| `gyhf-rsr3` | Citywide Budget & Spend by FY |
| `qj5n-h5qp` | Citywide Budget Spend History & Variance |
| `95tx-snak` | Citywide Schedule History & Variance |

## Quickstart

Requires Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run od-cpd init        # download + materialize all 4 datasets → ./var/cpd.duckdb
uv run od-cpd status      # per-dataset freshness vs Socrata
uv run od-cpd update      # re-ingest when Socrata publishes a new period
```

Optional: set `OD_CPD_SOCRATA_APP_TOKEN` to a free
[Socrata app token](https://dev.socrata.com/docs/app-tokens) to avoid
anonymous rate limits during ingest.

### Keeping data fresh

The source datasets report on a **Jan / May / Sep** cycle, and Socrata
typically publishes each period **~2.5–3 months later** (so new data usually
lands around **April, August, and December**). `od-cpd update` is a no-op when
nothing is newer, so it's safe to run any time — check around those months:

```bash
uv run od-cpd status      # what period is loaded now
uv run od-cpd update      # re-ingest only if Socrata is newer
```

To keep it fresh automatically, schedule `update` (e.g. monthly via cron):

```cron
# 9am on the 1st of each month
0 9 1 * * cd /path/to/repo && uv run od-cpd update
```

### Connect an MCP client

The server speaks stdio. With Claude Code:

```bash
claude mcp add od-cpd --env PYTHONPATH=/path/to/repo/src -- \
  uv run --directory /path/to/repo od-cpd-server
```

Or in any client's JSON config:

```json
{
  "mcpServers": {
    "od-cpd": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/repo", "od-cpd-server"],
      "env": { "PYTHONPATH": "/path/to/repo/src" }
    }
  }
}
```

(`PYTHONPATH` makes the launch robust when uv's editable install is flaky —
e.g. on iCloud-synced paths.)

## What's inside

- **`docs/FEATURES.md`** — the canonical inventory: all 16 tools and every
  domain rule the server encodes. Start here.

The headline domain rules, briefly:

- **"Project" is ambiguous.** A PID identifies a *schedule*; an FMS ID
  identifies a *budget line*. They are many-to-many (~3% fan out), so the
  tools list all counterparts rather than silently picking one.
- **Agency attribution is role-aware.** "Agency X's projects" means the
  sponsor (owner) view for normal agencies, but the managing (builder) view
  for the three construction-manager agencies (DDC/DCAS/EDC).
- **Values are reported signed and neutral** ("moved 45 days later",
  "budget grew $2.1M") rather than only surfacing one direction.

## Layout

- `src/od_cpd/` — ingest, materialization, and the MCP server + tools
- `data/` — curated agency/category dictionaries (YAML, tracked)
- `tests/` — unit tests + golden evals (`uv run pytest`)
- `var/`, `exports/` — runtime DuckDB + exports (gitignored, regenerable)

## Develop

```bash
uv run pytest             # fallback: PYTHONPATH=src python -m pytest
```

Classification is dictionary-driven: edit `data/agencies.yaml` /
`data/categories.yaml` (not Python) to adjust agency or category mappings,
then re-materialize. See `CLAUDE.md` for the atomic-swap pattern that applies
materialization changes without re-downloading.

## Data caveats

This is an independent project, not affiliated with the City of New York.
Figures reflect whatever reporting period the underlying Socrata datasets
carry at ingest time; always check `dataset_info` for the current period and
per-dataset caveats.
