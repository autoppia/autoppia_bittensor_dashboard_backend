# app/middleware/logging_middleware.py
"""
Advanced logging middleware for detailed request/response tracking.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("app.requests")

_REDACT_KEYS = {
    "gif_recording",
    "recording",
    "screenshot_before",
    "screenshot_after",
    "prev_html",
    "current_html",
    "payload",
    "steps",
    "execution_history",
    "raw",
    "html",
    "screenshot_base64",
}


def _scrub_body(value, *, _key: str | None = None):
    """Redact large/base64 fields and truncate long strings in request logs."""
    if _key in _REDACT_KEYS:
        if isinstance(value, (str, bytes)):
            size = len(value)
            return f"<redacted:{_key} size={size}>"
        return f"<redacted:{_key}>"
    if isinstance(value, dict):
        return {k: _scrub_body(v, _key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_body(v) for v in value]
    if isinstance(value, str) and len(value) > 1000:
        return value[:1000] + f"... (truncated {len(value)} chars)"
    return value


class DetailedLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs detailed information about requests and responses.

    Features:
    - Logs request method, path, query params, headers
    - Logs request body (for POST/PUT/PATCH)
    - Logs response status, body (if JSON)
    - Logs timing information
    - Handles errors gracefully
    """

    def __init__(self, app, log_request_body: bool = True, log_response_body: bool = True):
        super().__init__(app)
        self.log_request_body = log_request_body
        self.log_response_body = log_response_body

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()

        # Extract request info
        client_host = request.client.host if request.client else "unknown"
        method = request.method
        path = request.url.path
        query_params = dict(request.query_params)

        # Log request
        log_data = {
            "type": "request",
            "method": method,
            "path": path,
            "client": client_host,
            "query_params": query_params if query_params else None,
        }

        # Log request body for POST/PUT/PATCH
        request_body = None
        if self.log_request_body and method in ["POST", "PUT", "PATCH"]:
            try:
                # Read body
                body_bytes = await request.body()
                if body_bytes:
                    try:
                        request_body = json.loads(body_bytes)
                        log_data["body"] = _scrub_body(request_body)
                    except json.JSONDecodeError:
                        # If not JSON, log as string (truncated if too long)
                        body_str = body_bytes.decode("utf-8", errors="replace")
                        if len(body_str) > 1000:
                            body_str = body_str[:1000] + "... (truncated)"
                        log_data["body"] = body_str
            except Exception as e:
                logger.warning(f"Failed to read request body: {e}")

        logger.info(f"→ {method} {path} | {json.dumps(log_data, default=str)}")

        # Process request
        response_body = None
        error = None
        response = None
        status_code = 500  # Default in case of error

        try:
            response = await call_next(request)
            status_code = response.status_code

            # Try to capture response body
            if self.log_response_body and hasattr(response, "body"):
                try:
                    response_body_bytes = response.body
                    if response_body_bytes and isinstance(response_body_bytes, bytes):
                        try:
                            response_body = json.loads(response_body_bytes)
                        except json.JSONDecodeError:
                            # Not JSON, log as string (truncated)
                            body_str = response_body_bytes.decode("utf-8", errors="replace")
                            if len(body_str) > 1000:
                                body_str = body_str[:1000] + "... (truncated)"
                            response_body = body_str
                except Exception as e:
                    logger.debug(f"Could not capture response body: {e}")

        except Exception as exc:
            error = str(exc)
            logger.error(f"Error processing request: {exc}", exc_info=True)
            # Re-raise the exception to let FastAPI handle it
            raise
        finally:
            # Log response (only if response was created successfully)
            if response is not None:
                elapsed = time.time() - start_time

                response_log = {
                    "type": "response",
                    "method": method,
                    "path": path,
                    "status": status_code,
                    "elapsed_seconds": round(elapsed, 3),
                }

                if response_body:
                    response_log["body"] = response_body

                if error:
                    response_log["error"] = error

                if status_code >= 500:
                    logger.error(f"← {method} {path} {status_code} | {json.dumps(response_log, default=str)}")
                elif status_code >= 400:
                    logger.warning(f"← {method} {path} {status_code} | {json.dumps(response_log, default=str)}")
                else:
                    logger.info(f"← {method} {path} {status_code} | {json.dumps(response_log, default=str)}")

        # This should never be None if we reach here (exception would have been raised)
        if response is None:
            raise RuntimeError("Response was not created")

        return response
