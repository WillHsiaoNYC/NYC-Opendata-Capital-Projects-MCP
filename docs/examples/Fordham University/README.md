# Fordham University class reports and data tools

**[Browse class reports and data tools](https://willhsiaonyc.github.io/NYC-Opendata-Capital-Projects-MCP/examples/Fordham%20University/)** — installation instructions for the historical MCP and a dated index of class reports.

## Historical Capital Projects MCP — code-only v1.1.0

Students provide the historical Excel workbook separately and save it locally. The public package contains MCP code, the local workbook importer, field definitions and synthetic tests; it includes no Excel workbook, prepared database or project-record export. Historical and modern PID identifiers are separate systems and must not be joined automatically.

| Resource | Link |
| --- | --- |
| Student instructions and copyable agent install prompt | [Open installation page](./pre2023-mcp/) |
| Code-only package v1.1.0 | [Download ZIP](https://github.com/WillHsiaoNYC/NYC-Opendata-Capital-Projects-MCP/releases/download/cpd-pre2023-code-v1.1.0/cpd-pre2023-code-1.1.0.zip) |
| Package integrity | [SHA-256 checksum](./pre2023-mcp/cpd-pre2023-code-1.1.0.zip.sha256) |
| Complete setup instructions | [Package README](./pre2023-mcp/README.md) |
| Plain-text agent prompt | [INSTALL_PROMPT.txt](./pre2023-mcp/INSTALL_PROMPT.txt) |
| Definitions and verification | [Data dictionary](./pre2023-mcp/DATA_DICTIONARY.md) · [Verification notes](./pre2023-mcp/VERIFICATION.md) |

The importer validates the unchanged instructor-supplied historical export and creates a private local SQLite database outside the code folder. Expected coverage: September 2013–January 2023, 7,230 observations, 1,012 legacy PIDs and 24 reporting periods. The installation prompt directs an agent to preserve existing MCP connections and verify the new client connection. macOS installation and all eight stdio tools were verified; Windows execution remains untested.

## Class reports

Open the reports below in a browser, or download their HTML files for offline reading. Each file includes its styles, fonts, and report data. The typology discussion also includes its two downloadable 2025 Word source documents. The neighborhood explorer includes embedded photographs and links to articles and photo credits.

Filenames begin with the class date (`YYYY-MM-DD`). The comparison reports' `202505` suffix is their May 2025 data cutoff; the neighborhood report's `202605` suffix is its May 2026 data cutoff. These are separate from the class dates.

| Class date | Report | View | HTML file |
| --- | --- | --- | --- |
| September 25, 2026 | DDC public-building comparison | [Open report](https://willhsiaonyc.github.io/NYC-Opendata-Capital-Projects-MCP/examples/Fordham%20University/2026-09-25_ddc_pb_comparison_202505.html) | [Download](./2026-09-25_ddc_pb_comparison_202505.html) |
| September 25, 2026 | DPR public-building comparison | [Open report](https://willhsiaonyc.github.io/NYC-Opendata-Capital-Projects-MCP/examples/Fordham%20University/2026-09-25_dpr_pb_comparison_202505.html) | [Download](./2026-09-25_dpr_pb_comparison_202505.html) |
| October 2, 2026 | Typology prompts, answers, and recommendations | [Open report](https://willhsiaonyc.github.io/NYC-Opendata-Capital-Projects-MCP/examples/Fordham%20University/2026-10-02_typology_discussion_2026.html) | [Download](./2026-10-02_typology_discussion_2026.html) |
| October 9, 2026 | Capital projects near Fordham: budget and schedule history | [Open report](https://willhsiaonyc.github.io/NYC-Opendata-Capital-Projects-MCP/examples/Fordham%20University/2026-10-09_fordham_nearby_projects_202605.html) | [Download](./2026-10-09_fordham_nearby_projects_202605.html) |

The DDC and DPR reports preserve the original requests and reviewed PID-level comparisons with the supplied 2025 masterlists. The typology report preserves two prompts and answers; its recommendations are a proposal for class discussion. The neighborhood explorer preserves a text-identified five-line shortlist near the Lincoln Center campus, not an exhaustive radius ranking. Its May 2026 cutoff is separate from article and photo dates; source conflicts and site-context references remain labelled. These reports do not change the MCP's built-in classification rules or the original masterlists.

When adding a report or data tool, update both `index.html` and the relevant table. Keep the versioned GitHub release asset, checksum, instructions and agent prompt aligned. Publish no course Excel, generated database or older data-bundled release. GitHub Pages serves `index.html` at the folder URL; it does not generate a directory listing from this README.
