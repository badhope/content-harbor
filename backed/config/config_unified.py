"""统一配置模块 - 包含所有环境的配置类"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import List, Dict, Any, Optional
from enum import Enum
import os
import json
from pathlib import Path

# 项目根目录（config/ 的上一级），保证无论从哪启动都能读到根目录 .env.*
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_files(*names: str) -> tuple[str, ...]:
    """把相对文件名解析为项目根下的绝对路径。"""
    return tuple(str(_PROJECT_ROOT / name) for name in names)


class Environment(str, Enum):
    """环境枚举"""
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(str, Enum):
    """日志级别枚举"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class BaseConfig(BaseSettings):
    """基础配置类 - 所有环境的公共配置"""
    
    # =================================================================
    # 环境和项目基础信息
    # =================================================================
    environment: Environment = Environment.DEVELOPMENT
    project_name: str = "ai-content-hub-backend"
    project_version: str = "0.13.2"
    api_version: str = "v1"
    api_prefix: str = "/api/v1/hub"
    
    # =================================================================
    # 数据库基础配置
    # =================================================================
    database_url: str = "sqlite+aiosqlite:///./data/hub.db"
    
    # 数据库连接池基础配置
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_pool_recycle: int = 3600
    db_pool_pre_ping: bool = True
    db_pool_reset_on_return: str = "commit"
    
    # 数据库引擎配置
    db_echo: bool = False
    db_echo_pool: bool = False
    db_isolation_level: str = "READ_COMMITTED"
    
    # MySQL特定配置
    db_connect_timeout: int = 60
    db_read_timeout: int = 30
    db_write_timeout: int = 30
    db_charset: str = "utf8mb4"
    
    # =================================================================
    # 安全配置
    # =================================================================
    secret_key: str = "your-secret-key-change-in-production"
    jwt_secret_key: Optional[str] = None  # 如果不设置，将使用secret_key
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    
    # 密码安全
    password_min_length: int = 8
    password_require_special_chars: bool = True
    password_require_numbers: bool = True
    password_require_uppercase: bool = True
    
    # =================================================================
    # Redis配置
    # =================================================================
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None
    redis_url: Optional[str] = None
    
    # =================================================================
    # 应用基础配置
    # =================================================================
    debug: bool = True
    log_level: LogLevel = LogLevel.INFO
    timezone: str = "Asia/Shanghai"
    
    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    
    # =================================================================
    # 安全基础配置
    # =================================================================
    allowed_hosts: List[str] = []
    cors_origins: List[str] = ["*"]
    cors_allow_credentials: bool = True
    cors_allow_methods: List[str] = ["*"]
    cors_allow_headers: List[str] = ["*"]
    
    # 速率限制
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 100
    rate_limit_burst: int = 20
    
    # =================================================================
    # 文件上传配置
    # =================================================================
    max_request_size: int = 10 * 1024 * 1024  # 10MB
    max_file_size: int = 50 * 1024 * 1024     # 50MB
    upload_dir: str = "./uploads"
    quarantine_dir: str = "./quarantine"
    
    allowed_file_extensions: List[str] = [
        '.jpg', '.jpeg', '.png', '.gif', '.pdf', '.doc', '.docx',
        '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.zip'
    ]
    
    allowed_mime_types: List[str] = [
        'image/jpeg', 'image/png', 'image/gif', 'image/bmp',
        'application/pdf', 'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'text/plain', 'text/csv', 'application/json'
    ]
    
    # =================================================================
    # 审计和监控配置
    # =================================================================
    audit_log_enabled: bool = True
    audit_log_level: LogLevel = LogLevel.INFO
    audit_log_request_body: bool = False
    audit_log_response_body: bool = False
    audit_log_max_events: int = 10000
    
    # 安全监控
    security_monitoring: bool = True
    failed_login_threshold: int = 5
    suspicious_activity_threshold: int = 10
    
    # SQL注入和XSS防护
    sql_injection_protection: bool = True
    xss_protection: bool = True

    # =================================================================
    # 自定义中间件注册开关（统一在 app/middleware.py 中读取）
    # 默认全部关闭：避免在未显式开启时影响请求链路与切片测试。
    # 按环境在对应配置类中置 True 即可启用对应中间件。
    # =================================================================
    middleware_security_headers_enabled: bool = False
    middleware_rate_limit_enabled: bool = False
    middleware_audit_enabled: bool = False

    # =================================================================
    # 第三方服务配置
    # =================================================================
    # 邮件服务
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    
    # 监控服务
    prometheus_enabled: bool = False
    prometheus_port: int = 9090
    
    # APM服务
    new_relic_license_key: Optional[str] = None
    datadog_api_key: Optional[str] = None
    
    # =================================================================
    # 缓存配置
    # =================================================================
    cache_enabled: bool = True
    cache_ttl: int = 300  # 5分钟
    cache_prefix: str = "fastapi_security"
    
    # =================================================================
    # 特性开关
    # =================================================================
    feature_flags: Dict[str, bool] = {
        "user_registration": True,
        "password_reset": True,
        "two_factor_auth": False,
        "social_login": False,
        "api_versioning": True,
        "request_logging": True,
        "response_compression": True,
        "health_checks": True,
    }
    
    # API文档配置
    docs_url: str = "/docs"
    redoc_url: str = "/redoc"
    openapi_url: str = "/openapi.json"
    
    @field_validator(
        "allowed_hosts", "cors_origins", "cors_allow_methods",
        "cors_allow_headers", "allowed_file_extensions",
        "allowed_mime_types",
        mode="before",
    )
    @classmethod
    def _coerce_env_lists(cls, value: Any) -> Any:
        """兼容环境变量 / .env 中以 JSON 字符串或逗号分隔字符串形式传入的列表字段。

        注意：pydantic-settings 的 model_validator(mode="before") 收不到来自
        .env / 环境变量的注入值，因此这里改用 field_validator(mode="before")，
        它会对每个字段（含 .env 注入值）生效。

        例如 .env 里的 ALLOWED_HOSTS='["a","b"]' 或 ALLOWED_HOSTS=a,b
        都会被正确解析为 list[str]，避免 Pydantic 校验失败导致应用无法启动。
        """
        if isinstance(value, str):
            stripped = value.strip()
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            # 退化为逗号分隔
            return [v.strip() for v in stripped.split(",") if v.strip()]
        return value

    @property
    def effective_redis_url(self) -> str:
        """获取有效的Redis URL"""
        if self.redis_url:
            return self.redis_url
        
        auth_part = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth_part}{self.redis_host}:{self.redis_port}/{self.redis_db}"
    
    @property
    def effective_jwt_secret_key(self) -> str:
        """获取有效的JWT密钥"""
        return self.jwt_secret_key or self.secret_key
    
    @property
    def log_config(self) -> Dict[str, Any]:
        """获取日志配置"""
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
                "json": {
                    "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
                    "format": "%(asctime)s %(name)s %(levelname)s %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "level": self.log_level,
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "json",
                    "filename": "./logs/app.log",
                    "maxBytes": 10485760,  # 10 MB
                    "backupCount": 5,
                    "level": self.log_level,
                },
            },
            "loggers": {
                "app": {
                    "handlers": ["console", "file"],
                    "level": self.log_level,
                    "propagate": False,
                },
                "uvicorn": {
                    "handlers": ["console", "file"],
                    "level": self.log_level,
                    "propagate": False,
                },
                "fastapi": {
                    "handlers": ["console", "file"],
                    "level": self.log_level,
                    "propagate": False,
                },
            },
            "root": {
                "handlers": ["console", "file"],
                "level": self.log_level,
            },
        }
    
    def model_post_init(self, __context) -> None:
        """模型初始化后的处理"""
        # 确保日志目录存在
        log_dir = Path("./logs")
        log_dir.mkdir(exist_ok=True)
    
    model_config = SettingsConfigDict(
        env_file=_env_files(".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )


class DevelopmentConfig(BaseConfig):
    """开发环境配置"""
    
    # =================================================================
    # 环境基础配置
    # =================================================================
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = True
    log_level: LogLevel = LogLevel.DEBUG
    
    # =================================================================
    # 数据库配置 - 开发环境
    # =================================================================
    database_url: str = "sqlite+aiosqlite:///./data/hub.db"
    
    # 开发环境数据库连接池配置（较小）
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_echo: bool = True  # 开发环境显示SQL
    db_echo_pool: bool = False
    
    # =================================================================
    # Redis配置 - 开发环境
    # =================================================================
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 1  # 使用不同的数据库避免冲突
    redis_password: str = ""
    
    # =================================================================
    # 安全配置 - 开发环境（相对宽松）
    # =================================================================
    secret_key: str = "dev-secret-key-not-for-production-use-only"
    access_token_expire_minutes: int = 60 * 24  # 24小时，方便开发测试
    
    # CORS配置 - 开发环境允许本地前端
    cors_origins: List[str] = [
        "http://localhost:3000",    # React默认端口
        "http://localhost:3001",    # React备用端口
        "http://localhost:8080",    # Vue默认端口
        "http://localhost:8081",    # Vue备用端口
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8080",
        "http://localhost:5173",    # Vite默认端口
        "http://127.0.0.1:5173",
    ]
    
    allowed_hosts: List[str] = [
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "*.localhost",
        "*.127.0.0.1",
    ]
    
    # =================================================================
    # 速率限制 - 开发环境（宽松）
    # =================================================================
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 1000  # 开发环境更宽松
    rate_limit_burst: int = 100
    
    # =================================================================
    # 文件上传 - 开发环境
    # =================================================================
    max_request_size: int = 50 * 1024 * 1024   # 50MB
    max_file_size: int = 100 * 1024 * 1024     # 100MB
    upload_dir: str = "./dev_uploads"
    quarantine_dir: str = "./dev_quarantine"
    
    # =================================================================
    # 监控和审计 - 开发环境
    # =================================================================
    audit_log_enabled: bool = True
    audit_log_request_body: bool = True   # 开发环境记录请求体便于调试
    audit_log_response_body: bool = True  # 开发环境记录响应体便于调试
    
    # 安全监控阈值（宽松）
    failed_login_threshold: int = 20
    suspicious_activity_threshold: int = 50
    
    # =================================================================
    # 开发工具配置
    # =================================================================
    # 自动重载
    reload: bool = True
    reload_dirs: List[str] = ["./api", "./app", "./config", "./models"]
    
    # 开发环境特有的特性开关
    feature_flags: Dict[str, bool] = {
        "user_registration": True,
        "password_reset": True,
        "two_factor_auth": False,
        "social_login": False,
        "api_versioning": True,
        "request_logging": True,
        "response_compression": True,
        "health_checks": True,
        "debug_toolbar": True,
        "sql_debug": True,
        "mock_external_apis": True,
        "seed_test_data": True,
        "api_playground": True,
        "dev_routes": True,
    }
    
    # =================================================================
    # 测试和调试配置
    # =================================================================
    # 测试数据库
    test_database_url: str = "sqlite+aiosqlite:///./data/test_hub.db"
    
    # 邮件测试（使用MailHog或类似服务）
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    
    # =================================================================
    # 开发环境专用路径
    # =================================================================
    templates_dir: str = "./templates"
    
    # =================================================================
    # 性能配置 - 开发环境
    # =================================================================
    workers: int = 1  # 开发环境单进程便于调试
    
    # 缓存配置
    cache_enabled: bool = False  # 开发环境禁用缓存便于调试
    cache_ttl: int = 60  # 如果启用缓存，使用较短的TTL
    
    # =================================================================
    # API文档配置 - 开发环境
    # =================================================================
    docs_url: str = "/docs"
    redoc_url: str = "/redoc"
    openapi_url: str = "/openapi.json"
    
    # Swagger UI配置
    swagger_ui_parameters: dict = {
        "deepLinking": True,
        "displayRequestDuration": True,
        "docExpansion": "list",
        "operationsSorter": "method",
        "filter": True,
        "tryItOutEnabled": True,
    }
    
    model_config = SettingsConfigDict(
        env_file=_env_files(".env.development", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )


class TestingConfig(BaseConfig):
    """测试环境配置"""
    
    # =================================================================
    # 环境基础配置
    # =================================================================
    environment: Environment = Environment.TESTING
    debug: bool = True
    log_level: LogLevel = LogLevel.DEBUG
    
    # =================================================================
    # 数据库配置 - 测试环境
    # =================================================================
    database_url: str = "sqlite+aiosqlite:///./data/test_hub.db"
    
    # 测试环境数据库连接池配置
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = True  # 测试环境显示SQL便于调试
    
    # =================================================================
    # Redis配置 - 测试环境
    # =================================================================
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 2  # 使用专门的测试数据库
    
    # =================================================================
    # 安全配置 - 测试环境
    # =================================================================
    secret_key: str = "test-secret-key-for-testing-only"
    access_token_expire_minutes: int = 60  # 测试环境短期令牌
    
    # =================================================================
    # 测试特定配置
    # =================================================================
    # 测试数据
    test_user_email: str = "test@example.com"
    test_user_password: str = "Test@123456"
    
    # 测试覆盖率
    coverage_enabled: bool = True
    coverage_report_dir: str = "./coverage"
    
    # 测试性能
    performance_testing: bool = True
    
    # 缓存配置
    cache_enabled: bool = False  # 测试环境禁用缓存
    
    # 文件上传 - 测试环境
    upload_dir: str = "./test_uploads"
    quarantine_dir: str = "./test_quarantine"
    
    model_config = SettingsConfigDict(
        env_file=_env_files(".env.testing", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )


class StagingConfig(BaseConfig):
    """预发布环境配置"""
    
    # =================================================================
    # 环境基础配置
    # =================================================================
    environment: Environment = Environment.STAGING
    debug: bool = False
    log_level: LogLevel = LogLevel.INFO
    
    # =================================================================
    # 数据库配置 - 预发布环境
    # =================================================================
    database_url: str = os.environ.get("HARBOR_DATABASE_URL", "sqlite+aiosqlite:///./data/staging_hub.db")
    
    # 预发布环境数据库连接池配置
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False
    
    # =================================================================
    # Redis配置 - 预发布环境
    # =================================================================
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = "staging_redis_password"
    
    # =================================================================
    # 安全配置 - 预发布环境
    # =================================================================
    secret_key: str = os.environ.get("HARBOR_SECRET_KEY", "")
    
    # CORS配置 - 预发布环境
    cors_origins: List[str] = [
        "https://staging.example.com",
        "https://*.staging.example.com",
    ]
    
    allowed_hosts: List[str] = [
        "staging.example.com",
        "*.staging.example.com",
        "staging-api.example.com",
    ]
    
    # =================================================================
    # API文档配置 - 预发布环境
    # =================================================================
    docs_url: str = "/docs"  # 预发布环境保留文档
    redoc_url: str = "/redoc"
    openapi_url: str = "/openapi.json"
    
    model_config = SettingsConfigDict(
        env_file=_env_files(".env.staging", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )


class ProductionConfig(BaseConfig):
    """生产环境配置"""
    
    # =================================================================
    # 环境基础配置
    # =================================================================
    environment: Environment = Environment.PRODUCTION
    debug: bool = False
    log_level: LogLevel = LogLevel.WARNING
    
    # =================================================================
    # 数据库配置 - 生产环境
    # =================================================================
    database_url: str = os.environ.get("HARBOR_DATABASE_URL", "sqlite+aiosqlite:////app/data/hub.db")
    
    # 生产环境数据库连接池配置
    db_pool_size: int = 20
    db_max_overflow: int = 30
    db_echo: bool = False
    db_echo_pool: bool = False
    
    # =================================================================
    # Redis配置 - 生产环境
    # =================================================================
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = "prod_redis_password"
    
    # =================================================================
    # 安全配置 - 生产环境
    # =================================================================
    secret_key: str = os.environ.get("HARBOR_SECRET_KEY", "")
    
    # CORS配置 - 生产环境
    cors_origins: List[str] = [
        "https://example.com",
        "https://*.example.com",
    ]
    
    allowed_hosts: List[str] = [
        "example.com",
        "*.example.com",
        "api.example.com",
    ]
    
    # =================================================================
    # 速率限制 - 生产环境
    # =================================================================
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 60  # 生产环境更严格
    rate_limit_burst: int = 10
    
    # =================================================================
    # API文档配置 - 生产环境
    # =================================================================
    docs_url: Optional[str] = None  # 生产环境禁用文档
    redoc_url: Optional[str] = None
    openapi_url: Optional[str] = None
    
    # =================================================================
    # 性能配置 - 生产环境
    # =================================================================
    workers: int = 4  # 生产环境多进程
    
    # 缓存配置
    cache_enabled: bool = True
    cache_ttl: int = 300  # 5分钟
    
    model_config = SettingsConfigDict(
        env_file=_env_files(".env.production", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )


class DockerConfig(ProductionConfig):
    """Docker Compose 生产配置（读取 .env.docker）"""

    environment: Environment = Environment.PRODUCTION
    database_url: str = os.environ.get("HARBOR_DATABASE_URL", "sqlite+aiosqlite:////app/data/hub.db")
    allowed_hosts: List[str] = ["*"]
    cors_origins: List[str] = ["*"]

    model_config = SettingsConfigDict(
        env_file=_env_files(".env.docker", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )
