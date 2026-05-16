from __future__ import annotations

import copy
import mimetypes
from pathlib import Path


def file_mime_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return (mime or "").lower()


def is_text_attachment(path: Path, *, text_extensions, text_mime_types, file_mime_type_fn) -> bool:
    if path.suffix.lower() in text_extensions:
        return True
    mime = file_mime_type_fn(path)
    if mime.startswith("text/") or mime in text_mime_types:
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


def validate_attachment_selection(files: list[Path], image_support: bool, *, image_extensions, audio_extensions, is_text_attachment_fn) -> list[str]:
    errors = []
    for file_path in files or []:
        suffix = file_path.suffix.lower()
        if suffix in image_extensions:
            if not image_support:
                errors.append(f"{file_path.name} is an image, but the configured Hermes vision sidecar is not ready")
            continue
        if suffix in audio_extensions:
            errors.append(f"{file_path.name} is audio, and audio uploads are not supported in Hermes chat")
            continue
        if is_text_attachment_fn(file_path):
            continue
        errors.append(f"{file_path.name} is a binary file type Hermes chat cannot read")
    return errors


def summarize_attachments(
    files: list[Path],
    image_support: bool,
    display_names: dict | None = None,
    *,
    attachment_display_name_fn,
    image_extensions,
    audio_extensions,
    is_text_attachment_fn,
) -> dict:
    text_blocks = []
    image_files = []
    unsupported = []
    for file_path in files or []:
        display_name = attachment_display_name_fn(file_path, display_names)
        suffix = file_path.suffix.lower()
        if suffix in image_extensions:
            if image_support:
                image_files.append(file_path)
            else:
                unsupported.append(f"{display_name} (image attachments require a ready Hermes vision sidecar)")
            continue
        if suffix in audio_extensions:
            unsupported.append(f"{display_name} (audio attachments are not supported in Hermes chat)")
            continue
        if is_text_attachment_fn(file_path):
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
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


def compose_message_with_attachments(
    message: str,
    files: list[Path],
    image_support: bool,
    display_names: dict | None = None,
    *,
    summarize_attachments_fn,
) -> tuple[str, list[Path]]:
    summary = summarize_attachments_fn(files, image_support=image_support, display_names=display_names)
    sections = list(summary["text_blocks"])
    if message:
        sections.append(f"User message: {message}")
    if summary["unsupported"]:
        notes = "\n".join(f"- {note}" for note in summary["unsupported"])
        sections.append(f"Attachment notes:\n{notes}")
    if not sections:
        sections.append(message or "User attached files without an additional text message.")
    return "\n\n".join(sections), summary["image_files"]


def session_has_image_history(session: dict, *, image_extensions, path_class) -> bool:
    for message in session.get("messages", []):
        for file_name in message.get("files", []) or []:
            if path_class(file_name).suffix.lower() in image_extensions:
                return True
    return False


def messages_for_active_segment(session: dict, *, active_chat_segment_fn) -> list[dict]:
    messages = session.get("messages") or []
    active_segment = active_chat_segment_fn(session) or {}
    start_message_index = int(active_segment.get("start_message_index") or 0)
    if start_message_index <= 0:
        return messages
    return messages[start_message_index:]


def active_segment_has_image_history(session: dict, *, messages_for_active_segment_fn, image_extensions, path_class) -> bool:
    for message in messages_for_active_segment_fn(session):
        for file_name in message.get("files", []) or []:
            if path_class(file_name).suffix.lower() in image_extensions:
                return True
    return False


def latest_user_turn(session: dict) -> dict | None:
    for message in reversed(session.get("messages", [])):
        if isinstance(message, dict) and message.get("role") == "user":
            return message
    return None


def latest_sidecar_asset_group(session: dict, *, clean_string_list_fn) -> list[dict]:
    asset_ids = set()
    for message in reversed(session.get("messages", [])):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        sidecar = message.get("sidecar_vision") or {}
        asset_ids = set(clean_string_list_fn(sidecar.get("asset_ids")))
        if asset_ids:
            break
    if not asset_ids:
        return []
    assets = []
    for asset in session.get("vision_assets", []) or []:
        if isinstance(asset, dict) and asset.get("id") in asset_ids:
            assets.append(copy.deepcopy(asset))
    return assets


def latest_turn_used_sidecar_vision(session: dict, *, latest_user_turn_fn) -> bool:
    latest_user = latest_user_turn_fn(session)
    return bool((latest_user or {}).get("sidecar_vision", {}).get("used"))


def latest_turn_sidecar_asset_names(session: dict, *, latest_user_turn_fn, clean_string_list_fn) -> list[str]:
    latest_user = latest_user_turn_fn(session)
    if not latest_user:
        return []
    sidecar = latest_user.get("sidecar_vision") or {}
    asset_names = []
    asset_map = {
        asset.get("id"): asset for asset in session.get("vision_assets", []) or []
        if isinstance(asset, dict) and asset.get("id")
    }
    for asset_id in clean_string_list_fn(sidecar.get("asset_ids")):
        asset = asset_map.get(asset_id) or {}
        label = str(asset.get("display_name") or "").strip()
        if label:
            asset_names.append(label)
    return asset_names


def chat_session_meta(
    session: dict,
    *,
    normalize_chat_session_fn,
    copy_module,
    effective_session_context_fn,
    active_chat_segment_fn,
    active_request_for_session_fn,
    selected_hermes_profile_name_fn,
    chat_transport_auto: str,
    transport_preference_label_fn,
    chat_continuity_hermes: str,
    latest_turn_used_sidecar_vision_fn,
    latest_turn_sidecar_asset_names_fn,
) -> dict:
    normalized = normalize_chat_session_fn(copy_module.deepcopy(session))
    context = effective_session_context_fn(normalized)
    active_segment = active_chat_segment_fn(normalized) or {}
    active_request = active_request_for_session_fn(normalized.get("id") or "")
    return {
        "profile": normalized.get("profile") or selected_hermes_profile_name_fn(),
        "compare_temporary": bool(normalized.get("compare_temporary")),
        "compare_group_id": str(normalized.get("compare_group_id") or "").strip(),
        "compare_profiles": copy_module.deepcopy(normalized.get("compare_profiles") or []),
        "active_segment_id": active_segment.get("id") or "",
        "active_segment_index": active_segment.get("index") or 1,
        "segments": copy_module.deepcopy(normalized.get("segments") or []),
        "transport_mode": normalized.get("transport_mode"),
        "transport_preference": normalized.get("transport_preference") or chat_transport_auto,
        "transport_preference_label": transport_preference_label_fn(normalized.get("transport_preference")),
        "continuity_mode": normalized.get("continuity_mode"),
        "transport_notice": normalized.get("transport_notice") or "",
        "hermes_session_backed": normalized.get("continuity_mode") == chat_continuity_hermes,
        "last_turn_used_sidecar_vision": latest_turn_used_sidecar_vision_fn(normalized),
        "last_turn_sidecar_asset_names": latest_turn_sidecar_asset_names_fn(normalized),
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


def format_chat_context_block(
    session: dict,
    *,
    effective_session_context_fn,
    path_class,
    is_text_attachment_fn,
    chat_context_source_doc_limit: int,
    chat_context_source_doc_total_limit: int,
) -> str:
    context = effective_session_context_fn(session)
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
        path = path_class(source_doc)
        if not path.exists():
            notes.append(f"{source_doc} (missing)")
            continue
        if not is_text_attachment_fn(path):
            notes.append(f"{source_doc} (not a readable text file)")
            continue
        try:
            raw_bytes = path.read_bytes()
        except Exception as exc:
            notes.append(f"{source_doc} (could not be read: {exc})")
            continue
        remaining = chat_context_source_doc_total_limit - total_bytes
        if remaining <= 0:
            notes.append(f"{source_doc} (skipped because the source-doc context limit was reached)")
            continue
        snippet = raw_bytes[:min(chat_context_source_doc_limit, remaining)]
        total_bytes += len(snippet)
        text = snippet.decode("utf-8", errors="replace")
        if len(snippet) < len(raw_bytes):
            notes.append(f"{source_doc} (truncated to {len(snippet)} bytes for chat context)")
        doc_sections.append(f"Source doc: {source_doc}\n```\n{text}\n```")

    sections.extend(doc_sections)
    if notes:
        sections.append("Source doc notes:\n" + "\n".join(f"- {note}" for note in notes))
    return "\n\n".join(sections)


def compose_chat_turn_payload(
    session: dict,
    message: str,
    files: list[Path],
    image_support: bool,
    display_names: dict | None = None,
    *,
    compose_message_with_attachments_fn,
    format_chat_context_block_fn,
) -> tuple[str, list[Path]]:
    attachment_text, image_files = compose_message_with_attachments_fn(
        message,
        files,
        image_support=image_support,
        display_names=display_names,
    )
    context_block = format_chat_context_block_fn(session)
    sections = [section for section in (context_block, attachment_text) if section]
    return "\n\n".join(sections), image_files