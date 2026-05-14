from __future__ import annotations

from functools import wraps
import json

from flask import jsonify, request


def current_webui_token(*, runtime_env_value):
    return runtime_env_value("HERMES_WEBUI_TOKEN", "")


def load_session_tokens(*, token_store_path, time_fn) -> dict[str, float]:
    if not token_store_path.exists():
        return {}
    try:
        data = json.loads(token_store_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    now = time_fn()
    return {
        str(key): float(value)
        for key, value in data.items()
        if isinstance(value, (int, float)) and float(value) > now
    }


def save_session_tokens(tokens: dict[str, float], *, token_store_path) -> None:
    try:
        token_store_path.write_text(json.dumps(tokens), encoding="utf-8")
    except Exception:
        pass


def register_session_token(token: str, expiry: float, *, load_session_tokens_fn, save_session_tokens_fn) -> None:
    tokens = load_session_tokens_fn()
    tokens[token] = expiry
    save_session_tokens_fn(tokens)


def remove_session_token(token: str, *, load_session_tokens_fn, save_session_tokens_fn) -> None:
    tokens = load_session_tokens_fn()
    tokens.pop(token, None)
    save_session_tokens_fn(tokens)


def verify_session_cookie(*, load_session_tokens_fn, remove_session_token_fn, time_fn) -> bool:
    token = request.cookies.get("hermes_webui")
    if not token:
        return False
    tokens = load_session_tokens_fn()
    expiry = tokens.get(token)
    if expiry is None or time_fn() > expiry:
        if expiry is not None:
            remove_session_token_fn(token)
        return False
    return True


def register_auth_routes(
    app,
    *,
    verify_session_cookie,
    register_session_token,
    remove_session_token,
    dashboard_user,
    dashboard_pass,
    session_token_ttl: int,
    token_generator,
    time_fn,
) -> None:
    @app.route("/api/login", methods=["POST"])
    def webui_login():
        data = request.get_json(silent=True) or {}
        username = data.get("username", "")
        password = data.get("password", "")
        if username == dashboard_user() and password == dashboard_pass():
            token = token_generator(32)
            register_session_token(token, time_fn() + session_token_ttl)
            resp = jsonify({"ok": True})
            resp.set_cookie(
                "hermes_webui",
                token,
                httponly=True,
                samesite="Lax",
                secure=True,
                max_age=session_token_ttl,
            )
            return resp
        return jsonify({"ok": False, "error": "Invalid credentials"}), 401

    @app.route("/api/auth/check", methods=["GET"])
    def webui_auth_check():
        if verify_session_cookie():
            return jsonify({"authenticated": True})
        return jsonify({"authenticated": False}), 401

    @app.route("/api/logout", methods=["POST"])
    def webui_logout():
        token = request.cookies.get("hermes_webui")
        if token:
            remove_session_token(token)
        resp = jsonify({"ok": True})
        resp.delete_cookie("hermes_webui")
        return resp


def build_require_token(*, logger, verify_session_cookie, current_webui_token):
    def require_token(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if verify_session_cookie():
                return f(*args, **kwargs)

            expected_token = current_webui_token()
            if not expected_token:
                logger.warning("Authentication not configured - rejecting API request")
                return jsonify({"ok": False, "error": "API authentication not configured"}), 401

            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                logger.warning("API request missing Authorization header from %s", request.remote_addr)
                return jsonify({"ok": False, "error": "Missing or invalid Authorization header"}), 401

            provided_token = auth_header[7:]
            if provided_token != expected_token:
                logger.warning("API authentication failed - invalid token from %s", request.remote_addr)
                return jsonify({"ok": False, "error": "Invalid token"}), 401

            return f(*args, **kwargs)

        return decorated_function

    return require_token


def build_rate_limit(*, logger, rate_limit_store: dict, window_seconds: int, max_requests: int, time_fn):
    def rate_limit(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            client_ip = request.remote_addr
            now = time_fn()
            endpoint = request.endpoint

            if client_ip in rate_limit_store:
                rate_limit_store[client_ip] = [
                    (ts, ep) for ts, ep in rate_limit_store[client_ip]
                    if now - ts < window_seconds
                ]
            else:
                rate_limit_store[client_ip] = []

            request_count = len(rate_limit_store[client_ip])
            if request_count >= max_requests:
                logger.warning(
                    "Rate limit exceeded for %s on %s (%d requests in %ds)",
                    client_ip,
                    endpoint,
                    request_count,
                    window_seconds,
                )
                return jsonify({
                    "ok": False,
                    "error": "Rate limit exceeded. Please try again later.",
                }), 429

            rate_limit_store[client_ip].append((now, endpoint))
            return f(*args, **kwargs)

        return decorated_function

    return rate_limit
