"""Jira Cloud REST API v3 client (standard library only)."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from typing import Callable

ISSUE_FIELDS = ["summary", "description", "priority", "duedate", "labels", "status", "resolutiondate"]


class JiraError(Exception):
    """Jira call failed. Messages never contain the API token."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class JiraClient:
    def __init__(self, base_url: str, email: str, token: str, *,
                 sleep: Callable[[float], None] = time.sleep, max_attempts: int = 5,
                 page_cap: int = 100, page_size: int = 100, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._sleep = sleep
        self.max_attempts = max_attempts
        self.page_cap = page_cap
        self.page_size = page_size
        self.timeout = timeout
        self.request_log: list[tuple[str, str]] = []

    # -- plumbing -----------------------------------------------------------------
    def _redact(self, text: str) -> str:
        for secret in (self._token, self._auth):
            if secret:
                text = text.replace(secret, "***")
        return text

    @staticmethod
    def _error_text(raw: bytes) -> str:
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return raw.decode("utf-8", "replace")[:300]
        parts = list(data.get("errorMessages", [])) if isinstance(data, dict) else []
        if isinstance(data, dict):
            parts += [f"{k}: {v}" for k, v in (data.get("errors") or {}).items()]
        return "; ".join(parts) or str(data)[:300]

    def _request(self, method: str, path: str, body: dict | None = None, *, retry_5xx: bool = True):
        """Send one API call. 429 is always retried (Jira rejected it before doing anything).
        5xx is retried only for calls that are safe to repeat: a create that failed with 5xx
        may still have created the issue, and a blind retry would make a duplicate."""
        url = self.base_url + path
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        self.request_log.append((method, path))
        for attempt in range(1, self.max_attempts + 1):
            req = urllib.request.Request(url, data=payload, method=method)
            req.add_header("Authorization", "Basic " + self._auth)
            req.add_header("Accept", "application/json")
            if payload is not None:
                req.add_header("Content-Type", "application/json")
            retry_after = None
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    status, raw = resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                status, raw = exc.code, exc.read()
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                exc.close()
            except urllib.error.URLError as exc:
                raise JiraError(self._redact(f"cannot reach Jira at {self.base_url}: {exc.reason}")) from None
            except OSError as exc:
                raise JiraError(self._redact(f"network error talking to Jira: {exc}")) from None
            if status == 429 or (status >= 500 and retry_5xx):
                if attempt == self.max_attempts:
                    raise JiraError(f"{method} {path} failed with HTTP {status} after "
                                    f"{self.max_attempts} attempts: {self._redact(self._error_text(raw))}",
                                    status)
                delay = None
                if retry_after:
                    try:
                        delay = max(0.0, float(retry_after))
                    except ValueError:
                        delay = None
                if delay is None:
                    delay = min(0.5 * 2 ** (attempt - 1), 30.0)
                self._sleep(delay)
                continue
            if status >= 400:
                raise JiraError(f"{method} {path} failed with HTTP {status}: "
                                f"{self._redact(self._error_text(raw))}", status)
            return status, (json.loads(raw) if raw.strip() else None)
        raise AssertionError("unreachable")

    # -- API ----------------------------------------------------------------------
    def myself(self) -> dict:
        return self._request("GET", "/rest/api/3/myself")[1]

    def get_project(self, key: str) -> dict | None:
        try:
            return self._request("GET", f"/rest/api/3/project/{key}")[1]
        except JiraError as exc:
            if exc.status == 404:
                return None
            raise

    def create_project(self, key: str, name: str, lead_account_id: str) -> dict:
        body = {
            "key": key, "name": name, "projectTypeKey": "software",
            "projectTemplateKey": "com.pyxis.greenhopper.jira:gh-simplified-kanban-classic",
            "leadAccountId": lead_account_id,
        }
        return self._request("POST", "/rest/api/3/project", body)[1]

    def search_issues(self, project: str) -> list[dict]:
        jql = f'project = "{project}" ORDER BY key ASC'
        issues: list[dict] = []
        seen_keys: set[str] = set()
        seen_tokens: set[str] = set()
        token = None
        for _ in range(self.page_cap):
            body = {"jql": jql, "fields": ISSUE_FIELDS, "maxResults": self.page_size}
            if token:
                body["nextPageToken"] = token
            data = self._request("POST", "/rest/api/3/search/jql", body)[1] or {}
            for issue in data.get("issues", []):
                if issue["key"] in seen_keys:
                    raise JiraError(f"search returned issue {issue['key']} twice; stopping "
                                    "(pagination is repeating)")
                seen_keys.add(issue["key"])
                issues.append(issue)
            token = data.get("nextPageToken")
            if not token or data.get("isLast"):
                return issues
            if token in seen_tokens:
                raise JiraError("search returned the same nextPageToken twice; stopping "
                                "(pagination is repeating)")
            seen_tokens.add(token)
        raise JiraError(f"search did not finish within {self.page_cap} pages; stopping")

    def get_issue(self, key: str) -> dict:
        return self._request("GET", f"/rest/api/3/issue/{key}?fields={','.join(ISSUE_FIELDS)}")[1]

    def create_issue(self, project: str, fields: dict) -> tuple[int, dict]:
        body = {"fields": {"project": {"key": project}, "issuetype": {"name": "Task"}, **fields}}
        # No 5xx retry: the next run finds an issue that was created anyway by its risk label.
        status, data = self._request("POST", "/rest/api/3/issue", body, retry_5xx=False)
        return status, data

    def update_issue(self, key: str, fields: dict) -> int:
        status, _ = self._request("PUT", f"/rest/api/3/issue/{key}", {"fields": fields})
        return status
