import hashlib
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_ENV_FILE",
                 "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture
def register_path(tmp_path):
    dest = tmp_path / "register.xlsx"
    shutil.copy(EXAMPLES / "register.xlsx", dest)
    return dest


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
