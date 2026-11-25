from __future__ import annotations

import io
import sqlite3
import zipfile

import pytest

from psycho_carla.core.backup import create_backup, restore_backup


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "data").mkdir(parents=True)
    (h / "skills" / "mine").mkdir(parents=True)
    (h / "psycho_carla.toml").write_text("[persona]\nuser_name='X'\n")
    (h / "skills" / "mine" / "SKILL.md").write_text("# s")
    con = sqlite3.connect(h / "data" / "memory.db")
    con.execute("create table t(v)")
    con.execute("insert into t values ('remember me')")
    con.commit()
    con.close()
    (h / "models").mkdir()
    (h / "models" / "big.gguf").write_bytes(b"GGUF")
    (h / "vault.enc").write_bytes(b"secret")
    return h


def names(blob):
    return set(zipfile.ZipFile(io.BytesIO(blob)).namelist())


def test_backup_contains_yours_not_models_or_vault(home):
    n = names(create_backup(home))
    assert {"psycho_carla.toml", "data/memory.db", "skills/mine/SKILL.md", "BACKUP.txt"} <= n
    assert not any("gguf" in x or "vault" in x for x in n)
    assert "vault.enc" in names(create_backup(home, include_vault=True))


def test_restore_round_trip_keeps_old_state_aside(home, tmp_path):
    blob = create_backup(home)
    (home / "psycho_carla.toml").write_text("changed")
    with sqlite3.connect(home / "data" / "memory.db") as con:
        con.execute("delete from t")
    r = restore_backup(home, blob)
    assert r["ok"] and r["restored"] >= 3
    assert "user_name" in (home / "psycho_carla.toml").read_text()
    assert sqlite3.connect(home / "data" / "memory.db").execute("select v from t").fetchone()[0] == "remember me"
    assert (tmp_path / "home" / "backups").exists() and r["previous_state"]


def test_restore_rejects_junk_and_foreign_zips(home):
    assert restore_backup(home, b"nope")["ok"] is False
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "x")
    assert restore_backup(home, buf.getvalue())["ok"] is False


def test_restore_blocks_path_traversal_and_unlisted_files(home, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("BACKUP.txt", "x")
        z.writestr("../evil.txt", "x")
        z.writestr("skills/../../evil2.txt", "x")
        z.writestr("data/other.db", "x")
        z.writestr("models/big.gguf", "x")
    r = restore_backup(home, buf.getvalue())
    assert r["ok"] and r["restored"] == 0
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "evil2.txt").exists()
    assert not (home / "data" / "other.db").exists()
    assert (home / "models" / "big.gguf").read_bytes() == b"GGUF"


# -- fine-tune data checks ---------------------------------------------------
def test_finetune_data_validation(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ft", __import__("pathlib").Path(__file__).parent.parent / "tools" / "finetune_lora.py")
    ft = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ft)
    good = tmp_path / "g.jsonl"
    good.write_text('{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"yo"}]}\n')
    rows = ft.load_examples(good)
    assert len(rows) == 1 and "1 conversations" in ft.summarise(rows)
    for bad, why in [("not json", "valid JSON"), ('{"x":1}', "messages"),
                     ('{"messages":[{"role":"user","content":"hi"}]}', "assistant"),
                     ('{"messages":[{"role":"bot","content":"hi"}]}', "role")]:
        f = tmp_path / "b.jsonl"
        f.write_text(bad)
        with pytest.raises(ValueError, match=why):
            ft.load_examples(f)
    assert ft.main(["--data", str(good), "--dry-run"]) == 0
    assert ft.main(["--data", str(tmp_path / "missing.jsonl"), "--dry-run"]) == 2


# -- first launch (the .exe path) --------------------------------------------
def test_first_launch_creates_a_working_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path / "fresh"))
    monkeypatch.delenv("PSYCHO_CARLA_CONFIG", raising=False)
    from fastapi.testclient import TestClient

    from psycho_carla.core.config import load_config
    from psycho_carla.server.app import create_app

    assert not (tmp_path / "fresh" / "psycho_carla.toml").exists()
    with TestClient(create_app()) as c:
        s = c.get("/api/setup").json()
        assert s["needs_setup"] is True and s["provider"] == "native_gguf"
        assert c.get("/api/status").json()["active_tier"] == "fast_tier"
    cfg = tmp_path / "fresh" / "psycho_carla.toml"
    assert cfg.exists() and load_config().intelligence.tiers
    cfg.write_text(cfg.read_text() + "\n# mine\n")
    from psycho_carla.core.config import ensure_config

    ensure_config()
    assert "# mine" in cfg.read_text()                       # never overwritten


def test_cli_survives_a_legacy_windows_console_encoding(tmp_path):
    """CI caught `carla init` crashing on cp1252 stdout (the check mark)."""
    import os
    import subprocess
    import sys

    env = dict(os.environ, PSYCHO_CARLA_HOME=str(tmp_path), PYTHONIOENCODING="cp1252")
    env.pop("PYTHONUTF8", None)
    r = subprocess.run([sys.executable, "-m", "psycho_carla.cli.main", "init", "--force"],
                       env=env, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-300:]
