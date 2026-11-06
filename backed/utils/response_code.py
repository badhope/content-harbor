"""响应码定义模块"""

from enum import Enum


class ResponseCode(Enum):
    """响应码枚举"""
    
    # 成功
    SUCCESS = (200, "操作成功")
    CREATED = (201, "创建成功")
      # 客户端错误
    BAD_REQUEST = (400, "请求参数错误")
    INVALID_REQUEST = (400, "无效请求")
    UNAUTHORIZED = (401, "未授权访问")
    FORBIDDEN = (403, "禁止访问")
    NOT_FOUND = (404, "资源不存在")
    RESOURCE_NOT_FOUND = (404, "资源不存在")
    METHOD_NOT_ALLOWED = (405, "方法不允许")
    CONFLICT = (409, "资源冲突")
    PAYLOAD_TOO_LARGE = (413, "请求体过大")
    VALIDATION_ERROR = (422, "数据验证失败")
    TOO_MANY_REQUESTS = (429, "请求过于频繁")
    
    # 服务器错误
    INTERNAL_SERVER_ERROR = (500, "服务器内部错误")
    INTERNAL_ERROR = (500, "内部错误")
    BAD_GATEWAY = (502, "网关错误")
    SERVICE_UNAVAILABLE = (503, "服务不可用")
    
    # 业务错误
    USER_NOT_FOUND = (1001, "用户不存在")
    INVALID_PASSWORD = (1002, "密码错误")
    USER_DISABLED = (1003, "用户已被禁用")
    TOKEN_EXPIRED = (1004, "令牌已过期")
    INVALID_TOKEN = (1005, "无效的令牌")
    PERMISSION_DENIED = (1006, "权限不足")
    RESOURCE_NOT_OWNED = (1007, "资源不属于当前用户")
    
    # 数据相关错误
    DATA_NOT_FOUND = (2001, "数据不存在")
    DATA_ALREADY_EXISTS = (2002, "数据已存在")
    DATA_VALIDATION_ERROR = (2003, "数据验证失败")
    DATABASE_ERROR = (2004, "数据库操作失败")
    
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
    
    @property
    def status_code(self) -> int:
        """获取HTTP状态码"""
        if self.code < 1000:
            return self.code
        return 400  # 业务错误默认返回400
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "code": self.code,
            "message": self.message
        }
