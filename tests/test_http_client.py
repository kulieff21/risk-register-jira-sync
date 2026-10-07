import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from rrsync.jira import JiraClient, JiraError

TOKEN = "sekrit-token-12345"


class Server:
    """Local HTTP server that replies from a script of (status, headers, body) tuples."""

    def __init__(self, responder):
        outer = self
        self.requests = []
        self.responder = responder

        class H(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode() if length else ""
                outer.requests.append((self.command, self.path, dict(self.headers), body))
                status, headers, payload = outer.responder(len(outer.requests), self)
                data = json.dumps(payload).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _handle

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    made = []

    def make(responder):
        s = Server(responder)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def client(srv, sleeps, **kw):
    return JiraClient(srv.url, "me@example.test", TOKEN, sleep=sleeps.append, **kw)


def test_basic_auth_header(server):
    srv = server(lambda n, h: (200, {}, {"accountId": "a"}))
    assert client(srv, []).myself() == {"accountId": "a"}
    auth = srv.requests[0][2]["Authorization"]
    assert base64.b64decode(auth.split()[1]).decode() == f"me@example.test:{TOKEN}"


def test_429_retry_after_then_success(server):
    def responder(n, h):
        if n == 1:
            return 429, {"Retry-After": "3"}, {"errorMessages": ["slow down"]}
        return 200, {}, {"accountId": "a"}

    srv = server(responder)
    sleeps = []
    assert client(srv, sleeps).myself()["accountId"] == "a"
    assert sleeps == [3.0] and len(srv.requests) == 2


def test_5xx_retries_capped_at_5(server):
    srv = server(lambda n, h: (503, {}, {"errorMessages": ["down"]}))
    sleeps = []
    with pytest.raises(JiraError) as ei:
        client(srv, sleeps).myself()
    assert len(srv.requests) == 5 and len(sleeps) == 4
    assert sleeps == sorted(sleeps) and "5 attempts" in str(ei.value) and ei.value.status == 503


def test_5xx_then_success_backoff(server):
    srv = server(lambda n, h: (502, {}, {}) if n < 3 else (200, {}, {"ok": 1}))
    sleeps = []
    assert client(srv, sleeps).myself() == {"ok": 1}
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]


def test_create_is_not_retried_on_5xx(server):
    # A create that failed with 5xx may still have created the issue; retrying would duplicate it.
    srv = server(lambda n, h: (503, {}, {"errorMessages": ["down"]}))
    sleeps = []
    with pytest.raises(JiraError) as ei:
        client(srv, sleeps).create_issue("RISK", {"summary": "x"})
    assert len(srv.requests) == 1 and sleeps == [] and ei.value.status == 503


def test_create_is_retried_on_429(server):
    def responder(n, h):
        if n == 1:
            return 429, {"Retry-After": "1"}, {"errorMessages": ["slow down"]}
        return 201, {}, {"key": "RISK-1"}

    srv = server(responder)
    sleeps = []
    status, body = client(srv, sleeps).create_issue("RISK", {"summary": "x"})
    assert (status, body["key"], len(srv.requests), sleeps) == (201, "RISK-1", 2, [1.0])


def test_4xx_fails_immediately_with_jira_message(server):
    srv = server(lambda n, h: (400, {}, {"errorMessages": [], "errors": {"duedate": "bad date"}}))
    sleeps = []
    with pytest.raises(JiraError) as ei:
        client(srv, sleeps).update_issue("RISK-1", {"duedate": "x"})
    assert len(srv.requests) == 1 and sleeps == [] and "duedate: bad date" in str(ei.value)
    assert ei.value.status == 400


def test_404_project_is_none(server):
    srv = server(lambda n, h: (404, {}, {"errorMessages": ["No project could be found"]}))
    assert client(srv, []).get_project("NOPE") is None


def test_token_never_in_error_text(server):
    # A hostile/echoing server that puts the credentials in the error body.
    def responder(n, h):
        return 401, {}, {"errorMessages": [f"bad credentials {TOKEN} {h.headers['Authorization']}"]}

    srv = server(responder)
    with pytest.raises(JiraError) as ei:
        client(srv, []).myself()
    text = str(ei.value) + repr(ei.value)
    assert TOKEN not in text
    assert base64.b64encode(f"me@example.test:{TOKEN}".encode()).decode() not in text


def test_token_not_in_connection_error():
    c = JiraClient("http://127.0.0.1:1", "me@example.test", TOKEN, sleep=lambda s: None)
    with pytest.raises(JiraError) as ei:
        c.myself()
    assert TOKEN not in str(ei.value)


def page_server(server, pages):
    def responder(n, h):
        return 200, {}, pages(n)

    return server(responder)


def test_search_paginates(server):
    pages = {1: {"issues": [{"key": "R-1"}], "nextPageToken": "t2"},
             2: {"issues": [{"key": "R-2"}], "nextPageToken": "t3"},
             3: {"issues": [{"key": "R-3"}], "isLast": True}}
    srv = page_server(server, lambda n: pages[n])
    assert [i["key"] for i in client(srv, []).search_issues("RISK")] == ["R-1", "R-2", "R-3"]
    method, path, _, body = srv.requests[1]
    assert method == "POST" and path == "/rest/api/3/search/jql"
    sent = json.loads(body)
    assert sent["nextPageToken"] == "t2" and 'project = "RISK"' in sent["jql"]


def test_search_repeating_token_is_an_error(server):
    srv = page_server(server, lambda n: {"issues": [{"key": f"R-{n}"}], "nextPageToken": "same"})
    with pytest.raises(JiraError, match="nextPageToken twice"):
        client(srv, []).search_issues("RISK")
    assert len(srv.requests) <= 3


def test_search_repeating_issue_is_an_error(server):
    srv = page_server(server, lambda n: {"issues": [{"key": "R-1"}], "nextPageToken": f"t{n}"})
    with pytest.raises(JiraError, match="twice"):
        client(srv, []).search_issues("RISK")
    assert len(srv.requests) == 2


def test_search_page_cap(server):
    srv = page_server(server, lambda n: {"issues": [{"key": f"R-{n}"}], "nextPageToken": f"t{n}"})
    with pytest.raises(JiraError, match="3 pages"):
        client(srv, [], page_cap=3).search_issues("RISK")
    assert len(srv.requests) == 3


def test_create_issue_body(server):
    srv = server(lambda n, h: (201, {}, {"key": "RISK-1", "id": "1"}))
    status, data = client(srv, []).create_issue("RISK", {"summary": "s"})
    assert status == 201 and data["key"] == "RISK-1"
    sent = json.loads(srv.requests[0][3])["fields"]
    assert sent["project"] == {"key": "RISK"} and sent["issuetype"] == {"name": "Task"} and sent["summary"] == "s"


def test_create_project_body(server):
    srv = server(lambda n, h: (201, {}, {"key": "RISK"}))
    client(srv, []).create_project("RISK", "Risk Register (demo)", "acc-1")
    sent = json.loads(srv.requests[0][3])
    assert sent["projectTypeKey"] == "software" and sent["leadAccountId"] == "acc-1"
    assert sent["projectTemplateKey"] == "com.pyxis.greenhopper.jira:gh-simplified-kanban-classic"


def test_update_issue_204_has_no_body(server):
    srv = server(lambda n, h: (204, {}, None))
    # server writes b"null" for None; the client must cope with any JSON body
    assert client(srv, []).update_issue("RISK-1", {"summary": "s"}) == 204
