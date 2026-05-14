from __future__ import annotations


def validated_transport_preference(
    value,
    *,
    normalize_transport_preference,
    chat_runtime_status,
    check_api_server,
    chat_transport_api,
    chat_transport_cli,
):
    normalized = normalize_transport_preference(value)
    if normalized != chat_transport_api:
        return normalized, ""

    runtime = chat_runtime_status()
    if runtime.get("requires_cli"):
        return chat_transport_cli, runtime.get("cli_reason") or "Hermes CLI is required right now."
    if not check_api_server():
        return chat_transport_cli, "API replay is unavailable right now, so Hermes CLI will be used."
    return normalized, ""


def plan_chat_request(
    session,
    files,
    *,
    normalize_chat_session,
    normalize_transport_preference,
    image_extensions,
    image_attachment_support_status,
    check_api_server,
    chat_runtime_status,
    chat_transport_api,
    chat_transport_cli,
):
    normalized = normalize_chat_session(session)
    transport_preference = normalize_transport_preference(normalized.get("transport_preference"))
    has_image_files = any(file_path.suffix.lower() in image_extensions for file_path in files or [])
    image_support, image_reason = image_attachment_support_status()
    api_server_enabled = check_api_server()
    runtime = chat_runtime_status()
    notice = normalized.get("transport_notice") or ""

    if runtime.get("requires_cli"):
        transport = chat_transport_cli
        notice = runtime.get("cli_reason") or notice
    elif transport_preference == chat_transport_api and api_server_enabled:
        transport = chat_transport_api
    elif transport_preference == chat_transport_api:
        transport = chat_transport_cli
        notice = "API replay is unavailable right now, so Hermes CLI will be used."
    elif transport_preference == chat_transport_cli:
        transport = chat_transport_cli
    else:
        transport = chat_transport_api if api_server_enabled else chat_transport_cli
        if transport == chat_transport_cli and transport_preference is None and runtime.get("cli_reason"):
            notice = runtime.get("cli_reason")

    return {
        "transport": transport,
        "cancel_supported": transport == chat_transport_cli,
        "image_support": image_support,
        "image_reason": image_reason,
        "api_server_enabled": api_server_enabled,
        "transport_notice": notice,
        "use_sidecar_vision": transport == chat_transport_cli and has_image_files,
        "runtime": runtime,
    }