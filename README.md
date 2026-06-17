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

## What's an MCP — and why use one?

**MCP (Model Context Protocol)** is a standard way to give an AI assistant a new,
trusted skill. Instead of pasting a spreadsheet into a chat and hoping the model
reads it right, you hand it a set of well-defined tools it can call — with the
domain rules already baked in. It's the difference between telling an analyst
*"here's a spreadsheet, good luck"* and hiring one who already **knows the data cold**.

**Why not just ChatGPT + a CSV?** Public data is messy in ways a generic chatbot
can't see. Ask a raw LLM *"what's NYC's biggest library project?"* and it'll happily
double-count a budget line shared by several projects, call a long-finished branch
*"still under construction"* because its funding line is still open, or — seeing only
three reporting periods a year (Jan / May / Sep) — assume months of data have gone
missing. It sounds confident — and it's wrong. This server encodes the guardrails
once — the **PID↔FMS many-to-many**, **role-aware agency attribution**, the
**3×-a-year reporting cadence**, **signed reporting** — so every answer is consistent,
sourced, and reproducible.

## What one prompt can build

This isn't only a query tool. Point an AI agent at it and **a single prompt produces a
polished, self-contained interactive HTML report** — with the domain rules already applied.
Three real examples (one prompt → one file; click to open the live report):

**1 · PID ↔ FMS topology** — *the many-to-many anatomy of the portfolio*

> Analyze the PID↔FMS many-to-many relationship across NYC capital projects and build a
> single interactive HTML report — the 1:1-vs-fan-out split, the outlier extremes, a
> per-agency breakdown, and budget concentration.

▶ **[Open the report](https://raw.githack.com/WillHsiaoNYC/NYC-Opendata-Capital-Projects-MCP/main/docs/examples/pid_fms_budget_analysis.html)** — fan-out rings, a bipartite diagram, the "tangled few" outliers (hover to see the real PIDs and FMS lines), an agency scatter, and a budget concentration curve.

**2 · Parks projects over $50M** — *every big build, and what funds it*

> Build an interactive one-file HTML report on NYC Parks projects over $50M. For each
> budget line, show every schedule associated with it, with phase and forecast completion.

▶ **[Open the report](https://raw.githack.com/WillHsiaoNYC/NYC-Opendata-Capital-Projects-MCP/main/docs/examples/parks_over_50m.html)** — 23 budget lines; hover any to reveal its linked schedules. Quietly applies the category taxonomy, so the $1.9B "Park Pedestrian Bridges" route to Bridges, not Parks.

**3 · Budget & schedule change monitor** — *what moved this period, by agency*

> Build an interactive one-file HTML monitor of NYC capital projects' budget and schedule
> changes by managing agency, with a click-through detail view for each project's schedule
> and budget history.

▶ **[Open the report](https://raw.githack.com/WillHsiaoNYC/NYC-Opendata-Capital-Projects-MCP/main/docs/examples/cpd_budget_schedule_change_monitor.html)** — KPIs, a trend chart, a sortable watchlist, and a per-project popup with schedule-variance bars and a stacked budget-vs-spend chart.

> Each report was generated from the prompt shown, then lightly polished. The figures are a
> snapshot of reporting period 202601 (links render via [raw.githack](https://raw.githack.com)).

## 🚀 Quick Start

Want the data without the setup? If your AI can run commands on your computer,
just ask it to install everything for you.

### ✅ Let your AI install it (easiest)

Works with AI **agents that can run terminal commands** — **Claude Code**,
**Claude cowork** (Claude Desktop's local-agent mode), **Codex CLI**, or another
coding agent like Cursor.

1. Start your AI agent on this computer.
2. Paste the message below.
3. Approve each step (**Allow**, or press **y**) as it clones, installs, and
   connects the server.

**Message to paste:**

```
Install the MCP server at
https://github.com/WillHsiaoNYC/NYC-Opendata-Capital-Projects-MCP on this
machine — follow its README to clone the repo, install it with uv, run
`od-cpd init` to download the four NYC Open Data datasets into a local
database, and wire it into my MCP client config. Then run a verification
query to confirm it works.
```

**What "done" looks like:** your AI reports the loaded reporting period (e.g.
`202601`), confirms `od-cpd` is connected with its **16 tools**, and answers a
test question like *"What's the biggest NYC capital project right now?"* Takes a
few minutes, mostly the dataset download.

### 🖥️ Claude Desktop (chat)

Claude Desktop can **use** a local server but can't install one itself. Run the
[Manual install](#manual-install), then add `od-cpd` to **its own** config (with
the absolute path to `uv`) and fully restart — see
[Connect an MCP client](#connect-an-mcp-client).

### ☁️ claude.ai or ChatGPT (web)

These connect only to **remote** MCP servers, not a local one like this — so they
can't run `od-cpd` directly. Use one of the options above.

## Manual install

Requires [Python ≥ 3.12](https://www.python.org/) and
[uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/WillHsiaoNYC/NYC-Opendata-Capital-Projects-MCP.git
cd NYC-Opendata-Capital-Projects-MCP

uv sync
uv run od-cpd init        # download + materialize all 4 datasets → ./var/cpd.duckdb
uv run od-cpd status      # confirm the loaded reporting period
```

Optional: set `OD_CPD_SOCRATA_APP_TOKEN` to a free
[Socrata app token](https://dev.socrata.com/docs/app-tokens) to avoid
anonymous rate limits during ingest.

### Connect an MCP client

The server speaks stdio. Use the **absolute path to `uv`** (run `which uv` to
find it) — GUI apps like Claude Desktop don't inherit your shell `PATH`, so a
bare `uv` command fails silently.

**Claude Code** — from inside the repo folder:

```bash
claude mcp add od-cpd --env PYTHONPATH="$(pwd)/src" -- \
  "$(which uv)" run --directory "$(pwd)" od-cpd-server
```

**Claude Desktop** — edit its config (macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`), add the block
below, then **fully quit (⌘Q) and reopen** Claude Desktop:

```json
{
  "mcpServers": {
    "od-cpd": {
      "command": "/absolute/path/to/uv",
      "args": ["run", "--directory", "/absolute/path/to/repo", "od-cpd-server"],
      "env": { "PYTHONPATH": "/absolute/path/to/repo/src" }
    }
  }
}
```

(`PYTHONPATH` keeps the launch robust when uv's editable install is flaky — e.g.
on iCloud-synced paths.)

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
