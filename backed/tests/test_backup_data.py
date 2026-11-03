# -*- coding: utf-8 -*-
"""备份脚本回归：tech-debt 台账 #9（data/ 单副本无备份，L×H=3）。

资产清单依据 docs/design/m5-data.md §4：hub.db 可直接备份；*.auth.json 是
session cookie、config.json 是明文 API Key，都算敏感，只能落到仓库外的私有目录。
"""

import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup_data.py"


@pytest.fixture(scope="module")
def mod():
    """按文件路径加载脚本——它不是包内模块，且要带入它自己的 sys.path 处理。"""
    spec = importlib.util.spec_from_file_location("backup_data_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def fake_data(tmp_path, monkeypatch, mod):
    """在临时目录里造一份"真实数据"，把脚本的路径常量指过去。"""
    data = tmp_path / "svc" / "data"
    (data / "profiles").mkdir(parents=True)
    db_file = data / "hub.db"

    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO articles (title) VALUES ('要保住的文章')")
    conn.commit()
    conn.close()

    (data / "profiles" / "juejin_default.auth.json").write_text(
        json.dumps({"cookies": [{"name": "session", "value": "SECRET"}]}),
        encoding="utf-8")
    (tmp_path / "svc" / "config.json").write_text(
        json.dumps({"ai_api_key": "sk-SECRET"}), encoding="utf-8")
    (tmp_path / "backed_config.json").write_text(
        json.dumps({"api_token": "SECRET"}), encoding="utf-8")

    monkeypatch.setattr(mod, "DB_FILE", db_file)
    monkeypatch.setattr(mod, "DATA_DIR", data)
    monkeypatch.setattr(mod, "PROFILE_DIR", data / "profiles")
    monkeypatch.setattr(mod, "CONFIG_CANDIDATES",
                        [tmp_path / "svc" / "config.json",
                         tmp_path / "backed_config.json"])
    return {"root": tmp_path, "data": data, "db": db_file}


# ---------------- 安全红线 ----------------

def test_refuses_destination_inside_repo(mod, fake_data):
    """凭据类文件绝不能落进仓库——一旦进去就有被 commit 的风险。"""
    inside = mod.REPO_ROOT / "harbor-backups"
    with pytest.raises(mod.RefuseError):
        mod.build_plan(inside, include_profiles=False)


def test_cli_reports_refusal_with_exit_2(mod, fake_data, capsys):
    inside = mod.REPO_ROOT / "harbor-backups"
    rc = mod.main(["--dest", str(inside)])
    assert rc == 2
    assert "拒绝执行" in capsys.readouterr().err


def test_refusal_does_not_write_anything(mod, fake_data):
    inside = mod.REPO_ROOT / "harbor-backups"
    mod.run_backup(inside, keep_days=7)
    assert not inside.exists()


# ---------------- 在线安全备份 ----------------

def test_backup_produces_readable_db_copy(mod, fake_data, tmp_path):
    dest = tmp_path / "out"
    res = mod.run_backup(dest, keep_days=7)
    assert res.ok is True, res.error
    copies = list(dest.glob("backup-*/hub.db"))
    assert len(copies) == 1

    conn = sqlite3.connect(f"file:{copies[0]}?mode=ro", uri=True)
    titles = [r[0] for r in conn.execute("SELECT title FROM articles")]
    conn.close()
    assert titles == ["要保住的文章"]


def test_backup_survives_a_concurrent_writer(mod, fake_data, tmp_path):
    """核心价值：服务在写时备份不许拿到半截副本。

    起一个线程边写边备份，备份完立刻校验副本完整性 + 行数自洽。
    """
    dest = tmp_path / "out"
    stop = []

    def writer():
        conn = sqlite3.connect(str(fake_data["db"]), timeout=30)
        i = 0
        while not stop:
            conn.execute("INSERT INTO articles (title) VALUES (?)", (f"w{i}",))
            conn.commit()
            i += 1
            time.sleep(0.001)
        conn.close()

    import threading
    t = threading.Thread(target=writer, daemon=True)
    t.start()
    try:
        res = mod.run_backup(dest, keep_days=7)
    finally:
        stop.append(1)
        t.join(timeout=5)

    assert res.ok is True, res.error
    copy = next(dest.glob("backup-*/hub.db"))
    conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    n = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    check = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    assert check == "ok"
    assert n >= 1  # 一致性快照，行数不会出现"读到一半"的中间态


def test_backup_copies_sensitive_files(mod, fake_data, tmp_path):
    dest = tmp_path / "out"
    res = mod.run_backup(dest, keep_days=7)
    out = next(dest.glob("backup-*"))
    assert res.auth_count == 1
    assert res.config_count == 2
    auth = out / "juejin_default.auth.json"
    assert json.loads(auth.read_text(encoding="utf-8"))["cookies"][0]["value"] == "SECRET"
    assert (out / "config-svc.json").exists()
    assert (out / "config-tmp.json").exists() or \
        any(p.name.startswith("config-") for p in out.iterdir())


def test_backup_never_prints_secret_values(mod, fake_data, tmp_path, capsys):
    mod.main(["--dest", str(tmp_path / "out"), "--json"])
    printed = capsys.readouterr().out
    assert "SECRET" not in printed
    assert "sk-" not in printed


# ---------------- 可选内容与保留策略 ----------------

def test_profiles_excluded_by_default(mod, fake_data, tmp_path):
    dest = tmp_path / "out"
    mod.run_backup(dest, keep_days=7)
    out = next(dest.glob("backup-*"))
    assert res_profiles(out) == []


def res_profiles(out):
    return [p.name for p in out.iterdir() if p.name.startswith("profile-")]


def test_include_profiles_copies_browser_profile_dirs(mod, fake_data, tmp_path):
    prof = fake_data["data"] / "profiles" / "juejin_default"
    prof.mkdir()
    (prof / "Cookies").write_text("x", encoding="utf-8")

    res = mod.run_backup(tmp_path / "out", keep_days=7, include_profiles=True)
    assert res.ok is True
    assert res.profile_count == 1
    out = next((tmp_path / "out").glob("backup-*"))
    assert (out / "profile-juejin_default" / "Cookies").exists()


def test_dry_run_creates_nothing(mod, fake_data, tmp_path):
    dest = tmp_path / "out"
    res = mod.run_backup(dest, keep_days=7, dry_run=True)
    assert res.ok is True
    assert not dest.exists()


def test_missing_data_reports_failure(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "DB_FILE", tmp_path / "nope.db")
    monkeypatch.setattr(mod, "PROFILE_DIR", tmp_path / "nope-profiles")
    monkeypatch.setattr(mod, "CONFIG_CANDIDATES", [tmp_path / "nope.json"])
    res = mod.run_backup(tmp_path / "out", keep_days=7)
    assert res.ok is False
    assert res.error == "没有可备份的数据"


def test_missing_db_still_backs_up_credentials(mod, fake_data, tmp_path, monkeypatch):
    """hub.db 缺失多是数据事故信号，此时更要备份还在的凭据，而不是整体失败。"""
    monkeypatch.setattr(mod, "DB_FILE", tmp_path / "gone.db")
    res = mod.run_backup(tmp_path / "out", keep_days=7)
    assert res.ok is True
    assert res.db_bytes == 0
    assert res.auth_count == 1
    assert res.config_count == 2
    assert any("未备份" in s for s in res.skipped)


# ---------------- 清理 ----------------

def test_prune_removes_oldest_dirs_but_keeps_newest(mod, tmp_path):
    import os
    dest = tmp_path / "backups"
    dest.mkdir()
    for name in ["backup-20260101-000000", "backup-20260102-000000",
                 "backup-20260103-000000"]:
        d = dest / name
        d.mkdir()
        old = time.time() - 30 * 86400
        os.utime(d, (old, old))

    assert mod._prune(dest, keep_days=7) == 2
    # 最新的那份即使过期也保留——清理策略绝不能把备份清空
    assert sorted(p.name for p in dest.iterdir()) == ["backup-20260103-000000"]


def test_prune_keeps_newest_even_when_all_are_old(mod, tmp_path):
    import os
    dest = tmp_path / "backups"
    dest.mkdir()
    for name in ["backup-20260101-000000", "backup-20260102-000000"]:
        d = dest / name
        d.mkdir()
        old = time.time() - 30 * 86400
        os.utime(d, (old, old))
    mod._prune(dest, keep_days=7)
    assert sorted(p.name for p in dest.iterdir()) == ["backup-20260102-000000"]


def test_prune_zero_means_no_cleanup(mod, tmp_path):
    import os
    dest = tmp_path / "backups"
    dest.mkdir()
    d = dest / "backup-20260101-000000"
    d.mkdir()
    old = time.time() - 30 * 86400
    os.utime(d, (old, old))
    assert mod._prune(dest, keep_days=0) == 0
    assert d.exists()


def test_prune_ignores_unrelated_entries(mod, tmp_path):
    dest = tmp_path / "backups"
    dest.mkdir()
    (dest / "README.txt").write_text("说明", encoding="utf-8")
    keep = dest / "backup-20260102-000000"
    keep.mkdir()
    assert mod._prune(dest, keep_days=7) == 0
    assert (dest / "README.txt").exists()
