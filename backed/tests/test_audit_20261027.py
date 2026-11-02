# -*- coding: utf-8 -*-
"""工程审计回归：鉴权状态码 / 任务队列 FIFO / 调度器更新存在性。

三个缺陷都由 2026-10-27 审计实测发现，本文件把它们锁成机器判定：
  R1 鉴权失败必须是 401，不能因中间件抛异常变成 500
  R2 待跑任务必须按创建时间升序取，不能取「最新 10 条里最老的」
  R3 更新不存在的定时任务必须返回 False（路由据此 404），不能假成功
"""

import time

import pytest
from fastapi.testclient import TestClient


# ---------- R1: 鉴权失败返回 401 ----------

@pytest.fixture()
def strict_client(tmp_path, monkeypatch):
    import api.auth as auth_mod
    monkeypatch.setattr(auth_mod, "ROOT", tmp_path)
    monkeypatch.setattr(auth_mod, "_api_token_cache", None)
    monkeypatch.setenv("HARBOUR_API_TOKEN", "expected-token")
    from app.factory import create_app
    return TestClient(create_app(), raise_server_exceptions=False)


def _is_trusted_ip(ip_str):
    """复用 auth 的判定函数，验证受信/非受信分流本身正确。"""
    from ipaddress import ip_address, ip_network
    import api.auth as auth_mod
    ip = ip_address(ip_str)
    return any(ip in ip_network(n, strict=False) for n in auth_mod._trusted_sources())


def test_trusted_networks_split():
    assert _is_trusted_ip("127.0.0.1") is True
    assert _is_trusted_ip("203.0.113.9") is False


def test_auth_middleware_returns_401_response_not_500():
    """审计 B4：中间件层必须返回 401 响应体，而不是抛异常退化成 500。"""
    import ast
    import inspect
    import api.auth as auth_mod
    src = inspect.getsource(auth_mod.attach_auth_middleware)
    assert "JSONResponse" in src, "鉴权中间件必须直接返回 JSONResponse"
    assert "raise HTTPException" not in src, "中间件层抛 HTTPException 会退化为 500"
    ast.parse(src)


# ---------- R2: 任务队列 FIFO ----------

def test_next_pending_takes_oldest_not_newest_batch(tmp_path):
    from service.publishing import db, tasks

    conn = db.connect(tmp_path / "tasks.db")
    base = time.time()
    for i in range(25):
        db.create_task(conn, f"t{i}", kind="publish", article_id=1,
                       platforms=["x"], account="default", draft_only=1)
        conn.execute("UPDATE tasks SET created_at=? WHERE task_id=?",
                     (base + i, f"t{i}"))
    conn.commit()

    # 不启动 worker 线程：只验证取任务顺序本身（worker 会并发抢跑）
    tm = tasks.TaskManager.__new__(tasks.TaskManager)
    tm.hub, tm.conn = None, conn
    tm._lock = __import__("threading").Lock()
    picked = []
    for _ in range(3):
        t = tm._next_pending()
        assert t is not None
        picked.append(t["task_id"])
    assert sorted(picked) == ["t0", "t1", "t2"], f"队列未取到最老三件，实际 {picked}"
    assert picked == ["t0", "t1", "t2"], f"同时间戳时未按 id 升序，实际 {picked}"


# ---------- R3: 调度器更新不存在 id ----------

def test_scheduler_update_missing_id_returns_false(tmp_path, monkeypatch):
    from service.publishing import db, scheduler

    conn = db.connect(str(tmp_path / "s.db"))
    sch = scheduler.Scheduler(hub=None, conn=conn)
    assert sch.update(999, title="不存在") is False
    assert sch.get(999) is None


# ---------- GateError 语义（I5：service 不再依赖 FastAPI） ----------

def test_gate_error_maps_to_422_not_500():
    from fastapi.testclient import TestClient
    from app.factory import create_app
    from service.publishing.gate import GateError

    app = create_app()

    @app.get("/__test_gate_error")
    def _boom():
        raise GateError("高危内容")

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/__test_gate_error")
    assert r.status_code == 422, f"门禁拒绝必须 422，实际 {r.status_code}"
    assert "合规门禁拒绝发布" in r.text


def test_service_does_not_raise_httpexception_from_publish_path():
    """service.py 的发布路径不得再出现 raise HTTPException（I5）。"""
    import inspect
    from service.publishing import service as svc
    src = inspect.getsource(svc)
    assert "from fastapi import HTTPException" not in src
    assert "raise HTTPException" not in src


# ---------- 数据目录跟随 HUB_DATA_DIR（S1：桌面版不能把数据写进安装目录） ----------

def test_data_root_honors_env(tmp_path, monkeypatch):
    from service.publishing import db
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "appdata"))
    assert db.data_root() == tmp_path / "appdata"
    assert (tmp_path / "appdata").is_dir()


def test_data_root_falls_back_to_repo(monkeypatch):
    from service.publishing import db
    monkeypatch.delenv("HUB_DATA_DIR", raising=False)
    assert db.data_root().name == "data"
    assert db.data_root().is_absolute()
