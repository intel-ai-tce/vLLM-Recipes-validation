# Weekly validation comparison report

The reporting workflow (`.github/workflows/weekly-report.yml`) downloads the two most recent completed
`weekly-xeon6.yml` workflow runs that have an available `xeon6-weekly-<run_number>` artifact containing
`weekly-summary.json`. Runs whose overall conclusion is `failure` are valid candidates when model-level
failures produced a summary.

It uses a GitHub-hosted runner, not the Xeon self-hosted benchmark runner. Every Tuesday at 12:00 UTC,
it writes `weekly/latest.md` and an immutable-by-name historical comparison under `weekly/history/`
on the `report` branch. Run **Actions > Weekly Xeon 6 Comparison Report > Run workflow** for a manual report.
The report branch is created on its first successful execution.

Prerequisites:
- Two completed weekly runs with unexpired uploaded `xeon6-weekly-*` artifacts.
- The repository Actions setting must allow workflows to create and push commits with `GITHUB_TOKEN`;
  any branch protection/ruleset on `report` must permit the bot push.
- The reporting workflow must be present on the default branch to support scheduled/manual execution.

Check the generated report at `https://github.com/intel-ai-tce/vLLM-Recipes-validation/blob/report/weekly/latest.md`.
Metrics are observational; confirm equal workload, prompt count, CPU topology, model settings, and image
before interpreting performance changes as regressions. If Monday validation is still running Tuesday,
run the report workflow manually after completion.

Local test: `python3 -m unittest discover -s tests -p 'test_compare_weekly.py'`.


## Optional Wiki publishing

Initialize **intel-ai-tce/vLLM-Recipes-validation** Wiki with a Home page in the GitHub UI first.
Set repository Actions variable `WEEKLY_REPORT_WIKI_ENABLED=true`. Add a `WIKI_PUSH_TOKEN`
repository secret containing a fine-grained or classic access token that **actually permits
writing to the GitHub Wiki Git repository** (test Wiki Git push permissions before enabling).
The workflow will write **only** `Weekly-Validation-Reports.md`, not overwrite `Home.md` or
other hand-authored pages. Link to that page from Wiki Home manually once. GitHub Wiki
repository access may require a classic PAT with `repo` scope rather than `GITHUB_TOKEN`.
A missing token/uninitialized Wiki skips updating the Wiki with a warning.

## Optional Microsoft Power Automate email / Teams notification

Create a Power Automate cloud flow with **When an HTTP request is received** trigger;
configure request authentication/tenant policy according to your Microsoft 365 environment.
The trigger and Teams/Outlook connectors may have licensing or tenant restrictions.
Add the **Office 365 Outlook / Send an email (V2)** action to send the report to your
Intel distribution list; optionally add a **Teams / Post a message** action.
The JSON contains `report_id`, `date`, `tested`, `passed`, `failed`, `new_failures`,
`recovered_models`, `report_url`, `wiki_url`, and run links. Use `report_id` for
idempotency in the flow if the report job is rerun.

Store the full authenticated trigger URL in GitHub Actions secret
`POWER_AUTOMATE_REPORT_URL`, and set variable `WEEKLY_REPORT_EMAIL_ENABLED=true`.
The GitHub-hosted reporting job POSTs JSON over HTTPS *after* report publication.
No webhook credentials are embedded in source code. With notifications disabled,
no Microsoft connection is required. If enabled and delivery fails, the reporting job
fails (but the already-published Markdown remains available).

Security: this public repo's Wiki and report branch are public. Never publish Intel-private
results, internal IPs/hostnames, credentials, or sensitive errors to either destination.
Use a private repository and approved organizational delivery endpoints for private reports.
