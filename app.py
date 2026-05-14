#!/usr/bin/env python3
"""
Hermes Admin Panel — Flask Backend
===================================
Web UI backend for managing a Hermes Agent installation.
Reads/writes ~/.hermes/config.yaml and ~/.hermes/.env
"""

import os
import re
import copy
import json
import hashlib
import shlex
import shutil
import mimetypes
import subprocess
import signal
import time
import logging
import tempfile
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from contextlib import contextmanager
from urllib.parse import urlparse

import yaml
from dotenv import dotenv_values, load_dotenv, set_key, unset_key
from flask import Flask, g, has_request_context, jsonify, request, send_from_directory
from flask_cors import CORS
import uuid
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from werkzeug.utils import secure_filename
from webui_app.chat_persistence import chat_data_lock as _chat_data_lock_impl
from webui_app.chat_persistence import delete_folder as _delete_folder_impl
from webui_app.chat_persistence import attachment_display_name as _attachment_display_name_impl
from webui_app.chat_persistence import build_attachment_refs as _build_attachment_refs_impl
from webui_app.chat_persistence import chat_session_path as _chat_session_path_impl
from webui_app.chat_persistence import delete_session_from_disk as _delete_session_from_disk_impl
from webui_app.chat_persistence import folders_from_file as _folders_from_file_impl
from webui_app.chat_persistence import load_all_folders as _load_all_folders_impl_folders
from webui_app.chat_persistence import load_all_sessions as _load_all_sessions_impl
from webui_app.chat_persistence import load_folder as _load_folder_impl
from webui_app.chat_persistence import read_request_control as _read_request_control_impl
from webui_app.chat_persistence import remove_chat_request as _remove_chat_request_impl
from webui_app.chat_persistence import request_control_path as _request_control_path_impl
from webui_app.chat_persistence import request_output_path as _request_output_path_impl
from webui_app.chat_persistence import load_session as _load_session_impl
from webui_app.chat_persistence import session_from_file as _session_from_file_impl
from webui_app.chat_persistence import update_session_vision_assets as _update_session_vision_assets_impl
from webui_app.chat_persistence import write_request_control as _write_request_control_impl
from webui_app.chat_persistence import write_all_folders as _write_all_folders_impl
from webui_app.chat_persistence import write_folder as _write_folder_impl
from webui_app.chat_persistence import write_session as _write_session_impl
from webui_app.chat_dispatch import call_api_server as _call_api_server_impl
from webui_app.chat_dispatch import call_hermes_direct as _call_hermes_direct_impl
from webui_app.chat_dispatch import call_hermes_prompt as _call_hermes_prompt_impl
from webui_app.chat_dispatch import cancel_chat_request as _cancel_chat_request_impl
from webui_app.chat_dispatch import is_process_alive as _is_process_alive_impl
from webui_app.chat_dispatch import register_chat_request as _register_chat_request_impl
from webui_app.chat_dispatch import terminate_chat_process as _terminate_chat_process_impl
from webui_app.chat_dispatch import update_chat_request as _update_chat_request_impl
from webui_app.chat_transport import plan_chat_request as _plan_chat_request_impl
from webui_app.chat_transport import validated_transport_preference as _validated_transport_preference_impl
from webui_app.auth import build_rate_limit, build_require_token, current_webui_token as _current_webui_token_impl, load_session_tokens as _load_session_tokens_impl, register_auth_routes, register_session_token as _register_session_token_impl, remove_session_token as _remove_session_token_impl, save_session_tokens as _save_session_tokens_impl, verify_session_cookie as _verify_session_cookie_impl
from webui_app.config_manager import ConfigManager as _BaseConfigManager
from webui_app import provider_service as _provider_service
from webui_app import capability_agent_preset_service as _capability_agent_preset_service
from webui_app import capability_integration_service as _capability_integration_service
from webui_app import capability_skill_service as _capability_skill_service
from webui_app import skill_setup_service as _skill_setup_service
from webui_app import skill_runtime_service as _skill_runtime_service
from webui_app.routes.agents import register_agent_routes
from webui_app.routes.capabilities import register_capability_routes
from webui_app.routes.chat import register_chat_routes
from webui_app.routes.config import register_config_routes
from webui_app.routes.env import register_env_routes
from webui_app.routes.frontend import register_frontend_routes
from webui_app.routes.model_roles import register_model_role_routes
from webui_app.routes.operations import register_operations_routes
from webui_app.routes.providers import register_provider_routes
from webui_app.routes.skills import register_skill_routes
from webui_app.request_hooks import register_request_hooks
from webui_app.routes.system import register_system_routes

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('hermes-webui')

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
APP_ROOT = Path(__file__).resolve().parent
PROFILE_OVERRIDE = ContextVar("PROFILE_OVERRIDE", default="")


def _load_runtime_env() -> Path:
    """Load repo-local runtime env without overriding real process env."""
    repo_env_path = APP_ROOT / ".env"
    if repo_env_path.exists():
        load_dotenv(repo_env_path, override=False)
        logger.info("Loaded runtime env from %s", repo_env_path)
    else:
        logger.info("No runtime .env found at %s", repo_env_path)
    return repo_env_path


REPO_ENV_PATH = _load_runtime_env()


def _repo_env_values() -> dict:
    if REPO_ENV_PATH.exists():
        return {
            key: value for key, value in dotenv_values(str(REPO_ENV_PATH)).items()
            if value is not None
        }
    return {}


def _hermes_env_values() -> dict:
    env_path = _selected_env_path() if "_selected_env_path" in globals() else ENV_PATH
    if env_path.exists():
        return {
            key: value for key, value in dotenv_values(str(env_path)).items()
            if value is not None
        }
    return {}


def _runtime_env_value(key: str, default: str = "", allow_repo_env: bool = True) -> str:
    value = os.environ.get(key)
    if value not in (None, ""):
        return value
    if allow_repo_env:
        repo_value = _repo_env_values().get(key)
        if repo_value not in (None, ""):
            return str(repo_value)
    hermes_env_path = globals().get("ENV_PATH")
    if hermes_env_path:
        hermes_value = _hermes_env_values().get(key)
        if hermes_value not in (None, ""):
            return str(hermes_value)
    return default


def _runtime_env_source(key: str, allow_repo_env: bool = True) -> str:
    value = os.environ.get(key)
    if value not in (None, ""):
        return "process_env"
    if allow_repo_env:
        repo_value = _repo_env_values().get(key)
        if repo_value not in (None, ""):
            return "repo_env"
    hermes_env_path = globals().get("ENV_PATH")
    if hermes_env_path:
        hermes_value = _hermes_env_values().get(key)
        if hermes_value not in (None, ""):
            return "hermes_env"
    return ""


def _resolve_runtime_template(value: str) -> str:
    raw_value = str(value or "")
    if not raw_value:
        return ""

    def replace_env(match: re.Match[str]) -> str:
        env_key = str(match.group(1) or "").strip()
        return _runtime_env_value(env_key, "") if env_key else ""

    return re.sub(r"\$\{([A-Z0-9_]+)\}", replace_env, raw_value)


def _effective_hermes_api_url(default: str = "http://127.0.0.1:8642") -> str:
    explicit = _runtime_env_value("HERMES_API_URL", "").strip()
    if explicit:
        return explicit

    host = _runtime_env_value("API_SERVER_HOST", "").strip() or "127.0.0.1"
    port = _runtime_env_value("API_SERVER_PORT", "").strip()
    if not port:
        return default
    if host in ("0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"

HERMES_HOME = Path.home() / ".hermes"
HERMES_REPO_DIR = HERMES_HOME / "hermes-agent"
CONFIG_PATH = HERMES_HOME / "config.yaml"
ENV_PATH = HERMES_HOME / ".env"
SKILLS_DIR = HERMES_HOME / "skills"
HERMES_PROFILES_DIR = HERMES_HOME / "profiles"
WEBUI_PROFILE_STATE_PATH = APP_ROOT / "run" / "webui_profile"


def _normalize_hermes_profile_name(name: str | None) -> str:
    normalized = str(name or "").strip()
    return normalized or "default"


def _root_active_profile_name() -> str:
    active_profile_path = HERMES_HOME / "active_profile"
    debug_trace_lines = []
    try:
        return _normalize_hermes_profile_name(active_profile_path.read_text(encoding="utf-8").strip())
    except Exception:
        return "default"


def _available_hermes_profile_names() -> list[str]:
    names = ["default"]
    try:
        if HERMES_PROFILES_DIR.exists():
            names.extend(
                sorted(
                    entry.name
                    for entry in HERMES_PROFILES_DIR.iterdir()
                    if entry.is_dir() and entry.name.strip()
                )
            )
    except Exception:
        pass
    deduped = []
    for name in names:
        normalized = _normalize_hermes_profile_name(name)
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _profile_home(profile_name: str | None = None) -> Path:
    normalized = _normalize_hermes_profile_name(profile_name)
    if normalized == "default":
        return HERMES_HOME
    return HERMES_PROFILES_DIR / normalized


def _selected_hermes_profile_name() -> str:
    override_raw = str(PROFILE_OVERRIDE.get() or "").strip()
    if override_raw:
        override = _normalize_hermes_profile_name(override_raw)
        if override in _available_hermes_profile_names():
            return override
    try:
        if WEBUI_PROFILE_STATE_PATH.exists():
            stored = _normalize_hermes_profile_name(WEBUI_PROFILE_STATE_PATH.read_text(encoding="utf-8").strip())
            if stored in _available_hermes_profile_names():
                return stored
    except Exception:
        pass
    fallback = _root_active_profile_name()
    if fallback in _available_hermes_profile_names():
        return fallback
    return "default"


def _set_selected_hermes_profile_name(profile_name: str) -> str:
    normalized = _normalize_hermes_profile_name(profile_name)
    available = _available_hermes_profile_names()
    if normalized not in available:
        raise ValueError(f"Unknown Hermes profile: {normalized}")
    WEBUI_PROFILE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEBUI_PROFILE_STATE_PATH.write_text(normalized, encoding="utf-8")
    return normalized


@contextmanager
def _scoped_profile_override(profile_name: str | None):
    raw_profile = str(profile_name or "").strip()
    normalized = _normalize_hermes_profile_name(raw_profile) if raw_profile else ""
    if normalized and normalized not in _available_hermes_profile_names():
        normalized = ""
    token = PROFILE_OVERRIDE.set(normalized)
    try:
        yield normalized or _selected_hermes_profile_name()
    finally:
        PROFILE_OVERRIDE.reset(token)


def _selected_hermes_home() -> Path:
    if _selected_hermes_profile_name() == "default":
        return HERMES_HOME
    return _profile_home(_selected_hermes_profile_name())


def _selected_config_path() -> Path:
    if _selected_hermes_profile_name() == "default":
        return CONFIG_PATH
    return _selected_hermes_home() / "config.yaml"


def _selected_env_path() -> Path:
    if _selected_hermes_profile_name() == "default":
        return ENV_PATH
    return _selected_hermes_home() / ".env"


def _env_path_for_profile(profile_name: str | None = None) -> Path:
    normalized = _normalize_hermes_profile_name(profile_name)
    if normalized == "default":
        return ENV_PATH
    return _profile_home(normalized) / ".env"


def _api_url_port(api_url: str | None) -> str:
    raw = str(api_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    try:
        if parsed.port:
            return str(parsed.port)
    except ValueError:
        return ""
    if parsed.scheme == "https":
        return "443"
    if parsed.scheme == "http":
        return "80"
    return ""


def _api_token_repo_keys_for_port(port: str | None) -> list[str]:
    normalized = str(port or "").strip()
    if not normalized:
        return ["HERMES_API_TOKEN", "HERMES_API_KEY", "API_SERVER_TOKEN", "API_SERVER_KEY"]
    return [
        f"HERMES_API_TOKEN_PORT_{normalized}",
        f"HERMES_API_KEY_PORT_{normalized}",
        f"API_SERVER_TOKEN_PORT_{normalized}",
        f"API_SERVER_KEY_PORT_{normalized}",
        "HERMES_API_TOKEN",
        "HERMES_API_KEY",
        "API_SERVER_TOKEN",
        "API_SERVER_KEY",
    ]


def _profile_api_gateway_url(profile_name: str | None = None) -> str:
    # When no explicit profile is given, preserve the currently active
    # PROFILE_OVERRIDE so that nested calls don't accidentally clear it.
    effective = profile_name if profile_name is not None else (PROFILE_OVERRIDE.get("") or None)
    with _scoped_profile_override(effective):
        hermes_env = _hermes_env_values()
        explicit = str(hermes_env.get("HERMES_API_URL") or "").strip()
        if explicit:
            return explicit
        host = str(hermes_env.get("API_SERVER_HOST") or "").strip() or "127.0.0.1"
        port = str(hermes_env.get("API_SERVER_PORT") or "").strip()
        if port:
            if host in ("0.0.0.0", "::", "[::]"):
                host = "127.0.0.1"
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            return f"http://{host}:{port}"
    return _effective_hermes_api_url(DEFAULT_HERMES_API_URL)


def _profile_primary_api_url(profile_name: str | None = None) -> str:
    with _scoped_profile_override(profile_name):
        target = _resolve_api_target(prefer_vision=False) or {}
        target_url = str(target.get("base_url") or "").strip()
        if target_url:
            return target_url
    return _profile_api_gateway_url(profile_name)


def _selected_skills_dir() -> Path:
    if _selected_hermes_profile_name() == "default":
        return SKILLS_DIR
    return _selected_hermes_home() / "skills"


def _selected_sessions_dir() -> Path:
    if _selected_hermes_profile_name() == "default":
        return SESSIONS_DIR
    return _selected_hermes_home() / "sessions"


def _selected_backup_dir() -> Path:
    if _selected_hermes_profile_name() == "default":
        return BACKUP_DIR
    return _selected_hermes_home() / "backups"


def _selected_gateway_pid_path() -> Path:
    return _selected_hermes_home() / "gateway.pid"


def _selected_gateway_log_path() -> Path:
    return _selected_hermes_home() / "logs" / "gateway.log"


def _selected_hermes_profile_payload() -> dict:
    selected = _selected_hermes_profile_name()
    root_active = _root_active_profile_name()
    return {
        "selected": selected,
        "profiles": [
            {
                "name": name,
                "home": str(_profile_home(name)),
                "is_default": name == "default",
                "is_root_active": name == root_active,
            }
            for name in _available_hermes_profile_names()
        ],
        "paths": {
            "home": str(_selected_hermes_home()),
            "config": str(_selected_config_path()),
            "env": str(_selected_env_path()),
            "skills": str(_selected_skills_dir()),
            "sessions": str(_selected_sessions_dir()),
        },
    }


def _profile_api_token_metadata(profile_name: str | None = None) -> dict:
    normalized = _normalize_hermes_profile_name(profile_name)
    if normalized not in _available_hermes_profile_names():
        raise ValueError(f"Unknown Hermes profile: {normalized}")
    api_url = _profile_api_gateway_url(normalized)
    port = _api_url_port(api_url)
    env_path = REPO_ENV_PATH
    raw = _repo_env_values()
    token_key = ""
    token_value = ""
    for key in _api_token_repo_keys_for_port(port):
        value = str(raw.get(key) or "").strip()
        if value:
            token_key = key
            token_value = value
            break
    return {
        "profile": normalized,
        "api_url": api_url,
        "api_port": port,
        "env_path": str(env_path),
        "token_key": token_key,
        "has_token": bool(token_value),
        "masked_token": _mask_value(token_key or "HERMES_API_TOKEN", token_value) if token_value else "",
    }

# Hermes executable - try current install locations with fallback
def _find_hermes_bin():
    # Align with the current Hermes installer layout:
    # ~/.hermes/hermes-agent/venv/bin/hermes (plus ~/.local/bin/hermes on PATH).
    # Keep the legacy ~/.hermes/.venv layout as a last-resort fallback.
    import shutil as _shutil
    candidates = [
        os.environ.get("HERMES_WEBUI_HERMES_BIN"),
        os.environ.get("HERMES_BIN"),
        HERMES_REPO_DIR / "venv" / "bin" / "hermes",
        Path.home() / ".local" / "bin" / "hermes",
        _shutil.which("hermes"),
        HERMES_HOME / ".venv" / "bin" / "hermes",
    ]
    for path in candidates:
        candidate = Path(path).expanduser() if path else None
        if candidate and candidate.exists():
            return candidate
    return HERMES_REPO_DIR / "venv" / "bin" / "hermes"

HERMES_BIN = _find_hermes_bin()
SESSIONS_DIR = HERMES_HOME / "sessions"
UPLOADS_DIR = Path.home() / "hermes-web-ui" / "uploads"
UPLOAD_FOLDER = APP_ROOT / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
DEFAULT_MAX_REQUEST_BODY_SIZE = (MAX_UPLOAD_SIZE * 3) // 2 + (2 * 1024 * 1024)
MAX_REQUEST_BODY_SIZE = max(
    MAX_UPLOAD_SIZE,
    int(_runtime_env_value("HERMES_WEBUI_MAX_REQUEST_BYTES", str(DEFAULT_MAX_REQUEST_BODY_SIZE))),
)
REQUEST_LOG_SLOW_MS = max(250, int(_runtime_env_value("HERMES_WEBUI_SLOW_REQUEST_MS", "1500")))
UPLOAD_STREAM_CHUNK_SIZE = 1024 * 1024
DEFAULT_HERMES_API_URL = "http://127.0.0.1:8642"
HERMES_API_URL = _effective_hermes_api_url(DEFAULT_HERMES_API_URL)
BACKUP_DIR = HERMES_HOME / "backups"

# Chat session storage (persisted to disk)
CHAT_DATA_DIR = APP_ROOT / "chat_data"
CHAT_DATA_DIR.mkdir(exist_ok=True)
CHAT_DATA_LOCK = CHAT_DATA_DIR / ".lock"
CHAT_FOLDERS_PATH = CHAT_DATA_DIR / ".folders.json"
CHAT_REQUEST_DIR = APP_ROOT / "run" / "chat_requests"
CHAT_REQUEST_DIR.mkdir(parents=True, exist_ok=True)
CHAT_REQUEST_TIMEOUT = int(_runtime_env_value("HERMES_CHAT_TIMEOUT", "300"))
CHAT_PERSIST_DEBUG_TRACE = _runtime_env_value("HERMES_WEBUI_PERSIST_DEBUG_TRACE", "0").strip().lower() not in {"", "0", "false", "no", "off"}
CHAT_SERVER_TIMEOUT = int(
    _runtime_env_value(
        "GUNICORN_TIMEOUT",
        str(CHAT_REQUEST_TIMEOUT + int(_runtime_env_value("GUNICORN_TIMEOUT_HEADROOM", "90"))),
    )
)
CHAT_CANCEL_POLL_INTERVAL = 0.25
CHAT_CANCEL_GRACE_SECONDS = 5.0
CHAT_CONTEXT_SOURCE_DOC_LIMIT = 64 * 1024
CHAT_CONTEXT_SOURCE_DOC_TOTAL_LIMIT = 128 * 1024
CHAT_FOLDER_SOURCE_DIR = CHAT_DATA_DIR / "sources"
CHAT_FOLDER_SOURCE_DIR.mkdir(exist_ok=True)
CRON_JOBS_PATH = APP_ROOT / "run" / "cron_jobs.json"
CRON_JOB_MARKER = "hermes-web-ui-job"
HERMES_UPDATE_CACHE_SECONDS = max(
    60,
    int(_runtime_env_value("HERMES_WEBUI_UPDATE_CACHE_SECONDS", "600")),
)
HERMES_UPDATE_LOG_LINE_LIMIT = max(
    80,
    int(_runtime_env_value("HERMES_WEBUI_UPDATE_LOG_LINES", "400")),
)

chat_sessions: dict = {}  # runtime cache: sid -> session dict
chat_folders: dict = {}  # runtime cache: folder_id -> folder dict
hermes_update_cache: dict = {}  # cache_key -> {"ts": float, "payload": dict}
hermes_update_cache_lock = threading.Lock()
hermes_update_runtime_lock = threading.Lock()
hermes_update_runtime = {
    "status": "",
    "started_at": "",
    "finished_at": "",
    "returncode": None,
    "error": "",
    "summary": "",
    "logs": [],
    "install_key": "",
    "installed_version_before": "",
    "installed_version_after": "",
}

CHAT_TRANSPORT_CLI = "cli"
CHAT_TRANSPORT_API = "api"
CHAT_TRANSPORT_AUTO = "auto"
CHAT_CONTINUITY_HERMES = "hermes_resume"
CHAT_CONTINUITY_LOCAL = "local_replay"
CHAT_CONTINUITY_LIMITED = "cli_without_resume"
AUXILIARY_MODEL_KEYS = (
    "vision",
    "web_extract",
    "compression",
    "session_search",
    "summarization",
    "embedding",
    "tts",
    "stt",
)
MODEL_ROLE_LABELS = {
    "primary": "Primary Chat",
    "fallback": "Fallback Chat",
    "vision": "Vision",
}
PROVIDER_TYPE_LABELS = {
    "auto": "Generic OpenAI-Compatible",
    "openrouter": "OpenRouter",
    "openai": "OpenAI",
    "openai-codex": "OpenAI",
    "azure": "Azure OpenAI",
    "anthropic": "Anthropic",
    "groq": "Groq",
    "google": "Google",
    "gemini": "Google",
    "mistral": "Mistral",
    "together": "Together",
    "fireworks": "Fireworks",
    "deepseek": "DeepSeek",
    "cohere": "Cohere",
}
PROVIDER_DEFAULT_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "openai-codex": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "deepseek": "https://api.deepseek.com/v1",
}
PROVIDER_ENV_KEY_MAP = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-codex": "OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "google": "GOOGLE_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "together": "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "cohere": "COHERE_API_KEY",
}
PROVIDER_PRESETS = [
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "name": "openrouter",
        "intro": "Use OpenRouter as an OpenAI-compatible provider profile.",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "name": "openai",
        "intro": "Use OpenAI directly as a provider profile.",
    },
    {
        "id": "local",
        "label": "Local API",
        "provider": "auto",
        "base_url": "http://127.0.0.1:8000/v1",
        "name": "local-api",
        "intro": "Use a local OpenAI-compatible server as a provider profile.",
    },
]
SKILL_INSTALL_ERROR_MARKERS = (
    "error:",
    "could not fetch",
    "failed to fetch",
    "failed to install",
    "not a valid skill",
    "no skill found",
)
SKILL_SOURCE_METADATA_FILENAME = ".hermes-webui-source.json"
INTEGRATION_SECTION_LABELS = {
    "discord": "Discord",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "slack": "Slack",
    "matrix": "Matrix",
    "webhook": "Webhook",
}
INTEGRATION_SECTION_ORDER = tuple(INTEGRATION_SECTION_LABELS.keys())
INTEGRATION_CONFIG_TEMPLATES = {
    "discord": {
        "require_mention": True,
        "free_response_channels": "",
        "auto_thread": True,
    },
    "whatsapp": {},
    "telegram": {},
    "slack": {},
    "matrix": {},
    "webhook": {
        "url": "",
    },
}
INTEGRATION_ENV_TEMPLATES = {
    "discord": (
        {
            "key": "DISCORD_TOKEN",
            "group": "Channel",
            "label": "Discord Token",
            "description": "Bot token Hermes uses to connect to Discord.",
            "secret": True,
        },
    ),
    "telegram": (
        {
            "key": "TELEGRAM_BOT_TOKEN",
            "group": "Channel",
            "label": "Telegram Bot Token",
            "description": "Bot token Hermes uses to connect to Telegram.",
            "secret": True,
        },
    ),
    "slack": (
        {
            "key": "SLACK_BOT_TOKEN",
            "group": "Channel",
            "label": "Slack Bot Token",
            "description": "Bot token Hermes uses to connect to Slack.",
            "secret": True,
        },
    ),
    "matrix": (
        {
            "key": "MATRIX_ACCESS_TOKEN",
            "group": "Channel",
            "label": "Matrix Access Token",
            "description": "Access token Hermes uses to connect to Matrix.",
            "secret": True,
        },
    ),
}
AGENT_REASONING_EFFORT_OPTIONS = (
    "",
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
    "minimal",
)
ENV_GROUP_HELP = {
    "Provider": "Provider variables are API keys and optional endpoint overrides used by model providers and memory search. For standard OpenAI use, you usually only need OPENAI_API_KEY.",
    "Channel": "Channel variables are only needed when an integration or skill specifically asks for them. If you are not setting up Discord, WhatsApp, Slack, or another messaging bridge, you can usually leave this group alone.",
    "System": "System variables tune how Hermes itself behaves. Only change these when you are intentionally customizing the runtime.",
}
ENV_VAR_PRESETS = {
    "OPENAI_API_KEY": {
        "group": "Provider",
        "label": "OpenAI API Key",
        "description": "Used for OpenAI provider profiles and optional OpenAI-backed Hermes memory search.",
        "secret": True,
        "recommended": True,
        "starter_pack_item": "memory",
    },
    "OPENAI_BASE_URL": {
        "group": "Provider",
        "label": "OpenAI Base URL",
        "description": "Optional override for custom OpenAI-compatible gateways. Leave this unset for normal OpenAI API use.",
        "default_value": "https://api.openai.com/v1",
    },
    "OPENROUTER_API_KEY": {
        "group": "Provider",
        "label": "OpenRouter API Key",
        "description": "Lets Hermes and provider profiles call OpenRouter.",
        "secret": True,
        "recommended": True,
    },
    "ANTHROPIC_API_KEY": {
        "group": "Provider",
        "label": "Anthropic API Key",
        "description": "Lets Hermes call Anthropic models directly.",
        "secret": True,
    },
    "GOOGLE_API_KEY": {
        "group": "Provider",
        "label": "Google API Key",
        "description": "Used by some Google and Gemini provider flows. Google Workspace skills may still need OAuth files separately.",
        "secret": True,
    },
    "GROQ_API_KEY": {
        "group": "Provider",
        "label": "Groq API Key",
        "description": "Lets Hermes call Groq-hosted models.",
        "secret": True,
    },
    "DISCORD_TOKEN": {
        "group": "Channel",
        "label": "Discord Token",
        "description": "Only needed if your Discord integration or skill specifically asks for it.",
        "secret": True,
    },
    "SLACK_BOT_TOKEN": {
        "group": "Channel",
        "label": "Slack Bot Token",
        "description": "Only needed if you connect Hermes to Slack via a bot token.",
        "secret": True,
    },
    "TELEGRAM_BOT_TOKEN": {
        "group": "Channel",
        "label": "Telegram Bot Token",
        "description": "Only needed if you connect Hermes to Telegram.",
        "secret": True,
    },
    "HERMES_API_URL": {
        "group": "System",
        "label": "Hermes API URL",
        "description": "Overrides the URL the web UI uses for API replay.",
        "default_value": "http://127.0.0.1:8642",
    },
    "HERMES_CHAT_TIMEOUT": {
        "group": "System",
        "label": "Chat Timeout",
        "description": "How long the web UI waits for a Hermes chat turn before timing out.",
        "default_value": "300",
    },
}
STARTER_PACK_SKILL_GROUPS = (
    {
        "id": "google_workspace",
        "label": "Google Workspace",
        "terms": ("google-workspace", "gog"),
        "description": "Gmail, Calendar, Drive, Docs, and Sheets helpers.",
        "query": "gog",
        "setup_notes": [
            "Google Workspace usually needs OAuth setup after install.",
            "Expect to add Google client credentials and authorize the account before Gmail, Calendar, Drive, Sheets, or Docs actions will work.",
        ],
        "install_candidates": (
            {
                "identifier": "skills-sh/steipete/clawdis/gog",
                "label": "Gog",
                "source": "skills.sh",
                "description": "Google Workspace helpers from the Clawdis skill set.",
                "recommended": True,
            },
        ),
    },
    {
        "id": "summarize",
        "label": "Summarize",
        "terms": ("summarize",),
        "description": "Recommended summaries and transcripts for URLs, podcasts, videos, and local files.",
        "query": "summarize",
        "setup_notes": [
            "The summarize skill may need the local summarize CLI after the skill files are installed.",
            "If it still is not ready, install the tool with `brew install steipete/tap/summarize`.",
        ],
        "install_candidates": (
            {
                "identifier": "skills-sh/steipete/clawdis/summarize",
                "label": "Summarize",
                "source": "skills.sh",
                "description": "Summaries and transcripts for URLs, videos, and local files.",
                "recommended": True,
            },
        ),
    },
    {
        "id": "weather",
        "label": "Weather",
        "terms": ("weather",),
        "description": "Quick forecast and current weather lookups.",
        "query": "weather",
        "setup_notes": [
            "The recommended weather skill works without an API key.",
        ],
        "install_candidates": (
            {
                "identifier": "skills-sh/steipete/clawdis/weather",
                "label": "weather",
                "source": "skills.sh",
                "description": "Weather helpers from the Clawdis skill set.",
                "recommended": True,
            },
        ),
    },
)
CAPABILITY_RECOMMENDED_ORDER = (
    "skill",
    "integration",
    "agent_preset",
)
CAPABILITY_ARCHITECTURE_RULES = [
    "Skills are the primary Hermes extension mechanism.",
    "Integrations store reusable connection and config for external systems.",
    "Agent Presets compose model roles, skills, and integrations into a reusable working mode.",
    "Every capability type must support a draft preview before any files or config are written.",
]
CAPABILITY_MVP_SCOPE = [
    "Phase 1 ships Create Skill end-to-end first.",
    "Phase 2 adds Create Integration on top of the same preview-and-approve flow.",
    "Phase 3 adds Create Agent Preset that composes existing models, skills, and integrations.",
]
CAPABILITY_IMPLEMENTATION_ORDER = [
    "Create Skill",
    "Create Integration",
    "Create Agent Preset",
]
CAPABILITY_TYPE_DEFINITIONS = {
    "skill": {
        "id": "skill",
        "label": "Skill",
        "phase": "Phase 1",
        "status": "active",
        "summary": "Primary Hermes extension mechanism stored as a skill folder with SKILL.md and optional helper assets.",
        "data_model": [
            "identity: name, slug, category, description",
            "instructions: markdown body for when and how the skill should be used",
            "setup: env vars, credential files, and required commands",
            "assets: optional scripts/ and references/ folders",
        ],
        "ui_flow": [
            "Choose Skill",
            "Fill draft fields",
            "Preview generated files and readiness blockers",
            "Approve and write the skill folder",
        ],
        "layout": [
            {"kind": "folder", "path": "~/.hermes/skills/<slug>/", "purpose": "Skill root directory"},
            {"kind": "file", "path": "~/.hermes/skills/<slug>/SKILL.md", "purpose": "Skill instructions and metadata"},
            {"kind": "folder", "path": "~/.hermes/skills/<slug>/scripts/", "purpose": "Optional helper scripts"},
            {"kind": "folder", "path": "~/.hermes/skills/<slug>/references/", "purpose": "Optional reference material"},
        ],
        "mvp_scope": [
            "Create a new skill from the UI",
            "Preview generated SKILL.md before write",
            "Feed setup metadata into the existing readiness and env-var flows",
        ],
    },
    "integration": {
        "id": "integration",
        "label": "Integration",
        "phase": "Phase 2",
        "status": "active",
        "summary": "Reusable connection and config blocks for Discord, Slack, webhooks, and other external systems.",
        "data_model": [
            "identity: name, slug, provider, label",
            "config: structured JSON config block",
            "secrets: env vars and credential references stored outside the visible config",
            "readiness: configuration completeness and transport/runtime notes",
        ],
        "ui_flow": [
            "Choose integration kind",
            "Fill config and secret references",
            "Preview config diff and readiness",
            "Approve and write Hermes config",
        ],
        "layout": [
            {"kind": "file", "path": "~/.hermes/config.yaml", "purpose": "Top-level integration sections and legacy channels config"},
            {"kind": "file", "path": "~/.hermes/.env", "purpose": "Secrets and token storage when env vars are used"},
        ],
        "mvp_scope": [
            "Create top-level integration blocks that match the current Apps & Integrations UI",
            "Reuse existing env-var editing for secret setup",
            "Preview config changes before save",
        ],
    },
    "agent_preset": {
        "id": "agent_preset",
        "label": "Agent Preset",
        "phase": "Phase 3",
        "status": "active",
        "summary": "Reusable agent working modes that compose model roles, enabled skills, and connected integrations.",
        "data_model": [
            "identity: name, slug, description",
            "model composition: primary, fallback, and vision role bindings",
            "capability composition: selected skills and integrations",
            "agent settings: personality and execution defaults",
        ],
        "ui_flow": [
            "Pick model-role targets",
            "Select skills and integrations",
            "Preview the composed preset",
            "Approve and write the preset config",
        ],
        "layout": [
            {"kind": "file", "path": "~/.hermes/config.yaml", "purpose": "Future agent preset storage alongside Hermes agent config"},
        ],
        "mvp_scope": [
            "Compose existing model roles, skills, and integrations into reusable presets",
            "Preview final role and capability bindings before save",
        ],
    },
}
VISION_REFERENCE_HINT_RE = re.compile(
    r"\b("
    r"screenshot|screen|image|photo|picture|diagram|ui"
    r")\b.*\b("
    r"earlier|previous|prior|before|above|same|that|those|attached|last"
    r")\b"
    r"|"
    r"\b("
    r"earlier|previous|prior|before|above|same|that|those|attached|last"
    r")\b.*\b("
    r"screenshot|screen|image|photo|picture|diagram|ui"
    r")\b",
    re.IGNORECASE,
)


@contextmanager
def _chat_data_lock(shared: bool = False):
    """Serialize chat session file access across gunicorn workers."""
    with _chat_data_lock_impl(lock_path=lambda: CHAT_DATA_LOCK, shared=shared):
        yield


def _chat_session_path(session_id: str) -> Path:
    return _chat_session_path_impl(session_id, chat_data_dir=lambda: CHAT_DATA_DIR)


def _normalize_transport_preference(value) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in ("", CHAT_TRANSPORT_AUTO):
        return None
    if normalized in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
        return normalized
    return None


def _transport_preference_label(value) -> str:
    normalized = _normalize_transport_preference(value)
    if normalized == CHAT_TRANSPORT_CLI:
        return "Hermes CLI"
    if normalized == CHAT_TRANSPORT_API:
        return "API Replay"
    return "Auto"


def _normalize_chat_session(session: dict) -> dict:
    transport_mode = session.get("transport_mode")
    transport_preference = session.get("transport_preference")
    if "transport_preference" not in session and transport_mode in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
        # Preserve legacy session behavior by treating existing explicit mode
        # as the preferred transport when no separate preference exists yet.
        transport_preference = transport_mode
    transport_preference = _normalize_transport_preference(transport_preference)
    continuity_mode = session.get("continuity_mode")
    if transport_mode == CHAT_TRANSPORT_CLI and not continuity_mode:
        continuity_mode = CHAT_CONTINUITY_HERMES if session.get("hermes_session_id") else CHAT_CONTINUITY_LIMITED
    elif transport_mode == CHAT_TRANSPORT_API and not continuity_mode:
        continuity_mode = CHAT_CONTINUITY_LOCAL
    folder_id = session.get("folder_id")
    if not isinstance(folder_id, str):
        folder_id = ""
    profile_name = _normalize_hermes_profile_name(session.get("profile"))
    if not profile_name:
        profile_name = _selected_hermes_profile_name()
    session["transport_mode"] = transport_mode
    session["transport_preference"] = transport_preference
    session["continuity_mode"] = continuity_mode
    session.setdefault("transport_notice", "")
    session["messages"] = _normalize_chat_messages(session.get("messages"))
    session["vision_assets"] = _normalize_vision_assets(session.get("vision_assets"))
    session["segments"] = _normalize_chat_segments(session, profile_name)
    active_segment = _active_chat_segment(session)
    session["profile"] = (active_segment or {}).get("profile") or profile_name
    session["hermes_session_id"] = _segment_hermes_session_id(active_segment)
    session["folder_id"] = folder_id.strip()
    session["workspace_roots"] = _clean_string_list(session.get("workspace_roots"))
    session["source_docs"] = _clean_string_list(session.get("source_docs"))
    segment_map = {segment["id"]: segment for segment in session.get("segments") or []}
    default_segment = active_segment or ((session.get("segments") or [None])[0])
    for index, entry in enumerate(session["messages"]):
        segment = segment_map.get(str(entry.get("segment_id") or "").strip())
        if not segment:
            segment = default_segment
            for candidate in session.get("segments") or []:
                if index >= int(candidate.get("start_message_index") or 0):
                    segment = candidate
            if segment:
                entry["segment_id"] = segment["id"]
        if segment:
            entry["segment_index"] = int(segment.get("index") or 1)
            entry["profile"] = _normalize_hermes_profile_name(entry.get("profile")) or segment.get("profile") or session["profile"]
            transport = str(entry.get("transport") or "").strip().lower()
            if transport not in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
                transport = str(segment.get("transport") or session.get("transport_mode") or "").strip().lower()
            if transport in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
                entry["transport"] = transport
    return session


def _clean_hermes_session_id(value) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _normalize_chat_segments(session: dict, fallback_profile: str | None = None) -> list[dict]:
    raw_segments = session.get("segments")
    normalized = []
    base_profile = _normalize_hermes_profile_name(fallback_profile or session.get("profile")) or _selected_hermes_profile_name()
    base_transport = str(session.get("transport_mode") or "").strip().lower()
    legacy_hermes_session_id = _clean_hermes_session_id(session.get("hermes_session_id"))
    raw_active_segment_id = str(session.get("active_segment_id") or "").strip()
    if base_transport not in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
        base_transport = ""
    if isinstance(raw_segments, list):
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            profile = _normalize_hermes_profile_name(item.get("profile")) or base_profile
            segment_id = str(item.get("id") or f"segment-{len(normalized) + 1}").strip() or f"segment-{len(normalized) + 1}"
            transport = str(item.get("transport") or "").strip().lower()
            if transport not in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
                transport = ""
            start_message_index = item.get("start_message_index")
            if not isinstance(start_message_index, int) or start_message_index < 0:
                start_message_index = 0
            hermes_session_id = _clean_hermes_session_id(item.get("hermes_session_id"))
            if not hermes_session_id and segment_id == raw_active_segment_id:
                hermes_session_id = legacy_hermes_session_id
            normalized.append({
                "id": segment_id,
                "index": len(normalized) + 1,
                "profile": profile,
                "transport": transport,
                "hermes_session_id": hermes_session_id,
                "started_at": str(item.get("started_at") or session.get("created") or "").strip(),
                "start_message_index": start_message_index,
            })
    if not normalized:
        normalized = [{
            "id": "segment-1",
            "index": 1,
            "profile": base_profile,
            "transport": base_transport,
            "hermes_session_id": legacy_hermes_session_id,
            "started_at": str(session.get("created") or "").strip(),
            "start_message_index": 0,
        }]
    active_segment_id = str(session.get("active_segment_id") or "").strip()
    if not any(segment["id"] == active_segment_id for segment in normalized):
        active_segment_id = normalized[-1]["id"]
    session["active_segment_id"] = active_segment_id
    return normalized


def _trim_trailing_empty_chat_segments(session: dict) -> bool:
    segments = session.get("segments") or []
    if len(segments) <= 1:
        return False
    message_count = len(session.get("messages") or [])
    trimmed = False
    while len(segments) > 1:
        last_segment = segments[-1]
        start_message_index = int(last_segment.get("start_message_index") or 0)
        if start_message_index < message_count:
            break
        segments.pop()
        trimmed = True
    if not trimmed:
        return False
    for index, segment in enumerate(segments, start=1):
        segment["index"] = index
    session["segments"] = segments
    session["active_segment_id"] = segments[-1].get("id") or ""
    session["profile"] = segments[-1].get("profile") or session.get("profile")
    session["hermes_session_id"] = _segment_hermes_session_id(segments[-1])
    return True


def _active_chat_segment(session: dict) -> dict | None:
    segments = session.get("segments") or []
    active_segment_id = str(session.get("active_segment_id") or "").strip()
    for segment in segments:
        if segment.get("id") == active_segment_id:
            return segment
    return segments[-1] if segments else None


def _segment_hermes_session_id(segment: dict | None) -> str | None:
    if not isinstance(segment, dict):
        return None
    return _clean_hermes_session_id(segment.get("hermes_session_id"))


def _latest_chat_segment_for_profile(session: dict, profile_name: str) -> dict | None:
    normalized_profile = _normalize_hermes_profile_name(profile_name) or _selected_hermes_profile_name()
    for segment in reversed(session.get("segments") or []):
        if _normalize_hermes_profile_name(segment.get("profile")) == normalized_profile:
            return segment
    return None


def _append_chat_segment(session: dict, profile_name: str, *, transport: str = "") -> dict:
    normalized_profile = _normalize_hermes_profile_name(profile_name) or _selected_hermes_profile_name()
    if "segments" not in session:
        session["segments"] = _normalize_chat_segments(session, normalized_profile)
    active = _active_chat_segment(session)
    clean_transport = str(transport or "").strip().lower()
    if clean_transport not in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
        clean_transport = ""
    if active and active.get("profile") == normalized_profile:
        if clean_transport and not active.get("transport"):
            active["transport"] = clean_transport
        session["active_segment_id"] = active.get("id")
        session["profile"] = normalized_profile
        session["hermes_session_id"] = _segment_hermes_session_id(active)
        return active
    previous = _latest_chat_segment_for_profile(session, normalized_profile)
    next_index = len(session.get("segments") or []) + 1
    segment = {
        "id": f"segment-{next_index}",
        "index": next_index,
        "profile": normalized_profile,
        "transport": clean_transport,
        "hermes_session_id": _segment_hermes_session_id(previous),
        "started_at": datetime.now().isoformat(),
        "start_message_index": len(session.get("messages") or []),
    }
    session.setdefault("segments", []).append(segment)
    session["active_segment_id"] = segment["id"]
    session["profile"] = normalized_profile
    session["hermes_session_id"] = _segment_hermes_session_id(segment)
    return segment


def _normalize_chat_messages(messages) -> list[dict]:
    if not isinstance(messages, list):
        return []
    normalized = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        entry = copy.deepcopy(message)
        entry["role"] = str(entry.get("role") or "").strip() or "user"
        entry["content"] = str(entry.get("content") or "")
        entry["files"] = _clean_string_list(entry.get("files"))
        entry["attachment_refs"] = _normalize_attachment_refs(entry.get("attachment_refs"))
        sidecar_state = entry.get("sidecar_vision")
        if isinstance(sidecar_state, dict):
            entry["sidecar_vision"] = {
                "used": bool(sidecar_state.get("used")),
                "status": str(sidecar_state.get("status") or "").strip() or ("ok" if sidecar_state.get("used") else ""),
                "asset_ids": _clean_string_list(sidecar_state.get("asset_ids")),
                "summary": str(sidecar_state.get("summary") or "").strip(),
                "analysis_mode": str(sidecar_state.get("analysis_mode") or "").strip(),
                "reanalysis": bool(sidecar_state.get("reanalysis")),
            }
        elif "sidecar_vision" in entry:
            entry.pop("sidecar_vision", None)
        normalized.append(entry)
    return normalized


def _normalize_attachment_refs(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    refs = []
    seen = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        stored_as = secure_filename(str(item.get("stored_as") or "").strip())
        if not stored_as or stored_as in seen:
            continue
        seen.add(stored_as)
        display_name = str(
            item.get("display_name")
            or item.get("name")
            or stored_as
        ).strip() or stored_as
        ref = {
            "stored_as": stored_as,
            "display_name": display_name,
        }
        kind = str(item.get("kind") or "").strip().lower()
        mime_type = str(item.get("mime_type") or "").strip()
        if kind:
            ref["kind"] = kind
        if mime_type:
            ref["mime_type"] = mime_type
        refs.append(ref)
    return refs


def _normalize_vision_assets(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    assets = []
    seen = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id") or "").strip()
        stored_as = secure_filename(str(item.get("stored_as") or "").strip())
        if not asset_id or not stored_as or asset_id in seen:
            continue
        seen.add(asset_id)
        asset = {
            "id": asset_id,
            "stored_as": stored_as,
            "display_name": str(item.get("display_name") or stored_as).strip() or stored_as,
            "mime_type": str(item.get("mime_type") or "").strip(),
            "created_at": str(item.get("created_at") or "").strip(),
            "source_message_timestamp": str(item.get("source_message_timestamp") or "").strip(),
        }
        source_index = item.get("source_message_index")
        if isinstance(source_index, int):
            asset["source_message_index"] = source_index
        last_analysis = item.get("last_analysis")
        if isinstance(last_analysis, dict):
            asset["last_analysis"] = {
                "summary": str(last_analysis.get("summary") or "").strip(),
                "raw_text": str(last_analysis.get("raw_text") or "").strip(),
                "focus": str(last_analysis.get("focus") or "").strip(),
                "analyzed_at": str(last_analysis.get("analyzed_at") or "").strip(),
                "model": str(last_analysis.get("model") or "").strip(),
                "provider": str(last_analysis.get("provider") or "").strip(),
            }
        assets.append(asset)
    return assets


def _clean_string_list(values) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    cleaned = []
    seen = set()
    for item in values:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _normalize_context_path(raw_path: str) -> Path:
    path = Path(str(raw_path).strip()).expanduser()
    if not path.is_absolute():
        path = (APP_ROOT / path)
    try:
        return path.resolve(strict=False)
    except Exception:
        return path.absolute()


def _validated_context_paths(values, *, expect: str) -> tuple[list[str], list[str]]:
    normalized = []
    errors = []
    seen = set()
    for raw_value in _clean_string_list(values):
        path = _normalize_context_path(raw_value)
        path_str = str(path)
        if path_str in seen:
            continue
        if not path.exists():
            errors.append(f"{raw_value} does not exist")
            continue
        if expect == "dir" and not path.is_dir():
            errors.append(f"{raw_value} is not a directory")
            continue
        if expect == "file" and not path.is_file():
            errors.append(f"{raw_value} is not a file")
            continue
        seen.add(path_str)
        normalized.append(path_str)
    return normalized, errors


def _parse_chat_context_update(data: dict) -> tuple[dict, list[str]]:
    folder_id = str(data.get("folder_id") or "").strip()
    if len(folder_id) > 120:
        folder_id = folder_id[:120].rstrip()
    workspace_roots, root_errors = _validated_context_paths(data.get("workspace_roots"), expect="dir")
    source_docs, doc_errors = _validated_context_paths(data.get("source_docs"), expect="file")
    errors = root_errors + doc_errors
    return {
        "folder_id": folder_id,
        "workspace_roots": workspace_roots,
        "source_docs": source_docs,
    }, errors


def _merge_unique_strings(*groups) -> list[str]:
    merged = []
    seen = set()
    for group in groups:
        for item in _clean_string_list(group):
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def _folder_workspace_roots_for_docs(source_docs) -> list[str]:
    roots = []
    seen = set()
    for source_doc in _clean_string_list(source_docs):
        path = _normalize_context_path(source_doc)
        parent = str(path.parent)
        if parent in seen:
            continue
        seen.add(parent)
        roots.append(parent)
    return roots


def _parse_folder_update(data: dict, existing: dict | None = None) -> tuple[dict, list[str]]:
    existing = existing or {}
    title = str(data.get("title") if "title" in data else existing.get("title") or "").strip()
    if not title:
        title = "Untitled Folder"
    if len(title) > 120:
        title = title[:120].rstrip()

    source_values = data.get("source_docs") if "source_docs" in data else existing.get("source_docs", [])
    source_docs, doc_errors = _validated_context_paths(source_values, expect="file")
    upload_refs = data.get("source_uploads") or []
    for upload_ref in _clean_string_list(upload_refs):
        upload_path = UPLOAD_FOLDER / upload_ref
        if not upload_path.exists() or not upload_path.is_file():
            doc_errors.append(f"{upload_ref} is not an uploaded source file")
            continue
        source_docs.append(str(upload_path.resolve()))
    source_docs = _merge_unique_strings(source_docs)

    workspace_values = data.get("workspace_roots") if "workspace_roots" in data else existing.get("workspace_roots", [])
    workspace_roots, root_errors = _validated_context_paths(workspace_values, expect="dir")
    workspace_roots = _merge_unique_strings(workspace_roots, _folder_workspace_roots_for_docs(source_docs))
    return {
        "title": title,
        "source_docs": source_docs,
        "workspace_roots": workspace_roots,
    }, doc_errors + root_errors


def _folder_title_key(title: str) -> str:
    return str(title or "").strip().casefold()


def _folders_matching_title(title: str, folders: dict | None = None) -> list[dict]:
    folders = folders if folders is not None else _load_all_folders()
    title_key = _folder_title_key(title)
    if not title_key:
        return []
    matches = []
    for folder in folders.values():
        normalized = _normalize_chat_folder(folder)
        if _folder_title_key(normalized.get("title")) == title_key:
            matches.append(normalized)
    matches.sort(key=lambda folder: (
        folder.get("created") or "",
        folder.get("id") or "",
    ))
    return matches


def _unique_folder_for_title(title: str, folders: dict | None = None) -> dict | None:
    matches = _folders_matching_title(title, folders=folders)
    return matches[0] if len(matches) == 1 else None


def _folder_title_conflict(title: str, *, exclude_folder_id: str = "", folders: dict | None = None) -> dict | None:
    folders = folders if folders is not None else _load_all_folders()
    title_key = _folder_title_key(title)
    if not title_key:
        return None
    exclude_folder_id = str(exclude_folder_id or "").strip()
    for folder_id, folder in folders.items():
        if folder_id == exclude_folder_id:
            continue
        normalized = _normalize_chat_folder(folder)
        if _folder_title_key(normalized.get("title")) == title_key:
            return normalized
    return None


def _legacy_folder_from_sessions(folder_id: str, sessions: dict) -> dict | None:
    folder_id = str(folder_id or "").strip()
    if not folder_id:
        return None
    grouped = [session for session in sessions.values() if (session.get("folder_id") or "").strip() == folder_id]
    if not grouped:
        return None
    newest = max(grouped, key=lambda session: session.get("updated") or session.get("created") or "")
    source_docs = _merge_unique_strings(*(session.get("source_docs") or [] for session in grouped))
    workspace_roots = _merge_unique_strings(*(session.get("workspace_roots") or [] for session in grouped))
    return _normalize_chat_folder({
        "id": folder_id,
        "title": folder_id,
        "created": newest.get("created"),
        "updated": newest.get("updated") or newest.get("created"),
        "source_docs": source_docs,
        "workspace_roots": workspace_roots,
    })


def _resolve_folder_reference(folder_id: str, sessions: dict | None = None, folders: dict | None = None, include_legacy: bool = True) -> dict | None:
    folder_id = str(folder_id or "").strip()
    if not folder_id:
        return None
    folders = folders if folders is not None else _load_all_folders()
    direct = folders.get(folder_id)
    if direct:
        return _normalize_chat_folder(direct)
    by_title = _unique_folder_for_title(folder_id, folders=folders)
    if by_title:
        return by_title
    if not include_legacy:
        return None
    sessions = sessions if sessions is not None else _load_all_sessions()
    return _legacy_folder_from_sessions(folder_id, sessions)


def _folder_with_fallback(folder_id: str, sessions: dict | None = None) -> dict | None:
    return _resolve_folder_reference(folder_id, sessions=sessions)


def _ensure_folder_exists(folder_id: str) -> dict | None:
    folder_id = str(folder_id or "").strip()
    if not folder_id:
        return None
    existing = _folder_with_fallback(folder_id)
    if existing:
        return existing
    conflict = _folder_title_conflict(folder_id)
    if conflict:
        return conflict
    now = datetime.now().isoformat()
    return _write_folder({
        "id": folder_id,
        "title": folder_id,
        "created": now,
        "updated": now,
        "workspace_roots": [],
        "source_docs": [],
    })


def _dedupe_legacy_folder_titles() -> dict:
    folders = _load_all_folders()
    sessions = _load_all_sessions()
    groups: dict[str, list[dict]] = {}
    for folder in folders.values():
        normalized = _normalize_chat_folder(folder)
        title_key = _folder_title_key(normalized.get("title"))
        if not title_key:
            continue
        groups.setdefault(title_key, []).append(normalized)

    changed_sessions: list[str] = []
    changed_session_ids = set()
    merged_groups = []
    changed = False

    for duplicates in groups.values():
        if len(duplicates) < 2:
            continue
        duplicates.sort(key=lambda folder: (
            folder.get("created") or "",
            folder.get("id") or "",
        ))
        canonical = dict(duplicates[0])
        merged_workspace_roots = _merge_unique_strings(*(folder.get("workspace_roots") or [] for folder in duplicates))
        merged_source_docs = _merge_unique_strings(*(folder.get("source_docs") or [] for folder in duplicates))
        merged_updated = max(
            [canonical.get("updated") or canonical.get("created")] +
            [folder.get("updated") or folder.get("created") for folder in duplicates[1:]]
        )
        if canonical.get("workspace_roots") != merged_workspace_roots or canonical.get("source_docs") != merged_source_docs or canonical.get("updated") != merged_updated:
            canonical["workspace_roots"] = merged_workspace_roots
            canonical["source_docs"] = merged_source_docs
            canonical["updated"] = merged_updated
            folders[canonical["id"]] = canonical
            changed = True

        removed_ids = []
        for duplicate in duplicates[1:]:
            duplicate_id = duplicate["id"]
            removed_ids.append(duplicate_id)
            if duplicate_id in folders:
                folders.pop(duplicate_id, None)
                changed = True
            for session in sessions.values():
                if (session.get("folder_id") or "").strip() != duplicate_id:
                    continue
                session["folder_id"] = canonical["id"]
                session["updated"] = datetime.now().isoformat()
                if session["id"] not in changed_session_ids:
                    changed_session_ids.add(session["id"])
                    changed_sessions.append(session["id"])
                changed = True

        merged_groups.append({
            "title": canonical["title"],
            "kept_id": canonical["id"],
            "removed_ids": removed_ids,
            "source_count": len(canonical.get("source_docs") or []),
            "workspace_root_count": len(canonical.get("workspace_roots") or []),
        })

    if changed:
        _write_all_folders(folders)
        for session_id in changed_sessions:
            session = sessions.get(session_id)
            if session:
                _write_session(session)

    return {
        "changed": changed,
        "merged_group_count": len(merged_groups),
        "merged_groups": merged_groups,
        "updated_session_ids": changed_sessions,
        "folders": _load_all_folders() if changed else folders,
    }


def _folder_summaries(sessions: dict | None = None) -> list[dict]:
    sessions = sessions if sessions is not None else _load_all_sessions()
    folders = _load_all_folders()
    folder_map = {folder_id: _normalize_chat_folder(folder) for folder_id, folder in folders.items()}
    grouped_sessions: dict[str, list[dict]] = {folder_id: [] for folder_id in folder_map}
    legacy_refs = set()

    for session in sessions.values():
        folder_ref = (session.get("folder_id") or "").strip()
        if not folder_ref:
            continue
        resolved = _resolve_folder_reference(folder_ref, sessions=sessions, folders=folder_map, include_legacy=False)
        if resolved and resolved["id"] in folder_map:
            grouped_sessions.setdefault(resolved["id"], []).append(session)
            continue
        legacy_refs.add(folder_ref)

    for folder_ref in sorted(legacy_refs, key=lambda value: value.casefold()):
        legacy = _legacy_folder_from_sessions(folder_ref, sessions)
        if legacy:
            folder_map[folder_ref] = legacy
            grouped_sessions[folder_ref] = [
                session for session in sessions.values()
                if (session.get("folder_id") or "").strip() == folder_ref
            ]

    summaries = []
    for folder_id, folder in folder_map.items():
        related_sessions = list(grouped_sessions.get(folder_id) or [])
        related_sessions.sort(key=lambda session: session.get("updated") or session.get("created") or "", reverse=True)
        summaries.append({
            "id": folder["id"],
            "title": folder["title"],
            "created": folder["created"],
            "updated": max([folder.get("updated")] + [s.get("updated") or s.get("created") for s in related_sessions if isinstance(s, dict)]),
            "source_docs": folder.get("source_docs") or [],
            "workspace_roots": folder.get("workspace_roots") or [],
            "chat_count": len(related_sessions),
            "sessions": [{
                "id": s["id"],
                "title": s.get("title", "Untitled"),
                "message_count": len(s.get("messages", [])),
                "updated": s.get("updated", s.get("created")),
                "last_message": s["messages"][-1]["content"][:100] if s.get("messages") else "",
            } for s in related_sessions],
        })
    summaries.sort(key=lambda folder: (
        (folder.get("title") or "").casefold(),
        folder.get("created") or "",
        folder.get("id") or "",
    ))
    return summaries


def _effective_session_context(session: dict) -> dict:
    normalized = _normalize_chat_session(copy.deepcopy(session))
    folder_ref = normalized.get("folder_id") or ""
    folder = _folder_with_fallback(folder_ref) if folder_ref else None
    folder_id = folder.get("id") if folder else folder_ref
    folder_title = folder.get("title") if folder else ""
    workspace_roots = _merge_unique_strings(
        (folder or {}).get("workspace_roots"),
        normalized.get("workspace_roots"),
    )
    source_docs = _merge_unique_strings(
        (folder or {}).get("source_docs"),
        normalized.get("source_docs"),
    )
    return {
        "folder_id": folder_id,
        "folder_title": folder_title or folder_id,
        "folder_source_docs": (folder or {}).get("source_docs") or [],
        "folder_workspace_roots": (folder or {}).get("workspace_roots") or [],
        "workspace_roots": workspace_roots,
        "source_docs": source_docs,
    }


def _session_from_file(path: Path):
    return _session_from_file_impl(path, normalize_chat_session=lambda session: _normalize_chat_session(session))


def _load_all_sessions():
    """Load all persisted chat sessions from disk into memory."""
    return _load_all_sessions_impl(
        chat_data_lock_fn=_chat_data_lock,
        chat_data_dir=lambda: CHAT_DATA_DIR,
        chat_folders_path=lambda: CHAT_FOLDERS_PATH,
        session_from_file_fn=_session_from_file,
        chat_sessions=chat_sessions,
        logger=logger,
    )


def _load_session(session_id):
    """Load a single persisted session from disk into the runtime cache."""
    return _load_session_impl(
        session_id,
        chat_session_path_fn=_chat_session_path,
        chat_data_lock_fn=_chat_data_lock,
        session_from_file_fn=_session_from_file,
        chat_sessions=chat_sessions,
        logger=logger,
    )


def _write_session(session):
    """Persist a single session atomically and refresh the runtime cache."""
    _write_session_impl(
        session,
        normalize_chat_session=lambda value: _normalize_chat_session(value),
        chat_session_path_fn=_chat_session_path,
        chat_data_lock_fn=_chat_data_lock,
        chat_sessions=chat_sessions,
    )


def _delete_session_from_disk(session_id):
    """Remove a session from memory and disk."""
    _delete_session_from_disk_impl(
        session_id,
        chat_sessions=chat_sessions,
        chat_session_path_fn=_chat_session_path,
        chat_data_lock_fn=_chat_data_lock,
    )


def _normalize_chat_folder(folder: dict) -> dict:
    normalized = {
        "id": str(folder.get("id") or "").strip(),
        "title": str(folder.get("title") or "").strip() or "Untitled Folder",
        "created": folder.get("created") or datetime.now().isoformat(),
        "updated": folder.get("updated") or datetime.now().isoformat(),
        "workspace_roots": _clean_string_list(folder.get("workspace_roots")),
        "source_docs": _clean_string_list(folder.get("source_docs")),
    }
    return normalized


def _folders_from_file() -> dict:
    return _folders_from_file_impl(
        chat_folders_path=lambda: CHAT_FOLDERS_PATH,
        normalize_chat_folder=lambda folder: _normalize_chat_folder(folder),
    )


def _write_all_folders(folders: dict) -> dict:
    return _write_all_folders_impl(
        folders=folders,
        normalize_chat_folder=lambda folder: _normalize_chat_folder(folder),
        chat_folders_path=lambda: CHAT_FOLDERS_PATH,
        chat_data_lock_fn=_chat_data_lock,
        chat_folders=chat_folders,
    )


def _load_all_folders() -> dict:
    return _load_all_folders_impl_folders(
        chat_data_lock_fn=_chat_data_lock,
        folders_from_file_fn=_folders_from_file,
        chat_folders=chat_folders,
    )


def _load_folder(folder_id: str) -> dict | None:
    return _load_folder_impl(folder_id, load_all_folders_fn=_load_all_folders)


def _write_folder(folder: dict) -> dict:
    return _write_folder_impl(
        folder,
        normalize_chat_folder=lambda value: _normalize_chat_folder(value),
        load_all_folders_fn=_load_all_folders,
        write_all_folders_fn=_write_all_folders,
    )


def _delete_folder(folder_id: str) -> None:
    _delete_folder_impl(
        folder_id,
        load_all_folders_fn=_load_all_folders,
        write_all_folders_fn=_write_all_folders,
    )


def _crontab_available() -> bool:
    return shutil.which("crontab") is not None


def _load_cron_jobs() -> dict:
    if not CRON_JOBS_PATH.exists():
        return {}
    try:
        data = json.loads(CRON_JOBS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    jobs = {}
    for job_id, job in data.items():
        if not isinstance(job, dict):
            continue
        jobs[job_id] = {
            "id": str(job.get("id") or job_id).strip(),
            "name": str(job.get("name") or "").strip() or "Cron Job",
            "schedule": str(job.get("schedule") or "").strip(),
            "command": str(job.get("command") or "").strip(),
            "enabled": bool(job.get("enabled", True)),
            "created": job.get("created") or datetime.now().isoformat(),
            "updated": job.get("updated") or datetime.now().isoformat(),
        }
    return jobs


def _write_cron_jobs(jobs: dict) -> dict:
    payload = json.dumps(jobs or {}, ensure_ascii=False, indent=2)
    tmp_path = CRON_JOBS_PATH.with_suffix(CRON_JOBS_PATH.suffix + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, CRON_JOBS_PATH)
    return copy.deepcopy(jobs)


def _validate_cron_job_payload(data: dict) -> tuple[dict, list[str]]:
    name = str(data.get("name") or "").strip() or "Cron Job"
    schedule = str(data.get("schedule") or "").strip()
    command = str(data.get("command") or "").strip()
    enabled = bool(data.get("enabled", True))
    errors = []
    if len(schedule.split()) != 5:
        errors.append("Cron schedule must have exactly five fields")
    if not command:
        errors.append("Command is required")
    return {
        "name": name[:120].rstrip(),
        "schedule": schedule,
        "command": command,
        "enabled": enabled,
    }, errors


def _read_crontab_lines() -> list[str]:
    if not _crontab_available():
        raise ChatBackendError("crontab is not installed", status_code=501)
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip().lower()
        if "no crontab for" in stderr:
            return []
        raise ChatBackendError(proc.stderr.strip() or "Unable to read crontab", status_code=500)
    return [line.rstrip("\n") for line in proc.stdout.splitlines()]


def _write_crontab_lines(lines: list[str]) -> None:
    if not _crontab_available():
        raise ChatBackendError("crontab is not installed", status_code=501)
    text = "\n".join(lines).rstrip()
    if text:
        text += "\n"
    proc = subprocess.run(["crontab", "-"], input=text, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ChatBackendError(proc.stderr.strip() or "Unable to update crontab", status_code=500)


def _cron_job_line(job: dict) -> str:
    return f'{job["schedule"]} {job["command"]} # {CRON_JOB_MARKER}:{job["id"]}'


def _sync_cron_jobs_to_system(jobs: dict | None = None) -> None:
    jobs = jobs if jobs is not None else _load_cron_jobs()
    current_lines = _read_crontab_lines()
    preserved = [line for line in current_lines if CRON_JOB_MARKER not in line]
    managed = [_cron_job_line(job) for job in jobs.values() if job.get("enabled") and job.get("schedule") and job.get("command")]
    _write_crontab_lines(preserved + managed)


class ChatRequestCancelled(Exception):
    """Raised when an in-flight chat request is cancelled by the client."""


class ChatBackendError(Exception):
    """Raised when Hermes cannot produce a usable response for the request."""

    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class ChatRequestTimeout(ChatBackendError):
    """Raised when a chat request exceeds the configured timeout."""

    def __init__(self, message: str):
        super().__init__(message, status_code=504)


def _request_control_path(request_id: str) -> Path:
    return _request_control_path_impl(request_id, chat_request_dir=lambda: CHAT_REQUEST_DIR)


def _read_request_control(request_id: str) -> dict | None:
    return _read_request_control_impl(request_id, request_control_path_fn=_request_control_path, logger=logger)


def _write_request_control(request_id: str, payload: dict) -> None:
    _write_request_control_impl(request_id, payload, request_control_path_fn=_request_control_path)


def _request_output_path(request_id: str) -> Path:
    return _request_output_path_impl(request_id, chat_request_dir=lambda: CHAT_REQUEST_DIR)


def _truncate_recent_lines(lines: list[str], limit: int = 24) -> list[str]:
    cleaned = [str(line).rstrip("\n") for line in (lines or []) if str(line).strip()]
    if limit <= 0 or len(cleaned) <= limit:
        return cleaned
    return cleaned[-limit:]


def _request_progress_lines(request_id: str, limit: int = 0) -> list[str]:
    path = _request_output_path(request_id)
    if not path.exists():
        return []
    try:
        return _truncate_recent_lines(path.read_text(encoding="utf-8", errors="replace").splitlines(), limit=limit)
    except Exception:
        return []


def _filter_live_progress_lines(lines: list[str]) -> list[str]:
    filtered = []
    in_reasoning_block = False
    for raw_line in lines or []:
        line = str(raw_line or "").rstrip("\n")
        trimmed = line.strip()
        if not trimmed:
            continue
        if re.match(r"^[┌├└]─\s*Reasoning\s*─+[┐┤┘]?$", trimmed):
            in_reasoning_block = True
            continue
        if in_reasoning_block:
            if re.match(r"^[└┘]?[─]+", trimmed) or re.match(r"^[└├┌]─", trimmed):
                in_reasoning_block = False
            continue
        filtered.append(line)
    return filtered


def _active_request_for_session(session_id: str) -> dict | None:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    latest = None
    for path in CHAT_REQUEST_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(payload.get("session_id") or "").strip() != sid:
            continue
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"running", "cancel_requested"}:
            continue
        updated_at = str(payload.get("updated_at") or "")
        if latest is None or updated_at > latest.get("updated_at", ""):
            latest = {
                "request_id": str(payload.get("request_id") or "").strip(),
                "status": status,
                "cancel_supported": bool(payload.get("cancel_supported")),
                "transport": str(payload.get("transport") or "").strip(),
                "updated_at": updated_at,
            }
    return latest


def _register_chat_request(
    request_id: str,
    session_id: str | None,
    *,
    transport: str,
    cancel_supported: bool,
) -> None:
    _register_chat_request_impl(
        request_id,
        session_id,
        transport=transport,
        cancel_supported=cancel_supported,
        write_request_control=_write_request_control,
        request_output_path=_request_output_path,
    )


def _update_chat_request(request_id: str, **fields) -> dict | None:
    return _update_chat_request_impl(
        request_id,
        read_request_control=_read_request_control,
        write_request_control=_write_request_control,
        fields=fields,
    )


def _remove_chat_request(request_id: str) -> None:
    _remove_chat_request_impl(request_id, request_control_path_fn=_request_control_path)


def _is_process_alive(pid: int | None) -> bool:
    return _is_process_alive_impl(pid, os_module=os)


def _terminate_chat_process(pid: int | None, pgid: int | None, sig: int) -> bool:
    return _terminate_chat_process_impl(pid, pgid, sig, os_module=os, logger=logger)


def _cancel_chat_request(request_id: str) -> tuple[bool, str]:
    return _cancel_chat_request_impl(
        request_id,
        read_request_control=_read_request_control,
        update_chat_request=_update_chat_request,
        terminate_chat_process=_terminate_chat_process,
        chat_cancel_grace_seconds=CHAT_CANCEL_GRACE_SECONDS,
        chat_cancel_poll_interval=CHAT_CANCEL_POLL_INTERVAL,
        is_process_alive=_is_process_alive,
        time_module=time,
        signal_module=signal,
    )


# Load sessions on startup
_load_all_sessions()
_load_all_folders()

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(
    __name__,
    template_folder=str(APP_ROOT / "templates"),
    static_folder=str(APP_ROOT / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BODY_SIZE
# Restrict CORS to localhost by default
CORS(app, resources={
    r"/*": {
        "origins": [
            "http://localhost:*",
            "http://127.0.0.1:*",
        ]
    }
})


def _request_id_or_dash() -> str:
    if has_request_context():
        return getattr(g, "request_id", "-")
    return "-"


def _should_log_request_summary(path: str, status_code: int, duration_ms: int) -> bool:
    if status_code >= 400:
        return True
    if duration_ms >= REQUEST_LOG_SLOW_MS:
        return True
    return path.startswith("/api/chat") or path.startswith("/api/upload")


register_request_hooks(
    app,
    logger=logger,
    max_request_body_size=MAX_REQUEST_BODY_SIZE,
    max_upload_size=MAX_UPLOAD_SIZE,
    request_id_or_dash=_request_id_or_dash,
    should_log_request_summary=_should_log_request_summary,
)

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def _current_webui_token() -> str:
    return _current_webui_token_impl(runtime_env_value=lambda key, default="": _runtime_env_value(key, default))


HERMES_WEBUI_TOKEN = _current_webui_token()

# --- Cookie-based session auth (login screen) ---
import secrets as _secrets

_DASHBOARD_USER = os.environ.get("HERMES_DASHBOARD_USER", "admin")
_DASHBOARD_PASS = os.environ.get("HERMES_DASHBOARD_PASS", "Unaitxo@13")
_SESSION_TOKEN_TTL = 86400  # 24 hours
_SESSION_TOKEN_STORE = Path(os.environ.get(
    "HERMES_WEBUI_TOKEN_STORE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".session_tokens.json"),
))


def _load_session_tokens() -> dict[str, float]:
    return _load_session_tokens_impl(
        token_store_path=_SESSION_TOKEN_STORE,
        time_fn=time.time,
    )


def _save_session_tokens(tokens: dict[str, float]) -> None:
    _save_session_tokens_impl(tokens, token_store_path=_SESSION_TOKEN_STORE)


def _register_session_token(token: str, expiry: float) -> None:
    _register_session_token_impl(
        token,
        expiry,
        load_session_tokens_fn=lambda: _load_session_tokens(),
        save_session_tokens_fn=lambda tokens: _save_session_tokens(tokens),
    )


def _remove_session_token(token: str) -> None:
    _remove_session_token_impl(
        token,
        load_session_tokens_fn=lambda: _load_session_tokens(),
        save_session_tokens_fn=lambda tokens: _save_session_tokens(tokens),
    )


def _verify_session_cookie() -> bool:
    return _verify_session_cookie_impl(
        load_session_tokens_fn=lambda: _load_session_tokens(),
        remove_session_token_fn=lambda token: _remove_session_token(token),
        time_fn=time.time,
    )


register_auth_routes(
    app,
    verify_session_cookie=_verify_session_cookie,
    register_session_token=_register_session_token,
    remove_session_token=_remove_session_token,
    dashboard_user=lambda: _DASHBOARD_USER,
    dashboard_pass=lambda: _DASHBOARD_PASS,
    session_token_ttl=_SESSION_TOKEN_TTL,
    token_generator=_secrets.token_urlsafe,
    time_fn=time.time,
)


require_token = build_require_token(
    logger=logger,
    verify_session_cookie=_verify_session_cookie,
    current_webui_token=lambda: _current_webui_token(),
)

# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------
# Simple in-memory rate limiter: {ip: [(timestamp, endpoint), ...]}
_rate_limit_store = {}
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_REQUESTS = 60  # requests per window per IP

rate_limit = build_rate_limit(
    logger=logger,
    rate_limit_store=_rate_limit_store,
    window_seconds=_RATE_LIMIT_WINDOW,
    max_requests=_RATE_LIMIT_MAX_REQUESTS,
    time_fn=time.time,
)

# ---------------------------------------------------------------------------
# Secret-key patterns
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = re.compile(r"(key|token|secret|password|apikey|api_key)", re.IGNORECASE)


# ===================================================================
# ConfigManager
# ===================================================================
class ConfigManager(_BaseConfigManager):
    def __init__(self):
        super().__init__(
            config_path_getter=lambda: _selected_config_path(),
            backup_dir_getter=lambda: _selected_backup_dir(),
            secret_patterns=_SECRET_PATTERNS,
        )


# Global config manager instance
cfg = ConfigManager()


# ===================================================================
# Helper utilities
# ===================================================================

def _mask_value(key: str, value: str) -> str:
    """Mask a single env-var value if its key looks secret."""
    if not isinstance(value, str):
        return value
    if _SECRET_PATTERNS.search(key) and len(value) > 4:
        return "\u2022" * (len(value) - 4) + value[-4:]
    return value


_KEEP_EXISTING_SECRET = object()


def _preserve_masked_secret_updates(current, update, parent_key: str = ""):
    """Drop masked secret placeholders from updates so existing secrets survive."""
    if isinstance(update, dict):
        current_dict = current if isinstance(current, dict) else {}
        sanitized = {}
        for key, value in update.items():
            result = _preserve_masked_secret_updates(current_dict.get(key), value, key)
            if result is _KEEP_EXISTING_SECRET:
                continue
            sanitized[key] = result
        return sanitized
    if isinstance(update, list):
        current_list = current if isinstance(current, list) else []
        sanitized = []
        for index, value in enumerate(update):
            current_value = current_list[index] if index < len(current_list) else None
            result = _preserve_masked_secret_updates(current_value, value, parent_key)
            if result is _KEEP_EXISTING_SECRET:
                sanitized.append(copy.deepcopy(current_value))
            else:
                sanitized.append(result)
        return sanitized
    if (
        isinstance(update, str)
        and isinstance(current, str)
        and current
        and _SECRET_PATTERNS.search(parent_key)
        and update == _mask_value(parent_key, current)
    ):
        return _KEEP_EXISTING_SECRET
    return copy.deepcopy(update)


def _normalized_model_config() -> dict:
    return _provider_service.normalized_model_config(
        cfg_get_raw=lambda: cfg.get_raw(),
        auxiliary_model_keys=AUXILIARY_MODEL_KEYS,
    )


def _provider_display_name(provider_type: str) -> str:
    return _provider_service.provider_display_name(
        provider_type,
        provider_type_labels=PROVIDER_TYPE_LABELS,
    )


def _infer_provider_type(name: str = "", base_url: str = "") -> str:
    return _provider_service.infer_provider_type(name=name, base_url=base_url, re_module=re)


def _normalize_provider_type(value: str = "", *, name: str = "", base_url: str = "") -> str:
    return _provider_service.normalize_provider_type(
        value,
        name=name,
        base_url=base_url,
        infer_provider_type_fn=lambda name="", base_url="": _infer_provider_type(name=name, base_url=base_url),
    )


def _provider_default_base_url(provider: str = "") -> str:
    return _provider_service.provider_default_base_url(
        provider,
        normalize_provider_type_fn=lambda value, name="", base_url="": _normalize_provider_type(value, name=name, base_url=base_url),
        provider_default_base_urls=PROVIDER_DEFAULT_BASE_URLS,
    )


def _normalize_provider_profile(entry: dict | str | None) -> dict:
    return _provider_service.normalize_provider_profile(
        entry,
        normalize_provider_type_fn=lambda value, name="", base_url="": _normalize_provider_type(value, name=name, base_url=base_url),
        provider_default_base_url_fn=lambda provider="": _provider_default_base_url(provider),
    )


def _custom_provider_profiles(raw: dict | None = None) -> list[dict]:
    raw = raw if raw is not None else cfg.get_raw()
    return _provider_service.custom_provider_profiles(
        raw=raw,
        normalize_provider_profile_fn=lambda entry: _normalize_provider_profile(entry),
    )


def _raw_role_profile_candidate(role: str, *, model_cfg: dict | None = None, raw: dict | None = None) -> dict | None:
    raw = raw if raw is not None else cfg.get_raw()
    model_cfg = model_cfg if model_cfg is not None else _normalized_model_config()
    return _provider_service.raw_role_profile_candidate(
        role,
        model_cfg=model_cfg,
        raw=raw,
        normalize_provider_type_fn=lambda value, name="", base_url="": _normalize_provider_type(value, name=name, base_url=base_url),
        provider_default_base_url_fn=lambda provider="": _provider_default_base_url(provider),
        role_routing_provider_fn=lambda current_role, model_cfg=None: _role_routing_provider(current_role, model_cfg=model_cfg),
        normalize_provider_profile_fn=lambda entry: _normalize_provider_profile(entry),
    )


def _available_provider_profiles(raw: dict | None = None, model_cfg: dict | None = None) -> list[dict]:
    raw = raw if raw is not None else cfg.get_raw()
    model_cfg = model_cfg if model_cfg is not None else _normalized_model_config()
    return _provider_service.available_provider_profiles(
        raw=raw,
        model_cfg=model_cfg,
        custom_provider_profiles_fn=lambda raw=None: _custom_provider_profiles(raw),
        raw_role_profile_candidate_fn=lambda role, model_cfg=None, raw=None: _raw_role_profile_candidate(role, model_cfg=model_cfg, raw=raw),
        model_role_labels=MODEL_ROLE_LABELS,
        normalize_provider_profile_fn=lambda entry: _normalize_provider_profile(entry),
    )


def _get_provider_profile(name: str, raw: dict | None = None) -> dict | None:
    raw = raw if raw is not None else cfg.get_raw()
    return _provider_service.get_provider_profile(
        name,
        available_provider_profiles_fn=lambda raw=None: _available_provider_profiles(raw),
        raw=raw,
    )


def _role_linked_profile_name(role: str, *, model_cfg: dict | None = None, raw: dict | None = None) -> str:
    raw = raw if raw is not None else cfg.get_raw()
    model_cfg = model_cfg if model_cfg is not None else _normalized_model_config()
    return _provider_service.role_linked_profile_name(
        role,
        model_cfg=model_cfg,
        raw=raw,
        custom_provider_profiles_fn=lambda raw=None: _custom_provider_profiles(raw),
        raw_role_profile_candidate_fn=lambda current_role, model_cfg=None, raw=None: _raw_role_profile_candidate(current_role, model_cfg=model_cfg, raw=raw),
    )


def _provider_usage_map(raw: dict | None = None, model_cfg: dict | None = None) -> dict[str, list[str]]:
    raw = raw if raw is not None else cfg.get_raw()
    model_cfg = model_cfg if model_cfg is not None else _normalized_model_config()
    return _provider_service.provider_usage_map(
        raw=raw,
        model_cfg=model_cfg,
        model_role_labels=MODEL_ROLE_LABELS,
        role_linked_profile_name_fn=lambda role, model_cfg=None, raw=None: _role_linked_profile_name(role, model_cfg=model_cfg, raw=raw),
    )


def _role_routing_provider(role: str, *, model_cfg: dict | None = None) -> str:
    model_cfg = model_cfg if model_cfg is not None else _normalized_model_config()
    return _provider_service.role_routing_provider(role, model_cfg=model_cfg)


def _resolve_role_target(role: str) -> dict:
    raw = cfg.get_raw()
    model_cfg = _normalized_model_config()
    return _provider_service.resolve_role_target(
        role,
        raw=raw,
        model_cfg=model_cfg,
        normalize_provider_type_fn=lambda value, name="", base_url="": _normalize_provider_type(value, name=name, base_url=base_url),
        provider_default_base_url_fn=lambda provider="": _provider_default_base_url(provider),
        role_linked_profile_name_fn=lambda current_role, model_cfg=None, raw=None: _role_linked_profile_name(current_role, model_cfg=model_cfg, raw=raw),
        profile_api_gateway_url_fn=lambda: _profile_api_gateway_url(),
        effective_hermes_api_url_fn=lambda default="": _effective_hermes_api_url(default),
        default_hermes_api_url=DEFAULT_HERMES_API_URL,
        role_routing_provider_fn=lambda current_role, model_cfg=None: _role_routing_provider(current_role, model_cfg=model_cfg),
        get_provider_profile_fn=lambda name, raw=None: _get_provider_profile(name, raw),
        resolve_runtime_template_fn=lambda value: _resolve_runtime_template(value),
        resolved_target_api_key_fn=lambda target: _resolved_target_api_key(target),
    )


def _model_role_enabled(role: str, target: dict | None = None) -> bool:
    return _provider_service.model_role_enabled(
        role,
        target=target,
        resolve_role_target_fn=lambda current_role: _resolve_role_target(current_role),
    )


def _model_role_info(role: str) -> dict:
    return _provider_service.model_role_info(
        role,
        resolve_role_target_fn=lambda current_role: _resolve_role_target(current_role),
        model_role_labels=MODEL_ROLE_LABELS,
        provider_display_name_fn=lambda provider_type: _provider_display_name(provider_type),
        model_role_enabled_fn=lambda current_role, target=None: _model_role_enabled(current_role, target=target),
    )


def _profile_payload_for_role(profile_name: str, model_name: str, routing_provider: str = "") -> dict:
    return _provider_service.profile_payload_for_role(
        profile_name,
        model_name,
        routing_provider,
        get_provider_profile_fn=lambda name: _get_provider_profile(name),
        chat_backend_error_cls=ChatBackendError,
    )


def _sync_linked_provider_roles(profile_name: str, profile: dict) -> None:
    _provider_service.sync_linked_provider_roles(
        profile_name,
        profile,
        cfg_get_raw=lambda: cfg.get_raw(),
        normalized_model_config_fn=lambda: _normalized_model_config(),
        role_linked_profile_name_fn=lambda role, model_cfg=None, raw=None: _role_linked_profile_name(role, model_cfg=model_cfg, raw=raw),
        cfg_update=lambda section, data: cfg.update(section, data),
    )


def _classify_env_key(key: str) -> str:
    """Classify an env var into a display group."""
    k = key.lower()
    if any(p in k for p in ("anthropic", "openai", "groq", "google", "mistral", "ollama", "together", "fireworks", "deepseek", "cohere", "xai", "bedrock", "azure")):
        return "Provider"
    if any(p in k for p in ("discord", "whatsapp", "telegram", "slack", "webhook", "matrix")):
        return "Channel"
    return "System"


def _env_var_metadata(key: str) -> dict:
    preset = ENV_VAR_PRESETS.get(str(key or "").strip(), {})
    return {
        "key": str(key or "").strip(),
        "group": preset.get("group") or _classify_env_key(key),
        "label": preset.get("label") or str(key or "").strip(),
        "description": preset.get("description") or "",
        "secret": bool(preset.get("secret")),
        "recommended": bool(preset.get("recommended")),
        "default_value": str(preset.get("default_value") or ""),
        "starter_pack_item": str(preset.get("starter_pack_item") or ""),
    }


def _env_presets_by_group() -> dict[str, list[dict]]:
    grouped = {group: [] for group in ENV_GROUP_HELP}
    for key in sorted(ENV_VAR_PRESETS.keys()):
        meta = _env_var_metadata(key)
        grouped.setdefault(meta["group"], []).append(meta)
    return grouped


def _slugify_capability(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return text.strip("-")


def _capability_preview_token(capability_type: str, payload: dict) -> str:
    def _canonicalize(value):
        if isinstance(value, dict):
            return {
                str(key): _canonicalize(item)
                for key, item in value.items()
                if str(key) not in {"recorded_at"}
            }
        if isinstance(value, list):
            return [_canonicalize(item) for item in value]
        return value

    encoded = json.dumps({
        "type": str(capability_type or "").strip(),
        "payload": _canonicalize(payload),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _capability_status_badge(status: str) -> str:
    if status == "active":
        return "Active"
    if status == "planned":
        return "Planned Next"
    return "Preview"


def _capability_integration_options(raw: dict | None = None) -> list[dict]:
    raw = raw if raw is not None else cfg.get_raw()
    existing = {entry.get("name"): entry for entry in _integration_entries(raw)}
    options = []
    for key in INTEGRATION_SECTION_ORDER:
        entry = existing.get(key) or {}
        config_value = raw.get(key)
        if not isinstance(config_value, dict):
            config_value = copy.deepcopy(INTEGRATION_CONFIG_TEMPLATES.get(key) or {})
        options.append({
            "name": key,
            "label": INTEGRATION_SECTION_LABELS.get(key, key.title()),
            "kind": "integration",
            "configured": bool(entry.get("configured")),
            "exists": isinstance(raw.get(key), dict),
            "config": cfg.mask_secrets(copy.deepcopy(config_value)),
            "config_template": copy.deepcopy(INTEGRATION_CONFIG_TEMPLATES.get(key) or {}),
            "suggested_env_vars": [
                _normalize_capability_env_var(item)
                for item in (INTEGRATION_ENV_TEMPLATES.get(key) or ())
                if _normalize_capability_env_var(item)
            ],
        })
    return options


def _agent_defaults(raw: dict | None = None) -> dict:
    raw = raw if raw is not None else cfg.get_raw()
    agent_cfg = raw.get("agent", {})
    return copy.deepcopy(agent_cfg) if isinstance(agent_cfg, dict) else {}


def _agent_personality_sections(raw: dict | None = None) -> tuple[dict, dict]:
    raw = raw if raw is not None else cfg.get_raw()
    agent_cfg = raw.get("agent", {})
    nested = agent_cfg.get("personalities", {}) if isinstance(agent_cfg, dict) else {}
    legacy = raw.get("personalities", {})
    nested = copy.deepcopy(nested) if isinstance(nested, dict) else {}
    legacy = copy.deepcopy(legacy) if isinstance(legacy, dict) else {}
    return nested, legacy


def _agent_personality_entries(raw: dict | None = None) -> tuple[dict[str, object], dict[str, str]]:
    nested, legacy = _agent_personality_sections(raw)
    merged = {}
    storage = {}
    for name, value in legacy.items():
        merged[str(name)] = copy.deepcopy(value)
        storage[str(name)] = "legacy"
    for name, value in nested.items():
        merged[str(name)] = copy.deepcopy(value)
        storage[str(name)] = "agent"
    return merged, storage


def _normalize_personality_value(value) -> object:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return str(value or "")
    system_prompt = str(value.get("system_prompt") or value.get("prompt") or "").strip()
    description = str(value.get("description") or "").strip()
    tone = str(value.get("tone") or "").strip()
    style = str(value.get("style") or "").strip()
    metadata = copy.deepcopy(value.get("metadata")) if isinstance(value.get("metadata"), dict) else None
    extra = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"prompt", "system_prompt", "description", "tone", "style", "metadata"}
    }
    if not any((description, tone, style, metadata, extra)):
        return system_prompt
    normalized = {}
    if description:
        normalized["description"] = description
    if system_prompt:
        normalized["system_prompt"] = system_prompt
    if tone:
        normalized["tone"] = tone
    if style:
        normalized["style"] = style
    if metadata:
        normalized["metadata"] = metadata
    normalized.update(extra)
    return normalized


def _personality_system_prompt(value) -> str:
    if isinstance(value, dict):
        parts = [str(value.get("system_prompt") or value.get("prompt") or "").strip()]
        if value.get("tone"):
            parts.append(f"Tone: {value['tone']}")
        if value.get("style"):
            parts.append(f"Style: {value['style']}")
        return "\n".join(part for part in parts if part)
    return str(value or "")


def _personality_entry_for_api(name: str, value) -> dict:
    normalized = _normalize_personality_value(value)
    metadata = normalized.get("metadata") if isinstance(normalized, dict) and isinstance(normalized.get("metadata"), dict) else {}
    hermes_meta = metadata.get("hermes_web_ui") if isinstance(metadata, dict) else {}
    return {
        "name": name,
        "kind": str((hermes_meta or {}).get("capability_type") or "personality"),
        "description": str((normalized.get("description") if isinstance(normalized, dict) else "") or "").strip(),
        "system_prompt": _personality_system_prompt(normalized),
        "value": cfg.mask_secrets(copy.deepcopy(normalized)),
        "metadata": cfg.mask_secrets(copy.deepcopy(metadata)) if metadata else {},
    }


def _capability_catalog() -> dict:
    raw = cfg.get_raw()
    integrations = _integration_entries(raw)
    skills = _discover_skill_entries()
    configured_integrations = [entry for entry in integrations if entry.get("configured")]
    model_roles = {
        role: _model_role_info(role)
        for role in MODEL_ROLE_LABELS
    }
    profiles = []
    usage_map = _provider_usage_map(raw=raw)
    for profile in _available_provider_profiles(raw):
        safe = cfg.mask_secrets(profile)
        safe["used_by"] = usage_map.get(profile.get("name", ""), [])
        safe["has_api_key"] = bool(profile.get("api_key") or _provider_env_api_key(profile.get("provider")))
        safe["provider_label"] = _provider_display_name(profile.get("provider", ""))
        profiles.append(safe)
    personalities, storage = _agent_personality_entries(raw)
    return {
        "recommended_order": list(CAPABILITY_IMPLEMENTATION_ORDER),
        "architecture_rules": list(CAPABILITY_ARCHITECTURE_RULES),
        "mvp_scope": list(CAPABILITY_MVP_SCOPE),
        "types": [
            {
                **definition,
                "status_label": _capability_status_badge(definition.get("status", "")),
            }
            for definition in (CAPABILITY_TYPE_DEFINITIONS[key] for key in CAPABILITY_RECOMMENDED_ORDER)
        ],
        "context": {
            "skills_dir": str(SKILLS_DIR),
            "integrations_total": len(integrations),
            "integrations_configured": len(configured_integrations),
            "integration_names": [entry.get("name") for entry in integrations if entry.get("name")],
            "integration_options": _capability_integration_options(raw),
            "model_roles": model_roles,
            "provider_profiles": profiles,
            "skills": [
                {
                    "path": skill.get("path"),
                    "name": skill.get("name"),
                    "description": skill.get("description"),
                    "enabled": bool(skill.get("enabled")),
                    "ready": bool(((skill.get("setup") or {}).get("ready"))),
                }
                for skill in skills
            ],
            "agent_defaults": {
                key: value
                for key, value in _agent_defaults(raw).items()
                if key != "personalities"
            },
            "personality_names": sorted(personalities.keys(), key=str.casefold),
            "personality_storage": storage,
        },
    }


def _normalize_capability_env_var(entry) -> dict:
    return _capability_integration_service.normalize_capability_env_var(
        entry,
        re_module=re,
        env_var_metadata_fn=lambda key: _env_var_metadata(key),
        classify_env_key_fn=lambda key: _classify_env_key(key),
        env_group_help=ENV_GROUP_HELP,
    )


def _normalize_capability_credential_file(entry) -> dict:
    return _capability_skill_service.normalize_capability_credential_file(
        entry,
        safe_skill_rel_path_fn=lambda value: _safe_skill_rel_path(value),
        path_class=Path,
    )


def _normalize_capability_required_command(entry) -> dict:
    return _capability_skill_service.normalize_capability_required_command(entry)


def _normalize_capability_env_assignment(entry) -> dict:
    return _capability_integration_service.normalize_capability_env_assignment(
        entry,
        normalize_capability_env_var_fn=lambda value: _normalize_capability_env_var(value),
    )


def _restore_text_file(path: Path, previous_text: str | None) -> None:
    _capability_integration_service.restore_text_file(path, previous_text)


def _normalize_integration_capability_draft(data: dict | None) -> tuple[dict, list[str]]:
    return _capability_integration_service.normalize_integration_capability_draft(
        data,
        integration_config_templates=INTEGRATION_CONFIG_TEMPLATES,
        integration_section_labels=INTEGRATION_SECTION_LABELS,
        normalize_capability_env_assignment_fn=lambda entry: _normalize_capability_env_assignment(entry),
    )


def _integration_capability_conflicts(kind: str, raw: dict | None = None) -> list[str]:
    if raw is not None:
        current = raw.get(kind)
        if isinstance(current, dict) and _integration_config_is_configured(current):
            return [str(CONFIG_PATH)]
        return []
    return _capability_integration_service.integration_capability_conflicts(
        kind,
        cfg_get_raw=lambda: cfg.get_raw(),
        integration_config_is_configured_fn=lambda value: _integration_config_is_configured(value),
        config_path=CONFIG_PATH,
    )


def _integration_capability_readiness(draft: dict, env_values: dict[str, str] | None = None) -> dict:
    return _capability_integration_service.integration_capability_readiness(
        draft,
        env_values=env_values,
        classify_env_key_fn=lambda key: _classify_env_key(key),
        integration_config_is_configured_fn=lambda value: _integration_config_is_configured(value),
    )


def _preview_integration_capability(data: dict | None) -> tuple[dict, int]:
    return _capability_integration_service.preview_integration_capability(
        data,
        normalize_integration_capability_draft_fn=lambda value: _normalize_integration_capability_draft(value),
        cfg_get_raw=lambda: cfg.get_raw(),
        env_path=ENV_PATH,
        dotenv_values_fn=dotenv_values,
        integration_capability_conflicts_fn=lambda kind, raw=None: _integration_capability_conflicts(kind, raw=raw),
        integration_capability_readiness_fn=lambda draft, env_values=None: _integration_capability_readiness(draft, env_values=env_values),
        integration_entries_fn=lambda raw=None: _integration_entries(raw),
        integration_config_is_configured_fn=lambda value: _integration_config_is_configured(value),
        cfg_mask_secrets=lambda value: cfg.mask_secrets(value),
        config_path=CONFIG_PATH,
        mask_value_fn=lambda key, value: _mask_value(key, value),
        capability_preview_token_fn=lambda capability_type, payload: _capability_preview_token(capability_type, payload),
    )


def _apply_integration_capability(data: dict | None, preview_token: str) -> tuple[dict, int]:
    return _capability_integration_service.apply_integration_capability(
        data,
        preview_token,
        preview_integration_capability_fn=lambda value: _preview_integration_capability(value),
        config_path=CONFIG_PATH,
        env_path=ENV_PATH,
        set_env_value_fn=lambda path, key, value: _set_env_value(path, key, value),
        cfg_set=lambda section, value: cfg.set(section, value),
        restore_text_file_fn=lambda path, previous_text: _restore_text_file(path, previous_text),
        cfg_load=lambda: cfg.load(),
        integration_entries_fn=lambda raw=None: _integration_entries(raw),
    )


def _normalize_agent_preset_role(role: str, payload, profile_names: set[str]) -> tuple[dict, list[str]]:
    return _capability_agent_preset_service.normalize_agent_preset_role(
        role,
        payload,
        profile_names,
        model_role_labels=MODEL_ROLE_LABELS,
    )


def _render_agent_preset_fragment(name: str, personality: dict) -> str:
    return _capability_agent_preset_service.render_agent_preset_fragment(
        name,
        personality,
        yaml_module=yaml,
    )


def _normalize_agent_preset_draft(data: dict | None) -> tuple[dict, list[str]]:
    return _capability_agent_preset_service.normalize_agent_preset_draft(
        data,
        cfg_get_raw=lambda: cfg.get_raw(),
        available_provider_profiles_fn=lambda raw: _available_provider_profiles(raw),
        discover_skill_entries_fn=lambda: _discover_skill_entries(),
        capability_integration_options_fn=lambda raw: _capability_integration_options(raw),
        normalize_agent_preset_role_fn=lambda role, payload, profile_names: _normalize_agent_preset_role(role, payload, profile_names),
        model_role_labels=MODEL_ROLE_LABELS,
        agent_reasoning_effort_options=AGENT_REASONING_EFFORT_OPTIONS,
    )


def _agent_preset_conflicts(name: str, raw: dict | None = None) -> list[str]:
    return _capability_agent_preset_service.agent_preset_conflicts(
        name,
        agent_personality_entries_fn=lambda value=None: _agent_personality_entries(value),
        config_path=CONFIG_PATH,
        raw=raw,
    )


def _agent_preset_personality_manifest(draft: dict) -> dict:
    return _capability_agent_preset_service.agent_preset_personality_manifest(draft)


def _preview_agent_preset_capability(data: dict | None) -> tuple[dict, int]:
    return _capability_agent_preset_service.preview_agent_preset_capability(
        data,
        normalize_agent_preset_draft_fn=lambda value: _normalize_agent_preset_draft(value),
        cfg_get_raw=lambda: cfg.get_raw(),
        agent_personality_entries_fn=lambda value=None: _agent_personality_entries(value),
        discover_skill_entries_fn=lambda: _discover_skill_entries(),
        capability_integration_options_fn=lambda raw: _capability_integration_options(raw),
        agent_preset_conflicts_fn=lambda name, raw=None: _agent_preset_conflicts(name, raw=raw),
        agent_preset_personality_manifest_fn=lambda draft: _agent_preset_personality_manifest(draft),
        config_path=CONFIG_PATH,
        render_agent_preset_fragment_fn=lambda name, personality: _render_agent_preset_fragment(name, personality),
        capability_preview_token_fn=lambda capability_type, payload: _capability_preview_token(capability_type, payload),
    )


def _apply_agent_preset_capability(data: dict | None, preview_token: str) -> tuple[dict, int]:
    return _capability_agent_preset_service.apply_agent_preset_capability(
        data,
        preview_token,
        preview_agent_preset_capability_fn=lambda value: _preview_agent_preset_capability(value),
        config_path=CONFIG_PATH,
        cfg_get_raw=lambda: cfg.get_raw(),
        cfg_set=lambda section, value: cfg.set(section, value),
        restore_text_file_fn=lambda path, previous_text: _restore_text_file(path, previous_text),
        cfg_load=lambda: cfg.load(),
        agent_personality_entries_fn=lambda value=None: _agent_personality_entries(value),
    )


def _normalize_skill_capability_draft(data: dict | None) -> tuple[dict, list[str]]:
    return _capability_skill_service.normalize_skill_capability_draft(
        data,
        slugify_capability_fn=lambda value: _slugify_capability(value),
        normalize_capability_env_var_fn=lambda entry: _normalize_capability_env_var(entry),
        normalize_capability_credential_file_fn=lambda entry: _normalize_capability_credential_file(entry),
        normalize_capability_required_command_fn=lambda entry: _normalize_capability_required_command(entry),
    )


def _render_skill_capability_frontmatter(draft: dict) -> dict:
    return _capability_skill_service.render_skill_capability_frontmatter(draft)


def _render_skill_capability_markdown(draft: dict, frontmatter: dict) -> str:
    return _capability_skill_service.render_skill_capability_markdown(
        draft,
        frontmatter,
        yaml_module=yaml,
    )


def _capability_skill_source_metadata() -> dict:
    return _capability_skill_service.capability_skill_source_metadata(
        build_skill_source_record_fn=lambda *args, **kwargs: _build_skill_source_record(*args, **kwargs),
    )


def _capability_skill_conflicts(slug: str) -> list[str]:
    return _capability_skill_service.capability_skill_conflicts(
        slug,
        skill_request_paths_fn=lambda value: _skill_request_paths(value),
        path_class=Path,
    )


def _preview_skill_capability(data: dict | None) -> tuple[dict, int]:
    return _capability_skill_service.preview_skill_capability(
        data,
        normalize_skill_capability_draft_fn=lambda value: _normalize_skill_capability_draft(value),
        render_skill_capability_frontmatter_fn=lambda draft: _render_skill_capability_frontmatter(draft),
        render_skill_capability_markdown_fn=lambda draft, frontmatter: _render_skill_capability_markdown(draft, frontmatter),
        capability_skill_source_metadata_fn=lambda: _capability_skill_source_metadata(),
        skill_setup_readiness_fn=lambda skill: _skill_setup_readiness(skill),
        skills_dir=SKILLS_DIR,
        capability_skill_conflicts_fn=lambda slug: _capability_skill_conflicts(slug),
        capability_preview_token_fn=lambda capability_type, payload: _capability_preview_token(capability_type, payload),
    )


def _apply_skill_capability(data: dict | None, preview_token: str) -> tuple[dict, int]:
    return _capability_skill_service.apply_skill_capability(
        data,
        preview_token,
        preview_skill_capability_fn=lambda value: _preview_skill_capability(value),
        skills_dir=SKILLS_DIR,
        uuid_module=uuid,
        write_skill_source_metadata_fn=lambda skill_dir, metadata: _write_skill_source_metadata(skill_dir, metadata),
        capability_skill_source_metadata_fn=lambda: _capability_skill_source_metadata(),
        discover_skill_entries_fn=lambda: _discover_skill_entries(),
        shutil_module=shutil,
    )


OFFICIAL_HERMES_REPO_URLS = {
    "https://github.com/NousResearch/hermes-agent.git",
    "https://github.com/NousResearch/hermes-agent",
    "git@github.com:NousResearch/hermes-agent.git",
    "git@github.com:NousResearch/hermes-agent",
}
OFFICIAL_HERMES_REPO_URL = "https://github.com/NousResearch/hermes-agent.git"
HERMES_REINSTALL_COMMAND = (
    "curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash"
)


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_gateway_pid_record() -> dict:
    pid_file = _selected_gateway_pid_path()
    if not pid_file.exists():
        return {}
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        return payload
    if raw.isdigit():
        return {"pid": int(raw)}
    return {"raw": raw}


def _gateway_pid_record_is_live(record: dict) -> bool:
    pid = record.get("pid")
    return isinstance(pid, int) and _is_process_alive(pid)


def _candidate_hermes_bins() -> list[dict]:
    candidates = []
    seen = set()

    def _add(raw_path, source: str):
        if not raw_path:
            return
        try:
            path = Path(raw_path).expanduser()
        except Exception:
            return
        if not path.exists():
            return
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            resolved = path
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        candidates.append({
            "path": path,
            "resolved_path": resolved,
            "source": source,
        })

    gateway_record = _read_gateway_pid_record()
    argv = gateway_record.get("argv") if _gateway_pid_record_is_live(gateway_record) else None

    _add(os.environ.get("HERMES_WEBUI_HERMES_BIN"), "env_override")
    _add(os.environ.get("HERMES_BIN"), "env_hint")
    _add(HERMES_REPO_DIR / "venv" / "bin" / "hermes", "managed_repo")
    _add(Path.home() / ".local" / "bin" / "hermes", "user_local_bin")
    _add(shutil.which("hermes"), "path_lookup")
    _add(HERMES_BIN, "webui_default")
    if isinstance(argv, list) and argv:
        _add(argv[0], "active_gateway")
    _add(HERMES_HOME / ".venv" / "bin" / "hermes", "legacy_home_venv")
    return candidates


def _selected_hermes_candidate() -> dict:
    candidates = _candidate_hermes_bins()
    if candidates:
        return candidates[0]
    fallback = Path(HERMES_BIN).expanduser()
    try:
        resolved = fallback.resolve(strict=False)
    except Exception:
        resolved = fallback
    return {
        "path": fallback,
        "resolved_path": resolved,
        "source": "fallback",
    }


def _selected_hermes_bin() -> Path:
    return _selected_hermes_candidate()["path"]


def _selection_reason_for_candidate(source: str) -> str:
    if source == "active_gateway":
        return f"Using the live gateway binary recorded in {_selected_gateway_pid_path()}."
    if source == "env_override":
        return "Using the Hermes binary path from HERMES_WEBUI_HERMES_BIN."
    if source == "env_hint":
        return "Using the Hermes binary path from HERMES_BIN."
    if source == "managed_repo":
        return f"Using the repo-managed Hermes install under {HERMES_REPO_DIR}."
    if source == "legacy_home_venv":
        return f"Using the Hermes venv rooted at {_selected_hermes_home()}."
    if source == "user_local_bin":
        return "Using the Hermes launcher from ~/.local/bin."
    if source == "path_lookup":
        return "Using the Hermes binary found on PATH."
    return "Using the default Hermes binary configured for Hermes Web UI."


def _run_hermes_with_bin(bin_path: Path, *args, timeout: int = 30, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "NO_COLOR": "1", "HERMES_HOME": str(_selected_hermes_home())}
    return subprocess.run(
        [str(bin_path)] + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def _run_hermes(*args, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a hermes CLI command with the currently managed Hermes binary."""
    return _run_hermes_with_bin(_selected_hermes_bin(), *args, timeout=timeout)


def _combined_process_output(result: subprocess.CompletedProcess) -> str:
    return "\n".join(
        part.strip()
        for part in (result.stdout, result.stderr)
        if part and part.strip()
    ).strip()


def _first_output_line(*parts) -> str:
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        return text.splitlines()[0].strip()
    return ""


def _build_version_display(version: str = "", release_date: str = "") -> str:
    version = str(version or "").strip()
    release_date = str(release_date or "").strip()
    if version and release_date:
        return f"Hermes Agent v{version} ({release_date})"
    if version:
        return f"Hermes Agent v{version}"
    return "Unknown"


def _parse_hermes_version_output(raw_output: str) -> dict:
    lines = [line.strip() for line in str(raw_output or "").splitlines() if line.strip()]
    first_line = lines[0] if lines else ""
    match = re.search(r"Hermes Agent v?([^\s]+)(?: \(([^)]+)\))?", first_line)
    project_root = ""
    python_version = ""
    openai_sdk = ""
    update_hint = ""
    for line in lines[1:]:
        if line.startswith("Project:"):
            project_root = line.split(":", 1)[1].strip()
        elif line.startswith("Python:"):
            python_version = line.split(":", 1)[1].strip()
        elif line.startswith("OpenAI SDK:"):
            openai_sdk = line.split(":", 1)[1].strip()
        elif line.lower() == "up to date" or line.lower().startswith("update available:"):
            update_hint = line
    version = match.group(1).strip() if match else ""
    release_date = match.group(2).strip() if match and match.group(2) else ""
    return {
        "raw": str(raw_output or "").strip(),
        "display": _build_version_display(version, release_date) if match else first_line or "Unknown",
        "version": version,
        "release_date": release_date,
        "project_root": project_root,
        "python_version": python_version,
        "openai_sdk": openai_sdk,
        "update_hint": update_hint,
        "first_line": first_line,
    }


def _extract_version_from_git_init(raw_text: str) -> dict:
    version_match = re.search(r'__version__\s*=\s*"([^"]+)"', str(raw_text or ""))
    release_match = re.search(r'__release_date__\s*=\s*"([^"]+)"', str(raw_text or ""))
    version = version_match.group(1).strip() if version_match else ""
    release_date = release_match.group(1).strip() if release_match else ""
    return {
        "version": version,
        "release_date": release_date,
        "display": _build_version_display(version, release_date),
    }


def _classify_update_scope(availability_status: str, installed: dict, repo_state: dict) -> str:
    if availability_status == "up_to_date":
        return "current"
    if availability_status != "update_available":
        return "unknown"

    behind_commits = (repo_state or {}).get("behind_commits")
    if not isinstance(behind_commits, int) or behind_commits <= 0:
        return "unknown"

    latest = (repo_state or {}).get("latest_version") or {}
    installed_version = str((installed or {}).get("version") or "").strip()
    installed_release_date = str((installed or {}).get("release_date") or "").strip()
    latest_version = str(latest.get("version") or "").strip()
    latest_release_date = str(latest.get("release_date") or "").strip()

    same_version = bool(installed_version and latest_version and installed_version == latest_version)
    same_release_date = bool(
        installed_release_date and latest_release_date and installed_release_date == latest_release_date
    )
    if same_version and (not latest_release_date or not installed_release_date or same_release_date):
        return "revision"
    if same_release_date and not latest_version:
        return "revision"
    if (
        (latest_version and installed_version and latest_version != installed_version)
        or (latest_release_date and installed_release_date and latest_release_date != installed_release_date)
    ):
        return "release"
    if latest_version or latest_release_date:
        return "release"
    return "revision"


def _normalized_git_url(url: str) -> str:
    normalized = str(url or "").strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized


def _is_official_hermes_remote(url: str) -> bool:
    normalized = _normalized_git_url(url)
    return normalized in {_normalized_git_url(value) for value in OFFICIAL_HERMES_REPO_URLS}


def _detect_managed_install() -> tuple[str, str]:
    raw = str(os.environ.get("HERMES_MANAGED") or "").strip()
    marker = HERMES_HOME / ".managed"
    value = raw.lower()
    if value in {"homebrew", "brew"}:
        return "Homebrew", "brew upgrade hermes-agent"
    if value in {"true", "1", "yes", "nixos", "nix"} or marker.exists():
        return "NixOS", "sudo nixos-rebuild switch"
    if raw:
        return raw, ""
    return "", ""


def _guess_repo_root(bin_path: Path, project_root: str = "") -> Path | None:
    guesses = []
    if project_root:
        guesses.append(Path(project_root).expanduser())
    try:
        resolved = Path(bin_path).expanduser().resolve(strict=False)
        guesses.append(resolved.parent.parent.parent)
    except Exception:
        pass
    try:
        guesses.append(Path(bin_path).expanduser().parent.parent.parent)
    except Exception:
        pass
    guesses.extend([HERMES_HOME / "hermes-agent", HERMES_HOME])
    seen = set()
    for guess in guesses:
        guess_str = str(guess)
        if guess_str in seen:
            continue
        seen.add(guess_str)
        if (guess / ".git").exists():
            return guess
    return None


def _run_git(repo_dir: Path, *args, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(repo_dir),
    )


def _summarize_git_worktree(repo_dir: Path) -> dict:
    tracked = 0
    untracked = 0
    sample = []
    try:
        result = _run_git(repo_dir, "status", "--porcelain", timeout=10)
    except Exception as exc:
        return {
            "tracked": 0,
            "untracked": 0,
            "total": 0,
            "sample": [],
            "error": str(exc),
        }
    if result.returncode != 0:
        return {
            "tracked": 0,
            "untracked": 0,
            "total": 0,
            "sample": [],
            "error": _first_output_line(result.stderr, result.stdout),
        }
    for line in result.stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("??"):
            untracked += 1
        else:
            tracked += 1
        if len(sample) < 10:
            sample.append(line)
    return {
        "tracked": tracked,
        "untracked": untracked,
        "total": tracked + untracked,
        "sample": sample,
        "error": "",
    }


def _select_update_remote(repo_dir: Path) -> dict:
    remotes = []
    for name in ("origin", "upstream"):
        try:
            result = _run_git(repo_dir, "remote", "get-url", name, timeout=5)
        except Exception:
            continue
        if result.returncode != 0:
            continue
        url = str(result.stdout or "").strip()
        if not url:
            continue
        remotes.append({
            "remote": name,
            "url": url,
            "official": _is_official_hermes_remote(url),
        })
    for remote in remotes:
        if remote["official"] and remote["remote"] == "origin":
            return remote
    for remote in remotes:
        if remote["official"]:
            return remote
    if remotes:
        return remotes[0]
    return {"remote": "", "url": "", "official": False}


def _cache_key_for_repo(repo_dir: Path) -> str:
    return str(repo_dir.resolve())


def _invalidate_hermes_update_cache(repo_dir: Path | None = None) -> None:
    with hermes_update_cache_lock:
        if repo_dir is None:
            hermes_update_cache.clear()
            return
        hermes_update_cache.pop(_cache_key_for_repo(repo_dir), None)


def _probe_repo_update_state(repo_dir: Path) -> dict:
    remote = _select_update_remote(repo_dir)
    remote_name = remote.get("remote") or ""
    remote_url = remote.get("url") or ""
    remote_branch = "main"
    remote_ref = f"{remote_name}/{remote_branch}" if remote_name else ""
    fetch_error = ""
    fetched = False

    if remote_name:
        try:
            fetch_result = _run_git(repo_dir, "fetch", remote_name, "--quiet", timeout=20)
            fetched = fetch_result.returncode == 0
            if not fetched:
                fetch_error = _first_output_line(fetch_result.stderr, fetch_result.stdout)
        except Exception as exc:
            fetch_error = str(exc)

    local_commit = ""
    latest_commit = ""
    behind_commits = None
    ahead_commits = None
    latest_version = {"version": "", "release_date": "", "display": "Unknown"}

    try:
        local_result = _run_git(repo_dir, "rev-parse", "--short", "HEAD", timeout=5)
        if local_result.returncode == 0:
            local_commit = str(local_result.stdout or "").strip()
    except Exception:
        pass

    ref_exists = False
    if remote_ref:
        try:
            ref_check = _run_git(repo_dir, "rev-parse", "--verify", remote_ref, timeout=5)
            ref_exists = ref_check.returncode == 0
            if ref_exists:
                latest_commit = str(ref_check.stdout or "").strip()[:8]
        except Exception:
            ref_exists = False

    if ref_exists:
        try:
            behind_result = _run_git(repo_dir, "rev-list", "--count", f"HEAD..{remote_ref}", timeout=10)
            if behind_result.returncode == 0:
                behind_commits = int(str(behind_result.stdout or "0").strip() or "0")
        except Exception:
            behind_commits = None
        try:
            ahead_result = _run_git(repo_dir, "rev-list", "--count", f"{remote_ref}..HEAD", timeout=10)
            if ahead_result.returncode == 0:
                ahead_commits = int(str(ahead_result.stdout or "0").strip() or "0")
        except Exception:
            ahead_commits = None
        try:
            init_result = _run_git(repo_dir, "show", f"{remote_ref}:hermes_cli/__init__.py", timeout=10)
            if init_result.returncode == 0:
                latest_version = _extract_version_from_git_init(init_result.stdout)
        except Exception:
            pass

    availability_status = "unknown_latest"
    if behind_commits is not None:
        availability_status = "update_available" if behind_commits > 0 else "up_to_date"

    return {
        "availability_status": availability_status,
        "checked_at": _utc_now_z(),
        "source": {
            "remote": remote_name,
            "branch": remote_branch,
            "ref": remote_ref,
            "url": remote_url,
            "official": bool(remote.get("official")),
            "label": (
                f"GitHub {remote_name}/{remote_branch}"
                if remote_name and remote.get("official")
                else (f"{remote_name}/{remote_branch}" if remote_name else "Unavailable")
            ),
        },
        "fetched": fetched,
        "fetch_error": fetch_error,
        "behind_commits": behind_commits,
        "ahead_commits": ahead_commits,
        "local_commit": local_commit,
        "latest_commit": latest_commit,
        "latest_version": latest_version,
        "worktree": _summarize_git_worktree(repo_dir),
    }


def _get_repo_update_state(repo_dir: Path, *, force_refresh: bool = False) -> dict:
    cache_key = _cache_key_for_repo(repo_dir)
    now = time.time()
    with hermes_update_cache_lock:
        cached = hermes_update_cache.get(cache_key)
        if (
            cached
            and not force_refresh
            and (now - float(cached.get("ts") or 0)) < HERMES_UPDATE_CACHE_SECONDS
        ):
            return copy.deepcopy(cached["payload"])
    payload = _probe_repo_update_state(repo_dir)
    with hermes_update_cache_lock:
        hermes_update_cache[cache_key] = {
            "ts": now,
            "payload": copy.deepcopy(payload),
        }
    return payload


def _manual_update_command(bin_path: Path, repo_dir: Path | None, managed_system: str, managed_command: str) -> str:
    if managed_command:
        return managed_command
    if repo_dir and (repo_dir / ".git").exists():
        return f"cd {shlex.quote(str(repo_dir))} && {shlex.quote(str(bin_path))} update"
    return HERMES_REINSTALL_COMMAND


def _manual_update_reason(repo_dir: Path | None, managed_system: str, managed_command: str) -> str:
    if managed_command:
        label = managed_system or "your package manager"
        return f"This Hermes install is managed by {label}. Run the manual upgrade command instead."
    if repo_dir and (repo_dir / ".git").exists():
        return ""
    return "This Hermes install is not a git checkout that Hermes can safely update in place."


def _base_update_message(availability_status: str, update_scope: str, installed: dict, repo_state: dict) -> str:
    source_label = (((repo_state or {}).get("source") or {}).get("label") or "the configured update source").strip()
    installed_display = installed.get("display") or "Installed Hermes"
    latest = (repo_state or {}).get("latest_version") or {}
    latest_display = latest.get("display") or ""
    behind_commits = (repo_state or {}).get("behind_commits")

    if availability_status == "up_to_date":
        return f"{installed_display} is current with {source_label}."
    if availability_status == "update_available":
        if update_scope == "revision":
            if behind_commits:
                word = "commit" if behind_commits == 1 else "commits"
                return (
                    f"{installed_display} matches the latest released Hermes version, "
                    f"but {source_label} is {behind_commits} {word} ahead."
                )
            return f"{installed_display} matches the latest released Hermes version, but newer commits are available on {source_label}."
        if latest_display and latest.get("version"):
            return f"{latest_display} is available on {source_label}."
        if behind_commits:
            word = "commit" if behind_commits == 1 else "commits"
            return f"A newer Hermes revision is available on {source_label} ({behind_commits} {word} ahead)."
        return f"A newer Hermes revision is available on {source_label}."
    return f"Couldn't determine the latest Hermes version from {source_label}."


def _runtime_snapshot() -> dict:
    with hermes_update_runtime_lock:
        snapshot = copy.deepcopy(hermes_update_runtime)
    snapshot["log_text"] = "\n".join(snapshot.get("logs") or [])
    return snapshot


def _set_update_runtime(**updates) -> None:
    with hermes_update_runtime_lock:
        hermes_update_runtime.update(updates)


def _append_update_log(line: str) -> None:
    text = str(line or "").rstrip()
    if not text:
        return
    with hermes_update_runtime_lock:
        logs = list(hermes_update_runtime.get("logs") or [])
        logs.append(text)
        if len(logs) > HERMES_UPDATE_LOG_LINE_LIMIT:
            logs = logs[-HERMES_UPDATE_LOG_LINE_LIMIT:]
        hermes_update_runtime["logs"] = logs
        hermes_update_runtime["summary"] = text


def _build_hermes_update_payload(*, force_refresh: bool = False) -> dict:
    selected = _selected_hermes_candidate()
    bin_path = selected["path"]
    resolved_path = selected["resolved_path"]
    version_info = {
        "raw": "",
        "display": "Unknown",
        "version": "",
        "release_date": "",
        "project_root": "",
        "python_version": "",
        "openai_sdk": "",
        "update_hint": "",
        "first_line": "",
    }
    version_error = ""
    try:
        version_result = _run_hermes_with_bin(bin_path, "--version", timeout=20)
        raw_output = (version_result.stdout or "") + ("\n" + version_result.stderr if version_result.stderr else "")
        version_info = _parse_hermes_version_output(raw_output)
    except Exception as exc:
        version_error = str(exc)

    repo_dir = _guess_repo_root(resolved_path, version_info.get("project_root") or "")
    managed_system, managed_command = _detect_managed_install()
    repo_state = {
        "availability_status": "unknown_latest",
        "checked_at": "",
        "source": {"remote": "", "branch": "", "ref": "", "url": "", "official": False, "label": "Unavailable"},
        "fetched": False,
        "fetch_error": "",
        "behind_commits": None,
        "ahead_commits": None,
        "local_commit": "",
        "latest_commit": "",
        "latest_version": {"version": "", "release_date": "", "display": "Unknown"},
        "worktree": {"tracked": 0, "untracked": 0, "total": 0, "sample": [], "error": ""},
    }
    if repo_dir:
        repo_state = _get_repo_update_state(repo_dir, force_refresh=force_refresh)

    availability_status = repo_state.get("availability_status") or "unknown_latest"
    update_scope = _classify_update_scope(availability_status, version_info, repo_state)
    can_update = bool(bin_path.exists() and repo_dir and (repo_dir / ".git").exists() and not managed_command)
    install_method = "binary_only"
    if managed_command:
        install_method = "managed"
    elif repo_dir and repo_dir == HERMES_HOME and ".venv" in str(resolved_path):
        install_method = "git_home_venv"
    elif repo_dir and repo_dir.name == "hermes-agent":
        install_method = "git_repo_venv"
    elif repo_dir:
        install_method = "git_checkout"

    runtime = _runtime_snapshot()
    status = availability_status
    message = _base_update_message(availability_status, update_scope, version_info, repo_state)
    if runtime.get("status") == "update_in_progress":
        status = "update_in_progress"
        message = runtime.get("summary") or f"Updating Hermes from {version_info.get('display') or 'the installed version'}..."
    elif runtime.get("status") == "update_failed":
        status = "update_failed"
        message = runtime.get("error") or runtime.get("summary") or "Hermes update failed."

    other_candidates = [
        str(candidate["path"])
        for candidate in _candidate_hermes_bins()[1:]
    ]

    return {
        "status": status,
        "availability_status": availability_status,
        "update_scope": update_scope,
        "message": message,
        "checked_at": repo_state.get("checked_at") or "",
        "bin_path": str(bin_path),
        "resolved_bin_path": str(resolved_path),
        "project_root": str(repo_dir) if repo_dir else (version_info.get("project_root") or ""),
        "install_method": install_method,
        "selection_reason": _selection_reason_for_candidate(selected.get("source") or ""),
        "other_detected_bins": other_candidates,
        "installed_version": version_info,
        "latest_version": repo_state.get("latest_version") or {"version": "", "release_date": "", "display": "Unknown"},
        "official_source": repo_state.get("source") or {},
        "behind_commits": repo_state.get("behind_commits"),
        "ahead_commits": repo_state.get("ahead_commits"),
        "local_commit": repo_state.get("local_commit") or "",
        "latest_commit": repo_state.get("latest_commit") or "",
        "fetch_error": repo_state.get("fetch_error") or "",
        "fetched": bool(repo_state.get("fetched")),
        "version_error": version_error,
        "can_update": can_update,
        "managed_system": managed_system,
        "manual_command": _manual_update_command(bin_path, repo_dir, managed_system, managed_command),
        "manual_reason": _manual_update_reason(repo_dir, managed_system, managed_command),
        "worktree": repo_state.get("worktree") or {"tracked": 0, "untracked": 0, "total": 0, "sample": [], "error": ""},
        "update_action": runtime,
        "install_key": str(repo_dir or resolved_path or bin_path),
    }


def _run_hermes_update_worker(install_snapshot: dict) -> None:
    bin_path = Path(install_snapshot.get("bin_path") or "")
    repo_dir = Path(install_snapshot.get("project_root") or "").expanduser() if install_snapshot.get("project_root") else None
    install_key = str(install_snapshot.get("install_key") or "")

    try:
        _invalidate_hermes_update_cache(repo_dir)
        proc = subprocess.Popen(
            [str(bin_path), "update"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "NO_COLOR": "1"},
            cwd=str(repo_dir) if repo_dir else None,
            bufsize=1,
        )
        _append_update_log(f"$ {bin_path} update")
        if proc.stdout is not None:
            for line in proc.stdout:
                _append_update_log(line)
        returncode = proc.wait()
        runtime = _runtime_snapshot()
        combined_log = runtime.get("log_text") or ""
        lowered = combined_log.lower()
        restore_conflict = (
            "restoring local changes hit conflicts" in lowered
            or "working tree reset to clean state" in lowered
        )
        _invalidate_hermes_update_cache(repo_dir)
        post_update = _build_hermes_update_payload(force_refresh=True)

        error_message = ""
        if returncode != 0:
            error_message = _first_output_line(combined_log, "Hermes update failed.")
        elif restore_conflict:
            error_message = (
                "Hermes updated, but restoring local changes hit conflicts. Review the update log and reapply the saved git stash if needed."
            )
        elif post_update.get("availability_status") == "update_available":
            behind = post_update.get("behind_commits")
            if isinstance(behind, int) and behind > 0:
                word = "commit" if behind == 1 else "commits"
                error_message = f"Update finished, but Hermes is still {behind} {word} behind the selected update source."

        if error_message:
            _set_update_runtime(
                status="update_failed",
                finished_at=_utc_now_z(),
                returncode=returncode,
                error=error_message,
                summary=error_message,
                install_key=install_key,
                installed_version_after=(post_update.get("installed_version") or {}).get("display") or "",
            )
            return

        _set_update_runtime(
            status="",
            finished_at=_utc_now_z(),
            returncode=returncode,
            error="",
            summary="Hermes update completed successfully.",
            install_key=install_key,
            installed_version_after=(post_update.get("installed_version") or {}).get("display") or "",
        )
    except Exception as exc:
        _set_update_runtime(
            status="update_failed",
            finished_at=_utc_now_z(),
            returncode=-1,
            error=str(exc),
            summary=str(exc),
            install_key=install_key,
        )


def _hermes_skill_install_failed(result: subprocess.CompletedProcess, combined_output: str) -> bool:
    lowered_output = str(combined_output or "").lower()
    if "already installed" in lowered_output:
        return False
    if result.returncode != 0:
        return True
    return any(marker in lowered_output for marker in SKILL_INSTALL_ERROR_MARKERS)


def _normalize_skill_rel_path(value: str | Path) -> str:
    text = str(value or "").strip().replace("\\", "/").strip("/")
    return text


def _skill_source_metadata_path(skill_dir: Path) -> Path:
    return skill_dir / SKILL_SOURCE_METADATA_FILENAME


def _parse_skill_source_reference(identifier: str) -> dict:
    text = str(identifier or "").strip()
    if not text:
        return {
            "identifier": "",
            "source_repo": "",
            "source_path": "",
        }

    url_match = re.match(
        r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?(?:/tree/(?P<ref>[^/]+)(?:/(?P<path>[^?#]+))?)?/?(?:[?#].*)?$",
        text,
        re.IGNORECASE,
    )
    if url_match:
        owner = str(url_match.group("owner") or "").strip()
        repo = str(url_match.group("repo") or "").strip()
        source_path = _normalize_skill_rel_path(url_match.group("path") or "")
        return {
            "identifier": text,
            "source_repo": f"{owner}/{repo}" if owner and repo else "",
            "source_path": source_path,
        }

    parts = [part for part in text.replace("\\", "/").split("/") if part]
    if len(parts) >= 2:
        return {
            "identifier": text,
            "source_repo": "/".join(parts[:2]),
            "source_path": "/".join(parts[2:]),
        }
    return {
        "identifier": text,
        "source_repo": "",
        "source_path": "",
    }


def _build_skill_source_record(
    identifier: str,
    *,
    install_mode: str,
    display: str = "",
    catalog_source: str = "",
) -> dict:
    parsed = _parse_skill_source_reference(identifier)
    source_repo = parsed.get("source_repo") or ""
    source_path = parsed.get("source_path") or ""
    identifier_text = parsed.get("identifier") or str(identifier or "").strip()
    if display:
        display_text = str(display).strip()
    elif install_mode == "github_repo" and source_repo:
        display_text = source_repo
    else:
        display_text = identifier_text or source_repo or "Local / Unknown"
    return {
        "display": display_text,
        "identifier": identifier_text,
        "source_repo": source_repo,
        "source_path": source_path,
        "catalog_source": str(catalog_source or "").strip(),
        "install_mode": str(install_mode or "").strip(),
        "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def _read_skill_source_metadata(skill_dir: Path) -> dict:
    meta_path = _skill_source_metadata_path(skill_dir)
    if not meta_path.exists():
        return {}
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    source_repo = _normalize_skill_rel_path(payload.get("source_repo") or "")
    source_path = _normalize_skill_rel_path(payload.get("source_path") or "")
    identifier = str(payload.get("identifier") or "").strip()
    display = str(payload.get("display") or "").strip() or identifier or source_repo or "Local / Unknown"
    return {
        "display": display,
        "identifier": identifier,
        "source_repo": source_repo,
        "source_path": source_path,
        "catalog_source": str(payload.get("catalog_source") or "").strip(),
        "install_mode": str(payload.get("install_mode") or "").strip(),
        "recorded_at": str(payload.get("recorded_at") or "").strip(),
        "tracked": True,
    }


def _write_skill_source_metadata(skill_dir: Path, metadata: dict) -> dict:
    skill_dir.mkdir(parents=True, exist_ok=True)
    normalized = _read_skill_source_metadata(skill_dir)
    normalized.update({
        "display": str(metadata.get("display") or normalized.get("display") or "").strip(),
        "identifier": str(metadata.get("identifier") or normalized.get("identifier") or "").strip(),
        "source_repo": _normalize_skill_rel_path(metadata.get("source_repo") or normalized.get("source_repo") or ""),
        "source_path": _normalize_skill_rel_path(metadata.get("source_path") or normalized.get("source_path") or ""),
        "catalog_source": str(metadata.get("catalog_source") or normalized.get("catalog_source") or "").strip(),
        "install_mode": str(metadata.get("install_mode") or normalized.get("install_mode") or "").strip(),
        "recorded_at": str(metadata.get("recorded_at") or normalized.get("recorded_at") or "").strip()
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    })
    if not normalized.get("display"):
        normalized["display"] = normalized.get("identifier") or normalized.get("source_repo") or "Local / Unknown"
    _skill_source_metadata_path(skill_dir).write_text(
        json.dumps(normalized, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    normalized["tracked"] = True
    return normalized


def _record_skill_install_source(
    rel_paths: list[str],
    *,
    identifier: str,
    install_mode: str,
    display: str = "",
    catalog_source: str = "",
) -> list[str]:
    record = _build_skill_source_record(
        identifier,
        install_mode=install_mode,
        display=display,
        catalog_source=catalog_source,
    )
    updated = []
    seen = set()
    for rel_path in rel_paths or []:
        normalized_rel = _normalize_skill_rel_path(rel_path)
        if not normalized_rel or normalized_rel in seen:
            continue
        seen.add(normalized_rel)
        skill_dir = SKILLS_DIR / normalized_rel
        if not (skill_dir / "SKILL.md").exists():
            continue
        _write_skill_source_metadata(skill_dir, record)
        updated.append(normalized_rel)
    return updated


def _match_skill_paths_for_identifier(identifier: str, skills: list[dict]) -> list[str]:
    parsed = _parse_skill_source_reference(identifier)
    terms = []
    identifier_text = str(parsed.get("identifier") or "").strip()
    source_path = str(parsed.get("source_path") or "").strip()
    if identifier_text:
        terms.append(identifier_text.lower())
    if source_path:
        terms.append(source_path.lower())
        terms.append(Path(source_path).name.lower())
    terms = [term for term in dict.fromkeys(terms) if term]
    if not terms:
        return []
    matches = []
    for skill in skills or []:
        if _skill_matches_terms(skill, tuple(terms)):
            rel_path = _normalize_skill_rel_path(skill.get("path") or "")
            if rel_path:
                matches.append(rel_path)
    return list(dict.fromkeys(matches))


def _parse_github_skill_install_identifier(identifier: str) -> dict | None:
    text = str(identifier or "").strip()
    if not text:
        return None

    url_match = re.match(
        r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?(?:/tree/(?P<ref>[^/]+)(?:/(?P<path>[^?#]+))?)?/?(?:[?#].*)?$",
        text,
        re.IGNORECASE,
    )
    if url_match:
        owner = str(url_match.group("owner") or "").strip()
        repo = str(url_match.group("repo") or "").strip()
        if owner and repo:
            return {
                "owner": owner,
                "repo": repo,
                "ref": str(url_match.group("ref") or "").strip(),
                "path": str(url_match.group("path") or "").strip("/"),
                "clone_url": f"https://github.com/{owner}/{repo}.git",
            }
        return None

    short_match = re.match(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$", text)
    if not short_match:
        return None
    owner = str(short_match.group("owner") or "").strip()
    repo = str(short_match.group("repo") or "").strip()
    if not owner or not repo:
        return None
    return {
        "owner": owner,
        "repo": repo,
        "ref": "",
        "path": "",
        "clone_url": f"https://github.com/{owner}/{repo}.git",
    }


def _discover_skill_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []

    discovered = []
    for current_root, dirs, files in os.walk(str(root)):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        if "SKILL.md" not in files:
            continue
        discovered.append(Path(current_root))
    return sorted(discovered)


def _install_skills_from_github_repo(identifier: str) -> dict | None:
    spec = _parse_github_skill_install_identifier(identifier)
    if not spec:
        return None
    if not shutil.which("git"):
        raise RuntimeError("git is required to install skills from GitHub repos")

    clone_cmd = ["git", "clone", "--depth", "1", "--quiet"]
    if spec.get("ref"):
        clone_cmd.extend(["--branch", str(spec["ref"]), "--single-branch"])
    clone_cmd.append(str(spec["clone_url"]))

    with tempfile.TemporaryDirectory(prefix="hermes-skill-import-") as tmpdir:
        clone_cmd.append(tmpdir)
        clone_result = subprocess.run(
            clone_cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if clone_result.returncode != 0:
            message = _combined_process_output(clone_result) or f"git clone exited with status {clone_result.returncode}"
            raise RuntimeError(message)

        repo_root = Path(tmpdir)
        search_root = repo_root / str(spec.get("path") or "") if spec.get("path") else repo_root
        if not search_root.exists():
            raise RuntimeError(f"GitHub path not found inside repo: {spec.get('path')}")

        skill_dirs = _discover_skill_dirs(search_root)
        if not skill_dirs:
            label = str(spec.get("path") or "").strip() or f"{spec['owner']}/{spec['repo']}"
            raise RuntimeError(f"No SKILL.md directories found in {label}")

        installed_paths = []
        skipped_paths = []
        source_display = f"{spec['owner']}/{spec['repo']}"
        for skill_dir in skill_dirs:
            rel_repo_path = skill_dir.relative_to(repo_root)
            dest_rel_path = rel_repo_path if rel_repo_path != Path(".") else Path(str(spec["repo"]))
            normalized_rel_path = _normalize_skill_rel_path(dest_rel_path)
            destination = SKILLS_DIR / normalized_rel_path
            if destination.exists():
                _record_skill_install_source(
                    [normalized_rel_path],
                    identifier=identifier,
                    install_mode="github_repo",
                    display=source_display,
                )
                skipped_paths.append(normalized_rel_path)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skill_dir, destination)
            _record_skill_install_source(
                [normalized_rel_path],
                identifier=identifier,
                install_mode="github_repo",
                display=source_display,
            )
            installed_paths.append(normalized_rel_path)

    return {
        "mode": "github_repo",
        "source": f"{spec['owner']}/{spec['repo']}",
        "requested_identifier": identifier,
        "installed_paths": installed_paths,
        "skipped_paths": skipped_paths,
    }


def _gateway_status() -> dict:
    """Run `hermes gateway status` and parse the result.

    Returns:
        {
            "running": bool,          # True if gateway is running
            "pid": int | None,       # PID from CLI output if present
            "status_text": str,       # Raw first line of output
            "raw": str                # Full stdout
        }
    """
    try:
        r = _run_hermes("gateway", "status", timeout=10)
        lines = r.stdout.strip().split("\n")
        status_line = lines[0] if lines else ""
        raw = r.stdout + r.stderr

        normalized_status = raw.casefold()
        running = (
            "gateway is running" in normalized_status
            or "gateway service is running" in normalized_status
            or "user gateway service is running" in normalized_status
        ) and "not running" not in normalized_status
        pid: int | None = None
        if running:
            # Extract PID from either Hermes CLI or systemd output.
            import re
            m = re.search(r"PID:\s*(\d+)", raw)
            if not m:
                m = re.search(r"Main PID:\s*(\d+)", raw)
            if m:
                pid = int(m.group(1))

        return {"running": running, "pid": pid, "status_text": status_line, "raw": raw}
    except Exception as e:
        return {"running": False, "pid": None, "status_text": str(e), "raw": ""}


def _find_gateway_pid() -> int | None:
    """Try to locate the Hermes gateway process ID.

    The gateway runs as a python/hermes subprocess. We look for processes
    whose cmdline contains 'hermes' followed by 'gateway' (as separate
    arguments), or the python wrapper running hermes_cli.main with gateway.
    """
    try:
        # Try to read /proc entries directly for reliability
        proc_dir = Path("/proc")
        for entry in proc_dir.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline_path = entry / "cmdline"
                cmdline = cmdline_path.read_text(errors="replace")
                # Normalize: split on \0 and join with spaces
                args = cmdline.replace("\x00", " ").split()
                if not args:
                    continue
                # Check patterns used by hermes_cli/gateway.py find_gateway_pids()
                combined = " ".join(args)
                patterns = [
                    "hermes_cli.main gateway",
                    "hermes_cli/main.py gateway",
                    "hermes gateway",
                    "gateway/run.py",
                ]
                if any(p in combined for p in patterns):
                    pid = int(entry.name)
                    if pid != os.getpid():
                        return pid
            except (PermissionError, FileNotFoundError, ProcessLookupError, ValueError):
                continue
    except Exception:
        pass
    return None


def _read_log_file(path: Path, lines: int = 200) -> str:
    """Read the last *lines* from a log file."""
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
        return "".join(all_lines[-lines:])
    except Exception:
        return ""


def _skill_frontmatter(skill_md: Path) -> dict:
    """Parse YAML frontmatter from a SKILL.md file."""
    try:
        text = skill_md.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return yaml.safe_load(parts[1]) or {}
    except Exception:
        pass
    return {}


def _http_error(msg: str, status: int = 500):
    request_id = _request_id_or_dash()
    logger.error("HTTP error %d request_id=%s: %s", status, request_id, msg)
    payload = {"ok": False, "error": msg}
    if request_id != "-":
        payload["request_id"] = request_id
    return jsonify(payload), status


register_system_routes(
    app,
    require_token=require_token,
    http_error=_http_error,
    gateway_status=lambda: _gateway_status(),
    find_gateway_pid=lambda: _find_gateway_pid(),
    run_hermes=lambda *args, **kwargs: _run_hermes(*args, **kwargs),
    selected_hermes_home=lambda: _selected_hermes_home(),
    selected_hermes_bin=lambda: _selected_hermes_bin(),
    selected_hermes_profile_name=lambda: _selected_hermes_profile_name(),
    build_hermes_update_payload=lambda *args, **kwargs: _build_hermes_update_payload(*args, **kwargs),
    runtime_snapshot=lambda: _runtime_snapshot(),
    invalidate_hermes_update_cache=lambda repo_dir=None: _invalidate_hermes_update_cache(repo_dir),
    set_update_runtime=lambda **updates: _set_update_runtime(**updates),
    utc_now_z=lambda: _utc_now_z(),
    run_hermes_update_worker=lambda install_snapshot: _run_hermes_update_worker(install_snapshot),
    threading_module=lambda: threading,
)


register_config_routes(
    app,
    require_token=require_token,
    http_error=_http_error,
    cfg_get=lambda section=None: cfg.get(section),
    cfg_get_raw=lambda section=None: cfg.get_raw(section),
    cfg_update=lambda section, data: cfg.update(section, data),
    cfg_load=lambda: cfg.load(),
    preserve_masked_secret_updates=lambda current, data, parent_key="": _preserve_masked_secret_updates(current, data, parent_key),
    selected_hermes_profile_payload=lambda: _selected_hermes_profile_payload(),
    set_selected_hermes_profile_name=lambda profile_name: _set_selected_hermes_profile_name(profile_name),
    profile_api_token_metadata=lambda profile_name: _profile_api_token_metadata(profile_name),
    normalize_hermes_profile_name=lambda profile_name: _normalize_hermes_profile_name(profile_name),
    available_hermes_profile_names=lambda: _available_hermes_profile_names(),
    profile_api_gateway_url=lambda profile_name=None: _profile_api_gateway_url(profile_name),
    api_url_port=lambda api_url: _api_url_port(api_url),
    repo_env_path=lambda: REPO_ENV_PATH,
    dotenv_values_fn=dotenv_values,
    api_token_repo_keys_for_port=lambda port: _api_token_repo_keys_for_port(port),
    unset_key_fn=unset_key,
    set_env_value=lambda env_path, key, value: _set_env_value(env_path, key, value),
)


register_env_routes(
    app,
    require_token=require_token,
    http_error=_http_error,
    selected_env_path=lambda: _selected_env_path(),
    dotenv_values_fn=dotenv_values,
    mask_value=lambda key, value: _mask_value(key, value),
    discover_skill_entries=lambda: _discover_skill_entries(),
    skill_env_var_presets=lambda skills: _skill_env_var_presets(skills),
    classify_env_key=lambda key: _classify_env_key(key),
    env_var_metadata=lambda key: _env_var_metadata(key),
    env_presets_by_group=lambda: _env_presets_by_group(),
    env_group_help=ENV_GROUP_HELP,
    set_env_value=lambda env_path, key, value: _set_env_value(env_path, key, value),
    unset_key_fn=unset_key,
)


# ===================================================================
# 10–14. Providers
# ===================================================================

register_provider_routes(
    app,
    require_token=require_token,
    rate_limit=rate_limit,
    http_error=_http_error,
    cfg_get_raw=lambda: cfg.get_raw(),
    cfg_set=lambda section, value: cfg.set(section, value),
    cfg_mask_secrets=lambda value: cfg.mask_secrets(value),
    normalized_model_config=lambda: _normalized_model_config(),
    custom_provider_profiles=lambda raw=None: _custom_provider_profiles(raw),
    role_linked_profile_name=lambda role, *, model_cfg=None, raw=None: _role_linked_profile_name(role, model_cfg=model_cfg, raw=raw),
    provider_usage_map=lambda raw=None, model_cfg=None: _provider_usage_map(raw=raw, model_cfg=model_cfg),
    provider_display_name=lambda provider_type: _provider_display_name(provider_type),
    provider_presets=PROVIDER_PRESETS,
    auxiliary_model_keys=AUXILIARY_MODEL_KEYS,
    normalize_provider_profile=lambda entry: _normalize_provider_profile(entry),
    preserve_masked_secret_updates=lambda current, update, parent_key="": _preserve_masked_secret_updates(current, update, parent_key),
    deep_merge=lambda current, data: ConfigManager.deep_merge(current, data),
    sync_linked_provider_roles=lambda name, profile: _sync_linked_provider_roles(name, profile),
    get_provider_profile=lambda name, raw=None: _get_provider_profile(name, raw),
    resolve_role_target=lambda role: _resolve_role_target(role),
    build_openai_api_url=lambda base_url, path: _build_openai_api_url(base_url, path),
    api_server_headers=lambda api_key, provider_type="": _api_server_headers(api_key, provider_type),
    summarize_upstream_error_detail=lambda body, fallback="": _summarize_upstream_error_detail(body, fallback),
    model_role_info=lambda role: _model_role_info(role),
    openrouter_discovery_models=lambda vision_only=False: _openrouter_discovery_models(vision_only=vision_only),
    normalize_provider_type=lambda provider_type: _normalize_provider_type(provider_type),
    openrouter_discovery_endpoints=lambda model_id: _openrouter_discovery_endpoints(model_id),
)


register_model_role_routes(
    app,
    require_token=require_token,
    http_error=_http_error,
    cfg_mask_secrets=lambda value: cfg.mask_secrets(value),
    cfg_update=lambda section, data: cfg.update(section, data),
    provider_usage_map=lambda raw=None, model_cfg=None: _provider_usage_map(raw=raw, model_cfg=model_cfg),
    available_provider_profiles=lambda: _available_provider_profiles(),
    provider_env_api_key=lambda provider_type: _provider_env_api_key(provider_type),
    provider_display_name=lambda provider_type: _provider_display_name(provider_type),
    model_role_info=lambda role: _model_role_info(role),
    model_role_labels=MODEL_ROLE_LABELS,
    profile_payload_for_role=lambda profile_name, model_name, routing_provider="": _profile_payload_for_role(profile_name, model_name, routing_provider),
    chat_backend_error_cls=ChatBackendError,
)


# ===================================================================
# 16–20. Agents / Personalities
# ===================================================================

register_agent_routes(
    app,
    require_token=require_token,
    http_error=_http_error,
    cfg_get_raw=lambda: cfg.get_raw(),
    cfg_set=lambda section, value: cfg.set(section, value),
    cfg_mask_secrets=lambda value: cfg.mask_secrets(value),
    agent_defaults=lambda raw: _agent_defaults(raw),
    agent_personality_entries=lambda raw: _agent_personality_entries(raw),
    personality_entry_for_api=lambda name, value: _personality_entry_for_api(name, value),
    normalize_personality_value=lambda value: _normalize_personality_value(value),
    deep_merge=lambda current, data: ConfigManager.deep_merge(current, data),
)


register_capability_routes(
    app,
    require_token=require_token,
    http_error=_http_error,
    capability_catalog=lambda: _capability_catalog(),
    preview_skill_capability=lambda draft: _preview_skill_capability(draft),
    apply_skill_capability=lambda draft, preview_token: _apply_skill_capability(draft, preview_token),
    preview_integration_capability=lambda draft: _preview_integration_capability(draft),
    apply_integration_capability=lambda draft, preview_token: _apply_integration_capability(draft, preview_token),
    preview_agent_preset_capability=lambda draft: _preview_agent_preset_capability(draft),
    apply_agent_preset_capability=lambda draft, preview_token: _apply_agent_preset_capability(draft, preview_token),
)


# ===================================================================
# 24–25. Skills
# ===================================================================

register_skill_routes(
    app,
    require_token=require_token,
    http_error=_http_error,
    discover_skill_entries=lambda: _discover_skill_entries(),
    skill_request_paths=lambda requested: _skill_request_paths(requested),
    skill_apply_action=lambda requested, action: _skill_apply_action(requested, action),
    safe_skill_rel_path=lambda entry: _safe_skill_rel_path(entry),
    normalize_skill_rel_path=lambda path: _normalize_skill_rel_path(path),
    run_hermes=lambda *args, timeout=30: _run_hermes(*args, timeout=timeout),
    combined_process_output=lambda result: _combined_process_output(result),
    hermes_skill_install_failed=lambda result, combined_output: _hermes_skill_install_failed(result, combined_output),
    install_skills_from_github_repo=lambda identifier: _install_skills_from_github_repo(identifier),
    record_skill_install_source=lambda skill_paths, identifier, install_mode, catalog_source="": _record_skill_install_source(skill_paths, identifier=identifier, install_mode=install_mode, catalog_source=catalog_source),
    match_skill_paths_for_identifier=lambda identifier, skills: _match_skill_paths_for_identifier(identifier, skills),
    starter_pack_skill_group=lambda item_id: _starter_pack_skill_group(item_id),
    starter_pack_install_candidates=lambda group: _starter_pack_install_candidates(group),
    chat_runtime_status=lambda: _chat_runtime_status(),
)


register_chat_routes(
    app,
    require_token=require_token,
    rate_limit=rate_limit,
    deps={
        "normalize_profile_name": lambda value: _normalize_hermes_profile_name(value),
        "available_profile_names": lambda: _available_hermes_profile_names(),
        "normalize_transport_preference": lambda value: _normalize_transport_preference(value),
        "get_or_create_chat_session": lambda session_id=None, profile_name=None: _get_or_create_chat_session(session_id, profile_name=profile_name),
        "selected_profile_name": lambda: _selected_hermes_profile_name(),
        "validated_transport_preference": lambda value: _validated_transport_preference(value),
        "ensure_folder_exists": lambda folder_id: _ensure_folder_exists(folder_id),
        "scoped_profile_override": lambda profile: _scoped_profile_override(profile),
        "plan_chat_request": lambda session, files: _plan_chat_request(session, files),
        "append_chat_segment": lambda session, profile, transport="": _append_chat_segment(session, profile, transport=transport),
        "segment_hermes_session_id": lambda segment: _segment_hermes_session_id(segment),
        "validate_attachment_selection": lambda files, image_support: _validate_attachment_selection(files, image_support),
        "register_chat_request": lambda request_id, session_id, transport, cancel_supported: _register_chat_request(request_id, session_id, transport=transport, cancel_supported=cancel_supported),
        "attachment_display_name": lambda path, display_names=None: _attachment_display_name(path, display_names),
        "build_attachment_refs": lambda files, display_names=None: _build_attachment_refs(files, display_names),
        "write_session": lambda session: _write_session(session),
        "messages_for_active_segment": lambda session: _messages_for_active_segment(session),
        "call_api_server": lambda session, messages, session_id, files=None, prefer_vision=False, file_display_names=None: _call_api_server(session, messages, session_id, files=files, prefer_vision=prefer_vision, file_display_names=file_display_names),
        "active_segment_has_image_history": lambda session: _active_segment_has_image_history(session),
        "image_extensions": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"},
        "vision_reanalysis_requested": lambda message, session: _vision_reanalysis_requested(message, session),
        "run_sidecar_vision_analysis": lambda session, message, files, user_message=None, file_display_names=None: _run_sidecar_vision_analysis(session, message, files, user_message=user_message, file_display_names=file_display_names),
        "compose_cli_prompt_with_sidecar": lambda session, message, files, sidecar_result=None, file_display_names=None: _compose_cli_prompt_with_sidecar(session, message, files, sidecar_result=sidecar_result, file_display_names=file_display_names),
        "call_hermes_prompt": lambda session, prompt, request_id=None: _call_hermes_prompt(session, prompt, request_id=request_id),
        "call_hermes_direct": lambda session, message, files=None, request_id=None, file_display_names=None: _call_hermes_direct(session, message, files, request_id=request_id, file_display_names=file_display_names),
        "clean_hermes_session_id": lambda value: _clean_hermes_session_id(value),
        "update_chat_request": lambda request_id, **fields: _update_chat_request(request_id, **fields),
        "rollback_failed_chat_turn": lambda session, session_id, user_msg: _rollback_failed_chat_turn(session, session_id, user_msg),
        "debug_trace_lines_for_chat": lambda request_id, hermes_session_id: _debug_trace_lines_for_chat(request_id, hermes_session_id),
        "remove_chat_request": lambda request_id: _remove_chat_request(request_id),
        "chat_session_meta": lambda session: _chat_session_meta(session),
        "cancel_chat_request": lambda request_id: _cancel_chat_request(request_id),
        "request_id_or_dash": lambda: _request_id_or_dash(),
        "save_upload_stream": lambda file_storage, destination: _save_upload_stream(file_storage, destination),
        "upload_folder": lambda: UPLOAD_FOLDER,
        "max_request_body_size": lambda: MAX_REQUEST_BODY_SIZE,
        "max_upload_size": lambda: MAX_UPLOAD_SIZE,
        "estimate_base64_decoded_size": lambda b64: _estimate_base64_decoded_size(b64),
        "load_all_sessions": lambda: _load_all_sessions(),
        "folder_summaries": lambda sessions=None: _folder_summaries(sessions),
        "parse_folder_update": lambda data, existing=None: _parse_folder_update(data, existing=existing),
        "folder_title_conflict": lambda title, exclude_folder_id=None: _folder_title_conflict(title, exclude_folder_id=exclude_folder_id),
        "legacy_folder_from_sessions": lambda title, sessions: _legacy_folder_from_sessions(title, sessions),
        "write_folder": lambda folder: _write_folder(folder),
        "folder_with_fallback": lambda folder_id, sessions=None: _folder_with_fallback(folder_id, sessions),
        "load_all_folders": lambda: _load_all_folders(),
        "resolve_folder_reference": lambda folder_ref, sessions=None, folders=None, include_legacy=True: _resolve_folder_reference(folder_ref, sessions=sessions, folders=folders, include_legacy=include_legacy),
        "delete_folder": lambda folder_id: _delete_folder(folder_id),
        "load_session": lambda session_id: _load_session(session_id),
        "merge_unique_strings": lambda *values: _merge_unique_strings(*values),
        "folder_workspace_roots_for_docs": lambda docs: _folder_workspace_roots_for_docs(docs),
        "parse_chat_context_update": lambda data: _parse_chat_context_update(data),
        "delete_session_from_disk": lambda session_id: _delete_session_from_disk(session_id),
        "trim_trailing_empty_chat_segments": lambda session: _trim_trailing_empty_chat_segments(session),
        "read_request_control": lambda request_id: _read_request_control(request_id),
        "filter_live_progress_lines": lambda lines: _filter_live_progress_lines(lines),
        "request_progress_lines": lambda request_id: _request_progress_lines(request_id),
        "check_api_server": lambda: _check_api_server(),
        "api_server_probe": lambda timeout=2: _api_server_probe(timeout=timeout),
        "image_attachment_support_status": lambda: _image_attachment_support_status(),
        "vision_configured": lambda: _vision_configured(),
        "resolve_api_target": lambda prefer_vision=False: _resolve_api_target(prefer_vision=prefer_vision),
        "chat_runtime_status": lambda: _chat_runtime_status(),
        "effective_hermes_api_url": lambda default_url: _effective_hermes_api_url(default_url),
        "default_hermes_api_url": lambda: DEFAULT_HERMES_API_URL,
        "chat_request_timeout": lambda: CHAT_REQUEST_TIMEOUT,
        "chat_server_timeout": lambda: CHAT_SERVER_TIMEOUT,
        "chat_persist_debug_trace": lambda: CHAT_PERSIST_DEBUG_TRACE,
        "chat_transport_api": CHAT_TRANSPORT_API,
        "chat_transport_cli": CHAT_TRANSPORT_CLI,
        "chat_continuity_hermes": CHAT_CONTINUITY_HERMES,
        "chat_continuity_local": CHAT_CONTINUITY_LOCAL,
        "chat_continuity_limited": CHAT_CONTINUITY_LIMITED,
        "chat_request_cancelled": ChatRequestCancelled,
        "chat_backend_error": ChatBackendError,
        "logger": logger,
        "request_output_path": lambda request_id: _request_output_path(request_id),
        "folder_source_dir": lambda: CHAT_FOLDER_SOURCE_DIR,
    },
)

register_operations_routes(
    app,
    require_token=require_token,
    http_error=lambda message, code=500: _http_error(message, code),
    deps={
        "integration_entries": lambda raw=None: _integration_entries(raw),
        "cfg_get_raw": lambda: cfg.get_raw(),
        "cfg_get": lambda key: cfg.get(key),
        "cfg_set": lambda key, value: cfg.set(key, value),
        "preserve_masked_secret_updates": lambda current, data: _preserve_masked_secret_updates(current, data),
        "integration_section_labels": INTEGRATION_SECTION_LABELS,
        "sessions_dir": lambda: SESSIONS_DIR,
        "log_file_keys": {
            "agent": ["logs/agent.log"],
            "gateway": ["logs/gateway.log", "gateway.log"],
            "errors": ["logs/errors.log"],
        },
        "resolve_log_path": lambda key: _resolve_log_path(key),
        "read_log_file": lambda path, lines=200: _read_log_file(path, lines),
        "selected_hermes_home": lambda: _selected_hermes_home(),
        "crontab_available": lambda: _crontab_available(),
        "load_cron_jobs": lambda: _load_cron_jobs(),
        "validate_cron_job_payload": lambda payload: _validate_cron_job_payload(payload),
        "write_cron_jobs": lambda jobs: _write_cron_jobs(jobs),
        "sync_cron_jobs_to_system": lambda jobs: _sync_cron_jobs_to_system(jobs),
        "chat_backend_error": ChatBackendError,
        "run_hermes": lambda *args, timeout=30: _run_hermes(*args, timeout=timeout),
        "selected_hermes_bin": lambda: _selected_hermes_bin(),
        "selected_gateway_log_path": lambda: _selected_gateway_log_path(),
        "gateway_status": lambda: _gateway_status(),
        "selected_hermes_home_for_service": lambda: _selected_hermes_home(),
        "time_module": lambda: time,
        "popen": lambda args, **kwargs: subprocess.Popen(args, **kwargs),
        "timeout_expired": subprocess.TimeoutExpired,
        "normalized_model_config": lambda: _normalized_model_config(),
        "env_path": lambda: ENV_PATH,
        "secret_patterns": _SECRET_PATTERNS,
    },
)

register_frontend_routes(
    app,
    index_path=lambda: APP_ROOT / "templates" / "index.html",
)


# Allowed log file keys mapped to candidate relative paths (security: no arbitrary paths)
# _selected_hermes_home() already resolves the active profile
# (HERMES_HOME for default, HERMES_PROFILES_DIR/<profile> for named profiles)
_LOG_FILE_KEYS: dict = {
    "agent":   ["logs/agent.log"],
    "gateway": ["logs/gateway.log", "gateway.log"],
    "errors":  ["logs/errors.log"],
}

def _resolve_log_path(key: str) -> "Path | None":
    """Return the first existing path for a known log key, or the primary candidate if none exist."""
    key = (key or "").strip().lower()
    if key not in _LOG_FILE_KEYS:
        return None
    home = _selected_hermes_home()
    for rel in _LOG_FILE_KEYS[key]:
        p = home / rel
        if p.exists():
            return p
    # Return primary candidate even if it doesn't exist yet
    return home / _LOG_FILE_KEYS[key][0]


# ===================================================================
# Chat endpoints (from V1 chat UI)
# ===================================================================

def _hermes_native_session_dirs() -> list[Path]:
    candidates = []
    request_root = CHAT_REQUEST_DIR.parents[1] if len(CHAT_REQUEST_DIR.parents) > 1 else None
    for path in (request_root, APP_ROOT, Path.home(), _selected_hermes_home()):
        if not path:
            continue
        resolved = Path(path)
        if resolved.exists() and resolved not in candidates:
            candidates.append(resolved)
    return candidates


def _snapshot_hermes_native_sessions() -> dict[str, tuple[int, int]]:
    snapshot = {}
    for base in _hermes_native_session_dirs():
        for path in base.glob("session_*.json"):
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _hermes_native_session_file_candidates(hermes_session_id: str | None = None) -> list[Path]:
    candidates = []
    session_id = _clean_hermes_session_id(hermes_session_id)
    for base in _hermes_native_session_dirs():
        if session_id:
            preferred = base / f"session_{session_id}.json"
            if preferred.exists() and preferred not in candidates:
                candidates.append(preferred)
        for path in sorted(base.glob("session_*.json")):
            if path not in candidates:
                candidates.append(path)
    return candidates


def _find_updated_hermes_native_session(
    before: dict[str, tuple[int, int]] | None,
    hermes_session_id: str | None = None,
) -> Path | None:
    before = before or {}
    session_id = _clean_hermes_session_id(hermes_session_id)
    changed = []
    preferred = []
    for path in _hermes_native_session_file_candidates(hermes_session_id):
        try:
            stat = path.stat()
        except OSError:
            continue
        current = (stat.st_mtime_ns, stat.st_size)
        previous = before.get(str(path))
        if previous != current:
            changed.append((current[0], path))
        if session_id and path.name == f"session_{session_id}.json":
            preferred.append((current[0], path))
    pool = preferred or changed
    if not pool:
        return None
    pool.sort(key=lambda item: item[0], reverse=True)
    return pool[0][1]


def _load_hermes_native_session_reply(path: Path) -> tuple[str | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    hermes_session_id = _clean_hermes_session_id(data.get("session_id") or path.stem.removeprefix("session_"))
    messages = data.get("messages")
    if not isinstance(messages, list):
        return None, hermes_session_id
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "assistant":
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip(), hermes_session_id
    return None, hermes_session_id


def _trace_summary_text(value, limit: int = 160) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _trace_summary_url(value, limit: int = 140) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        compact = f"{parsed.netloc}{parsed.path or ''}".strip()
        if parsed.query:
            compact = f"{compact}?{parsed.query}"
        return _trace_summary_text(compact or raw, limit)
    return _trace_summary_text(raw, limit)


def _parse_trace_json(value) -> dict | list | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _summarize_native_tool_call(tool_name: str, arguments) -> str:
    payload = arguments if isinstance(arguments, dict) else {}
    if tool_name in {"browser_navigate", "fetch_webpage"} and payload.get("url"):
        return _trace_summary_url(payload.get("url"), 140)
    if tool_name == "browser_click" and payload.get("ref"):
        return f"ref {payload.get('ref')}"
    if tool_name == "browser_type" and payload.get("text"):
        return _trace_summary_text(payload.get("text"), 120)
    if tool_name == "skill_view" and payload.get("name"):
        return _trace_summary_text(payload.get("name"), 120)
    if tool_name == "terminal" and payload.get("command"):
        return _trace_summary_text(payload.get("command"), 140)
    if payload.get("query"):
        return _trace_summary_text(payload.get("query"), 140)
    for key in ("path", "file_path", "name", "url"):
        if payload.get(key):
            return _trace_summary_text(payload.get(key), 140)
    return ""


def _summarize_native_tool_result(tool_name: str, content) -> str:
    parsed = _parse_trace_json(content)
    if isinstance(parsed, dict):
        if parsed.get("error"):
            return _trace_summary_text(parsed.get("error"), 140)
        if tool_name in {"browser_navigate", "fetch_webpage"} and parsed.get("url"):
            return _trace_summary_url(parsed.get("url"), 140)
        if tool_name == "skill_view" and parsed.get("name"):
            return _trace_summary_text(parsed.get("name"), 120)
        if tool_name == "terminal":
            exit_code = parsed.get("exit_code")
            if exit_code not in (None, ""):
                return f"exit_code={exit_code}"
        for key in ("message", "title", "path"):
            if parsed.get(key):
                return _trace_summary_text(parsed.get(key), 140)
    return _trace_summary_text(content, 140) if isinstance(content, str) else ""


def _native_trace_icon(tool_name: str) -> str:
    name = str(tool_name or "").strip().lower()
    if not name:
        return "⚙"
    if name.startswith("browser_"):
        return "🌐"
    if name.startswith("skill_"):
        return "📚"
    if name in {"terminal", "process"}:
        return "💻"
    if name.startswith("web_") or name in {"fetch_webpage", "search_files"}:
        return "🔍"
    if name.startswith("memory"):
        return "🧠"
    return "⚙"


def _format_native_trace_line(tool_name: str, summary: str = "") -> str:
    name = str(tool_name or "").strip()
    if not name:
        return ""
    icon = _native_trace_icon(name)
    if summary:
        return f"  ┊ {icon} {name} {summary}".rstrip()
    return f"  ┊ {icon} {name}"


def _load_hermes_native_session_trace_lines(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    messages = data.get("messages")
    if not isinstance(messages, list):
        return []

    lines = []
    tool_names_by_call_id = {}
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role == "assistant":
            tool_calls = item.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                tool_name = str(function.get("name") or "").strip()
                if not tool_name:
                    continue
                arguments = _parse_trace_json(function.get("arguments"))
                summary = _summarize_native_tool_call(tool_name, arguments)
                formatted = _format_native_trace_line(tool_name, summary)
                if formatted:
                    lines.append(formatted)
                tool_call_id = str(call.get("tool_call_id") or call.get("call_id") or call.get("id") or "").strip()
                if tool_call_id:
                    tool_names_by_call_id[tool_call_id] = tool_name
        elif role == "tool":
            tool_name = str(item.get("name") or "").strip()
            tool_call_id = str(item.get("tool_call_id") or "").strip()
            if not tool_name and tool_call_id:
                tool_name = tool_names_by_call_id.get(tool_call_id, "")
            if not tool_name:
                continue
            summary = _summarize_native_tool_result(tool_name, item.get("content"))
            if summary:
                formatted = _format_native_trace_line(tool_name, summary)
                if formatted:
                    lines.append(formatted)

    return _truncate_recent_lines(lines, limit=120)


def _looks_like_rich_cli_trace(lines: list[str]) -> bool:
    rows = [str(line or "") for line in (lines or []) if str(line or "").strip()]
    if not rows:
        return False
    tool_progress = sum(1 for line in rows if re.search(r"^\s*┊\s+[📚🌐💻🔍🧠⚙]", line))
    if tool_progress >= 2:
        return True
    tool_progress = sum(1 for line in rows if re.search(r"^\s*┊\s+", line))
    return tool_progress >= 3


def _debug_trace_lines_for_chat(request_id: str, hermes_session_id: str | None) -> list[str]:
    raw_lines = _request_progress_lines(request_id)
    if _looks_like_rich_cli_trace(raw_lines):
        return raw_lines

    native_path = _find_updated_hermes_native_session(None, hermes_session_id)
    if native_path:
        native_lines = _load_hermes_native_session_trace_lines(native_path)
        if native_lines:
            return native_lines
    return raw_lines


def _extract_cli_reply_after_session_marker(output: str) -> str:
    matches = list(re.finditer(r"(?mi)^session_id:\s*\S+\s*$", output))
    if not matches:
        return ""
    tail = output[matches[-1].end():]
    tail = re.sub(r"(?mi)^Resume this session with:\s*$", "", tail)
    tail = re.sub(r"(?mi)^\s*hermes\s+--resume\s+\S+\s*$", "", tail)
    tail = re.sub(r"(?mi)^Session:\s*\S+\s*$", "", tail)
    tail = re.sub(r"(?mi)^Duration:\s*.*$", "", tail)
    tail = re.sub(r"(?mi)^Messages:\s*.*$", "", tail)
    return tail.strip()

def _clean_cli_output(output: str) -> str:
    """Extract the final Hermes reply from verbose CLI output while discarding trace noise."""
    lines = output.split('\n')

    # Prefer the final Hermes response box when verbose CLI output is available.
    in_response_box = False
    response_lines = []
    latest_response = ""
    for line in lines:
        if re.match(r'^\s*╭.*Hermes.*╮\s*$', line):
            in_response_box = True
            response_lines = []
            continue
        if in_response_box and re.match(r'^\s*╰.*╯\s*$', line):
            normalized_lines = [part.strip() if part.strip() else '' for part in response_lines]
            while normalized_lines and not normalized_lines[0]:
                normalized_lines.pop(0)
            while normalized_lines and not normalized_lines[-1]:
                normalized_lines.pop()
            response = '\n'.join(normalized_lines).strip()
            if response:
                latest_response = response
            in_response_box = False
            continue
        if in_response_box:
            response_lines.append(line)

    if latest_response:
        return latest_response

    quiet_response = _extract_cli_reply_after_session_marker(output)
    if quiet_response:
        return quiet_response

    # Strip think blocks: matches <think>...</think> tags and everything between them
    output = re.sub(r'<think>.*?</think>', '', output, flags=re.DOTALL)
    lines = output.split('\n')
    clean = []
    skip = False
    for line in lines:
        if 'Hermes Agent v' in line or 'Available Tools' in line or 'Available Skills' in line:
            skip = True; continue
        if 'Query:' in line:
            skip = False; continue
        if 'Resume this session' in line or line.strip().startswith('hermes --resume'):
            skip = True; continue
        if skip:
            continue
        # Skip separator lines (box drawing chars including corners)
        if re.match(r'^[\u2500-\u257f\u2550-\u256f\u2800-\u28ff\u2580-\u259f\u2591-\u2593\u2b1b\u2b1c ]+$', line.strip()):
            continue
        # Skip lines that are box-drawing borders with text (e.g. ╭─── Health ───╮)
        stripped = line.strip()
        if any(c in stripped for c in '\u2500\u2501\u2502\u2503\u2504\u2505\u2506\u2507\u2508\u2509\u250a\u250b\u250c\u250d\u250e\u250f\u2510\u2511\u2512\u2513\u2514\u2515\u2516\u2517\u2518\u2519\u251a\u251b\u251c\u251d\u251e\u251f\u2520\u2521\u2522\u2523\u2524\u2525\u2526\u2527\u2528\u2529\u252a\u252b\u252c\u252d\u252e\u252f\u2530\u2531\u2532\u2533\u2534\u2535\u2536\u2537\u2538\u2539\u253a\u253b\u253c\u253d\u253e\u253f\u2540\u2541\u2542\u2543\u2544\u2545\u2546\u2547\u2548\u2549\u254a\u254b\u254c\u254d\u254e\u254f\u2550\u2551\u2552\u2553\u2554\u2555\u2556\u2557\u2558\u2559\u255a\u255b\u255c\u255d\u255e\u255f\u2560\u2561\u2562\u2563\u2564\u2565\u2566\u2567\u2568\u2569\u256a\u256b\u256c\u256d\u256e\u256f\u2570\u2571\u2572\u2573\u2574\u2575\u2576\u2577\u2578\u2579\u257a\u257b\u257c\u257d\u257e\u257f'):
            box_char_count = sum(1 for c in stripped if ord(c) in range(0x2500, 0x2580) or ord(c) in range(0x2550, 0x2570))
            if box_char_count > len(stripped) * 0.6:
                continue
        if re.match(r'^\s*(Session|Duration|Messages):', line):
            continue
        # Skip braille-heavy lines (tool checkboxes)
        braille = sum(1 for c in line if '\u2800' <= c <= '\u28ff')
        if braille > len(line) * 0.4:
            continue
        stripped = line.strip()
        if stripped:
            if '\u2502' in stripped:
                parts = stripped.split('\u2502')
                content = [p.strip() for p in parts if p.strip() and not re.match(r'^[\u2800-\u28ff\s]+$', p.strip())]
                if content:
                    text = ' '.join(content)
                    if any(s in text for s in ['Available Tools','Available Skills','/help','hermes --resume','Session:','Duration:','Messages:','Hermes Agent v','Nous Research','/home/']):
                        continue
                    clean.append(text)
            else:
                clean.append(stripped)
    result = '\n'.join(l for l in clean if len(l) > 1)
    return result or "(Empty response)"


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".webm", ".m4a", ".aac", ".ogg", ".flac"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".xml", ".html", ".htm", ".css", ".js", ".jsx", ".ts", ".tsx", ".py", ".sh", ".bash",
    ".zsh", ".ini", ".cfg", ".conf", ".toml", ".sql", ".env", ".gitignore", ".dockerfile",
}
TEXT_MIME_TYPES = {
    "application/json", "application/ld+json", "application/xml", "application/javascript",
    "application/x-javascript", "application/x-sh", "application/x-shellscript",
    "application/x-yaml", "application/yaml", "application/toml",
}


def _file_mime_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return (mime or "").lower()


def _is_text_attachment(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    mime = _file_mime_type(path)
    if mime.startswith("text/") or mime in TEXT_MIME_TYPES:
        return True
    try:
        sample = path.read_bytes()[:4096]
    except Exception:
        return False
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _validate_attachment_selection(files: list[Path], image_support: bool) -> list[str]:
    errors = []
    for f in files or []:
        suffix = f.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            if not image_support:
                errors.append(f"{f.name} is an image, but the configured Hermes vision sidecar is not ready")
            continue
        if suffix in AUDIO_EXTENSIONS:
            errors.append(f"{f.name} is audio, and audio uploads are not supported in Hermes chat")
            continue
        if _is_text_attachment(f):
            continue
        errors.append(f"{f.name} is a binary file type Hermes chat cannot read")
    return errors


def _attachment_display_name(path: Path, display_names: dict | None = None) -> str:
    return _attachment_display_name_impl(path, display_names=display_names)


def _summarize_attachments(files: list[Path], image_support: bool, display_names: dict | None = None) -> dict:
    text_blocks = []
    image_files = []
    unsupported = []
    for f in files or []:
        display_name = _attachment_display_name(f, display_names)
        suffix = f.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            if image_support:
                image_files.append(f)
            else:
                unsupported.append(f"{display_name} (image attachments require a ready Hermes vision sidecar)")
            continue
        if suffix in AUDIO_EXTENSIONS:
            unsupported.append(f"{display_name} (audio attachments are not supported in Hermes chat)")
            continue
        if _is_text_attachment(f):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                text_blocks.append(f"File: {display_name}\n```\n{content}\n```")
            except Exception:
                unsupported.append(f"{display_name} (could not read text content)")
            continue
        unsupported.append(f"{display_name} (binary attachments cannot be read as text in this chat mode)")
    return {
        "text_blocks": text_blocks,
        "image_files": image_files,
        "unsupported": unsupported,
    }


def _compose_message_with_attachments(
    message: str,
    files: list[Path],
    image_support: bool,
    display_names: dict | None = None,
) -> tuple[str, list[Path]]:
    summary = _summarize_attachments(files, image_support=image_support, display_names=display_names)
    sections = list(summary["text_blocks"])
    if message:
        sections.append(f"User message: {message}")
    if summary["unsupported"]:
        notes = "\n".join(f"- {note}" for note in summary["unsupported"])
        sections.append(f"Attachment notes:\n{notes}")
    if not sections:
        sections.append(message or "User attached files without an additional text message.")
    return "\n\n".join(sections), summary["image_files"]


def _session_has_image_history(session: dict) -> bool:
    for message in session.get("messages", []):
        for file_name in message.get("files", []) or []:
            if Path(file_name).suffix.lower() in IMAGE_EXTENSIONS:
                return True
    return False


def _messages_for_active_segment(session: dict) -> list[dict]:
    messages = session.get("messages") or []
    active_segment = _active_chat_segment(session) or {}
    start_message_index = int(active_segment.get("start_message_index") or 0)
    if start_message_index <= 0:
        return messages
    return messages[start_message_index:]


def _active_segment_has_image_history(session: dict) -> bool:
    for message in _messages_for_active_segment(session):
        for file_name in message.get("files", []) or []:
            if Path(file_name).suffix.lower() in IMAGE_EXTENSIONS:
                return True
    return False


def _build_attachment_refs(files: list[Path], display_names: dict | None = None) -> list[dict]:
    return _build_attachment_refs_impl(
        files,
        display_names=display_names,
        image_extensions=IMAGE_EXTENSIONS,
        audio_extensions=AUDIO_EXTENSIONS,
        is_text_attachment=_is_text_attachment,
        file_mime_type=_file_mime_type,
        attachment_display_name_fn=_attachment_display_name,
    )


def _latest_user_turn(session: dict) -> dict | None:
    for message in reversed(session.get("messages", [])):
        if isinstance(message, dict) and message.get("role") == "user":
            return message
    return None


def _latest_sidecar_asset_group(session: dict) -> list[dict]:
    asset_ids = set()
    for message in reversed(session.get("messages", [])):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        sidecar = message.get("sidecar_vision") or {}
        asset_ids = set(_clean_string_list(sidecar.get("asset_ids")))
        if asset_ids:
            break
    if not asset_ids:
        return []
    assets = []
    for asset in session.get("vision_assets", []) or []:
        if isinstance(asset, dict) and asset.get("id") in asset_ids:
            assets.append(copy.deepcopy(asset))
    return assets


def _latest_turn_used_sidecar_vision(session: dict) -> bool:
    latest_user = _latest_user_turn(session)
    return bool((latest_user or {}).get("sidecar_vision", {}).get("used"))


def _latest_turn_sidecar_asset_names(session: dict) -> list[str]:
    latest_user = _latest_user_turn(session)
    if not latest_user:
        return []
    sidecar = latest_user.get("sidecar_vision") or {}
    asset_names = []
    asset_map = {
        asset.get("id"): asset for asset in session.get("vision_assets", []) or []
        if isinstance(asset, dict) and asset.get("id")
    }
    for asset_id in _clean_string_list(sidecar.get("asset_ids")):
        asset = asset_map.get(asset_id) or {}
        label = str(asset.get("display_name") or "").strip()
        if label:
            asset_names.append(label)
    return asset_names


def _chat_session_meta(session: dict) -> dict:
    normalized = _normalize_chat_session(copy.deepcopy(session))
    context = _effective_session_context(normalized)
    active_segment = _active_chat_segment(normalized) or {}
    active_request = _active_request_for_session(normalized.get("id") or "")
    return {
        "profile": normalized.get("profile") or _selected_hermes_profile_name(),
        "active_segment_id": active_segment.get("id") or "",
        "active_segment_index": active_segment.get("index") or 1,
        "segments": copy.deepcopy(normalized.get("segments") or []),
        "transport_mode": normalized.get("transport_mode"),
        "transport_preference": normalized.get("transport_preference") or CHAT_TRANSPORT_AUTO,
        "transport_preference_label": _transport_preference_label(normalized.get("transport_preference")),
        "continuity_mode": normalized.get("continuity_mode"),
        "transport_notice": normalized.get("transport_notice") or "",
        "hermes_session_backed": normalized.get("continuity_mode") == CHAT_CONTINUITY_HERMES,
        "last_turn_used_sidecar_vision": _latest_turn_used_sidecar_vision(normalized),
        "last_turn_sidecar_asset_names": _latest_turn_sidecar_asset_names(normalized),
        "vision_asset_count": len(normalized.get("vision_assets") or []),
        "folder_id": context.get("folder_id") or "",
        "folder_title": context.get("folder_title") or "",
        "workspace_roots": context.get("workspace_roots") or [],
        "source_docs": context.get("source_docs") or [],
        "folder_workspace_roots": context.get("folder_workspace_roots") or [],
        "folder_source_docs": context.get("folder_source_docs") or [],
        "active_request_id": (active_request or {}).get("request_id") or "",
        "active_request_status": (active_request or {}).get("status") or "",
        "active_request_cancel_supported": bool((active_request or {}).get("cancel_supported")),
        "active_request_transport": (active_request or {}).get("transport") or "",
    }


def _format_chat_context_block(session: dict) -> str:
    context = _effective_session_context(session)
    folder_title = context.get("folder_title") or ""
    workspace_roots = context.get("workspace_roots") or []
    source_docs = context.get("source_docs") or []
    if not any((folder_title, workspace_roots, source_docs)):
        return ""

    sections = ["Chat workspace context:"]
    if folder_title:
        sections.append(f"Folder: {folder_title}")
    if workspace_roots:
        sections.append("Workspace roots:\n" + "\n".join(f"- {root}" for root in workspace_roots))
    if source_docs:
        sections.append("Pinned source docs:\n" + "\n".join(f"- {doc}" for doc in source_docs))

    doc_sections = []
    notes = []
    total_bytes = 0
    for source_doc in source_docs:
        path = Path(source_doc)
        if not path.exists():
            notes.append(f"{source_doc} (missing)")
            continue
        if not _is_text_attachment(path):
            notes.append(f"{source_doc} (not a readable text file)")
            continue
        try:
            raw_bytes = path.read_bytes()
        except Exception as exc:
            notes.append(f"{source_doc} (could not be read: {exc})")
            continue
        remaining = CHAT_CONTEXT_SOURCE_DOC_TOTAL_LIMIT - total_bytes
        if remaining <= 0:
            notes.append(f"{source_doc} (skipped because the source-doc context limit was reached)")
            continue
        snippet = raw_bytes[:min(CHAT_CONTEXT_SOURCE_DOC_LIMIT, remaining)]
        total_bytes += len(snippet)
        text = snippet.decode("utf-8", errors="replace")
        if len(snippet) < len(raw_bytes):
            notes.append(f"{source_doc} (truncated to {len(snippet)} bytes for chat context)")
        doc_sections.append(f"Source doc: {source_doc}\n```\n{text}\n```")

    sections.extend(doc_sections)
    if notes:
        sections.append("Source doc notes:\n" + "\n".join(f"- {note}" for note in notes))
    return "\n\n".join(sections)


def _compose_chat_turn_payload(
    session: dict,
    message: str,
    files: list[Path],
    image_support: bool,
    display_names: dict | None = None,
) -> tuple[str, list[Path]]:
    attachment_text, image_files = _compose_message_with_attachments(
        message,
        files,
        image_support=image_support,
        display_names=display_names,
    )
    context_block = _format_chat_context_block(session)
    sections = [section for section in (context_block, attachment_text) if section]
    return "\n\n".join(sections), image_files


def _integration_config_is_configured(value) -> bool:
    if isinstance(value, dict):
        if not value:
            return False
        return any(_integration_config_is_configured(item) for item in value.values())
    if isinstance(value, list):
        return any(_integration_config_is_configured(item) for item in value)
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return True


def _integration_entries(raw: dict | None = None) -> list[dict]:
    raw = raw if raw is not None else cfg.get_raw()
    integrations = []

    for key in INTEGRATION_SECTION_ORDER:
        if key not in raw:
            continue
        value = raw.get(key)
        if not isinstance(value, dict):
            continue
        integrations.append({
            "name": key,
            "label": INTEGRATION_SECTION_LABELS.get(key, key.title()),
            "kind": "integration",
            "config": cfg.mask_secrets(copy.deepcopy(value)),
            "configured": _integration_config_is_configured(value),
            "source": "top_level",
        })

    channels_cfg = raw.get("channels", {})
    toolsets = raw.get("platform_toolsets", {})
    if isinstance(channels_cfg, dict):
        for name, config_value in channels_cfg.items():
            if not isinstance(config_value, dict):
                continue
            integrations.append({
                "name": name,
                "label": name,
                "kind": "legacy_channel",
                "config": cfg.mask_secrets(copy.deepcopy(config_value)),
                "configured": _integration_config_is_configured(config_value),
                "enabled": bool(toolsets.get(name)),
                "source": "channels",
            })

    return integrations


def _discover_skill_entries() -> list[dict]:
    return _skill_setup_service.discover_skill_entries(
        skills_dir=SKILLS_DIR,
        os_module=os,
        path_class=Path,
        skill_frontmatter_fn=lambda path: _skill_frontmatter(path),
        read_skill_source_metadata_fn=lambda path: _read_skill_source_metadata(path),
        skill_setup_readiness_fn=lambda skill: _skill_setup_readiness(skill),
    )


def _configured_hook_keys(raw: dict | None = None) -> list[str]:
    return _skill_setup_service.configured_hook_keys(
        raw,
        cfg_get_raw=lambda: cfg.get_raw(),
        integration_config_is_configured_fn=lambda value: _integration_config_is_configured(value),
    )


def _skill_matches_terms(skill: dict, terms: tuple[str, ...]) -> bool:
    return _skill_runtime_service.skill_matches_terms(skill, terms)


def _joined_labels(values: list[str]) -> str:
    return _skill_runtime_service.joined_labels(values)


def _skill_absolute_path(skill: dict) -> Path | None:
    rel_path = _safe_skill_rel_path(skill.get("path") or "")
    if not rel_path:
        return None
    return SKILLS_DIR / rel_path


def _safe_skill_rel_path(value: str | Path) -> str:
    text = _normalize_skill_rel_path(value)
    if not text:
        return ""
    parts = []
    for part in PurePosixPath(text).parts:
        if part in ("", "."):
            continue
        if part == "..":
            return ""
        parts.append(part)
    return "/".join(parts)


def _skill_request_paths(requested: str | Path) -> dict:
    requested_rel = _safe_skill_rel_path(requested)
    if not requested_rel:
        return {}

    requested_parts = requested_rel.split("/")
    base_name = requested_parts[-1]
    while base_name.endswith(".disabled"):
        base_name = base_name[:-9]
    if not base_name:
        return {}

    base_rel = "/".join(requested_parts[:-1] + [base_name])
    base_path = SKILLS_DIR / base_rel
    disabled_rel = base_rel + ".disabled"
    disabled_path = SKILLS_DIR / disabled_rel

    variants = []
    seen = set()
    for rel in (requested_rel, base_rel, disabled_rel):
        if rel and rel not in seen:
            variants.append(SKILLS_DIR / rel)
            seen.add(rel)

    parent_dir = base_path.parent
    if parent_dir.exists():
        disabled_prefix = base_path.name + ".disabled"
        for sibling in sorted(parent_dir.iterdir(), key=lambda item: item.name):
            if not sibling.is_dir() or not sibling.name.startswith(disabled_prefix):
                continue
            sibling_rel = _safe_skill_rel_path(sibling.relative_to(SKILLS_DIR))
            if sibling_rel and sibling_rel not in seen:
                variants.append(sibling)
                seen.add(sibling_rel)

    return {
        "requested_rel": requested_rel,
        "base_rel": base_rel,
        "disabled_rel": disabled_rel,
        "base_path": base_path,
        "disabled_path": disabled_path,
        "variants": variants,
    }


def _replace_skill_dir(src: Path, dst: Path) -> None:
    if dst.exists() and dst != src:
        shutil.rmtree(dst)
    shutil.move(str(src), str(dst))


def _skill_apply_action(requested: str | Path, action: str) -> dict:
    info = _skill_request_paths(requested)
    if not info:
        return {"found": False, "error": "Skill path is required"}

    base_path = info["base_path"]
    disabled_path = info["disabled_path"]
    existing_variants = [path for path in info["variants"] if path.exists()]
    action_name = str(action or "").strip().lower()

    if action_name == "enable":
        if base_path.exists():
            return {"found": True, "changed": False, "enabled": True, "path": info["base_rel"]}
        candidate = next((path for path in existing_variants if path != base_path), None)
        if not candidate:
            return {"found": False, "error": f"Skill '{requested}' not found"}
        _replace_skill_dir(candidate, base_path)
        return {"found": True, "changed": True, "enabled": True, "path": info["base_rel"]}

    if action_name == "disable":
        if base_path.exists():
            _replace_skill_dir(base_path, disabled_path)
            return {"found": True, "changed": True, "enabled": False, "path": info["disabled_rel"]}
        candidate = next((path for path in existing_variants if path != base_path), None)
        if candidate:
            candidate_rel = _safe_skill_rel_path(candidate.relative_to(SKILLS_DIR))
            return {"found": True, "changed": False, "enabled": False, "path": candidate_rel}
        return {"found": False, "error": f"Skill '{requested}' not found"}

    if action_name == "remove":
        target = base_path if base_path.exists() else next(iter(existing_variants), None)
        if not target:
            return {"found": False, "error": f"Skill '{requested}' not found"}
        removed_rel = _safe_skill_rel_path(target.relative_to(SKILLS_DIR))
        shutil.rmtree(target)
        return {"found": True, "changed": True, "removed": True, "path": removed_rel}

    return {"found": False, "error": f"Unsupported action '{action}'"}


def _skill_wants_integration_setup(skill: dict, env_blockers: list[dict]) -> bool:
    return _skill_setup_service.skill_wants_integration_setup(skill, env_blockers)


def _skill_setup_details(skill: dict) -> dict:
    return _skill_setup_service.skill_setup_details(
        skill,
        normalize_capability_env_var_fn=lambda entry: _normalize_capability_env_var(entry),
        clean_string_list_fn=lambda value: _clean_string_list(value),
        normalize_capability_credential_file_fn=lambda entry: _normalize_capability_credential_file(entry),
        normalize_capability_required_command_fn=lambda entry: _normalize_capability_required_command(entry),
    )


def _skill_setup_readiness(skill: dict) -> dict:
    return _skill_setup_service.skill_setup_readiness(
        skill,
        skill_absolute_path_fn=lambda value: _skill_absolute_path(value),
        skill_setup_details_fn=lambda value: _skill_setup_details(value),
        path_class=Path,
        runtime_env_value_fn=lambda key, default="": _runtime_env_value(key, default),
        classify_env_key_fn=lambda key: _classify_env_key(key),
        shutil_module=shutil,
        skill_wants_integration_setup_fn=lambda skill_data, env_blockers: _skill_wants_integration_setup(skill_data, env_blockers),
    )


def _skill_env_var_presets(skills: list[dict] | None = None) -> dict[str, dict]:
    return _skill_setup_service.skill_env_var_presets(
        skills,
        discover_skill_entries_fn=lambda: _discover_skill_entries(),
        normalize_capability_env_var_fn=lambda entry: _normalize_capability_env_var(entry),
        env_var_metadata_fn=lambda key: _env_var_metadata(key),
    )


def _starter_pack_skill_group(item_id: str) -> dict | None:
    return _skill_runtime_service.starter_pack_skill_group(
        item_id,
        starter_pack_skill_groups=STARTER_PACK_SKILL_GROUPS,
    )


def _starter_pack_install_candidates(group: dict) -> list[dict]:
    return _skill_runtime_service.starter_pack_install_candidates(group)


def _starter_pack_candidate_matches_enabled_skill(candidate: dict, enabled_skills: list[dict]) -> bool:
    return _skill_runtime_service.starter_pack_candidate_matches_enabled_skill(
        candidate,
        enabled_skills,
        skill_matches_terms_fn=lambda skill, terms: _skill_matches_terms(skill, terms),
    )


def _starter_pack_item_from_group(group: dict, enabled_skills: list[dict]) -> dict:
    return _skill_runtime_service.starter_pack_item_from_group(
        group,
        enabled_skills,
        skill_matches_terms_fn=lambda skill, terms: _skill_matches_terms(skill, terms),
        starter_pack_install_candidates_fn=lambda group_data: _starter_pack_install_candidates(group_data),
        starter_pack_candidate_matches_enabled_skill_fn=lambda candidate, skills: _starter_pack_candidate_matches_enabled_skill(candidate, skills),
        safe_skill_rel_path_fn=lambda value: _safe_skill_rel_path(value),
        skill_setup_readiness_fn=lambda skill: _skill_setup_readiness(skill),
        joined_labels_fn=lambda values: _joined_labels(values),
    )


def _memory_runtime_status(raw: dict | None = None) -> dict:
    return _skill_runtime_service.memory_runtime_status(
        raw,
        cfg_get_raw=lambda: cfg.get_raw(),
        clean_string_list_fn=lambda value: _clean_string_list(value),
        runtime_env_source_fn=lambda key: _runtime_env_source(key),
    )


def _chat_runtime_status(raw: dict | None = None, *, skills: list[dict] | None = None) -> dict:
    return _skill_runtime_service.chat_runtime_status(
        raw,
        skills=skills,
        cfg_get_raw=lambda: cfg.get_raw(),
        discover_skill_entries_fn=lambda: _discover_skill_entries(),
        integration_entries_fn=lambda raw_value: _integration_entries(raw_value),
        configured_hook_keys_fn=lambda raw_value: _configured_hook_keys(raw_value),
        clean_string_list_fn=lambda value: _clean_string_list(value),
        memory_runtime_status_fn=lambda raw_value: _memory_runtime_status(raw_value),
        starter_pack_skill_groups=STARTER_PACK_SKILL_GROUPS,
        starter_pack_item_from_group_fn=lambda group, enabled_skills: _starter_pack_item_from_group(group, enabled_skills),
        joined_labels_fn=lambda values: _joined_labels(values),
    )


def _validated_transport_preference(value) -> tuple[str | None, str]:
    return _validated_transport_preference_impl(
        value,
        normalize_transport_preference=_normalize_transport_preference,
        chat_runtime_status=_chat_runtime_status,
        check_api_server=_check_api_server,
        chat_transport_api=CHAT_TRANSPORT_API,
        chat_transport_cli=CHAT_TRANSPORT_CLI,
    )


def _vision_reanalysis_requested(message: str, session: dict) -> bool:
    if not message or not _latest_sidecar_asset_group(session):
        return False
    return bool(VISION_REFERENCE_HINT_RE.search(message))


def _vision_asset_disk_path(asset: dict) -> Path | None:
    stored_as = secure_filename(str(asset.get("stored_as") or "").strip())
    if not stored_as:
        return None
    path = UPLOAD_FOLDER / stored_as
    return path if path.exists() else None


def _strip_json_fence(text: str) -> str:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _coerce_sidecar_string_list(value) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                decoded = json.loads(text)
            except Exception:
                decoded = None
            if isinstance(decoded, list):
                items = decoded
            else:
                items = [part.strip() for part in re.split(r"(?:\r?\n|;\s*)", text) if part.strip()]
        else:
            items = [part.strip() for part in re.split(r"(?:\r?\n|;\s*)", text) if part.strip()]
            if not items:
                items = [text]
    else:
        return []
    normalized = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        text = re.sub(r"^[\-\*\u2022]+\s*", "", text)
        text = re.sub(r"^\d+[\.\)]\s*", "", text)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _find_json_object_candidates(text: str) -> list[str]:
    raw = str(text or "")
    candidates = []
    seen = set()

    def add(candidate: str):
        snippet = str(candidate or "").strip()
        if not snippet or snippet in seen:
            return
        seen.add(snippet)
        candidates.append(snippet)

    add(_strip_json_fence(raw))
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE):
        add(match.group(1))

    depth = 0
    start = None
    in_string = False
    escape = False
    for idx, ch in enumerate(raw):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                add(raw[start:idx + 1])
                start = None

    candidates.sort(key=len, reverse=True)
    return candidates


def _looks_like_sidecar_payload(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = set(payload.keys())
    return bool({"overall_summary", "images", "follow_up_hints", "visible_text", "details"} & keys)


def _extract_sidecar_json_payload(raw_text: str) -> dict | None:
    for candidate in _find_json_object_candidates(raw_text):
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if _looks_like_sidecar_payload(payload):
            return payload
        if isinstance(payload, dict):
            for value in payload.values():
                if _looks_like_sidecar_payload(value):
                    return value
            return payload
    return None


def _parse_sidecar_payload(raw_text: str, image_labels: list[str]) -> dict:
    raw_text = str(raw_text or "").strip()
    fallback = {
        "overall_summary": raw_text,
        "images": [],
        "follow_up_hints": [],
        "raw_text": raw_text,
    }
    if not raw_text:
        return fallback
    payload = _extract_sidecar_json_payload(raw_text)
    if not payload:
        return fallback
    images = payload.get("images")
    normalized_images = []
    overall_summary = str(payload.get("overall_summary") or "").strip()
    if isinstance(images, list):
        for idx, item in enumerate(images):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                label = image_labels[idx] if idx < len(image_labels) else f"Image {idx + 1}"
            summary = str(item.get("summary") or "").strip()
            if not summary:
                summary = overall_summary
            normalized_images.append({
                "label": label,
                "summary": summary,
                "visible_text": _coerce_sidecar_string_list(item.get("visible_text")),
                "details": _coerce_sidecar_string_list(item.get("details")),
                "follow_up_hints": _coerce_sidecar_string_list(item.get("follow_up_hints")),
            })
    if not overall_summary and normalized_images:
        overall_summary = normalized_images[0].get("summary") or ""
    if image_labels and not normalized_images:
        top_level_visible_text = _coerce_sidecar_string_list(payload.get("visible_text"))
        top_level_details = _coerce_sidecar_string_list(payload.get("details"))
        top_level_hints = _coerce_sidecar_string_list(payload.get("follow_up_hints"))
        for idx, label in enumerate(image_labels):
            normalized_images.append({
                "label": label,
                "summary": overall_summary if idx == 0 else "",
                "visible_text": top_level_visible_text if idx == 0 else [],
                "details": top_level_details if idx == 0 else [],
                "follow_up_hints": top_level_hints if idx == 0 else [],
            })
    return {
        "overall_summary": overall_summary,
        "images": normalized_images,
        "follow_up_hints": _coerce_sidecar_string_list(payload.get("follow_up_hints")),
        "raw_text": raw_text,
    }


def _format_sidecar_context_block(sidecar_result: dict) -> str:
    if not sidecar_result:
        return ""
    lines = ["Vision sidecar analysis:"]
    if sidecar_result.get("reanalysis"):
        lines.append("This analysis refreshes an earlier screenshot because the user referred back to it.")
    overall_summary = str(sidecar_result.get("overall_summary") or "").strip()
    if overall_summary:
        lines.append(f"Overall summary: {overall_summary}")
    image_results = sidecar_result.get("images") or []
    for idx, item in enumerate(image_results, start=1):
        label = str(item.get("label") or f"Image {idx}").strip()
        asset_id = str(item.get("asset_id") or "").strip()
        lines.append(f"{label}{f' [{asset_id}]' if asset_id else ''}:")
        summary = str(item.get("summary") or "").strip()
        if summary:
            lines.append(f"- Summary: {summary}")
        visible_text = [str(value).strip() for value in (item.get("visible_text") or []) if str(value).strip()]
        if visible_text:
            lines.append("- Visible text:")
            lines.extend(f"  - {value}" for value in visible_text)
        details = [str(value).strip() for value in (item.get("details") or []) if str(value).strip()]
        if details:
            lines.append("- Details:")
            lines.extend(f"  - {value}" for value in details)
        follow_up_hints = [str(value).strip() for value in (item.get("follow_up_hints") or []) if str(value).strip()]
        if follow_up_hints:
            lines.append("- Follow-up hints:")
            lines.extend(f"  - {value}" for value in follow_up_hints)
    return "\n".join(lines)


def _update_session_vision_assets(
    session: dict,
    image_files: list[Path],
    parsed_payload: dict,
    *,
    source_message_index: int,
    source_message_timestamp: str,
    focus_message: str,
    target: dict,
) -> list[str]:
    return _update_session_vision_assets_impl(
        session,
        image_files,
        parsed_payload,
        source_message_index=source_message_index,
        source_message_timestamp=source_message_timestamp,
        focus_message=focus_message,
        target=target,
        file_mime_type=_file_mime_type,
    )


def _chat_backend_error_detail(exc: Exception) -> str:
    message = str(exc or "").strip()
    message = re.sub(r"^API server returned HTTP \d+:\s*", "", message)
    message = re.sub(r"^API server error:\s*", "", message)
    return message.strip()


def _chat_backend_error_is_rate_limited(exc: Exception) -> bool:
    detail = _chat_backend_error_detail(exc).lower()
    return (
        "rate limit" in detail
        or "rate-limit" in detail
        or "rate limited" in detail
        or "too many requests" in detail
        or "http 429" in str(exc).lower()
    )


def _chat_backend_error_is_retryable(exc: Exception) -> bool:
    status_code = int(getattr(exc, "status_code", 502) or 502)
    if status_code >= 500 or _chat_backend_error_is_rate_limited(exc):
        return True
    detail = _chat_backend_error_detail(exc).lower()
    retryable_terms = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "service unavailable",
        "unreachable",
        "overloaded",
        "connection refused",
        "connection reset",
        "upstream request failed",
    )
    return any(term in detail for term in retryable_terms)


def _targets_equivalent(left: dict | None, right: dict | None) -> bool:
    left = left or {}
    right = right or {}
    return (
        str(left.get("provider") or "").strip().lower(),
        str(left.get("base_url") or "").strip().rstrip("/"),
        str(left.get("model") or "").strip(),
        str(left.get("routing_provider") or "").strip(),
    ) == (
        str(right.get("provider") or "").strip().lower(),
        str(right.get("base_url") or "").strip().rstrip("/"),
        str(right.get("model") or "").strip(),
        str(right.get("routing_provider") or "").strip(),
    )


def _chat_completion_request(target: dict, messages: list[dict]) -> str:
    import socket
    import urllib.error
    import urllib.request

    payload = {"model": target["model"] or "hermes-agent", "messages": messages, "stream": False}
    api_url = str(target.get("base_url") or "").strip() or _effective_hermes_api_url(DEFAULT_HERMES_API_URL)
    provider_type = str(target.get("provider") or "").strip().lower()
    routing_provider = str(target.get("routing_provider") or "").strip()
    if provider_type == "openrouter" and routing_provider:
        payload["provider"] = {
            "order": [routing_provider],
            "allow_fallbacks": True,
        }
    headers = _api_server_headers(target.get("api_key"), target.get("provider"), target)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        _build_openai_api_url(api_url, "chat/completions"),
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=CHAT_REQUEST_TIMEOUT) as resp:
            result = json.loads(resp.read().decode())
            if isinstance(result, dict) and result.get("error"):
                error_payload = result.get("error") or {}
                if isinstance(error_payload, dict):
                    error_message = error_payload.get("message") or error_payload.get("code") or "API server returned an error"
                else:
                    error_message = str(error_payload)
                raise ChatBackendError(f"API server error: {error_message}")
            choices = result.get("choices") or []
            if not choices:
                raise ChatBackendError("API server returned no choices")
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    item.get("text", "") for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ).strip()
            if not isinstance(content, str) or not content.strip():
                raise ChatBackendError("API server returned an empty message")
            return content
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        detail = _summarize_upstream_error_detail(body, str(exc.reason or "upstream request failed"))
        raise ChatBackendError(f"API server returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        reason_text = str(reason)
        if isinstance(reason, TimeoutError) or "timed out" in reason_text.lower():
            raise ChatRequestTimeout(f"API server did not respond within {CHAT_REQUEST_TIMEOUT} seconds") from exc
        raise ChatBackendError(f"API server is unreachable: {reason_text}") from exc
    except socket.timeout as exc:
        raise ChatRequestTimeout(f"API server did not respond within {CHAT_REQUEST_TIMEOUT} seconds") from exc


def _run_sidecar_vision_analysis(
    session: dict,
    message: str,
    files: list[Path],
    *,
    user_message: dict,
    file_display_names: dict | None = None,
) -> dict:
    image_files = [path for path in files or [] if path.suffix.lower() in IMAGE_EXTENSIONS]
    reanalysis = False
    if not image_files and _vision_reanalysis_requested(message, session):
        image_files = [path for path in (
            _vision_asset_disk_path(asset) for asset in _latest_sidecar_asset_group(session)
        ) if path is not None]
        reanalysis = bool(image_files)
    if not image_files:
        return {}

    import base64

    image_labels = [
        _attachment_display_name(path, file_display_names) if path in (files or []) else (path.name if path else "Image")
        for path in image_files
    ]
    user_text = message.strip() or "User attached screenshots without extra text."
    if reanalysis:
        analysis_goal = (
            "The user is asking a follow-up about an earlier screenshot. Refresh the visual analysis with extra focus on "
            f"this question: {user_text}"
        )
    else:
        analysis_goal = f"The user attached new screenshots. Focus on what matters for this request: {user_text}"
    content = [{
        "type": "text",
        "text": (
            "You are a sidecar vision interpreter for Hermes CLI. Analyze the images and return JSON only with this schema: "
            "{\"overall_summary\":\"...\",\"follow_up_hints\":[\"...\"],\"images\":["
            "{\"label\":\"...\",\"summary\":\"...\",\"visible_text\":[\"...\"],\"details\":[\"...\"],\"follow_up_hints\":[\"...\"]}"
            "]}. Keep each list concise and preserve exact visible text when it matters.\n"
            f"{analysis_goal}"
        ),
    }]
    for idx, image_file in enumerate(image_files, start=1):
        label = image_labels[idx - 1] if idx - 1 < len(image_labels) else image_file.name
        try:
            with image_file.open("rb") as handle:
                b64 = base64.b64encode(handle.read()).decode("utf-8")
        except Exception as exc:
            raise ChatBackendError(f"Vision sidecar could not read {image_file.name}: {exc}") from exc
        ext = image_file.suffix.lower().replace(".", "") or "png"
        content.append({"type": "text", "text": f"Image {idx}: {label}"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}})

    target = _resolve_api_target(prefer_vision=True)
    try:
        raw_text = _chat_completion_request(target, [{"role": "user", "content": content}])
    except ChatBackendError as exc:
        model_id = str(target.get("model") or "the configured vision model").strip()
        detail = _chat_backend_error_detail(exc) or "upstream request failed"
        if _chat_backend_error_is_rate_limited(exc):
            raise ChatBackendError(
                f"Vision sidecar is temporarily rate-limited for {model_id}. Retry shortly or switch the vision model/provider in Providers. Details: {detail}",
                status_code=503,
            ) from exc
        raise ChatBackendError(
            f"Vision sidecar failed for {model_id}. Details: {detail}",
            status_code=getattr(exc, "status_code", 502),
        ) from exc
    parsed_payload = _parse_sidecar_payload(raw_text, image_labels)
    asset_ids = _update_session_vision_assets(
        session,
        image_files,
        parsed_payload,
        source_message_index=len(session.get("messages", [])) - 1,
        source_message_timestamp=str(user_message.get("timestamp") or ""),
        focus_message=user_text,
        target=target,
    )
    summary = parsed_payload.get("overall_summary") or parsed_payload.get("raw_text") or ""
    user_message["sidecar_vision"] = {
        "used": True,
        "status": "ok",
        "asset_ids": asset_ids,
        "summary": str(summary).strip(),
        "analysis_mode": "reanalysis" if reanalysis else "sidecar",
        "reanalysis": reanalysis,
    }
    return {
        "overall_summary": parsed_payload.get("overall_summary") or parsed_payload.get("raw_text") or "",
        "images": parsed_payload.get("images") or [],
        "follow_up_hints": parsed_payload.get("follow_up_hints") or [],
        "raw_text": parsed_payload.get("raw_text") or raw_text.strip(),
        "asset_ids": asset_ids,
        "reanalysis": reanalysis,
        "analysis_mode": "reanalysis" if reanalysis else "sidecar",
    }


def _compose_cli_prompt_with_sidecar(
    session: dict,
    message: str,
    files: list[Path],
    *,
    sidecar_result: dict | None = None,
    file_display_names: dict | None = None,
) -> str:
    non_image_files = [path for path in files or [] if path.suffix.lower() not in IMAGE_EXTENSIONS]
    prompt, _ = _compose_chat_turn_payload(
        session,
        message,
        non_image_files,
        image_support=False,
        display_names=file_display_names,
    )
    sidecar_block = _format_sidecar_context_block(sidecar_result or {})
    sections = [section for section in (prompt, sidecar_block) if section]
    return "\n\n".join(sections)


def _plan_chat_request(session: dict, files: list[Path]) -> dict:
    return _plan_chat_request_impl(
        copy.deepcopy(session),
        files,
        normalize_chat_session=_normalize_chat_session,
        normalize_transport_preference=_normalize_transport_preference,
        image_extensions=IMAGE_EXTENSIONS,
        image_attachment_support_status=_image_attachment_support_status,
        check_api_server=_check_api_server,
        chat_runtime_status=_chat_runtime_status,
        chat_transport_api=CHAT_TRANSPORT_API,
        chat_transport_cli=CHAT_TRANSPORT_CLI,
    )


def _parse_hermes_chat_result(output: str) -> tuple[str, str | None]:
    session_match = re.search(r"(?mi)^session_id:\s*(\S+)\s*$", output)
    resume_match = re.search(r"(?mi)^\s*hermes\s+--resume\s+(\S+)\s*$", output)
    summary_match = re.search(r"(?mi)^Session:\s*(\S+)\s*$", output)
    hermes_session_id = None
    for match in (session_match, resume_match, summary_match):
        if match and match.group(1):
            hermes_session_id = match.group(1)
            break
    cleaned = output
    cleaned = re.sub(r"(?mi)^Resume this session with:\s*$", "", cleaned)
    cleaned = re.sub(r"(?mi)^\s*hermes\s+--resume\s+\S+\s*$", "", cleaned)
    cleaned = re.sub(r"(?m)^↻ Resumed session .*$", "", cleaned)
    cleaned = re.sub(r"(?m)^Resumed session .*$", "", cleaned)
    return _clean_cli_output(cleaned), hermes_session_id


def _call_hermes_prompt(
    session: dict,
    prompt: str,
    *,
    request_id: str | None = None,
) -> tuple[str, str | None]:
    """Call Hermes CLI subprocess with an already-assembled text prompt."""
    return _call_hermes_prompt_impl(
        session,
        prompt,
        request_id=request_id,
        read_request_control=_read_request_control,
        update_chat_request=_update_chat_request,
        chat_request_cancelled=ChatRequestCancelled,
        snapshot_hermes_native_sessions=_snapshot_hermes_native_sessions,
        selected_hermes_bin=_selected_hermes_bin,
        selected_hermes_home=_selected_hermes_home,
        request_output_path=_request_output_path,
        path_home=Path.home,
        terminate_chat_process=_terminate_chat_process,
        chat_cancel_grace_seconds=CHAT_CANCEL_GRACE_SECONDS,
        chat_cancel_poll_interval=CHAT_CANCEL_POLL_INTERVAL,
        chat_request_timeout=CHAT_REQUEST_TIMEOUT,
        parse_hermes_chat_result=_parse_hermes_chat_result,
        find_updated_hermes_native_session=_find_updated_hermes_native_session,
        load_hermes_native_session_reply=_load_hermes_native_session_reply,
        chat_request_timeout_error=ChatRequestTimeout,
        chat_backend_error=ChatBackendError,
        signal_module=signal,
        os_module=os,
    )


def _call_hermes_direct(
    session: dict,
    message: str,
    files: list = None,
    request_id: str | None = None,
    file_display_names: dict | None = None,
) -> tuple[str, str | None]:
    """Call Hermes via CLI subprocess (fallback when API server is unavailable)."""
    return _call_hermes_direct_impl(
        session,
        message,
        files=files,
        request_id=request_id,
        file_display_names=file_display_names,
        compose_chat_turn_payload=_compose_chat_turn_payload,
        call_hermes_prompt=_call_hermes_prompt,
    )


def _call_api_server(
    session: dict,
    messages: list,
    session_id: str,
    files: list = None,
    prefer_vision: bool = False,
    file_display_names: dict | None = None,
) -> str:
    """Call Hermes via its OpenAI-compatible API server. Handles image files as base64."""
    return _call_api_server_impl(
        session,
        messages,
        session_id,
        files=files,
        prefer_vision=prefer_vision,
        file_display_names=file_display_names,
        compose_chat_turn_payload=_compose_chat_turn_payload,
        resolve_api_target=_resolve_api_target,
        chat_completion_request=_chat_completion_request,
        chat_backend_error=ChatBackendError,
        chat_backend_error_is_retryable=_chat_backend_error_is_retryable,
        resolve_fallback_api_target=_resolve_fallback_api_target,
        model_role_enabled=_model_role_enabled,
        targets_equivalent=_targets_equivalent,
        chat_backend_error_detail=_chat_backend_error_detail,
        image_extensions=IMAGE_EXTENSIONS,
    )


def _provider_env_api_key(provider: str | None) -> str:
    provider_name = _normalize_provider_type(provider or "")
    env_key = PROVIDER_ENV_KEY_MAP.get(provider_name)
    return _runtime_env_value(env_key, "") if env_key else ""


def _resolved_target_api_key(target: dict | None) -> str:
    target = target or {}
    explicit_api_key = _resolve_runtime_template((target.get("api_key") or "").strip()).strip()
    if explicit_api_key:
        return explicit_api_key
    provider_api_key = _provider_env_api_key(target.get("provider"))
    if provider_api_key:
        return provider_api_key
    target_port = _api_url_port(target.get("base_url") or _effective_hermes_api_url(DEFAULT_HERMES_API_URL))
    repo_env = _repo_env_values()
    return (
        next((str(repo_env.get(key) or "").strip() for key in _api_token_repo_keys_for_port(target_port) if str(repo_env.get(key) or "").strip()), "")
        or str(os.environ.get("HERMES_API_KEY") or "").strip()
        or str(os.environ.get("HERMES_API_TOKEN") or "").strip()
        or str(os.environ.get("API_SERVER_KEY") or "").strip()
        or str(os.environ.get("API_SERVER_TOKEN") or "").strip()
    )


def _api_server_headers(api_key: str | None = None, provider: str | None = None, target: dict | None = None) -> dict:
    headers = {}
    resolved_api_key = (api_key or "").strip() if api_key is not None else ""
    if not resolved_api_key and provider:
        resolved_api_key = _provider_env_api_key(provider)
    if not resolved_api_key:
        resolved_api_key = _resolved_target_api_key(target)
    if resolved_api_key:
        headers["Authorization"] = f"Bearer {resolved_api_key}"
    return headers


def _set_env_value(path: Path, key: str, value: str) -> None:
    """Write dotenv entries without forcing single-quoted values."""
    set_key(str(path), key, value, quote_mode="never")


def _resolve_api_target(prefer_vision: bool = False) -> dict:
    return _resolve_role_target("vision" if prefer_vision else "primary")


def _resolve_fallback_api_target() -> dict:
    return _resolve_role_target("fallback")


def _api_target_missing_credentials(target: dict | None) -> bool:
    import urllib.parse

    target = target or {}
    base_url = str(target.get("base_url") or "").strip()
    if not base_url:
        return False
    parsed = urllib.parse.urlparse(base_url)
    hostname = (parsed.hostname or "").strip().lower()
    if hostname in {"", "localhost", "127.0.0.1", "::1"}:
        return False
    return not bool(_resolved_target_api_key(target))


def _openrouter_model_supports_images(target: dict, timeout: int = 3) -> tuple[bool | None, str]:
    import urllib.request

    if (target.get("provider") or "").strip().lower() != "openrouter":
        return None, ""
    model_id = (target.get("model") or "").strip()
    if not model_id:
        return None, ""

    req = urllib.request.Request(
        _build_openai_api_url(target.get("base_url") or "", "models"),
        headers=_api_server_headers(target.get("api_key"), target.get("provider")),
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        return None, f"Could not verify image support for {model_id}: {exc}"

    for item in payload.get("data", []) or []:
        if item.get("id") != model_id:
            continue
        architecture = item.get("architecture") or {}
        input_modalities = architecture.get("input_modalities") or []
        if "image" in input_modalities:
            return True, ""
        modality = str(architecture.get("modality") or "")
        if "image" in modality.lower():
            return True, ""
        return False, f"The configured OpenRouter model {model_id} does not advertise image input support"

    return None, f"Could not find model metadata for {model_id} on OpenRouter"


def _openrouter_fetch_json(path: str, timeout: int = 10) -> dict:
    import urllib.request

    req = urllib.request.Request(
        _build_openai_api_url("https://openrouter.ai/api/v1", path),
        headers={"User-Agent": "hermes-web-ui"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _openrouter_discovery_models(*, vision_only: bool = False, timeout: int = 10) -> list[dict]:
    payload = _openrouter_fetch_json("models", timeout=timeout)
    models = []
    for item in payload.get("data", []) or []:
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        architecture = item.get("architecture") or {}
        input_modalities = architecture.get("input_modalities") or []
        modality = str(architecture.get("modality") or "")
        supports_image = "image" in input_modalities or "image" in modality.lower()
        if vision_only and not supports_image:
            continue
        models.append({
            "id": model_id,
            "name": str(item.get("name") or model_id).strip(),
            "description": str(item.get("description") or "").strip(),
            "supports_image": supports_image,
            "input_modalities": input_modalities,
        })
    return sorted(models, key=lambda item: item.get("id", ""))


def _openrouter_discovery_endpoints(model_id: str, timeout: int = 10) -> list[dict]:
    import urllib.parse

    # OpenRouter expects slash-delimited model ids in the path here.
    encoded_model = urllib.parse.quote(str(model_id or "").strip(), safe="/")
    payload = _openrouter_fetch_json(f"models/{encoded_model}/endpoints", timeout=timeout)
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    endpoints = data.get("endpoints", []) if isinstance(data, dict) else []
    normalized = []
    for endpoint in endpoints or []:
        normalized.append({
            "provider_name": str(endpoint.get("provider_name") or endpoint.get("name") or "").strip(),
            "tag": str(endpoint.get("tag") or "").strip(),
            "status": endpoint.get("status"),
            "uptime_last_30m": endpoint.get("uptime_last_30m"),
            "context_length": endpoint.get("context_length"),
        })
    return normalized


def _summarize_upstream_error_detail(raw_body: str, fallback: str = "") -> str:
    detail = (raw_body or "").strip()
    if not detail:
        return fallback or "upstream request failed"
    try:
        payload = json.loads(detail)
    except Exception:
        return detail

    if isinstance(payload, dict):
        error_payload = payload.get("error", payload)
        if isinstance(error_payload, dict):
            metadata = error_payload.get("metadata") if isinstance(error_payload.get("metadata"), dict) else {}
            metadata_raw = str(metadata.get("raw") or "").strip()
            message = str(error_payload.get("message") or "").strip()
            code = str(error_payload.get("code") or "").strip()
            if metadata_raw:
                return metadata_raw
            if message and code and message.lower() != code.lower():
                return f"{message} ({code})"
            if message:
                return message
            if code:
                return code
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return detail


def _estimate_base64_decoded_size(payload: str) -> int:
    cleaned = "".join(str(payload).split())
    if not cleaned:
        return 0
    if len(cleaned) % 4 != 0:
        raise ValueError("Invalid base64 length")
    padding = len(cleaned) - len(cleaned.rstrip("="))
    return (len(cleaned) * 3) // 4 - padding


def _save_upload_stream(file_storage, destination: Path) -> int:
    temp_path = destination.with_name(f".{destination.name}.part")
    total = 0
    stream = getattr(file_storage, "stream", file_storage)
    if hasattr(stream, "seek"):
        stream.seek(0)
    try:
        with temp_path.open("wb") as handle:
            while True:
                chunk = stream.read(UPLOAD_STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_SIZE:
                    raise RequestEntityTooLarge()
                handle.write(chunk)
        temp_path.replace(destination)
        return total
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _build_openai_api_url(base_url: str, path: str) -> str:
    base = (base_url or "").rstrip("/")
    clean_path = path.lstrip("/")
    if base.endswith("/v1"):
        return f"{base}/{clean_path}"
    return f"{base}/v1/{clean_path}"


def _api_server_probe(timeout: int = 3, prefer_vision: bool = False) -> tuple[bool, str, dict | None]:
    import urllib.request
    import urllib.error

    target = _resolve_api_target(prefer_vision=prefer_vision)
    base_url = (target.get("base_url") or "").strip()
    if not base_url:
        return False, "API base URL is not configured", None

    headers = _api_server_headers(target.get("api_key"), target.get("provider"))
    probes = [
        ("health", f"{base_url.rstrip('/')}/health"),
        ("models", _build_openai_api_url(base_url, "models")),
    ]
    last_error = "API endpoint did not respond"

    for probe_name, url in probes:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return True, "", {"probe": probe_name, "url": url, "status": resp.status}
                last_error = f"{probe_name} probe returned HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            if probe_name == "health" and exc.code == 404:
                last_error = "health probe returned HTTP 404"
                continue
            last_error = f"{probe_name} probe returned HTTP {exc.code}"
        except Exception as exc:
            last_error = f"{probe_name} probe failed: {exc}"

    return False, last_error, None


def _check_api_server() -> bool:
    """Check if the configured API target is reachable."""
    try:
        return _api_server_healthcheck()
    except Exception:
        return False


def _api_server_healthcheck(timeout: int = 3) -> bool:
    ok, _, _ = _api_server_probe(timeout=timeout)
    return ok


def _vision_configured() -> tuple[bool, str]:
    model_cfg = _normalized_model_config()
    vision_cfg = model_cfg.get("vision")
    if isinstance(vision_cfg, str):
        if vision_cfg.strip():
            return True, ""
    elif isinstance(vision_cfg, dict):
        provider = (vision_cfg.get("provider") or "").strip()
        model = (vision_cfg.get("model") or "").strip()
        base_url = (vision_cfg.get("base_url") or "").strip()
        if model or base_url or (provider and provider.lower() != "auto"):
            return True, ""
    return False, "Hermes vision is not configured"


def _image_attachment_support_status() -> tuple[bool, str]:
    vision_ready, vision_reason = _vision_configured()
    if not vision_ready:
        return False, vision_reason
    target = _resolve_api_target(prefer_vision=True)
    if _api_target_missing_credentials(target):
        api_url = target.get("base_url") or _effective_hermes_api_url(DEFAULT_HERMES_API_URL)
        return False, f"Vision API key is missing for remote endpoint {api_url}"
    api_ok, api_reason, _ = _api_server_probe(timeout=2, prefer_vision=True)
    if api_ok:
        image_model_ok, image_model_reason = _openrouter_model_supports_images(target, timeout=3)
        if image_model_ok is False:
            return False, image_model_reason
        return True, ""
    api_url = target.get("base_url") or _effective_hermes_api_url(DEFAULT_HERMES_API_URL)
    return False, f"OpenAI-compatible vision sidecar API is not reachable at {api_url} ({api_reason})"


def _get_or_create_chat_session(session_id=None, profile_name=None):
    existing = _load_session(session_id) if session_id else None
    if existing:
        return _normalize_chat_session(existing)
    if not session_id:
        session_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    selected_profile = _normalize_hermes_profile_name(profile_name) or _selected_hermes_profile_name()
    session = {
        "id": session_id,
        "messages": [],
        "created": now,
        "title": "New Chat",
        "updated": now,
        "profile": selected_profile,
        "segments": [{
            "id": "segment-1",
            "index": 1,
            "profile": selected_profile,
            "transport": "",
            "hermes_session_id": None,
            "started_at": now,
            "start_message_index": 0,
        }],
        "active_segment_id": "segment-1",
        "hermes_session_id": None,
        "transport_mode": None,
        "transport_preference": None,
        "continuity_mode": None,
        "transport_notice": "",
        "folder_id": "",
        "workspace_roots": [],
        "source_docs": [],
    }
    session = _normalize_chat_session(session)
    _write_session(session)
    return copy.deepcopy(session)


def _rollback_failed_chat_turn(session: dict, session_id: str, user_msg: dict) -> None:
    if session.get("messages") and session["messages"][-1] == user_msg:
        session["messages"].pop()
    if not session.get("messages"):
        _delete_session_from_disk(session_id)
        return
    session["updated"] = datetime.now().isoformat()
    _write_session(session)


# ===================================================================
# Main
# ===================================================================

if __name__ == "__main__":
    print("[Hermes Admin Panel] ERROR: do not run this file directly.")
    print("[Hermes Admin Panel] Use ./start.sh 5000 to run in production, or DEV=1 ./start.sh 5000 for development.")
    exit(1)
