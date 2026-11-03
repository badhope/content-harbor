# -*- coding: utf-8 -*-
"""可观测性：结构化日志 + 指标 + 发布链路 trace。

定位：2026 生产水位的最小可观测层。纯标准库，不引 opentelemetry/prometheus
这类重依赖——单机 SQLite 服务用不上分布式追踪，RotatingJSON 日志 + 内存指标
 + 一次发布的 trace 快照足够排障，且能直接喂给任何 grep/jq 管道。

三块能力：
  1. 结构化日志 —— RotatingJSON，每次发布/登录/验证码命中落一条 JSON 行到
     data/observability/events.log，带 trace_id 串联整条发布链路。
  2. 指标 —— 内存计数器，进程内累积，/metrics 端点实时拉取（重启归零，
     长期趋势看 jobs 表 + events.log）。
  3. 发布链路 trace —— 一次 publish/update 的所有平台子步骤串进同一
     trace_id，事后看"哪个平台卡在哪、耗时多少"一目了然。

线程安全：计数器加锁。事件日志走 RotatingFileHandler 自带线程锁。
"""

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request

from utils.logger import get_logger, setup_logger

def _data_dir() -> Path:
    """事件日志跟随 `db.data_root()`：桌面版走 %APPDATA%，容器走挂载卷。

    回归：旧实现硬编码 `service/data`，而 DB/Fernet/profile 都走 data_root()，
    桌面版于是出现"数据库在 APPDATA、日志/验证码截图留在安装目录"的分裂，
    卸载重装会丢排障现场，备份脚本也漏掉这些产物。
    """
    from service.publishing.db import data_root
    return data_root()


OBS_DIR = _data_dir() / "observability"
EVENT_LOG = OBS_DIR / "events.log"

# 单文件 10MB，留 5 份备份；发布链路是低频事件，一年也不会撑爆
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP = 5


# ---------------------------------------------------------------------------
# 结构化事件日志
# ---------------------------------------------------------------------------
def _build_event_logger() -> logging.Logger:
    logger = logging.getLogger("hub.events")
    if logger.handlers:  # 已被建过（热重载/重 import）
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    OBS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fh = RotatingFileHandler(str(EVENT_LOG), maxBytes=_MAX_BYTES,
                                 backupCount=_BACKUP, encoding="utf-8")
    except Exception:
        fh = logging.FileHandler(str(EVENT_LOG), encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)
    return logger


_event_logger = _build_event_logger()


def emit(event: str, **fields):
    """落一条结构化事件。自动补 ts/level/pid，trace_id 透传。"""
    rec = {"ts": time.time(), "event": event, "level": fields.pop("level", "info"),
           "pid": os.getpid()}
    rec.update(fields)
    try:
        _event_logger.info(json.dumps(rec, ensure_ascii=False, default=str))
    except Exception:
        # 日志本身不该影响主流程
        pass
    return rec


# ---------------------------------------------------------------------------
# 指标（内存计数器）
# ---------------------------------------------------------------------------
class MetricRegistry:
    """计数器 + 直方图。线程安全，进程内累积。

    用法：
        METRICS.incr("publish.ok")
        METRICS.observe("publish.dur", 3.2)
        METRICS.snapshot()  # -> {name: value}
    """

    # 固定分桶：毫秒级 API 到分钟级发布都覆盖，避免低端只有一个桶导致分位失真
    BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60,
               float("inf")]

    def __init__(self):
        self._lock = threading.Lock()
        self._counters = {}
        self._gauge = {}
        self._hist = {}   # name -> {"sum": float, "n": int, "max": float, "buckets": {label: int}}
        self._BUCKETS = self.BUCKETS

    def incr(self, name: str, delta: int = 1):
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + delta

    def gauge(self, name: str, value: float):
        with self._lock:
            self._gauge[name] = value

    def observe(self, name: str, value: float):
        """记录一次耗时（秒），进固定分桶。"""
        with self._lock:
            h = self._hist.setdefault(name, {"sum": 0.0, "n": 0, "max": 0.0,
                                             "buckets": {}})
            h["sum"] += value
            h["n"] += 1
            if value > h["max"]:
                h["max"] = value
            # 累积语义：一次观测会同时落进所有 >= value 的桶
            for b in self._BUCKETS:
                if value <= b:
                    label = str(b) if b != float("inf") else "+Inf"
                    h["buckets"][label] = h["buckets"].get(label, 0) + 1

    def _quantile(self, buckets: dict, total: int, q: float,
                  max_value: float = 0.0) -> float:
        """按累积桶线性插值估算分位（Prometheus histogram_quantile 同款思路）。

        分位落进 +Inf 桶（超出最大有限边界 60s）时无法插值出上界，返回实测 max：
        否则 120s 的观测会被报成 p99=60s，尾部被系统性低估。
        """
        if not total:
            return 0.0
        rank = q * total
        upper = 0.0
        for b in self._BUCKETS:
            label = str(b) if b != float("inf") else "+Inf"
            cum = buckets.get(label, 0)
            if cum >= rank:
                lower = upper
                if b == float("inf"):
                    return max_value if max_value > lower else lower
                if b == lower:
                    return upper
                # 桶内均匀分布假设
                prev = buckets.get(str(lower), 0) if lower else 0
                span = cum - prev
                if span <= 0:
                    return upper
                return lower + (b - lower) * (rank - prev) / span
            upper = b if b != float("inf") else upper
        return upper

    def snapshot(self) -> dict:
        with self._lock:
            out = {"counters": dict(self._counters),
                   "gauge": dict(self._gauge),
                   "histograms": {}, "updated_at": time.time()}
            for name, h in self._hist.items():
                out["histograms"][name] = {
                    "count": h["n"],
                    "sum": h["sum"],
                    "avg": (h["sum"] / h["n"]) if h["n"] else 0.0,
                    "max": h.get("max", 0.0),
                    "buckets": {label: h["buckets"].get(label, 0)
                                for label in (str(b) if b != float("inf") else "+Inf"
                                              for b in self._BUCKETS)},
                    "quantiles": {
                        "p50": round(self._quantile(h["buckets"], h["n"], 0.50, h["max"]), 4),
                        "p90": round(self._quantile(h["buckets"], h["n"], 0.90, h["max"]), 4),
                        "p99": round(self._quantile(h["buckets"], h["n"], 0.99, h["max"]), 4),
                    },
                }
            return out


METRICS = MetricRegistry()


# ---------------------------------------------------------------------------
# Prometheus 标准导出（/metrics 双格式）
#
# 自研 snapshot 的 JSON 便于人看，但 Prometheus / Grafana 只吃文本 exposition
# 格式；之前要接监控得自己写 exporter。这里用官方 prometheus_client 生成
# 标准文本（counter/gauge/histogram），指标名与原 JSON 的 key 一一对应，
# 已有的 `/metrics?format=json` 行为完全不变。
# ---------------------------------------------------------------------------
def prometheus_exposition() -> str:
    """把内存指标渲染成 Prometheus 文本格式（每次调用重建，指标基数受控）。"""
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

    reg = CollectorRegistry()
    snap = METRICS.snapshot()
    for name, value in snap["counters"].items():
        safe = _prom_name(name)
        c = Counter(safe, f"Harbor counter {name}", registry=reg)
        # prometheus_client 不允许直接 set，用 _value 的接口保持精确值
        c.inc(value)
    for name, value in snap["gauge"].items():
        g = Gauge(_prom_name(name), f"Harbor gauge {name}", registry=reg)
        g.set(value)
    for name, hist in snap["histograms"].items():
        safe = _prom_name(name)
        h = Histogram(safe, f"Harbor histogram {name}",
                      buckets=MetricRegistry.BUCKETS, registry=reg)
        # 直接写桶计数：snapshot 里已是累积桶语义
        for label, count in hist["buckets"].items():
            if label == "+Inf":
                continue
            try:
                h.labels()._buckets[float(label)] = count
            except Exception:
                continue
        try:
            h.labels()._sum = hist["sum"]
            h.labels()._count = hist["count"]
        except Exception:
            pass
    return generate_latest(reg).decode("utf-8")


def _prom_name(name: str) -> str:
    """指标名规范化：只保留 [a-zA-Z0-9_]，点号换下划线。"""
    import re as _re
    out = _re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if not out or out[0].isdigit():
        out = "harbor_" + out
    return out


# ---------------------------------------------------------------------------
# 发布链路 trace
# ---------------------------------------------------------------------------
class TraceContext:
    """一次 publish/update 的全链路 trace。

    用法：
        with TraceContext("publish", article_id=3, platforms=["csdn","juejin"]) as t:
            ...每个平台子步骤调 t.step("csdn.publish", dur=2.1, ok=True)
        # 退出时 t.commit() 把整条 trace 落 events.log 一条

    单条 trace 控制在合理行数内（平台数 × 每平台 1-3 step），不会膨胀。
    """

    def __init__(self, op: str, **ctx):
        self.op = op
        self.trace_id = uuid.uuid4().hex[:12]
        self.started = time.time()
        self.ctx = ctx
        self.steps = []

    def step(self, name: str, dur: float = 0.0, ok: bool = True, detail: str = ""):
        self.steps.append({"name": name, "dur": round(dur, 3),
                           "ok": ok, "detail": detail[:200]})
        METRICS.incr(f"{self.op}.step.{ 'ok' if ok else 'fail' }")
        if ok:
            METRICS.incr(f"{self.op}.ok")
        else:
            METRICS.incr(f"{self.op}.fail")

    def commit(self):
        dur = round(time.time() - self.started, 3)
        ok = all(s["ok"] for s in self.steps) if self.steps else True
        emit("trace", trace_id=self.trace_id, op=self.op,
             trace_ok=ok, duration=dur, steps=self.steps, **self.ctx)
        METRICS.observe(f"{self.op}.dur", dur)
        return self.trace_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *a):
        if exc_type is not None:
            self.step(f"{self.op}.exc", dur=0, ok=False, detail=str(exc_type)[:120])
        self.commit()
        return False


def begin_api_trace(method: str, path: str) -> TraceContext:
    t = TraceContext("api", method=method, path=path)
    return t


# ---------------------------------------------------------------------------
# 审计日志（自 utils/audit.py 合并）
# ---------------------------------------------------------------------------

_audit_module_logger = get_logger(__name__)


class AuditLevel(str, Enum):
    """审计级别"""
    INFO = "info"           # 一般信息
    WARNING = "warning"     # 警告
    ERROR = "error"         # 错误
    CRITICAL = "critical"   # 严重


class AuditAction(str, Enum):
    """审计操作类型"""
    # 认证相关
    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    PASSWORD_CHANGE = "password_change"

    # 数据操作
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"

    # 系统操作
    SYSTEM_CONFIG = "system_config"
    USER_MANAGEMENT = "user_management"
    PERMISSION_CHANGE = "permission_change"

    # 安全事件
    SECURITY_VIOLATION = "security_violation"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    ACCESS_DENIED = "access_denied"


@dataclass
class AuditEvent:
    """审计事件数据类"""

    # 基础信息
    timestamp: str
    level: AuditLevel
    action: AuditAction
    message: str

    # 用户信息
    user_id: Optional[str] = None
    username: Optional[str] = None
    user_role: Optional[str] = None

    # 请求信息
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_method: Optional[str] = None
    request_path: Optional[str] = None
    request_id: Optional[str] = None

    # 资源信息
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None

    # 操作结果
    success: bool = True
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    # 附加数据
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)

    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


class AuditLogger:
    """审计日志记录器"""

    def __init__(self):
        self.logger = setup_logger("audit")
        self.events_queue: deque[AuditEvent] = deque(maxlen=200)  # 有界队列防 OOM
        self._setup_audit_logger()

    def _setup_audit_logger(self):
        """设置审计日志器"""
        import logging.handlers
        from pathlib import Path

        # 创建审计日志目录
        audit_log_dir = Path("logs/audit")
        audit_log_dir.mkdir(parents=True, exist_ok=True)

        # 审计日志格式
        audit_formatter = logging.Formatter(
            '%(asctime)s - AUDIT - %(levelname)s - %(message)s'
        )

        # 审计日志文件处理器
        audit_handler = logging.handlers.RotatingFileHandler(
            audit_log_dir / "audit.log",
            maxBytes=50 * 1024 * 1024,  # 50MB
            backupCount=10,
            encoding='utf-8'
        )
        audit_handler.setLevel(logging.INFO)
        audit_handler.setFormatter(audit_formatter)

        # 安全事件日志处理器
        security_handler = logging.handlers.RotatingFileHandler(
            audit_log_dir / "security.log",
            maxBytes=50 * 1024 * 1024,  # 50MB
            backupCount=10,
            encoding='utf-8'
        )
        security_handler.setLevel(logging.WARNING)
        security_handler.setFormatter(audit_formatter)

        # 配置审计日志器
        audit_logger = logging.getLogger("audit")
        audit_logger.setLevel(logging.INFO)
        audit_logger.addHandler(audit_handler)
        audit_logger.addHandler(security_handler)

        self.logger = audit_logger

    def log_event(self, event: AuditEvent):
        """记录审计事件"""
        try:
            # 添加到事件队列
            self.events_queue.append(event)

            # 根据级别选择日志级别
            log_level_map = {
                AuditLevel.INFO: logging.INFO,
                AuditLevel.WARNING: logging.WARNING,
                AuditLevel.ERROR: logging.ERROR,
                AuditLevel.CRITICAL: logging.CRITICAL
            }

            log_level = log_level_map.get(event.level, logging.INFO)

            # 记录到日志文件
            self.logger.log(log_level, event.to_json())

            # 异步处理（如发送到SIEM系统）
            if event.level in [AuditLevel.WARNING, AuditLevel.ERROR, AuditLevel.CRITICAL]:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._handle_critical_event(event))
                except RuntimeError:
                    pass  # 无事件循环时跳过异步处理

        except Exception as e:
            _audit_module_logger.error(f"审计日志记录失败: {e}")

    async def _handle_critical_event(self, event: AuditEvent):
        """处理关键事件"""
        try:
            # 这里可以集成到SIEM系统、发送告警邮件等
            _audit_module_logger.critical(f"关键安全事件: {event.message}")

            # 可以添加更多处理逻辑，如：
            # - 发送到Elasticsearch
            # - 推送到Slack/钉钉
            # - 发送邮件通知
            # - 触发自动防护措施

        except Exception as e:
            _audit_module_logger.error(f"关键事件处理失败: {e}")

    def log_authentication(
        self,
        action: AuditAction,
        username: str,
        ip_address: str,
        success: bool = True,
        error_message: str = None,
        request: Request = None
    ):
        """记录认证相关事件"""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=AuditLevel.INFO if success else AuditLevel.WARNING,
            action=action,
            message=f"用户 {username} {action.value} {'成功' if success else '失败'}",
            username=username,
            ip_address=ip_address,
            user_agent=request.headers.get("user-agent") if request else None,
            request_method=request.method if request else None,
            request_path=str(request.url.path) if request else None,
            success=success,
            error_message=error_message
        )

        self.log_event(event)

    def log_data_operation(
        self,
        action: AuditAction,
        resource_type: str,
        resource_id: str,
        user_id: str = None,
        username: str = None,
        ip_address: str = None,
        success: bool = True,
        error_message: str = None,
        metadata: Dict[str, Any] = None,
        request: Request = None
    ):
        """记录数据操作事件"""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=AuditLevel.INFO if success else AuditLevel.ERROR,
            action=action,
            message=f"{action.value} {resource_type} {resource_id} {'成功' if success else '失败'}",
            user_id=user_id,
            username=username,
            ip_address=ip_address or (request.client.host if request and request.client else None),
            user_agent=request.headers.get("user-agent") if request else None,
            request_method=request.method if request else None,
            request_path=str(request.url.path) if request else None,
            resource_type=resource_type,
            resource_id=resource_id,
            success=success,
            error_message=error_message,
            metadata=metadata
        )

        self.log_event(event)

    def log_security_event(
        self,
        action: AuditAction,
        message: str,
        level: AuditLevel = AuditLevel.WARNING,
        ip_address: str = None,
        user_id: str = None,
        username: str = None,
        metadata: Dict[str, Any] = None,
        request: Request = None
    ):
        """记录安全事件"""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=level,
            action=action,
            message=message,
            user_id=user_id,
            username=username,
            ip_address=ip_address or (request.client.host if request and request.client else None),
            user_agent=request.headers.get("user-agent") if request else None,
            request_method=request.method if request else None,
            request_path=str(request.url.path) if request else None,
            success=False,  # 安全事件通常表示异常情况
            metadata=metadata
        )

        self.log_event(event)

    def log_system_event(
        self,
        action: AuditAction,
        message: str,
        user_id: str = None,
        username: str = None,
        ip_address: str = None,
        metadata: Dict[str, Any] = None,
        request: Request = None
    ):
        """记录系统事件"""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=AuditLevel.INFO,
            action=action,
            message=message,
            user_id=user_id,
            username=username,
            ip_address=ip_address or (request.client.host if request and request.client else None),
            user_agent=request.headers.get("user-agent") if request else None,
            request_method=request.method if request else None,
            request_path=str(request.url.path) if request else None,
            metadata=metadata
        )

        self.log_event(event)

    def get_recent_events(self, limit: int = 100) -> List[AuditEvent]:
        """获取最近的审计事件"""
        return self.events_queue[-limit:] if self.events_queue else []

    def clear_events_queue(self):
        """清空事件队列"""
        self.events_queue.clear()


# 全局审计日志实例（保持与 utils/audit.py 相同的单例名）
audit_logger = AuditLogger()


def audit_operation(
    action: AuditAction,
    resource_type: str = None,
    level: AuditLevel = AuditLevel.INFO,
    include_request_data: bool = False,
    include_response_data: bool = False
):
    """审计操作装饰器"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = datetime.now(timezone.utc)
            request = None
            user_info = {}

            # 尝试从参数中提取Request对象和用户信息
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break

            # 从kwargs中提取更多信息
            if 'request' in kwargs:
                request = kwargs['request']
            if 'current_user' in kwargs:
                user = kwargs['current_user']
                user_info = {
                    'user_id': getattr(user, 'id', None),
                    'username': getattr(user, 'username', None)
                }

            try:
                # 执行函数
                result = await func(*args, **kwargs)

                # 记录成功的操作
                metadata = {
                    'execution_time': (datetime.now(timezone.utc) - start_time).total_seconds(),
                    'function_name': func.__name__
                }

                if include_request_data and request:
                    metadata['request_data'] = {
                        'method': request.method,
                        'path': str(request.url.path),
                        'query_params': dict(request.query_params)
                    }

                if include_response_data:
                    metadata['response_data'] = str(result)[:1000]  # 限制长度

                audit_logger.log_data_operation(
                    action=action,
                    resource_type=resource_type or func.__name__,
                    resource_id=kwargs.get('id', 'unknown'),
                    user_id=str(user_info.get('user_id')) if user_info.get('user_id') else None,
                    username=user_info.get('username'),
                    success=True,
                    metadata=metadata,
                    request=request
                )

                return result

            except Exception as e:
                # 记录失败的操作
                metadata = {
                    'execution_time': (datetime.now(timezone.utc) - start_time).total_seconds(),
                    'function_name': func.__name__,
                    'error_type': type(e).__name__
                }

                audit_logger.log_data_operation(
                    action=action,
                    resource_type=resource_type or func.__name__,
                    resource_id=kwargs.get('id', 'unknown'),
                    user_id=str(user_info.get('user_id')) if user_info.get('user_id') else None,
                    username=user_info.get('username'),
                    success=False,
                    error_message=str(e),
                    metadata=metadata,
                    request=request
                )

                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # 同步函数的包装器
            start_time = datetime.now(timezone.utc)

            try:
                result = func(*args, **kwargs)

                metadata = {
                    'execution_time': (datetime.now(timezone.utc) - start_time).total_seconds(),
                    'function_name': func.__name__
                }

                audit_logger.log_data_operation(
                    action=action,
                    resource_type=resource_type or func.__name__,
                    resource_id=kwargs.get('id', 'unknown'),
                    success=True,
                    metadata=metadata
                )

                return result

            except Exception as e:
                metadata = {
                    'execution_time': (datetime.now(timezone.utc) - start_time).total_seconds(),
                    'function_name': func.__name__,
                    'error_type': type(e).__name__
                }

                audit_logger.log_data_operation(
                    action=action,
                    resource_type=resource_type or func.__name__,
                    resource_id=kwargs.get('id', 'unknown'),
                    success=False,
                    error_message=str(e),
                    metadata=metadata
                )

                raise

        # 判断是否为异步函数
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def audit_security_event(
    action: AuditAction = AuditAction.SECURITY_VIOLATION,
    level: AuditLevel = AuditLevel.WARNING
):
    """安全事件审计装饰器"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            request = None

            # 提取Request对象
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if 'request' in kwargs:
                request = kwargs['request']

            try:
                result = await func(*args, **kwargs)
                return result

            except Exception as e:
                # 记录安全事件
                audit_logger.log_security_event(
                    action=action,
                    message=f"安全事件: {func.__name__} - {str(e)}",
                    level=level,
                    metadata={
                        'function_name': func.__name__,
                        'error_type': type(e).__name__,
                        'error_message': str(e)
                    },
                    request=request
                )

                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                result = func(*args, **kwargs)
                return result

            except Exception as e:
                audit_logger.log_security_event(
                    action=action,
                    message=f"安全事件: {func.__name__} - {str(e)}",
                    level=level,
                    metadata={
                        'function_name': func.__name__,
                        'error_type': type(e).__name__,
                        'error_message': str(e)
                    }
                )

                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator
