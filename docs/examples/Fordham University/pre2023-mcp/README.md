# Historical Capital Projects MCP — code-only v1.1.0

**Save the course Excel locally, then ask your agent to install the MCP.** This ZIP contains software, the importer, field definitions and synthetic tests. It contains no Excel workbook, prepared database, project-record export or real-data test fixture. Obtain the unchanged historical Excel export separately from your instructor.

The approved workbook covers September 2013–January 2023: **7,230 observations, 1,012 legacy PIDs, 24 irregular reporting periods**, and 410 projects in the final snapshot. These are expected validation counts, not data included with the software.

This is separate from the current CPD MCP. Legacy and modern PIDs are different identifier systems; matching numbers may refer to unrelated projects. Never join them automatically.

## Start here: let your agent install it

1. Save the instructor-supplied historical workbook on your computer. Keep the original file unchanged; the filename may vary, but its content must match the approved export. The 2025 public-building and infrastructure masterlists are different files.
2. Extract this code-only ZIP to a permanent folder. You can give your agent this folder, or, after publication, the URL of the course installation page.
3. Copy **[INSTALL_PROMPT.txt](INSTALL_PROMPT.txt)** into a terminal-enabled AI agent. It asks for your local workbook path and intended MCP client when those are not already known. You do not need to upload the workbook or attach it to chat.

The agent should create a local environment, run the documented setup command, merge only this MCP's client entry, and verify the connection. A chat-only session cannot install software. Initial installation needs internet for Python/dependencies; imports and normal MCP queries make no network requests.

**Public installation page:** [Install the historical MCP with your local Excel](https://willhsiaonyc.github.io/NYC-Opendata-Capital-Projects-MCP/examples/Fordham%20University/pre2023-mcp/). It includes the versioned code-only ZIP, SHA-256 checksum and a copyable agent installation prompt. You can also install directly from this extracted folder. The workbook is supplied separately and is never part of the public download.

## Prerequisites

- Python 3.11 or newer; Python 3.12 is the recommended tested installation choice. See [VERIFICATION.md](VERIFICATION.md) for executed platform evidence.
- `uv`, installed using its official instructions if needed: <https://docs.astral.sh/uv/getting-started/installation/>.
- A client that supports local stdio MCP servers. Identify the actual client before changing any settings.
- The exact instructor-supplied historical `.xlsx` export. Expected worksheet: `Data Export`; 40 ordered columns. The approved SHA-256 is public validation metadata in `src/cpd_pre2023/resources/course.json`.

## Installation commands

From the extracted code folder containing this README:

```text
uv sync --locked --no-dev --python 3.12
```

This creates `.venv` inside the code folder. Do not install globally or modify another MCP's environment.

On **macOS / Linux**, replace the example paths with full local paths:

```sh
./.venv/bin/python -m cpd_pre2023.cli setup --workbook "/FULL/PATH/course.xlsx" --data-dir "/FULL/PATH/private-cpd-pre2023"
./.venv/bin/python -m cpd_pre2023.cli verify --data-dir "/FULL/PATH/private-cpd-pre2023"
./.venv/bin/python -m cpd_pre2023.cli smoke-test --data-dir "/FULL/PATH/private-cpd-pre2023"
./.venv/bin/python -m cpd_pre2023.cli config --data-dir "/FULL/PATH/private-cpd-pre2023"
```

On **Windows PowerShell**:

```powershell
& .\.venv\Scripts\python.exe -m cpd_pre2023.cli setup --workbook "C:\FULL\PATH\course.xlsx" --data-dir "C:\FULL\PATH\private-cpd-pre2023"
& .\.venv\Scripts\python.exe -m cpd_pre2023.cli verify --data-dir "C:\FULL\PATH\private-cpd-pre2023"
& .\.venv\Scripts\python.exe -m cpd_pre2023.cli smoke-test --data-dir "C:\FULL\PATH\private-cpd-pre2023"
& .\.venv\Scripts\python.exe -m cpd_pre2023.cli config --data-dir "C:\FULL\PATH\private-cpd-pre2023"
```

No shell activation is required. Windows commands are supplied, but Windows execution is not claimed as tested.

`--data-dir` is optional. Its default is a private per-user `cpd-pre2023/course-1` folder under macOS Application Support, Windows LOCALAPPDATA, or Linux XDG_DATA_HOME (falling back to `~/.local/share`). The code folder is prohibited as a data destination. Prefer an explicit private absolute path when an agent configures the client. The `CPD_PRE2023_DATA_DIR` environment variable selects the same folder for the server and all CLI commands.

## What setup does — and preserves

- Validates the workbook's SHA-256, worksheet, exact column order, complete populations, periods and unique PID-period keys before creating import files. Resaved or edited workbooks are rejected even if their filenames match. Obtain the unchanged course file; do not bypass the check or update its expected hash to force a pass.
- Reads the Excel without modifying, renaming, moving or copying it into the package. All 40 original cell values are retained in the private database's raw table; normalization and quality flags follow the dictionary.
- Builds and validates a staged SQLite database. A per-destination setup lock allows one importer. Existing data folders, including empty or incomplete folders, are never overwritten. A complete matching import is verified and reused.
- Publishes the verified database and then its ready manifest. Every database-opening callee rejects missing, incomplete or incompatible imports. The server never imports, refreshes or writes data during a query.
- Stores only `historical.sqlite` and `manifest.json` in the selected private data folder. The manifest records the original local workbook path, hash, dataset counts, code fingerprint and verification metadata. It contains no copied workbook.

After a successful import, queries do not require the Excel to remain accessible. `verify` checks its hash when the recorded file is present and reports when it is unavailable. Changing the original Excel does not refresh the database automatically. Preserve that original for reproducibility.

## Register your MCP client

`config` prints the actual environment interpreter path and arguments, with the private data folder explicitly included:

```json
{
  "mcpServers": {
    "cpd-pre2023": {
      "command": "/PERMANENT/CODE/FOLDER/.venv/bin/python",
      "args": ["-m", "cpd_pre2023.server"],
      "env": {"CPD_PRE2023_DATA_DIR": "/PRIVATE/DATA/FOLDER"}
    }
  }
}
```

Use the generated paths. Keep the virtual-environment interpreter path; do not resolve its symlink to a global Python. The configuration works without relying on a terminal working directory or PATH. An installed client may use TOML, JSON or a registration command: inspect its supported mechanism and translate this one entry accordingly. Back up a configuration before a manual merge. Preserve all unrelated settings and the current CPD MCP; inspect an existing `cpd-pre2023` entry before changing it.

The software prints configuration; it does not edit a student's client settings itself. `smoke-test` runs a real stdio MCP exchange and calls all eight tools, but **does not prove the student's desktop client loaded this server**. After any required reload, ask the agent:

> Using MCP pre-2023 only, call `pre2023_dataset_info`. Confirm 7,230 observations, 1,012 legacy PIDs and 24 reporting periods from September 2013 through January 2023. Explain what one row represents.

Report terminal and client checks separately. If a restart, permission or manual step remains, state it clearly.

## Analysis tools and rules

The eight tools remain `pre2023_dataset_info`, `pre2023_data_dictionary`, `pre2023_describe_table`, `pre2023_search_projects`, `pre2023_project_history`, `pre2023_compare_periods`, `pre2023_portfolio_summary`, and `pre2023_run_sql`.

Read [DATA_DICTIONARY.md](DATA_DICTIONARY.md) for all 40 fields and derived measures. A row is **legacy PID × reporting period**. FMS cells are references, not recoverable line allocations; reported PID sums are not proven unique funding totals. Never sum repeated snapshots as separate projects. Missing is not zero.

Search defaults to each PID's latest-known observation; pass `period="202301"` for the final snapshot. History preserves all available observations and gaps without carry-forward. Follow `next_offset` for complete paginated results. SQL is SQLite, not DuckDB: read-only SELECT/CTE queries, five-second budget and at most 500 rows per page. Choose the period and grain and prevent reference joins from multiplying money.

Period forecast change is current forecast minus the preceding available observation's forecast; missing operands remain NULL. Reported legacy cost variance is a different source measure. Its component-level baseline interpretation is inferred, not verified, and does not replace reported values. Preliminary budgets may be revised. Original amount remains audit-only and hidden from default history output.

Mixed observed milestones on or before the 15th of their reporting month are classified actual; later dates are forecasts. This is the owner-agreed convention, not an exported actual-date flag. Plan dates remain plans. Invalid dates, reversed intervals and unusual values remain visible in raw values and quality flags. Status and phase remain independent; substantial completion is separate from close-out.

The importer and server do not upload files. Information returned to an AI agent is handled under that client's/model provider's settings. No workbook, database or export belongs in a public repository, code release or installation log.

## Troubleshooting and updates

- **Workbook rejected:** check that it is the original historical course export, not a 2025 masterlist or a resaved copy. The instructor must intentionally approve any new dataset version.
- **Data folder exists but setup refuses it:** preserve the folder. Choose a new private data folder. Setup never repairs or replaces existing user data automatically.
- **Setup lock exists:** another setup may be running. Do not delete an active lock. If a previous process crashed, confirm that process has exited before removing only its named lock.
- **No completed local import / incompatible import:** run setup into a new private data folder and generate config for that folder. New code with a changed import fingerprint requires a new import; old data remains intact.
- **MCP absent in client:** confirm the generated absolute interpreter and data paths and the required reload. A successful CLI test alone is insufficient.
- **Code folder moved:** recreate its environment with locked dependencies, regenerate config and update only this server entry. Keep the private data folder and original workbook.

To update, extract new code into a new permanent folder, verify it with a new private import as required, then switch only `cpd-pre2023`. To uninstall, remove only its client entry and dedicated code folder; preserve course Excel, analysis files and private data unless the student explicitly wants them removed. Leave shared Python/uv and other MCPs alone.

## Maintainers

```text
uv sync --locked --python 3.12
uv run --locked pytest
uv run --locked python scripts/package_bundle.py
```

The ZIP builder uses an explicit allowlist. It does not collect the working tree recursively. Public tests generate fictional workbooks temporarily; no real project records or course database are fixtures. Perform private full-workbook fidelity checks, then verify a fresh ZIP extraction with `uv sync --locked --no-dev --python 3.12`, local setup, integrity checks, absolute-path configuration and all eight stdio tool calls. Record actual platform coverage. Never include private verification records or data in the release.
