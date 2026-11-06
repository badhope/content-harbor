# -*- coding: utf-8 -*-
"""测试公共夹具：把一切落盘副作用关进 tmp_path。

项目真实数据落在 `backed/data/hub.db`（gitignore），测试绝不能碰它——
否则跑一次测试就把开发库改了。这里在导入被测模块后把路径常量指到
临时目录，并对只读不写的地方做无害化替换。
"""

import sys
from pathlib import Path

import pytest

BACKED_ROOT = Path(__file__).resolve().parents[1]
if str(BACKED_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKED_ROOT))

from service.publishing import db, events, observability as obs  # noqa: E402
from service.publishing import _webhook_store as _wh  # noqa: E402
from service.publishing import _event_notifier as _nt  # noqa: E402


class _NullLogger:
    """吞掉结构化事件日志，不在测试机上乱写 data/observability/。"""

    def info(self, *args, **kwargs):
        pass

    warning = info
    error = info
    debug = info
    exception = info


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """隔离 DB 路径 / 事件日志 / webhook 派发线程。"""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "observability").mkdir(parents=True, exist_ok=True)
    db_file = data / "hub.db"

    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setattr(_wh, "_DB_PATH", db_file)
    monkeypatch.setattr(obs, "EVENT_LOG", data / "observability" / "events.log")
    monkeypatch.setattr(obs, "_event_logger", _NullLogger())

    # webhook 派发线程：testing 中 stop 兜底（F-15 后由 init_events 显式启动）
    events.stop_dispatcher()
    # 确保测试不读 config.json 里的 api_token（防止本地创建 config.json 后 401 把 REST 测试打挂）
    import api.auth as _auth_mod
    monkeypatch.setattr(_auth_mod, "_api_token", lambda: "")

    # 标签别名配置也要隔离：tag_alias_map 常驻真实 config.json，若不隔离，
    # 测试里插入的 Javscript/异步编程 会被别名提前归一化，
    # cluster_similar_tags 就再也看不到这些近似对（既存回归，2026-11-04 修）。
    import service.publishing.tags as _tags
    monkeypatch.setattr(_tags, "_config_path", lambda: data / "config.json")

    yield {"data": data, "db_file": db_file, "root": tmp_path}

    # 测试后再兜一次：派发线程不允许跨测试存活（join + 允许下个测试重启）
    events.stop_dispatcher()


@pytest.fixture(autouse=True)
def _reset_notifier():
    """F-14：站内通知是模块级环形缓冲（进程内全局），每个测试前清空防串扰。"""
    _nt.clear()
    yield


@pytest.fixture
def conn(sandbox):
    """一条指向临时库的连接，带齐 SCHEMA + 迁移。"""
    c = db.connect(sandbox["db_file"])
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _isolate_config_json(tmp_path, monkeypatch):
    """把 config.json 指向沙箱，绝不允许测试写真实 backed/config.json。

    回归：ai_config 曾经把配置路径存成模块级常量，导入时就钉死成真实路径；
    新增的 base_url 校验用例因此把 "providers" 段落写进了开发配置，
    进而让另一个用例（test_provider 无 key 分支）读到脏数据而失败。
    这类"测试互相污染 + 污染开发环境"的坑最难查，用环境变量彻底隔离。
    """
    cfg = tmp_path / "sandbox-config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HARBOR_CONFIG_FILE", str(cfg))
    yield cfg


@pytest.fixture
def client():
    """共享 TestClient（带完整应用装配）。

    必须重置 Hub 单例：`api.deps._hub_instance` 是进程级全局，上一个测试
    留下的 Hub（连着已关闭/已迁移的库）会被下一个测试直接复用，
    表现为随机 500 或 database is locked。每次建 client 前先清空并
    在结束后再清一次。
    """
    from fastapi.testclient import TestClient

    import api.deps as deps
    from app.factory import create_app

    def _reset():
        hub = getattr(deps, "_hub_instance", None)
        if hub is not None:
            try:
                hub.close_all()
            except Exception:
                pass
        deps._hub_instance = None

    _reset()
    with TestClient(create_app()) as c:
        yield c
    _reset()
