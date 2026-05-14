from __future__ import annotations

import re
import time
import uuid

from flask import g, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge


def register_request_hooks(
    app,
    *,
    logger,
    max_request_body_size: int,
    max_upload_size: int,
    request_id_or_dash,
    should_log_request_summary,
) -> None:
    @app.before_request
    def _start_request_tracking():
        raw_request_id = (request.headers.get("X-Request-ID") or "").strip()
        if raw_request_id:
            sanitized = re.sub(r"[^A-Za-z0-9._:-]", "", raw_request_id)[:64]
            g.request_id = sanitized or uuid.uuid4().hex[:12]
        else:
            g.request_id = uuid.uuid4().hex[:12]
        g.request_started_at = time.monotonic()

    @app.after_request
    def _finish_request_tracking(response):
        request_id = request_id_or_dash()
        response.headers.setdefault("X-Request-ID", request_id)
        started_at = getattr(g, "request_started_at", None)
        if started_at is None:
            return response
        duration_ms = int((time.monotonic() - started_at) * 1000)
        if should_log_request_summary(request.path, response.status_code, duration_ms):
            logger.info(
                "HTTP %s %s status=%s duration_ms=%s request_id=%s content_length=%s remote=%s",
                request.method,
                request.path,
                response.status_code,
                duration_ms,
                request_id,
                request.content_length,
                request.remote_addr,
            )
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def _handle_request_too_large(exc):
        logger.warning(
            "Rejected oversized request path=%s request_id=%s content_length=%s remote=%s limit=%s",
            request.path,
            request_id_or_dash(),
            request.content_length,
            request.remote_addr,
            max_request_body_size,
        )
        if request.path.startswith("/api/"):
            return jsonify({
                "ok": False,
                "error": f"Request too large (max upload {max_upload_size // (1024 * 1024)}MB)",
                "request_id": request_id_or_dash(),
                "max_upload_mb": max_upload_size // (1024 * 1024),
            }), 413
        return "Request too large", 413

    @app.errorhandler(BadRequest)
    def _handle_bad_request(exc):
        if not request.path.startswith("/api/"):
            return exc
        logger.warning(
            "Rejected bad request path=%s request_id=%s remote=%s detail=%s",
            request.path,
            request_id_or_dash(),
            request.remote_addr,
            exc.description,
        )
        return jsonify({
            "ok": False,
            "error": "Invalid request body",
            "detail": exc.description,
            "request_id": request_id_or_dash(),
        }), 400
