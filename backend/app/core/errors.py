"""RFC 9457 problem+json error handling."""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

log = logging.getLogger("procureai.errors")


class AppError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"

    def __init__(self, detail: str | None = None, *, code: str | None = None, status_code: int | None = None,
                 extra: dict | None = None):
        self.detail = detail or self.code
        self.code = code or self.code
        if status_code:
            self.status_code = status_code
        self.extra = extra or {}
        super().__init__(self.detail)


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


class PolicyViolation(AppError):
    status_code = 422
    code = "policy_violation"


def _problem(request: Request, status_code: int, code: str, detail: str, extra: dict | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    body = {
        "type": f"https://procureai.mantiq.tech/errors/{code}",
        "title": code.replace("_", " ").title(),
        "status": status_code,
        "detail": detail,
        "code": code,
        "request_id": request_id,
        "instance": str(request.url.path),
        **(extra or {}),
    }
    return JSONResponse(body, status_code=status_code, media_type="application/problem+json")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):  # noqa: ANN202
        return _problem(request, exc.status_code, exc.code, exc.detail, exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):  # noqa: ANN202
        errors = [
            {"field": ".".join(str(p) for p in e.get("loc", [])), "message": e.get("msg", "invalid")}
            for e in exc.errors()
        ]
        return _problem(request, 422, "validation_failed", "Request payload failed validation.",
                        {"errors": errors})

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):  # noqa: ANN202
        log.warning("integrity error on %s: %s", request.url.path, exc)
        return _problem(request, 409, "conflict", "The record conflicts with existing data.")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # noqa: ANN202
        log.exception("unhandled error on %s", request.url.path)
        return _problem(request, 500, "internal_error", "An unexpected error occurred.")
