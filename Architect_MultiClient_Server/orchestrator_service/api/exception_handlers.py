from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from orchestrator_service.api.contracts.errors import ErrorDetail, ErrorResponse
from orchestrator_service.exceptions import QueueNotFoundError, SummaryRetryNotFoundError
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError
) -> JSONResponse:
    details = [
        ErrorDetail(
            location=list(error["loc"]),
            message=error["msg"],
            type=error["type"]
        )
        for error in exc.errors()
    ]
    response = ErrorResponse(
        code="VALIDATION_ERROR",
        message="Request validation failed",
        details=details,
    )

    return JSONResponse(
        status_code=422,
        content=response.model_dump()
    )

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

async def unhandled_exception_handler(
    request: Request,
    exc: Exception
) -> JSONResponse:
    logger.exception("Unhandled exception while processing request")
    response = ErrorResponse(
        code="INTERNAL_SERVER_ERROR",
        message="Internal server error"
    )
    return JSONResponse(
        status_code=500,
        content=response.model_dump()
    )

async def queue_not_found_handler(
    request: Request,
    exc: QueueNotFoundError
) -> JSONResponse:
    response = ErrorResponse(
        code="QUEUE_NOT_FOUND",
        message=str(exc),
    )
    return JSONResponse(
        status_code=404,
        content=response.model_dump(),
    )


async def summary_retry_not_found_handler(
    request: Request,
    exc: SummaryRetryNotFoundError
) -> JSONResponse:
    response = ErrorResponse(
        code="SUMMARY_RETRY_NOT_FOUND",
        message=str(exc),
    )
    return JSONResponse(
        status_code=404,
        content=response.model_dump(),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(QueueNotFoundError, queue_not_found_handler) # type: ignore[arg-type]
    app.add_exception_handler(SummaryRetryNotFoundError, summary_retry_not_found_handler) # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler) # type: ignore[arg-type]
