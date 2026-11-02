"""配置工厂模块 - 简化版本"""

import os
from typing import Dict, Type, Any, Optional
from functools import lru_cache

from .config_unified import (
    BaseConfig,
    DevelopmentConfig,
    TestingConfig,
    StagingConfig,
    ProductionConfig,
    DockerConfig,
    Environment,
)


class ConfigFactory:
    """配置工厂类"""
    
    # 环境配置映射
    CONFIG_MAPPING: Dict[str, Type[BaseConfig]] = {
        Environment.DEVELOPMENT: DevelopmentConfig,
        Environment.TESTING: TestingConfig,
        Environment.STAGING: StagingConfig,
        Environment.PRODUCTION: ProductionConfig,
        "docker": DockerConfig,
    }
    
    @classmethod
    def create_config(cls, environment: Optional[str] = None) -> BaseConfig:
        """
        创建配置实例
        
        Args:
            environment: 环境名称，如果为None则从环境变量获取
            
        Returns:
            BaseConfig: 配置实例
        """
        if environment is None:
            environment = os.getenv("FASTAPI_ENV", Environment.DEVELOPMENT)
        
        # 标准化环境名称
        environment = environment.lower()
        
        # 映射别名
        env_aliases = {
            "dev": Environment.DEVELOPMENT,
            "development": Environment.DEVELOPMENT,
            "docker": "docker",  # 容器起步栈：读取 .env.docker
            "test": Environment.TESTING,
            "testing": Environment.TESTING,
            "stage": Environment.STAGING,
            "staging": Environment.STAGING,
            "prod": Environment.PRODUCTION,
            "production": Environment.PRODUCTION,
        }
        
        environment = env_aliases.get(environment, environment)
        
        # 获取配置类
        config_class = cls.CONFIG_MAPPING.get(environment)
        if config_class is None:
            raise ValueError(f"不支持的环境: {environment}")
        
        return config_class()
    
    @classmethod
    def get_available_environments(cls) -> list:
        """获取可用的环境列表"""
        return list(cls.CONFIG_MAPPING.keys())


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, config: BaseConfig):
        self.config = config
        self._validated = False
    
    def validate_config(self) -> Dict[str, Any]:
        """
        验证配置
        
        Returns:
            Dict[str, Any]: 验证结果
        """
        validation_result = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "info": {
                "environment": self.config.environment,
                "debug": self.config.debug,
                "log_level": self.config.log_level,
            }
        }
        
        # 验证必需的配置项
        required_configs = [
            ("secret_key", "SECRET_KEY不能使用默认值"),
            ("database_url", "数据库URL必须配置"),
        ]
        
        for config_name, error_msg in required_configs:
            value = getattr(self.config, config_name, None)
            if not value or (config_name == "secret_key" and "change" in value.lower()):
                validation_result["errors"].append(f"{config_name}: {error_msg}")
                validation_result["valid"] = False
        
        # 生产环境特殊验证
        if self.config.environment == Environment.PRODUCTION:
            prod_validations = self._validate_production_config()
            validation_result["errors"].extend(prod_validations["errors"])
            validation_result["warnings"].extend(prod_validations["warnings"])
            if prod_validations["errors"]:
                validation_result["valid"] = False
        
        # 数据库连接验证
        db_validation = self._validate_database_config()
        validation_result["warnings"].extend(db_validation["warnings"])
        
        # Redis连接验证
        redis_validation = self._validate_redis_config()
        validation_result["warnings"].extend(redis_validation["warnings"])
        
        self._validated = True
        return validation_result
    
    def _validate_production_config(self) -> Dict[str, list]:
        """验证生产环境配置"""
        errors = []
        warnings = []
        
        # 安全配置检查
        if self.config.debug:
            errors.append("生产环境不应启用debug模式")
        
        if not self.config.cors_origins or "*" in self.config.cors_origins:
            warnings.append("生产环境应限制CORS来源")
        
        if not self.config.allowed_hosts or "*" in self.config.allowed_hosts:
            warnings.append("生产环境应限制允许的主机")
        
        # SSL检查
        if not getattr(self.config, 'ssl_enabled', False):
            warnings.append("生产环境建议启用SSL")
        
        # 文档接口检查
        if (self.config.docs_url or self.config.redoc_url or 
            self.config.openapi_url):
            warnings.append("生产环境建议禁用API文档接口")
        
        return {"errors": errors, "warnings": warnings}
    
    def _validate_database_config(self) -> Dict[str, list]:
        """验证数据库配置"""
        warnings = []
        
        # 连接池配置检查
        if self.config.db_pool_size < 5:
            warnings.append("数据库连接池大小可能过小")
        
        if self.config.db_pool_size > 50:
            warnings.append("数据库连接池大小可能过大")
        
        # 生产环境不应该显示SQL
        if (self.config.environment == Environment.PRODUCTION and 
            self.config.db_echo):
            warnings.append("生产环境不应启用SQL日志输出")
        
        return {"warnings": warnings}
    
    def _validate_redis_config(self) -> Dict[str, list]:
        """验证Redis配置"""
        warnings = []
        
        # 生产环境Redis密码检查
        if (self.config.environment == Environment.PRODUCTION and 
            not self.config.redis_password):
            warnings.append("生产环境Redis建议设置密码")
        
        return {"warnings": warnings}
    
    def get_database_config(self) -> Dict[str, Any]:
        """获取数据库配置"""
        return {
            "url": self.config.database_url,
            "pool_size": self.config.db_pool_size,
            "max_overflow": self.config.db_max_overflow,
            "pool_timeout": self.config.db_pool_timeout,
            "pool_recycle": self.config.db_pool_recycle,
            "pool_pre_ping": self.config.db_pool_pre_ping,
            "echo": self.config.db_echo,
            "echo_pool": self.config.db_echo_pool,
            "isolation_level": self.config.db_isolation_level,
            "connect_args": {
                "connect_timeout": self.config.db_connect_timeout,
                "read_timeout": self.config.db_read_timeout,
                "write_timeout": self.config.db_write_timeout,
                "charset": self.config.db_charset,
            }
        }
    
    def get_redis_config(self) -> Dict[str, Any]:
        """获取Redis配置"""
        return {
            "host": self.config.redis_host,
            "port": self.config.redis_port,
            "db": self.config.redis_db,
            "password": self.config.redis_password,
            "url": self.config.effective_redis_url,
        }
    
    def get_security_config(self) -> Dict[str, Any]:
        """获取安全配置"""
        return {
            "secret_key": self.config.secret_key,
            "jwt_secret_key": self.config.effective_jwt_secret_key,
            "algorithm": self.config.algorithm,
            "access_token_expire_minutes": self.config.access_token_expire_minutes,
        }
    
    def is_feature_enabled(self, feature_name: str) -> bool:
        """检查特性是否启用"""
        return self.config.feature_flags.get(feature_name, False)
    
    def get_feature_flags(self) -> Dict[str, bool]:
        """获取所有特性开关"""
        return dict(self.config.feature_flags)
    
    def export_config(self, exclude_sensitive: bool = True) -> Dict[str, Any]:
        """
        导出配置为字典
        
        Args:
            exclude_sensitive: 是否排除敏感信息
            
        Returns:
            Dict[str, Any]: 配置字典
        """
        config_dict = self.config.model_dump()
        
        # 排除敏感信息
        if exclude_sensitive:
            sensitive_keys = [
                "secret_key", "jwt_secret_key", "database_url",
                "redis_password", "smtp_password", "new_relic_license_key",
                "datadog_api_key"
            ]
            
            for key in sensitive_keys:
                if key in config_dict:
                    config_dict[key] = "******"
        
        return config_dict


# 单例配置实例
@lru_cache()
def get_config(env: Optional[str] = None) -> BaseConfig:
    """
    获取配置实例
    
    Args:
        env: 环境名称，如果为None则从环境变量获取
        
    Returns:
        BaseConfig: 配置实例
    """
    return ConfigFactory.create_config(env)


@lru_cache()
def get_config_manager(env: Optional[str] = None) -> ConfigManager:
    """
    获取配置管理器
    
    Args:
        env: 环境名称，如果为None则从环境变量获取
        
    Returns:
        ConfigManager: 配置管理器
    """
    config = get_config(env)
    return ConfigManager(config)


def reload_config(env: Optional[str] = None) -> BaseConfig:
    """
    重新加载配置
    
    Args:
        env: 环境名称，如果为None则从环境变量获取
        
    Returns:
        BaseConfig: 配置实例
    """
    # 清除缓存
    get_config.cache_clear()
    get_config_manager.cache_clear()
    
    # 重新加载
    return get_config(env)


# 全局实例（当前环境配置）
config = get_config()
config_manager = get_config_manager()


def validate_startup_config() -> None:
    """Fail fast only for explicit production/docker startup."""
    environment = str(getattr(config, "environment", "")).lower()
    if environment not in {"production", "environment.production", "docker"}:
        return
    result = config_manager.validate_config()
    if not result["valid"]:
        raise RuntimeError("配置校验失败: " + "; ".join(result["errors"]))


class Settings:
    """兼容门面：属性访问转发到当前环境的 config（config_unified 实例）。

    业务代码统一：`from config import settings`，使用扁平字段
    （如 settings.database_url、settings.debug），不要再引入第二套配置模块。
    """

    def __getattr__(self, name: str):
        return getattr(config, name)


# 对外推荐入口（与 get_config() 指向同一套配置）
settings = Settings() 