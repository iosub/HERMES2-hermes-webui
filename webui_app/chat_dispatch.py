from __future__ import annotations

import base64
import os
import subprocess
import time
from datetime import datetime


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


def call_hermes_direct(
    session,
    message,
    *,
    files=None,
    request_id=None,
    file_display_names=None,
    compose_chat_turn_payload,
    call_hermes_prompt,
):
    prompt, _ = compose_chat_turn_payload(
        session,
        message,
        files or [],
        image_support=False,
        display_names=file_display_names,
    )
    return call_hermes_prompt(session, prompt, request_id=request_id)


def call_hermes_prompt(
    session,
    prompt,
    *,
    request_id=None,
    read_request_control,
    update_chat_request,
    chat_request_cancelled,
    snapshot_hermes_native_sessions,
    selected_hermes_bin,
    selected_hermes_home,
    request_output_path,
    path_home,
    terminate_chat_process,
    chat_cancel_grace_seconds,
    chat_cancel_poll_interval,
    chat_request_timeout,
    parse_hermes_chat_result,
    find_updated_hermes_native_session,
    load_hermes_native_session_reply,
    chat_request_timeout_error,
    chat_backend_error,
    signal_module,
    os_module,
):
    if request_id:
        state = read_request_control(request_id)
        if state and state.get("cancel_requested_at"):
            update_chat_request(request_id, status="cancelled")
            raise chat_request_cancelled("Request cancelled before Hermes started")

    native_session_snapshot = snapshot_hermes_native_sessions()
    cmd = [str(selected_hermes_bin()), "chat"]
    if session.get("hermes_session_id"):
        cmd.extend(["--resume", session["hermes_session_id"]])
    cmd.extend(["-q", prompt])

    try:
        output_path = request_output_path(request_id) if request_id else None
        if output_path:
            try:
                output_path.unlink(missing_ok=True)
            except Exception:
                pass
        output_handle = output_path.open("w", encoding="utf-8") if output_path else None
        proc = subprocess.Popen(
            cmd,
            cwd=str(path_home()),
            env={**os.environ, "NO_COLOR": "1", "HERMES_HOME": str(selected_hermes_home())},
            stdout=output_handle if output_handle is not None else subprocess.PIPE,
            stderr=subprocess.STDOUT if output_handle is not None else subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        if request_id:
            update_chat_request(request_id, pid=proc.pid, pgid=os_module.getpgid(proc.pid))

        last_activity_at = time.time()
        last_output_size = 0
        while True:
            if request_id:
                state = read_request_control(request_id)
                if state and state.get("cancel_requested_at"):
                    pgid = os_module.getpgid(proc.pid)
                    terminate_chat_process(proc.pid, pgid, signal_module.SIGTERM)
                    try:
                        stdout, stderr = proc.communicate(timeout=chat_cancel_grace_seconds)
                    except subprocess.TimeoutExpired:
                        terminate_chat_process(proc.pid, pgid, signal_module.SIGKILL)
                        stdout, stderr = proc.communicate()
                    if output_handle is not None:
                        output_handle.flush()
                    update_chat_request(request_id, status="cancelled")
                    raise chat_request_cancelled("Request cancelled")
            if output_path and output_path.exists():
                try:
                    current_size = output_path.stat().st_size
                    if current_size > last_output_size:
                        last_output_size = current_size
                        last_activity_at = time.time()
                except OSError:
                    pass
            remaining = chat_request_timeout - (time.time() - last_activity_at)
            if remaining <= 0:
                terminate_chat_process(proc.pid, os_module.getpgid(proc.pid), signal_module.SIGKILL)
                proc.communicate()
                raise subprocess.TimeoutExpired(proc.args, chat_request_timeout)
            try:
                stdout, stderr = proc.communicate(timeout=min(chat_cancel_poll_interval, remaining))
                break
            except subprocess.TimeoutExpired:
                if output_handle is not None:
                    output_handle.flush()
                if output_path and output_path.exists():
                    try:
                        current_size = output_path.stat().st_size
                        if current_size > last_output_size:
                            last_output_size = current_size
                            last_activity_at = time.time()
                    except OSError:
                        pass
                continue

        if output_handle is not None:
            output_handle.flush()

        if request_id:
            state = read_request_control(request_id)
            if state and state.get("cancel_requested_at"):
                update_chat_request(request_id, status="cancelled")
                raise chat_request_cancelled("Request cancelled")

        if proc.returncode != 0:
            if output_path and output_path.exists():
                error_output = output_path.read_text(encoding="utf-8", errors="replace").strip()
            else:
                error_output = (stderr or "").strip() or (stdout or "").strip()
            error_output = error_output or f"Hermes CLI exited with status {proc.returncode}"
            raise chat_backend_error(error_output)

        if output_path and output_path.exists():
            output = output_path.read_text(encoding="utf-8", errors="replace").strip()
        else:
            output = (stdout or "").strip()
        if not output:
            raise chat_backend_error(((stderr or "").strip()) or "Hermes returned an empty response")

        response_text, hermes_session_id = parse_hermes_chat_result(output)
        native_session_path = find_updated_hermes_native_session(native_session_snapshot, hermes_session_id)
        native_text = None
        native_session_id = None
        if native_session_path:
            native_text, native_session_id = load_hermes_native_session_reply(native_session_path)
        return native_text or response_text, native_session_id or hermes_session_id
    except chat_request_cancelled:
        raise
    except subprocess.TimeoutExpired:
        raise chat_request_timeout_error(f"Hermes did not produce activity within {chat_request_timeout} seconds")
    except chat_backend_error:
        raise
    except Exception as exc:
        raise chat_backend_error(f"Error calling Hermes: {exc}") from exc
    finally:
        if "output_handle" in locals() and output_handle is not None:
            output_handle.close()


def register_chat_request(
    request_id,
    session_id,
    *,
    transport,
    cancel_supported,
    write_request_control,
    request_output_path,
):
    write_request_control(request_id, {
        "request_id": request_id,
        "session_id": session_id,
        "status": "running",
        "transport": transport,
        "cancel_supported": cancel_supported,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "pid": None,
        "pgid": None,
        "cancel_requested_at": None,
        "output_path": str(request_output_path(request_id)),
    })


def update_chat_request(request_id, *, read_request_control, write_request_control, fields):
    payload = read_request_control(request_id)
    if payload is None:
        return None
    payload.update(fields)
    payload["updated_at"] = datetime.now().isoformat()
    write_request_control(request_id, payload)
    return payload


def is_process_alive(pid, *, os_module):
    if not pid:
        return False
    try:
        os_module.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_chat_process(pid, pgid, sig, *, os_module, logger):
    try:
        if pgid:
            os_module.killpg(pgid, sig)
        elif pid:
            os_module.kill(pid, sig)
        else:
            return False
        return True
    except ProcessLookupError:
        return True
    except Exception as exc:
        logger.warning("Failed to signal chat process pid=%s pgid=%s: %s", pid, pgid, exc)
        return False


def cancel_chat_request(
    request_id,
    *,
    read_request_control,
    update_chat_request,
    terminate_chat_process,
    chat_cancel_grace_seconds,
    chat_cancel_poll_interval,
    is_process_alive,
    time_module,
    signal_module,
):
    payload = read_request_control(request_id)
    if payload is None:
        return False, "Request not found"
    if not payload.get("cancel_supported", False):
        return False, "This request is using the API/vision path and cannot be cancelled server-side"
    if payload.get("status") == "completed":
        return False, "Request already completed"
    if payload.get("status") == "cancelled":
        return True, "Request already cancelled"

    pid = payload.get("pid")
    pgid = payload.get("pgid")
    update_chat_request(
        request_id,
        status="cancel_requested",
        cancel_requested_at=datetime.now().isoformat(),
    )

    if not pid:
        return True, "Cancellation queued before subprocess start"

    if not terminate_chat_process(pid, pgid, signal_module.SIGTERM):
        return False, "Failed to terminate Hermes process"

    deadline = time_module.time() + chat_cancel_grace_seconds
    while time_module.time() < deadline:
        if not is_process_alive(pid):
            update_chat_request(request_id, status="cancelled")
            return True, "Request cancelled"
        time_module.sleep(chat_cancel_poll_interval)

    terminate_chat_process(pid, pgid, signal_module.SIGKILL)
    for _ in range(int(1 / chat_cancel_poll_interval)):
        if not is_process_alive(pid):
            update_chat_request(request_id, status="cancelled")
            return True, "Request cancelled"
        time_module.sleep(chat_cancel_poll_interval)

    return False, "Hermes process did not exit after cancellation"