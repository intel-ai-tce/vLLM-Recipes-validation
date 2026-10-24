# Xeon 6 major-version comparison report

The workflow `Major Version Xeon 6 Comparison Report` runs automatically when
`Major Version Xeon 6 Recipe Validation` completes on `main`, or manually from
Actions. Validation failures still qualify when the summary artifact exists.

It selects the most recent eligible full-catalog pinned-version run, then the
most recent run from another release with matching runner, benchmark count,
recipe API, recipe tools and environment overrides. The **lower semantic
release** is always the baseline, even if validated more recently. Single-model
runs and custom Docker image overrides are intentionally ignored.

**Migration:** Each major validation run must include `major-run-metadata.json`
in its `xeon6-major-<run_number>-<attempt>` artifact. The patched validation
workflow writes it. Earlier artifacts lacking metadata are skipped, rather than
risking an incorrect version comparison. Complete at least one full run on each
pinned release after merging these changes.

The report publishes to the `report` branch under `major/latest.md` and
`major/history/`. Existing `weekly/` reports are not changed. GitHub Actions
uses a shared publication concurrency group for both report workflows.

To email the complete HTML report, set the repository variable
`MAJOR_REPORT_EMAIL_ENABLED=true` and reuse the existing
`POWER_AUTOMATE_REPORT_URL` secret. Power Automate receives JSON with
`report_type=major`, `baseline_version`, `candidate_version`, `email_html`,
`title`, `date`, and links. Use `email_html` as the **HTML Body** of Outlook
**Send an email (V2)**, and for Subject use, for example:

`Xeon 6 Major Validation - @{triggerBody()?['baseline_version']} vs @{triggerBody()?['candidate_version']} - @{formatDateTime(utcNow(),'yyyy-MM-dd')}`

If the current Power Automate trigger uses a strict schema, add these properties
or create a separate major-report flow and change its webhook secret. The
GitHub report step sends one notification; Teams can post a concise message
from the same flow without reproducing the three detailed metric tables.

Before relying on any performance regression, verify that the two releases
use comparable vLLM configurations and model workloads. The neutral band of
absolute changes below 3% is a run-to-run variance heuristic.
