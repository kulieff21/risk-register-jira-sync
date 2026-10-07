# risk-register-jira-sync

Keeps an ISO/IEC 27001:2022 risk register (Excel) and the treatment work in Jira consistent, and
leaves an audit trail of every change it makes.

**Demo project with synthetic data.** The example register describes a fictional payment
institution, "Larkspur Payments". No real company, person or system is involved.

Built with AI assistance and reviewed by a human.

## What it does

- Validates the register (10 error rules, 2 warnings) and reports every problem with its sheet row.
- Creates and updates one Jira issue per risk, found by the exact label `risk-R-001`, never by
  summary text. A create whose response was lost is found again by its label on the next run.
- Detects drift, conflicts and orphans, and never deletes or closes anything.
- Writes `out/audit/<UTC timestamp>.jsonl` for every `--apply`, `out/report.md` and
  `out/register-status.xlsx` (a copy of the register with Jira status columns appended).

## Field ownership

Each field has exactly one owner; the owner's value wins.

| Field | Owner |
|---|---|
| Summary, description, priority (from inherent score), due date, labels `grc-risk`, `risk-<id>`, `treatment-<t>`, `ctrl-<id>` | register |
| Status, resolution date | Jira (read only) |

If someone edits a register-owned field in Jira by hand, that is **drift**: the plan shows it, the
register wins on `--apply`, and the audit log records the Jira value that was overwritten. Labels
you add by hand (anything other than the owned prefixes) are kept. "Done" means the Jira status
category is `done`, not a status name.

Priority from inherent score (likelihood x impact): 20-25 Highest, 15-19 High, 8-14 Medium,
4-7 Low, 1-3 Lowest.

## Setup

Python 3.12 and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run pytest -q
```

Jira Cloud credentials come from environment variables `JIRA_BASE_URL`, `JIRA_EMAIL`,
`JIRA_API_TOKEN`, optionally loaded from the file named in `JIRA_ENV_FILE`. The token is never
printed, logged or written to the audit file.

## Commands

```
rrsync validate examples/register.xlsx
rrsync setup --project RISK
rrsync sync examples/register.xlsx --project RISK            # dry run: prints the plan
rrsync sync examples/register.xlsx --project RISK --apply
rrsync report examples/register.xlsx --project RISK [--today 2026-10-08]
```

Exit codes: 0 ok, 1 validation errors or conflicts, 2 configuration, authentication or HTTP errors.
`sync` refuses to run when validation has errors. Last-synced values are kept in `out/state.json`;
without it the tool cannot tell drift from a normal change and rewrites each description once.

`tools/make_register.py` regenerates `examples/register.xlsx` and `examples/register-with-errors.xlsx`.

## Results

Live-run results against a real Jira Cloud site will be added here. The tests in this repository
run offline against an in-memory fake Jira and a local HTTP server.

See `DESIGN.md` for the full contract. Licence: MIT.
