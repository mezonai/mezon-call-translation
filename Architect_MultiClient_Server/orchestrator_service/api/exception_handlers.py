from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from orchestrator_service.api.contracts.errors import ErrorDetail, ErrorResponse
from orchestrator_service.exceptions import (
    AccessDeniedError,
    AuthenticationFailedError,
    BusinessException,
    InfrastructureError,
    IntegrationError,
    InternalServiceError,
    InvalidStateError,
    ResourceNotFoundError,
)
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


async def request_validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        ErrorDetail(location=list(error["loc"]), message=error["msg"], type=error["type"]) for error in exc.errors()
    ]
    response = ErrorResponse(
        code="VALIDATION_ERROR",
        message="Request validation failed",
        details=details,
    )

    return JSONResponse(status_code=422, content=response.model_dump())


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    response = ErrorResponse(
        code=f"HTTP_{exc.status_code}",
        message=str(exc.detail),
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=response.model_dump(),
        headers=exc.headers,
    )


async def business_exception_handler(request: Request, exc: BusinessException) -> JSONResponse:
    response = ErrorResponse(code=exc.error_code, message=str(exc))
    if isinstance(exc, ResourceNotFoundError):
        return JSONResponse(status_code=404, content=response.model_dump())
    elif isinstance(exc, AccessDeniedError):
        return JSONResponse(status_code=403, content=response.model_dump())
    elif isinstance(exc, AuthenticationFailedError):
        return JSONResponse(status_code=401, content=response.model_dump())
    elif isinstance(exc, InvalidStateError):
        return JSONResponse(status_code=409, content=response.model_dump())
    elif isinstance(exc, IntegrationError):
        return JSONResponse(status_code=502, content=response.model_dump())
    elif isinstance(exc, InternalServiceError):
        return JSONResponse(status_code=500, content=response.model_dump())
    elif isinstance(exc, InfrastructureError):
        return JSONResponse(status_code=503, content=response.model_dump())
    else:
        logger.error(f"Unexpected business exception: {exc}")
        return JSONResponse(status_code=500, content=response.model_dump())


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception while processing request")
    response = ErrorResponse(code="INTERNAL_SERVER_ERROR", message="Internal server error")
    return JSONResponse(status_code=500, content=response.model_dump())


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(BusinessException, business_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)  # type: ignore[arg-type]
