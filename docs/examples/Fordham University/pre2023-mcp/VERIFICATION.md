# Release verification — code-only 1.1.0

Verified locally on November 9, 2026. The workbook was supplied separately and was not included in the ZIP.

## Executed checks

- macOS (Apple silicon), Python 3.12.13: **30 synthetic regression tests passed**. Fixtures generate fictional workbooks spanning all 24 reporting periods; no actual project records are distributed as fixtures.
- Covered original-file preservation, schema/hash/key/population validation, incomplete-data rejection before persistent side effects, existing empty/user-data directory preservation, verified import reuse, interrupted publication cleanup, same-destination concurrency, readiness enforcement, private absolute-path configuration, source fidelity, date conventions, financial distinctions, pagination, reference joins and read-only SQL.
- An independent verification agent extracted the candidate ZIP into a fresh temporary folder with spaces and installed it with `uv sync --locked --no-dev --python 3.12`.
- Local setup using the separately stored course workbook, integrity verification, generated configuration and a real stdio MCP exchange passed. The exact eight tools were discovered and each was called.
- Counts matched: **7,230 observations, 1,012 legacy PIDs, 24 reporting periods and 410 final-snapshot rows**.
- All column names and ordered values matched the private baseline across `observations`, `raw_observations`, `fms_references`, `reporting_periods`, `project_history` and `latest_known`. This checks all 40 source fields and retained derived behavior, rather than counts alone. SQLite file bytes may differ while table values are identical.
- The source Excel SHA-256 was unchanged before/after setup. Repeated setup reported verified reuse and preserved database hash and modification time. A wrong workbook was rejected; a user-data sentinel was preserved; missing-import errors were actionable.
- Configuration retained the absolute virtual-environment interpreter and included the explicit private `CPD_PRE2023_DATA_DIR` value. No client settings were modified.
- ZIP checksum, CRC and explicit-file audit passed: no Excel, SQLite, data export, private import manifest, real project-record fixture, environment or cache was included.
- No dependency/build warnings appeared. The stdio server's normal tool-request diagnostics were retained separately as verification evidence.
- Temporary verification code, environment and private import were removed after evidence capture. The current CPD MCP and original bundled release were not modified.

## Final archive and platform scope

This verification document and the README's public installation-page link were finalized after the independently tested ZIP, before initial public distribution. The public ZIP was rebuilt and checked so every other member, including all runtime code, dependencies, resources, agent prompt and synthetic tests, is byte-identical to the tested candidate. Its adjacent `.zip.sha256` file supplies the final artifact digest.

Windows commands and portable path handling are supplied, but **Windows execution is not tested**. Python 3.11 is permitted by package metadata but is not claimed as tested. No student's or maintainer's desktop-client registration was changed or verified: a real stdio exchange establishes protocol behavior, and each student's client still requires the README's connection check after registration/reload.

The Fordham landing/installation pages are published separately from this ZIP. No Excel or private database is published with either. Private verification logs are retained outside the public code package and contain no printed project records.
