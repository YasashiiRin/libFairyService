"""The one error format of the API: { "detail": str, "code": str | None } (api-contract.md#errors)."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# Default codes for errors raised without one (e.g. FastAPI's own 404/405).
_DEFAULT_CODES = {
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "file_too_large",
    422: "validation_error",
}


class ApiError(Exception):
    """Raise from routes and services: ApiError(404, "Không tìm thấy truyện", "comic_not_found")."""

    def __init__(self, status_code: int, detail: str, code: str | None = None, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.detail = detail
        self.code = code
        self.headers = headers


def error_response(status_code: int, detail: str, code: str | None, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": code}, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.detail, exc.code, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Đã có lỗi xảy ra"
        if exc.status_code == 404 and detail == "Not Found":
            detail = "Không tìm thấy đường dẫn này"
        return error_response(exc.status_code, detail, _DEFAULT_CODES.get(exc.status_code), getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        messages = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
            messages.append(f"{field}: {err.get('msg')}" if field else str(err.get("msg")))
        return error_response(422, "Dữ liệu chưa hợp lệ: " + "; ".join(messages), "validation_error")

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return error_response(500, "Máy chủ gặp chút trục trặc, nàng thử lại sau nhé", "internal_error")
