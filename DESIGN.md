# risk-register-jira-sync: design

A GRC team keeps its risk register in Excel. Treatment work happens in Jira. This tool keeps the
two consistent without letting either side silently overwrite the other, and leaves an audit trail
of every change it makes.

**Demo project with synthetic data.** The register describes a fictional payment institution,
"Larkspur Payments". No real company, person or system.

## Vocabulary

| Term | Meaning here |
|---|---|
| Risk register | One row per risk: what can go wrong, how likely, how bad, who owns it, which controls apply, what is being done |
| Inherent risk | likelihood × impact before treatment (1–5 × 1–5 = 1–25) |
| Residual risk | likelihood × impact expected after the treatment plan is done |
| Treatment | `mitigate`, `accept`, `transfer` or `avoid` (ISO/IEC 27001:2022 clause 6.1.3) |
| Control | An ISO/IEC 27001:2022 Annex A control, e.g. `5.18 Access rights` (93 controls, 4 themes) |

## Data flow

```
register.xlsx ──► validate ──► plan (dry run) ──► apply ──► Jira project RISK
   (source of truth                │                  │         (one issue per risk)
    for risk content)              ▼                  ▼               │
                              plan printed      audit/*.jsonl         │
                                                                      ▼
out/report.md, out/register-status.xlsx ◄── report ◄── Jira status, resolution date
```

## Field ownership (the central rule)

Each field has exactly one owner. The owner's value wins; the other side never edits it silently.

| Field | Owner | Jira field |
|---|---|---|
| Summary `[R-001] title` | register | `summary` |
| Description (asset, threat, vulnerability, owner, scores, controls, plan) | register | `description` (ADF) |
| Priority (from inherent score) | register | `priority` |
| Due date | register | `duedate` |
| Labels `grc-risk`, `risk-R-001`, `treatment-<t>`, `ctrl-5.18`… | register | `labels` |
| Status (To Do / In Progress / Done), resolution date | **Jira** | read only |

If a register-owned field was changed by hand in Jira since the last sync, that is **drift**. The
register still wins on `--apply`, but the plan shows the drift and the audit log records the Jira
value that was overwritten. Nothing changes without a record.

Priority from inherent score: 20–25 Highest, 15–19 High, 8–14 Medium, 4–7 Low, 1–3 Lowest.

## Identity and idempotency

- A risk's Jira issue is found by the exact label `risk-<risk_id>` inside the project, never by
  summary text. The tool does not store Jira keys in the register.
- Create only when no issue carries the label. If a create succeeds but the response is lost, the
  next run finds the issue by its label and updates it: no duplicate.
- Two or more issues with the same risk label is a **conflict**: that risk is skipped and reported;
  the tool never picks one.
- A Jira issue whose risk is no longer in the register is **orphaned**: reported, never deleted or
  closed automatically.
- `sync` without `--apply` is a dry run and makes no write call.
- Second `--apply` with an unchanged register and unchanged Jira: zero writes.

## Validation (runs before every sync; sync refuses on any error)

All errors are collected and printed with sheet row numbers, not just the first one.

Errors:
1. Required columns missing (`risk_id, title, asset, threat, vulnerability, owner, likelihood,
   impact, controls, treatment, treatment_plan, due_date, residual_likelihood, residual_impact,
   approved_by`). Extra columns are allowed.
2. `risk_id` not matching `R-\d{3}`, or duplicated.
3. Empty `title`, `owner`, `asset` or `threat`.
4. `likelihood`, `impact`, `residual_likelihood`, `residual_impact` not an integer 1–5.
5. Residual score greater than inherent score.
6. `controls` empty, or a control ID that is not one of the 93 Annex A 2022 IDs. A 2013-style ID
   (`A.9.2.3`) gets a hint that the 2013 numbering is not accepted.
7. `treatment` not in {mitigate, accept, transfer, avoid}.
8. `mitigate`, `transfer`, `avoid` without `treatment_plan` or `due_date`.
9. `accept` without `approved_by` (a risk acceptance needs a named approver).
10. `due_date` not a date.

Warnings (do not block):
- Inherent score ≥ 15 with treatment `accept`.
- `mitigate` where residual score equals inherent score (the plan changes nothing).

## Report

`report` reads the register and the current Jira state (no writes) and writes:

- `out/report.md`: counts by treatment and status; 5×5 heat maps (inherent and residual);
  **overdue** treatments (due date before the report date and status not Done); orphaned issues;
  conflicts; Annex A coverage (how many of the 93 controls at least one risk references, per theme).
- `out/register-status.xlsx`: a copy of the register with `jira_key`, `jira_status`,
  `resolved`, `overdue`, `report_date` appended. The source register is never modified.

The report date is a parameter (`--today`), defaulting to the local date, so reports are reproducible.

## Audit log

Every `--apply` writes `out/audit/<UTC timestamp>.jsonl`: one line per write call with risk ID,
action (`create`/`update`), Jira key, fields with before → after, drift flag, HTTP status. A run
header line records register file SHA-256, project, tool version and plan counts.

## Jira client

- Jira Cloud REST API v3, Basic auth (account e-mail + API token), from env vars
  `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, optionally loaded from the file named in
  `JIRA_ENV_FILE`. The token never appears in logs, errors or the audit file.
- Search with `POST /rest/api/3/search/jql` (the old `/search` endpoint is removed) and
  `nextPageToken` pagination. Community reports describe the token repeating forever; the client
  stops with an error when a token or an issue key repeats, or after a page cap.
- Retries on 429 (honours `Retry-After`) and 5xx with backoff, at most 5 attempts. 4xx other than
  429 fails immediately with Jira's error message.
- `setup` checks the credentials (`/myself`) and the project; it can create the project when the
  account is a Jira admin, otherwise it prints what to create by hand.

## Commands

```
rrsync validate data/register.xlsx
rrsync setup --project RISK
rrsync sync data/register.xlsx --project RISK            # dry run: prints the plan
rrsync sync data/register.xlsx --project RISK --apply
rrsync report data/register.xlsx --project RISK [--today 2026-10-08]
```

## Tests

Offline, against an in-memory fake Jira with the same interface as the real client, plus a local
HTTP server for the client's retry and pagination behaviour. Live runs against a real Jira Cloud
site are recorded separately.
