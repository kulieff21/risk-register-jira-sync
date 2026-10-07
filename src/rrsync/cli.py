"""Command line interface. Exit codes: 0 ok, 1 validation errors or conflicts, 2 config/auth/HTTP errors."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import re
import sys
from pathlib import Path

from .apply import apply_plan
from .jira import JiraClient, JiraError
from .plan import State, build_plan, describe
from .register import RegisterError, read_register
from .report import build_markdown, build_rows, write_status_xlsx
from .validate import errors, validate, warnings

PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
ENV_NAMES = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")


class ConfigError(Exception):
    pass


def load_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip("'\"")
    return values


def make_client() -> JiraClient:
    env: dict[str, str] = {}
    env_file = os.environ.get("JIRA_ENV_FILE")
    if env_file:
        try:
            env.update(load_env_file(env_file))
        except OSError as exc:
            raise ConfigError(f"cannot read JIRA_ENV_FILE: {exc.strerror}") from None
    env.update({k: os.environ[k] for k in ENV_NAMES if os.environ.get(k)})
    missing = [k for k in ENV_NAMES if not env.get(k)]
    if missing:
        raise ConfigError("missing configuration: " + ", ".join(missing)
                          + " (set them, or point JIRA_ENV_FILE at a file that defines them)")
    return JiraClient(env["JIRA_BASE_URL"], env["JIRA_EMAIL"], env["JIRA_API_TOKEN"])


def _project(value: str) -> str:
    if not PROJECT_KEY.match(value):
        raise argparse.ArgumentTypeError("project key must look like RISK (2-10 upper-case letters/digits)")
    return value


def _load_validated(path: str):
    """Return (register, issues). Prints validation output."""
    reg = read_register(path)
    issues = validate(reg)
    for i in issues:
        print(i.format())
    print(f"{len(reg.risks)} risks, {len(errors(issues))} errors, {len(warnings(issues))} warnings")
    return reg, issues


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cmd_validate(args, client) -> int:
    _, issues = _load_validated(args.register)
    return 1 if errors(issues) else 0


MANUAL_STEPS = """Could not create the project automatically. Create it by hand:
  1. In Jira: Projects > Create project > Kanban (company-managed or team-managed).
  2. Name it 'Risk Register (demo)' and set the key to {key}.
  3. Make sure the issue type 'Task' and the priorities Highest..Lowest exist.
  4. Run 'rrsync setup --project {key}' again."""


def cmd_setup(args, client) -> int:
    me = client.myself()
    print(f"Authenticated as {me.get('displayName', '?')}")
    if client.get_project(args.project):
        print(f"Project {args.project} exists.")
        return 0
    print(f"Project {args.project} not found; trying to create it.")
    try:
        client.create_project(args.project, "Risk Register (demo)", me["accountId"])
    except JiraError as exc:
        print(f"Jira refused: {exc}")
        print(MANUAL_STEPS.format(key=args.project))
        return 2
    print(f"Created project {args.project}.")
    return 0


def cmd_sync(args, client) -> int:
    reg, issues_v = _load_validated(args.register)
    if errors(issues_v):
        print("Validation errors found; sync refused.")
        return 1
    out = Path(args.out)
    state = State(out / "state.json")
    jira_issues = client.search_issues(args.project)
    plan = build_plan(reg.risks, jira_issues, state.project(args.project), args.project)
    for a in plan.actions:
        if a.kind != "noop":
            print(describe(a))
    counts = plan.counts()
    print("plan: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    code = 1 if counts["conflict"] else 0
    if not args.apply:
        print("Dry run: no changes made. Use --apply to write.")
        return code
    result = apply_plan(client, plan, state, out, _sha256(Path(args.register)))
    print(f"Applied: {result.writes} writes. Audit log: {result.audit_path}")
    for e in result.errors:
        print(f"error: {e}")
    return 2 if result.errors else code


def cmd_report(args, client) -> int:
    reg, issues_v = _load_validated(args.register)
    if errors(issues_v):
        print("Validation errors found; report refused.")
        return 1
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    out = Path(args.out)
    jira_issues = client.search_issues(args.project)
    state = State(out / "state.json")
    plan = build_plan(reg.risks, jira_issues, state.project(args.project), args.project)
    rows = build_rows(reg.risks, jira_issues, today)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(build_markdown(rows, plan, today), encoding="utf-8")
    write_status_xlsx(reg, rows, today, out / "register-status.xlsx")
    print(f"Wrote {out / 'report.md'} and {out / 'register-status.xlsx'}")
    return 0


def _date(value: str) -> str:
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD") from None
    return value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rrsync", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="validate the register, no Jira access")
    v.add_argument("register")
    s = sub.add_parser("setup", help="check credentials and the Jira project")
    s.add_argument("--project", required=True, type=_project)
    y = sub.add_parser("sync", help="dry run by default; --apply writes to Jira")
    y.add_argument("register")
    y.add_argument("--project", required=True, type=_project)
    y.add_argument("--apply", action="store_true")
    y.add_argument("--out", default="out")
    r = sub.add_parser("report", help="write out/report.md and out/register-status.xlsx (no Jira writes)")
    r.add_argument("register")
    r.add_argument("--project", required=True, type=_project)
    r.add_argument("--today", type=_date)
    r.add_argument("--out", default="out")
    return p


def main(argv: list[str] | None = None, client=None) -> int:
    args = build_parser().parse_args(argv)
    handler = {"validate": cmd_validate, "setup": cmd_setup, "sync": cmd_sync, "report": cmd_report}[args.command]
    try:
        if client is None and args.command != "validate":
            client = make_client()
        return handler(args, client)
    except (ConfigError, RegisterError, JiraError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
