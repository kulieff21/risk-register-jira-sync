import shutil
import subprocess
import sys
from pathlib import Path

from rrsync.cli import load_env_file, main
from rrsync.jira import JiraError
from tests.conftest import EXAMPLES, ROOT
from tests.fake_jira import FakeJira


def test_validate_exit_codes(capsys):
    assert main(["validate", str(EXAMPLES / "register.xlsx")]) == 0
    assert "0 errors, 1 warnings" in capsys.readouterr().out
    assert main(["validate", str(EXAMPLES / "register-with-errors.xlsx")]) == 1
    assert main(["validate", str(EXAMPLES / "missing.xlsx")]) == 2


def test_missing_config_exit_2(register_path, capsys):
    assert main(["sync", str(register_path), "--project", "RISK"]) == 2
    err = capsys.readouterr().err
    assert "JIRA_BASE_URL" in err and "JIRA_API_TOKEN" in err


def test_env_file_parsing(tmp_path):
    p = tmp_path / "x.env"
    p.write_text("# c\nJIRA_BASE_URL=https://x.example\nexport JIRA_EMAIL='a@b.example'\nJIRA_API_TOKEN=t\n")
    assert load_env_file(str(p)) == {"JIRA_BASE_URL": "https://x.example", "JIRA_EMAIL": "a@b.example",
                                     "JIRA_API_TOKEN": "t"}


def test_setup_existing_and_create(capsys):
    assert main(["setup", "--project", "RISK"], client=FakeJira()) == 0
    fake = FakeJira(projects=())
    assert main(["setup", "--project", "RISK"], client=fake) == 0
    assert fake.writes == [("create_project", "RISK", "Risk Register (demo)")]


def test_setup_refused_prints_manual_steps(capsys):
    class Refusing(FakeJira):
        def create_project(self, *a):
            raise JiraError("You do not have permission to create projects", 403)

    assert main(["setup", "--project", "RISK"], client=Refusing(projects=())) == 2
    out = capsys.readouterr().out
    assert "Create it by hand" in out and "permission" in out


def test_bad_project_key():
    import pytest
    with pytest.raises(SystemExit):
        main(["setup", "--project", "bad key"], client=FakeJira())


def test_make_register_is_deterministic(tmp_path):
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_register.py"), str(tmp_path)], check=True,
                   capture_output=True)
    for name in ("register.xlsx", "register-with-errors.xlsx"):
        assert (tmp_path / name).read_bytes() == (EXAMPLES / name).read_bytes()
