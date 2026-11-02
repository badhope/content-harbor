"""中间件包初始化（项目基线 BASELINE）

【BASELINE】由 sql-to-fastapi-restfulapi-scaffold 提供，见 project_baseline.json。
空项目首次写入；目标已有 middleware/ 则整包跳过。

仅导出企业级常用中间件：
- RateLimitMiddleware       限流（OWASP API4）
- SecurityHeadersMiddleware 安全响应头
- AuditMiddleware           HTTP 审计日志

由 app/middleware.py 按配置开关注册（默认关闭，避免干扰测试）。
"""

from .rate_limiter import RateLimitMiddleware, RateLimiter
from .security_headers import SecurityHeadersMiddleware
from .audit_middleware import AuditMiddleware
from .public_base import PublicBaseMiddleware

__all__ = [
    "RateLimitMiddleware",
    "RateLimiter",
    "SecurityHeadersMiddleware",
    "AuditMiddleware",
    "PublicBaseMiddleware",
]
