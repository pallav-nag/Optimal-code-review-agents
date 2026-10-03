import json
import shutil

from conftest import CASES, FIXTURE

from graphreview.cli import main


def test_seed_case_edits_checkout_in_place(tmp_path, capsys):
    shutil.copytree(FIXTURE, tmp_path / "repo")
    main(["seed-case", "--case", "07", "--path", str(tmp_path / "repo"), "--cases", str(CASES)])
    meta = json.loads(capsys.readouterr().out)
    assert meta["title"] == "Speed up password hashing"
    assert "hashlib.md5(" in (tmp_path / "repo" / "shopapp" / "auth.py").read_text()
