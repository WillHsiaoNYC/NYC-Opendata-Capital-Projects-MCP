# NYC Open Data — Capital Projects MCP Server

A local [MCP](https://modelcontextprotocol.io) server over the NYC Capital
Projects Dashboard (CPD) datasets on [NYC Open Data](https://opendata.cityofnewyork.us/).
It ingests four public Socrata datasets into a single local DuckDB and exposes
**18 tools** so an AI assistant can answer schedule, budget, and lifecycle
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
Four real examples (one prompt → one file; click to open the live report):

**1 · NYC capital projects overview** — *a first-timer's field guide to the dataset*

> Use this MCP to create a general overview of project schedule/budget and agency count
> for first-timer to quickly understand the coverage of the dataset, in an html file for
> presentation.

▶ **[Open the presentation](https://willhsiaonyc.github.io/NYC-Opendata-Capital-Projects-MCP/examples/capital_projects_dataset_overview.html)** — a nine-slide introduction to the dataset's schedule and budget units, reporting coverage, lifecycle mix, budget and spend, agency roles, and category coverage.

**2 · Schedule ↔ Budget topology** — *the many-to-many anatomy of the portfolio*

> Analyze the schedule and budget many-to-many relationship across NYC capital projects and
> build a single interactive HTML report — the 1:1-vs-fan-out split, the outlier extremes, a
> per-agency breakdown, and budget concentration.

▶ **[Open the report](https://willhsiaonyc.github.io/NYC-Opendata-Capital-Projects-MCP/examples/pid_fms_budget_analysis.html)** — fan-out rings, a bipartite diagram, the "tangled few" outliers (hover to see the real schedules and budget lines), an agency scatter, and a budget concentration curve.

**3 · Parks projects over $50M** — *every big build, and what funds it*

> Build an interactive one-file HTML report on NYC Parks projects over $50M. For each
> budget line, show every schedule associated with it, with phase and forecast completion.

▶ **[Open the report](https://willhsiaonyc.github.io/NYC-Opendata-Capital-Projects-MCP/examples/parks_over_50m.html)** — 23 budget lines; hover any to reveal its linked schedules. Quietly applies the category taxonomy, so the $1.9B "Park Pedestrian Bridges" route to Bridges, not Parks.

**4 · Budget & schedule change monitor** — *what moved this period, by agency*

> Build an interactive one-file HTML monitor of NYC capital projects' budget and schedule
> changes by managing agency, with a click-through detail view for each project's schedule
> and budget history.

▶ **[Open the report](https://willhsiaonyc.github.io/NYC-Opendata-Capital-Projects-MCP/examples/cpd_budget_schedule_change_monitor.html)** — KPIs, a trend chart, a sortable watchlist, and a per-project popup with schedule-variance bars and a stacked budget-vs-spend chart.

> Each report was generated from the prompt shown, then lightly polished. The overview uses
> reporting period 202605; the other three use 202601. Browse all four in the [report gallery](https://willhsiaonyc.github.io/NYC-Opendata-Capital-Projects-MCP/).

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
`od-cpd init --source opendata` to download all four datasets into a local
database, and wire it into my MCP client config. If Open Data downloads
fail, explain the GitHub snapshot fallback and ask before switching to
`--source github`. Report the source, snapshot download date when applicable,
and loaded reporting periods. Then run a verification query to confirm it works.
```

**What "done" looks like:** your AI reports the loaded reporting period (e.g.
`202605`), confirms `od-cpd` is connected with its **18 tools**, and answers a
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

uv sync --locked
uv run od-cpd init        # download + materialize all 4 datasets → ./var/cpd.duckdb
uv run od-cpd status      # confirm the loaded reporting period
```

Optional: set `OD_CPD_SOCRATA_APP_TOKEN` to a free
[Socrata app token](https://dev.socrata.com/docs/app-tokens) to avoid
anonymous rate limits during ingest.

### Choosing a download source

NYC Open Data is the default and supplies the current published files. The
installer downloads complete CSVs through Socrata's v3 export service with
bounded retries. It explicitly selects every native column as text to preserve
values; ordinary website exports can format or round currency, percentages, and
dates. Metadata validates the fields and maps headers automatically. Users and
installing agents do not need to rename columns or transform values.

```bash
uv run od-cpd init --source opendata
uv run od-cpd init --source github                   # release tag data-latest
uv run od-cpd init --source github --release TAG     # choose a dated snapshot
```

GitHub snapshots are release attachments in **this repository**, not files in
Git history. The default `data-latest` tag is a maintainer-managed data snapshot;
it does not mean GitHub's latest software release. A snapshot can be older than
Open Data. Check its download date and reporting periods before choosing it.
The GitHub path needs no live Socrata access: its manifest supplies source
metadata, field mappings, row counts, byte counts, and SHA-256 checksums. Downloads
are pinned to release asset IDs in this repository. Both sources pass schema,
completeness, and database health checks before publication. An older source
revision cannot replace newer local data, even within the same reporting period;
use a separate database to explore an older snapshot.

Downloads never silently switch sources. On an Open Data failure, the CLI exits
with a suggested GitHub command; it does not start an interactive prompt.
An installing agent should explain that fallback and obtain the user's source
choice, or honor a source choice already provided. Report the chosen source and
freshness after installation. If the requested snapshot has not been published,
the GitHub path fails clearly; adding this option does not itself publish assets.

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
lands around **April, August, and December**). Source revision times and reporting
periods are separate: check both before interpreting freshness.

```bash
uv run od-cpd status                    # local period, ingestion time and build
uv run od-cpd status --check-upstream   # verify source revisions and complete periods
uv run od-cpd update                    # check revisions; fully reload if sources changed
uv run od-cpd update --force            # fully reload even if revisions appear unchanged
uv run od-cpd update --source github    # fully reload the selected GitHub snapshot
uv run od-cpd rematerialize             # rebuild local raw data without downloading
```

Every data refresh downloads **all four datasets in full** and rebuilds the
database, including historical records. There is no incremental append: Open
Data can backfill or correct older records. The default Open Data update checks
source revisions first and skips downloading when unchanged; use `--force` to
require a full refresh regardless. A GitHub update always downloads and rebuilds
from the selected release (`--release TAG`, default `data-latest`). Refreshing a
GitHub snapshot does not make it newer than the files its maintainer published.

Refresh and rematerialization build in an isolated shadow, validate its inputs,
then atomically replace the database. Before/after health reports remain under
`var/ingest-runs/`; a failed build preserves the prior database and its diagnostics.
Reconnect MCP clients after changing server code. See `docs/FEATURES.md` for
snapshot scopes, source-coverage reconciliation and export provenance. `status`
and MCP build provenance retain the download source and, for GitHub snapshots,
the release and snapshot date, including after rematerialization.

To keep it fresh automatically, schedule `update` (e.g. monthly via cron):

```cron
# 9am on the 1st of each month
0 9 1 * * cd /path/to/repo && uv run od-cpd update
```

### Preparing GitHub snapshots (maintainers)

```bash
uv run od-cpd snapshot /path/to/new-snapshot-directory
```

The output directory must not already exist. This command downloads and
validates all four lossless CSV exports and creates `manifest.json`, without
touching the live database. Assets retain the exact bytes returned by the text
projection export. The manifest marks this format as `socrata-text-v1` and records
source URLs, metadata mappings, download dates, reporting periods, row counts,
byte counts, and SHA-256 checksums. Ordinary website exports are not substitutes
for these assets.

Publish those five files together as assets of a dated GitHub release in this
repository. Publishing is a separate maintainer action; the snapshot command
does not create or upload a release. Optionally promote a complete, validated
snapshot to the `data-latest` release. Never commit the generated CSVs to Git.
Keep dated releases available for users who pin `--release TAG`, and publish the
snapshot date and reporting periods in the release notes. Until a maintainer
publishes a complete snapshot, the GitHub fallback is unavailable.

## What's inside

- **`docs/FEATURES.md`** — the canonical inventory: all 18 tools and every
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
