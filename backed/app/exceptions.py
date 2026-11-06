"""异常处理器配置"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from utils.logger import get_logger, log_security_event
from service.publishing.gate import GateError
from utils.custom_exceptions import BaseAPIException
from utils.response_code import ResponseCode

logger = get_logger(__name__)


class ValidationErrorDetail(BaseModel):
    field: str
    message: str
    value: str | None = None


class ErrorResponse(BaseModel):
    code: int
    message: str
    success: bool
    data: object = None


class ValidationErrorResponse(BaseModel):
    code: int
    message: str
    success: bool
    data: list[ValidationErrorDetail]


def setup_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(GateError)
    async def gate_exception_handler(request: Request, exc: GateError):
        """合规门禁拒绝：语义 422（内容不合规），不是 500。"""
        return JSONResponse(status_code=422,
                            content={"code": 422,
                                     "message": f"合规门禁拒绝发布: {exc}",
                                     "success": False, "data": None})

    @app.exception_handler(BaseAPIException)
    async def api_exception_handler(request: Request, exc: BaseAPIException):
        log_security_event(
            "API_EXCEPTION",
            details={
                "exception_type": exc.__class__.__name__,
                "message": exc.detail,
                "path": str(request.url.path),
                "method": request.method,
            },
        )
        body = ErrorResponse(
            code=exc.response_code.code,
            message=exc.detail or exc.response_code.message,
            success=False,
            data=None,
        )
        return JSONResponse(
            status_code=exc.response_code.status_code,
            content=body.model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        errors = []
        for error in exc.errors():
            field = ".".join(str(loc) for loc in error["loc"][1:])
            errors.append(
                ValidationErrorDetail(
                    field=field,
                    message=error["msg"],
                    value=str(error.get("input", "")),
                )
            )
        logger.warning("Validation error path=%s", request.url.path)
        body = ValidationErrorResponse(
            data=errors, code=422, message="数据验证失败", success=False
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            response_code = ResponseCode.NOT_FOUND
        elif exc.status_code == 405:
            response_code = ResponseCode.METHOD_NOT_ALLOWED
        elif exc.status_code >= 500:
            response_code = ResponseCode.INTERNAL_SERVER_ERROR
        else:
            response_code = ResponseCode.BAD_REQUEST
        body = ErrorResponse(
            code=response_code.code,
            message=str(exc.detail) if exc.detail else response_code.message,
            success=False,
            data=None,
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        log_security_event(
            "UNHANDLED_EXCEPTION",
            details={
                "exception_type": exc.__class__.__name__,
                "message": str(exc),
                "path": str(request.url.path),
                "method": request.method,
            },
        )
        # 响应体只给固定文案：即使 debug=True 也不能把异常字符串回给客户端。
        # 异常字符串经常包含 SQL 片段、文件路径、上游响应体——这是最容易被
        # 忽略的信息泄漏面（实测 500 里能直接读到本地绝对路径）。
        # 完整信息只进日志（含 traceback），排障时看日志即可。
        logger.exception("未处理异常 path=%s method=%s", request.url.path, request.method)
        body = ErrorResponse(
            code=ResponseCode.INTERNAL_SERVER_ERROR.code,
            message=ResponseCode.INTERNAL_SERVER_ERROR.message,
            success=False,
            data=None,
        )
        return JSONResponse(status_code=500, content=body.model_dump())
