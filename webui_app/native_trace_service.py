from __future__ import annotations

import json
import re
from urllib.parse import urlparse


def trace_summary_text(value, limit: int = 160) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def trace_summary_url(value, limit: int = 140, *, trace_summary_text_fn) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        compact = f"{parsed.netloc}{parsed.path or ''}".strip()
        if parsed.query:
            compact = f"{compact}?{parsed.query}"
        return trace_summary_text_fn(compact or raw, limit)
    return trace_summary_text_fn(raw, limit)


def parse_trace_json(value) -> dict | list | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def summarize_native_tool_call(tool_name: str, arguments, *, trace_summary_url_fn, trace_summary_text_fn) -> str:
    payload = arguments if isinstance(arguments, dict) else {}
    if tool_name in {"browser_navigate", "fetch_webpage"} and payload.get("url"):
        return trace_summary_url_fn(payload.get("url"), 140)
    if tool_name == "browser_click" and payload.get("ref"):
        return f"ref {payload.get('ref')}"
    if tool_name == "browser_type" and payload.get("text"):
        return trace_summary_text_fn(payload.get("text"), 120)
    if tool_name == "skill_view" and payload.get("name"):
        return trace_summary_text_fn(payload.get("name"), 120)
    if tool_name == "terminal" and payload.get("command"):
        return trace_summary_text_fn(payload.get("command"), 140)
    if payload.get("query"):
        return trace_summary_text_fn(payload.get("query"), 140)
    for key in ("path", "file_path", "name", "url"):
        if payload.get(key):
            return trace_summary_text_fn(payload.get(key), 140)
    return ""


def summarize_native_tool_result(tool_name: str, content, *, parse_trace_json_fn, trace_summary_text_fn, trace_summary_url_fn) -> str:
    parsed = parse_trace_json_fn(content)
    if isinstance(parsed, dict):
        if parsed.get("error"):
            return trace_summary_text_fn(parsed.get("error"), 140)
        if tool_name in {"browser_navigate", "fetch_webpage"} and parsed.get("url"):
            return trace_summary_url_fn(parsed.get("url"), 140)
        if tool_name == "skill_view" and parsed.get("name"):
            return trace_summary_text_fn(parsed.get("name"), 120)
        if tool_name == "terminal":
            exit_code = parsed.get("exit_code")
            if exit_code not in (None, ""):
                return f"exit_code={exit_code}"
        for key in ("message", "title", "path"):
            if parsed.get(key):
                return trace_summary_text_fn(parsed.get(key), 140)
    return trace_summary_text_fn(content, 140) if isinstance(content, str) else ""


def native_trace_icon(tool_name: str) -> str:
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


def format_native_trace_line(tool_name: str, summary: str = "", *, native_trace_icon_fn) -> str:
    name = str(tool_name or "").strip()
    if not name:
        return ""
    icon = native_trace_icon_fn(name)
    if summary:
        return f"  ┊ {icon} {name} {summary}".rstrip()
    return f"  ┊ {icon} {name}"


def load_hermes_native_session_trace_lines(
    path,
    *,
    parse_trace_json_fn,
    summarize_native_tool_call_fn,
    format_native_trace_line_fn,
    summarize_native_tool_result_fn,
    truncate_recent_lines_fn,
) -> list[str]:
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
                arguments = parse_trace_json_fn(function.get("arguments"))
                summary = summarize_native_tool_call_fn(tool_name, arguments)
                formatted = format_native_trace_line_fn(tool_name, summary)
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
            summary = summarize_native_tool_result_fn(tool_name, item.get("content"))
            if summary:
                formatted = format_native_trace_line_fn(tool_name, summary)
                if formatted:
                    lines.append(formatted)

    return truncate_recent_lines_fn(lines, limit=120)


def looks_like_rich_cli_trace(lines: list[str]) -> bool:
    rows = [str(line or "") for line in (lines or []) if str(line or "").strip()]
    if not rows:
        return False
    tool_progress = sum(1 for line in rows if re.search(r"^\s*┊\s+[📚🌐💻🔍🧠⚙]", line))
    if tool_progress >= 2:
        return True
    tool_progress = sum(1 for line in rows if re.search(r"^\s*┊\s+", line))
    return tool_progress >= 3


def debug_trace_lines_for_chat(
    request_id: str,
    hermes_session_id: str | None,
    *,
    request_progress_lines_fn,
    looks_like_rich_cli_trace_fn,
    find_updated_hermes_native_session_fn,
    load_hermes_native_session_trace_lines_fn,
) -> list[str]:
    raw_lines = request_progress_lines_fn(request_id)
    if looks_like_rich_cli_trace_fn(raw_lines):
        return raw_lines

    if not str(hermes_session_id or '').strip():
        return raw_lines

    native_path = find_updated_hermes_native_session_fn(None, hermes_session_id)
    if native_path:
        native_lines = load_hermes_native_session_trace_lines_fn(native_path)
        if native_lines:
            return native_lines
    return raw_lines