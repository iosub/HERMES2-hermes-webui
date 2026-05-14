from __future__ import annotations

from pathlib import Path


def run_sidecar_vision_analysis(
    session: dict,
    message: str,
    files: list[Path],
    *,
    user_message: dict,
    file_display_names: dict | None = None,
    image_extensions,
    vision_reanalysis_requested_fn,
    vision_asset_disk_path_fn,
    latest_sidecar_asset_group_fn,
    attachment_display_name_fn,
    resolve_api_target_fn,
    chat_completion_request_fn,
    chat_backend_error,
    chat_backend_error_detail_fn,
    chat_backend_error_is_rate_limited_fn,
    parse_sidecar_payload_fn,
    update_session_vision_assets_fn,
) -> dict:
    image_files = [path for path in files or [] if path.suffix.lower() in image_extensions]
    reanalysis = False
    if not image_files and vision_reanalysis_requested_fn(message, session):
        image_files = [path for path in (
            vision_asset_disk_path_fn(asset) for asset in latest_sidecar_asset_group_fn(session)
        ) if path is not None]
        reanalysis = bool(image_files)
    if not image_files:
        return {}

    import base64

    image_labels = [
        attachment_display_name_fn(path, file_display_names) if path in (files or []) else (path.name if path else "Image")
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
            raise chat_backend_error(f"Vision sidecar could not read {image_file.name}: {exc}") from exc
        ext = image_file.suffix.lower().replace(".", "") or "png"
        content.append({"type": "text", "text": f"Image {idx}: {label}"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}})

    target = resolve_api_target_fn(prefer_vision=True)
    try:
        raw_text = chat_completion_request_fn(target, [{"role": "user", "content": content}])
    except chat_backend_error as exc:
        model_id = str(target.get("model") or "the configured vision model").strip()
        detail = chat_backend_error_detail_fn(exc) or "upstream request failed"
        if chat_backend_error_is_rate_limited_fn(exc):
            raise chat_backend_error(
                f"Vision sidecar is temporarily rate-limited for {model_id}. Retry shortly or switch the vision model/provider in Providers. Details: {detail}",
                status_code=503,
            ) from exc
        raise chat_backend_error(
            f"Vision sidecar failed for {model_id}. Details: {detail}",
            status_code=getattr(exc, "status_code", 502),
        ) from exc
    parsed_payload = parse_sidecar_payload_fn(raw_text, image_labels)
    asset_ids = update_session_vision_assets_fn(
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


def compose_cli_prompt_with_sidecar(
    session: dict,
    message: str,
    files: list[Path],
    *,
    sidecar_result: dict | None = None,
    file_display_names: dict | None = None,
    image_extensions,
    compose_chat_turn_payload_fn,
    format_sidecar_context_block_fn,
) -> str:
    non_image_files = [path for path in files or [] if path.suffix.lower() not in image_extensions]
    prompt, _ = compose_chat_turn_payload_fn(
        session,
        message,
        non_image_files,
        image_support=False,
        display_names=file_display_names,
    )
    sidecar_block = format_sidecar_context_block_fn(sidecar_result or {})
    sections = [section for section in (prompt, sidecar_block) if section]
    return "\n\n".join(sections)