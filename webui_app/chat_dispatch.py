from __future__ import annotations

import base64


def call_api_server(
    session,
    messages,
    session_id,
    *,
    files=None,
    prefer_vision=False,
    file_display_names=None,
    compose_chat_turn_payload,
    resolve_api_target,
    chat_completion_request,
    chat_backend_error,
    chat_backend_error_is_retryable,
    resolve_fallback_api_target,
    model_role_enabled,
    targets_equivalent,
    chat_backend_error_detail,
    image_extensions,
):
    msgs = list(messages)
    use_vision_target = prefer_vision
    if files and msgs and msgs[-1].get("role") == "user":
        text_content, image_files = compose_chat_turn_payload(
            session,
            msgs[-1].get("content", "") or "",
            files,
            image_support=True,
            display_names=file_display_names,
        )
        if image_files:
            use_vision_target = True
            img_content = []
            if text_content:
                img_content.append({"type": "text", "text": text_content})
            for image_path in image_files:
                try:
                    with open(image_path, "rb") as handle:
                        b64 = base64.b64encode(handle.read()).decode("utf-8")
                    ext = image_path.suffix.lower().replace(".", "")
                    img_content.append({"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}})
                except Exception:
                    pass
            if img_content:
                msgs[-1] = {"role": "user", "content": img_content}
        else:
            msgs[-1] = {"role": "user", "content": text_content}
    elif msgs and msgs[-1].get("role") == "user":
        text_content, _ = compose_chat_turn_payload(
            session,
            msgs[-1].get("content", "") or "",
            [],
            image_support=False,
            display_names=file_display_names,
        )
        msgs[-1] = {"role": "user", "content": text_content}

    target = resolve_api_target(prefer_vision=use_vision_target)
    try:
        return chat_completion_request(target, msgs)
    except chat_backend_error as exc:
        if use_vision_target or not chat_backend_error_is_retryable(exc):
            raise
        fallback_target = resolve_fallback_api_target()
        if (
            not model_role_enabled("fallback", target=fallback_target)
            or targets_equivalent(target, fallback_target)
        ):
            raise
        try:
            return chat_completion_request(fallback_target, msgs)
        except chat_backend_error as fallback_exc:
            primary_detail = chat_backend_error_detail(exc) or str(exc)
            fallback_detail = chat_backend_error_detail(fallback_exc) or str(fallback_exc)
            raise chat_backend_error(
                "Primary chat model failed"
                f" ({target.get('model') or 'primary'}): {primary_detail}. "
                "Fallback chat model also failed"
                f" ({fallback_target.get('model') or 'fallback'}): {fallback_detail}",
                status_code=max(
                    int(getattr(exc, "status_code", 502) or 502),
                    int(getattr(fallback_exc, "status_code", 502) or 502),
                ),
            ) from fallback_exc
    except Exception as exc:
        raise chat_backend_error(f"API server error: {exc}") from exc