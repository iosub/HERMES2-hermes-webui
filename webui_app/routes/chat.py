from __future__ import annotations

import base64
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import jsonify, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename


def register_chat_routes(app, *, require_token, rate_limit, deps) -> None:
    normalize_profile_name = deps["normalize_profile_name"]
    available_profile_names = deps["available_profile_names"]
    normalize_transport_preference = deps["normalize_transport_preference"]
    get_or_create_chat_session = deps["get_or_create_chat_session"]
    selected_profile_name = deps["selected_profile_name"]
    validated_transport_preference = deps["validated_transport_preference"]
    ensure_folder_exists = deps["ensure_folder_exists"]
    scoped_profile_override = deps["scoped_profile_override"]
    plan_chat_request = deps["plan_chat_request"]
    append_chat_segment = deps["append_chat_segment"]
    segment_hermes_session_id = deps["segment_hermes_session_id"]
    validate_attachment_selection = deps["validate_attachment_selection"]
    register_chat_request = deps["register_chat_request"]
    attachment_display_name = deps["attachment_display_name"]
    build_attachment_refs = deps["build_attachment_refs"]
    write_session = deps["write_session"]
    messages_for_active_segment = deps["messages_for_active_segment"]
    call_api_server = deps["call_api_server"]
    active_segment_has_image_history = deps["active_segment_has_image_history"]
    image_extensions = deps["image_extensions"]
    vision_reanalysis_requested = deps["vision_reanalysis_requested"]
    run_sidecar_vision_analysis = deps["run_sidecar_vision_analysis"]
    compose_cli_prompt_with_sidecar = deps["compose_cli_prompt_with_sidecar"]
    call_hermes_prompt = deps["call_hermes_prompt"]
    call_hermes_direct = deps["call_hermes_direct"]
    clean_hermes_session_id = deps["clean_hermes_session_id"]
    update_chat_request = deps["update_chat_request"]
    rollback_failed_chat_turn = deps["rollback_failed_chat_turn"]
    debug_trace_lines_for_chat = deps["debug_trace_lines_for_chat"]
    remove_chat_request = deps["remove_chat_request"]
    chat_session_meta = deps["chat_session_meta"]
    cancel_chat_request = deps["cancel_chat_request"]
    request_id_or_dash = deps["request_id_or_dash"]
    save_upload_stream = deps["save_upload_stream"]
    upload_folder = deps["upload_folder"]
    max_request_body_size = deps["max_request_body_size"]
    max_upload_size = deps["max_upload_size"]
    estimate_base64_decoded_size = deps["estimate_base64_decoded_size"]
    load_all_sessions = deps["load_all_sessions"]
    folder_summaries = deps["folder_summaries"]
    parse_folder_update = deps["parse_folder_update"]
    folder_title_conflict = deps["folder_title_conflict"]
    legacy_folder_from_sessions = deps["legacy_folder_from_sessions"]
    write_folder = deps["write_folder"]
    folder_with_fallback = deps["folder_with_fallback"]
    load_all_folders = deps["load_all_folders"]
    resolve_folder_reference = deps["resolve_folder_reference"]
    delete_folder = deps["delete_folder"]
    load_session = deps["load_session"]
    merge_unique_strings = deps["merge_unique_strings"]
    folder_workspace_roots_for_docs = deps["folder_workspace_roots_for_docs"]
    parse_chat_context_update = deps["parse_chat_context_update"]
    delete_session_from_disk = deps["delete_session_from_disk"]
    trim_trailing_empty_chat_segments = deps["trim_trailing_empty_chat_segments"]
    read_request_control = deps["read_request_control"]
    filter_live_progress_lines = deps["filter_live_progress_lines"]
    request_progress_lines = deps["request_progress_lines"]
    check_api_server = deps["check_api_server"]
    api_server_probe = deps["api_server_probe"]
    image_attachment_support_status = deps["image_attachment_support_status"]
    vision_configured = deps["vision_configured"]
    resolve_api_target = deps["resolve_api_target"]
    chat_runtime_status = deps["chat_runtime_status"]
    effective_hermes_api_url = deps["effective_hermes_api_url"]
    default_hermes_api_url = deps["default_hermes_api_url"]
    chat_request_timeout = deps["chat_request_timeout"]
    chat_server_timeout = deps["chat_server_timeout"]
    chat_persist_debug_trace = deps["chat_persist_debug_trace"]
    chat_transport_api = deps["chat_transport_api"]
    chat_transport_cli = deps["chat_transport_cli"]
    chat_continuity_hermes = deps["chat_continuity_hermes"]
    chat_continuity_local = deps["chat_continuity_local"]
    chat_continuity_limited = deps["chat_continuity_limited"]
    chat_request_cancelled = deps["chat_request_cancelled"]
    chat_backend_error = deps["chat_backend_error"]
    logger = deps["logger"]
    request_output_path = deps["request_output_path"]
    folder_source_dir = deps["folder_source_dir"]

    @app.route("/api/chat", methods=["POST"])
    @require_token
    @rate_limit
    def api_chat():
        chat_started_at = time.monotonic()
        data = request.get_json(silent=True) or {}
        message = data.get("message", "").strip()
        session_id = data.get("session_id")
        requested_profile = normalize_profile_name(data.get("profile") or "")
        if requested_profile and requested_profile not in available_profile_names():
            return jsonify({"error": "Invalid profile"}), 400
        requested_transport_preference = normalize_transport_preference(data.get("transport_preference"))
        requested_folder_id = str(data.get("folder_id") or "").strip()
        request_id = (data.get("request_id") or str(uuid.uuid4())).strip()
        files = []
        file_display_names = {}
        for ref in (data.get("files") or []):
            stored_as = None
            display_name = None
            if isinstance(ref, str):
                stored_as = ref
                display_name = Path(ref).name
            elif isinstance(ref, dict):
                stored_as = str(ref.get("stored_as") or "").strip()
                display_name = str(ref.get("name") or "").strip() or None
            if not stored_as:
                continue
            file_path = upload_folder() / stored_as
            if file_path.exists():
                files.append(file_path)
                if display_name:
                    file_display_names[file_path.name] = display_name
        if not message and not files:
            return jsonify({"error": "Message or attachment is required"}), 400

        sess = get_or_create_chat_session(session_id, profile_name=requested_profile)
        sess["profile"] = normalize_profile_name(sess.get("profile")) or selected_profile_name()
        if data.get("transport_preference") is not None:
            validated_preference, preference_notice = validated_transport_preference(requested_transport_preference)
            sess["transport_preference"] = validated_preference
            sess["transport_notice"] = preference_notice or ""
        if requested_folder_id:
            ensured = ensure_folder_exists(requested_folder_id)
            requested_folder_id = ensured["id"] if ensured else requested_folder_id
        if requested_folder_id and not sess.get("folder_id"):
            sess["folder_id"] = requested_folder_id
        sid = sess["id"]

        with scoped_profile_override(sess.get("profile")):
            request_plan = plan_chat_request(sess, files)
            active_segment = append_chat_segment(sess, sess["profile"], transport=request_plan["transport"])
            sess["hermes_session_id"] = segment_hermes_session_id(active_segment)
            attachment_errors = validate_attachment_selection(files, request_plan["image_support"])
        if attachment_errors:
            return jsonify({"error": "Unsupported attachment selection", "details": attachment_errors}), 400

        logger.info(
            "Chat request started request_id=%s session_id=%s transport=%s files=%s folder_id=%s",
            request_id,
            sid,
            request_plan["transport"],
            len(files),
            sess.get("folder_id") or "",
        )

        register_chat_request(
            request_id,
            sid,
            transport=request_plan["transport"],
            cancel_supported=request_plan["cancel_supported"],
        )
        user_msg = {
            "role": "user",
            "content": message,
            "files": [attachment_display_name(file_path, file_display_names) for file_path in files],
            "attachment_refs": build_attachment_refs(files, file_display_names),
            "timestamp": datetime.now().isoformat(),
            "segment_id": active_segment.get("id"),
            "segment_index": active_segment.get("index"),
            "profile": active_segment.get("profile") or sess.get("profile"),
            "transport": request_plan["transport"],
        }
        sess["messages"].append(user_msg)
        if len(sess["messages"]) == 1 and sess.get("title") == "New Chat":
            if message:
                sess["title"] = message[:60] + ("..." if len(message) > 60 else "")
            elif files:
                file_label = ", ".join(file_path.name for file_path in files[:2])
                if len(files) > 2:
                    file_label += f" +{len(files) - 2} more"
                sess["title"] = f"Files: {file_label}"
        sess["updated"] = datetime.now().isoformat()
        write_session(sess)
        if sess.get("messages"):
            user_msg = sess["messages"][-1]

        try:
            with scoped_profile_override(sess.get("profile")):
                use_api_server = request_plan["transport"] == chat_transport_api
                if use_api_server:
                    if sess.get("transport_mode") != chat_transport_api:
                        sess["transport_mode"] = chat_transport_api
                        sess["continuity_mode"] = chat_continuity_local
                        sess["transport_notice"] = request_plan["transport_notice"]
                        sess["hermes_session_id"] = None
                    api_msgs = []
                    for msg in messages_for_active_segment(sess):
                        payload = {"role": msg["role"], "content": msg["content"]}
                        if msg.get("files"):
                            payload["files"] = msg["files"]
                        api_msgs.append(payload)
                    response_text = call_api_server(
                        sess,
                        api_msgs,
                        sid,
                        files,
                        prefer_vision=active_segment_has_image_history(sess) or any(
                            file_path.suffix.lower() in image_extensions for file_path in files
                        ),
                        file_display_names=file_display_names,
                    )
                else:
                    sidecar_result = {}
                    if any(file_path.suffix.lower() in image_extensions for file_path in files) or vision_reanalysis_requested(message, sess):
                        sidecar_result = run_sidecar_vision_analysis(
                            sess,
                            message,
                            files,
                            user_message=user_msg,
                            file_display_names=file_display_names,
                        )
                    if sidecar_result:
                        prompt = compose_cli_prompt_with_sidecar(
                            sess,
                            message,
                            files,
                            sidecar_result=sidecar_result,
                            file_display_names=file_display_names,
                        )
                        response_text, hermes_session_id = call_hermes_prompt(
                            sess,
                            prompt,
                            request_id=request_id,
                        )
                    else:
                        response_text, hermes_session_id = call_hermes_direct(
                            sess,
                            message,
                            files,
                            request_id=request_id,
                            file_display_names=file_display_names,
                        )
                    sess["transport_mode"] = chat_transport_cli
                    effective_hermes_session_id = clean_hermes_session_id(hermes_session_id) or segment_hermes_session_id(active_segment)
                    if effective_hermes_session_id:
                        active_segment["hermes_session_id"] = effective_hermes_session_id
                        sess["hermes_session_id"] = effective_hermes_session_id
                        sess["continuity_mode"] = chat_continuity_hermes
                        sess["transport_notice"] = ""
                    else:
                        active_segment["hermes_session_id"] = None
                        sess["hermes_session_id"] = None
                        sess["continuity_mode"] = chat_continuity_limited
                        sess["transport_notice"] = (
                            "Hermes CLI did not return a resumable session id for this chat yet. "
                            "Follow-up turns may not preserve Hermes-side context."
                        )
            update_chat_request(request_id, status="completed")
        except chat_request_cancelled:
            rollback_failed_chat_turn(sess, sid, user_msg)
            update_chat_request(request_id, status="cancelled")
            logger.info(
                "Chat request cancelled request_id=%s session_id=%s duration_ms=%s",
                request_id,
                sid,
                int((time.monotonic() - chat_started_at) * 1000),
            )
            return jsonify({"ok": False, "cancelled": True, "session_id": sid}), 499
        except chat_backend_error as exc:
            rollback_failed_chat_turn(sess, sid, user_msg)
            update_chat_request(request_id, status="failed", error=str(exc))
            logger.warning(
                "Chat request failed request_id=%s session_id=%s duration_ms=%s detail=%s",
                request_id,
                sid,
                int((time.monotonic() - chat_started_at) * 1000),
                exc,
            )
            return jsonify({"error": str(exc), "request_id": request_id, "session_id": sid}), exc.status_code
        except Exception as exc:
            rollback_failed_chat_turn(sess, sid, user_msg)
            update_chat_request(request_id, status="failed", error=str(exc))
            logger.exception(
                "Unexpected chat request failure request_id=%s session_id=%s duration_ms=%s",
                request_id,
                sid,
                int((time.monotonic() - chat_started_at) * 1000),
            )
            return jsonify({"error": f"Unexpected chat error: {exc}", "request_id": request_id, "session_id": sid}), 500
        finally:
            debug_trace_lines = []
            if request_id:
                with scoped_profile_override(sess.get("profile")):
                    debug_trace_lines = debug_trace_lines_for_chat(request_id, sess.get("hermes_session_id"))
            remove_chat_request(request_id)

        assistant_msg = {
            "role": "assistant",
            "content": response_text,
            "timestamp": datetime.now().isoformat(),
            "segment_id": active_segment.get("id"),
            "segment_index": active_segment.get("index"),
            "profile": active_segment.get("profile") or sess.get("profile"),
            "transport": sess.get("transport_mode") or request_plan["transport"],
        }
        if debug_trace_lines:
            assistant_msg["debug_trace_lines"] = debug_trace_lines
            assistant_msg["debug_trace_transport"] = sess.get("transport_mode") or request_plan["transport"] or ""
            assistant_msg["debug_trace_status"] = "Completed"
            assistant_msg["show_debug_trace"] = True
        sess["messages"].append(assistant_msg)
        sess["updated"] = datetime.now().isoformat()
        write_session(sess)
        logger.info(
            "Chat request completed request_id=%s session_id=%s duration_ms=%s response_chars=%s transport=%s",
            request_id,
            sid,
            int((time.monotonic() - chat_started_at) * 1000),
            len(response_text),
            sess.get("transport_mode"),
        )
        session_meta = chat_session_meta(sess)
        return jsonify({
            "session_id": sid,
            "response": response_text,
            "message_count": len(sess["messages"]),
            "title": sess.get("title", ""),
            "cancel_supported": request_plan["cancel_supported"],
            "session": session_meta,
            "user_message": user_msg,
            "assistant_message": assistant_msg,
        })

    @app.route("/api/chat/cancel", methods=["POST"])
    @require_token
    @rate_limit
    def api_chat_cancel():
        data = request.get_json(silent=True) or {}
        request_id = (data.get("request_id") or "").strip()
        if not request_id:
            return jsonify({"error": "request_id is required"}), 400
        cancelled, detail = cancel_chat_request(request_id)
        status_code = 200 if cancelled else 409
        if detail == "Request not found":
            status_code = 404
        return jsonify({"cancelled": cancelled, "detail": detail, "request_id": request_id}), status_code

    @app.route("/api/upload", methods=["POST"])
    @require_token
    @rate_limit
    def api_upload():
        if request.content_length and request.content_length > max_request_body_size():
            raise RequestEntityTooLarge()
        if "file" not in request.files:
            return jsonify({"error": "No file"}), 400
        uploaded = request.files["file"]
        if not uploaded.filename:
            return jsonify({"error": "No filename"}), 400
        safe = secure_filename(uploaded.filename) or "file"
        unique = f"{uuid.uuid4().hex[:8]}_{safe}"
        target = upload_folder() / unique
        try:
            size = save_upload_stream(uploaded, target)
        except RequestEntityTooLarge:
            logger.warning(
                "Rejected oversized multipart upload request_id=%s name=%s content_length=%s remote=%s",
                request_id_or_dash(),
                safe,
                request.content_length,
                request.remote_addr,
            )
            raise
        logger.info(
            "Stored upload request_id=%s stored_as=%s name=%s size=%s type=%s remote=%s",
            request_id_or_dash(),
            unique,
            safe,
            size,
            uploaded.content_type,
            request.remote_addr,
        )
        return jsonify({
            "name": safe,
            "stored_as": unique,
            "size": size,
            "type": uploaded.content_type,
            "url": f"/uploads/{unique}",
        })

    @app.route("/api/upload/base64", methods=["POST"])
    @require_token
    @rate_limit
    def api_upload_base64():
        if request.content_length and request.content_length > max_request_body_size():
            raise RequestEntityTooLarge()
        data = request.get_json(silent=True) or {}
        b64 = data.get("data", "")
        if not b64:
            return jsonify({"error": "No data"}), 400
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        try:
            estimated_size = estimate_base64_decoded_size(b64)
        except ValueError:
            return jsonify({"error": "Invalid base64"}), 400
        if estimated_size > max_upload_size():
            logger.warning(
                "Rejected oversized base64 upload request_id=%s estimated_size=%s remote=%s",
                request_id_or_dash(),
                estimated_size,
                request.remote_addr,
            )
            return jsonify({"error": f"Too large (max {max_upload_size()//(1024*1024)}MB)"}), 400
        try:
            img_bytes = base64.b64decode(b64, validate=True)
        except Exception:
            return jsonify({"error": "Invalid base64"}), 400
        if len(img_bytes) > max_upload_size():
            return jsonify({"error": f"Too large (max {max_upload_size()//(1024*1024)}MB)"}), 400
        ext = secure_filename(str(data.get("ext", "png"))).lower().lstrip(".") or "png"
        if ext not in {"png", "jpg", "jpeg", "webp", "gif"}:
            ext = "png"
        unique = f"{uuid.uuid4().hex[:8]}_clipboard.{ext}"
        (upload_folder() / unique).write_bytes(img_bytes)
        logger.info(
            "Stored base64 upload request_id=%s stored_as=%s size=%s type=image/%s remote=%s",
            request_id_or_dash(),
            unique,
            len(img_bytes),
            "jpeg" if ext == "jpg" else ext,
            request.remote_addr,
        )
        return jsonify({
            "name": f"clipboard.{ext}",
            "stored_as": unique,
            "size": len(img_bytes),
            "type": f"image/{'jpeg' if ext == 'jpg' else ext}",
            "url": f"/uploads/{unique}",
        })

    @app.route("/uploads/<path:filename>")
    @require_token
    def serve_upload(filename):
        return send_from_directory(str(upload_folder()), filename)

    @app.route("/api/chat/sessions", methods=["GET"])
    @require_token
    def api_chat_sessions():
        sessions = []
        for _, session in load_all_sessions().items():
            meta = chat_session_meta(session)
            sessions.append({
                "id": session["id"],
                "title": session.get("title", "Untitled"),
                "message_count": len(session["messages"]),
                "created": session["created"],
                "updated": session.get("updated", session["created"]),
                "last_message": session["messages"][-1]["content"][:100] if session["messages"] else "",
                "session": meta,
            })
        sessions.sort(key=lambda item: item.get("updated", ""), reverse=True)
        return jsonify({"sessions": sessions})

    @app.route("/api/chat/folders", methods=["GET"])
    @require_token
    def api_chat_folders():
        sessions = load_all_sessions()
        return jsonify({"folders": folder_summaries(sessions)})

    @app.route("/api/chat/folders", methods=["POST"])
    @require_token
    def api_chat_folders_create():
        data = request.get_json() or {}
        folder_payload, errors = parse_folder_update(data)
        if errors:
            return jsonify({"error": "Invalid folder", "details": errors}), 400
        existing = folder_title_conflict(folder_payload["title"])
        if existing:
            return jsonify({"error": "Folder name already exists", "folder": existing}), 409
        sessions = load_all_sessions()
        legacy = legacy_folder_from_sessions(folder_payload["title"], sessions)
        if legacy:
            folder = write_folder({
                "id": legacy["id"],
                "title": folder_payload["title"],
                "created": legacy.get("created"),
                "updated": datetime.now().isoformat(),
                "workspace_roots": folder_payload["workspace_roots"] or legacy.get("workspace_roots") or [],
                "source_docs": folder_payload["source_docs"] or legacy.get("source_docs") or [],
            })
            summary = next((item for item in folder_summaries(sessions) if item["id"] == folder["id"]), None)
            return jsonify({"ok": True, "folder": summary or folder})
        now = datetime.now().isoformat()
        folder = write_folder({
            "id": str(uuid.uuid4())[:8],
            "title": folder_payload["title"],
            "created": now,
            "updated": now,
            "workspace_roots": folder_payload["workspace_roots"],
            "source_docs": folder_payload["source_docs"],
        })
        return jsonify({"ok": True, "folder": folder})

    @app.route("/api/chat/folders/<folder_id>", methods=["GET"])
    @require_token
    def api_chat_folder_get(folder_id):
        folder = folder_with_fallback(folder_id)
        if not folder:
            return jsonify({"error": "Folder not found"}), 404
        sessions = load_all_sessions()
        summary = next((item for item in folder_summaries(sessions) if item["id"] == folder["id"]), None)
        return jsonify({"folder": summary or folder})

    @app.route("/api/chat/folders/<folder_id>", methods=["PUT"])
    @require_token
    def api_chat_folder_update(folder_id):
        existing = folder_with_fallback(folder_id)
        if not existing:
            return jsonify({"error": "Folder not found"}), 404
        folder_payload, errors = parse_folder_update(request.get_json() or {}, existing=existing)
        if errors:
            return jsonify({"error": "Invalid folder", "details": errors}), 400
        conflict = folder_title_conflict(folder_payload["title"], exclude_folder_id=existing["id"])
        if conflict:
            return jsonify({"error": "Folder name already exists", "folder": conflict}), 409
        folder = write_folder({
            "id": existing["id"],
            "title": folder_payload["title"],
            "created": existing.get("created"),
            "updated": datetime.now().isoformat(),
            "workspace_roots": folder_payload["workspace_roots"],
            "source_docs": folder_payload["source_docs"],
        })
        summary = next((item for item in folder_summaries() if item["id"] == folder["id"]), None)
        return jsonify({"ok": True, "folder": summary or folder})

    @app.route("/api/chat/folders/<folder_id>", methods=["DELETE"])
    @require_token
    def api_chat_folder_delete(folder_id):
        sessions = load_all_sessions()
        folder = folder_with_fallback(folder_id, sessions)
        if not folder:
            return jsonify({"error": "Folder not found"}), 404
        moved_session_ids = []
        now = datetime.now().isoformat()
        folders = load_all_folders()
        for session in sessions.values():
            session_folder = resolve_folder_reference(session.get("folder_id"), sessions=sessions, folders=folders, include_legacy=False)
            if not session_folder or session_folder["id"] != folder["id"]:
                continue
            session["folder_id"] = ""
            session["updated"] = now
            write_session(session)
            moved_session_ids.append(session["id"])
        delete_folder(folder_id)
        return jsonify({
            "ok": True,
            "deleted_folder_id": folder_id,
            "moved_session_count": len(moved_session_ids),
            "moved_session_ids": moved_session_ids,
        })

    @app.route("/api/chat/folders/<folder_id>/sources/from-chat", methods=["POST"])
    @require_token
    def api_chat_folder_source_from_chat(folder_id):
        folder = folder_with_fallback(folder_id)
        if not folder:
            return jsonify({"error": "Folder not found"}), 404
        data = request.get_json() or {}
        session_id = str(data.get("session_id") or "").strip()
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        lines = [
            f"# {session.get('title') or 'Chat'}",
            "",
            f"Session ID: {session.get('id')}",
            "",
        ]
        for message in session.get("messages", []):
            role = "User" if message.get("role") == "user" else "Hermes"
            lines.append(f"## {role}")
            lines.append("")
            lines.append(message.get("content") or "")
            if message.get("files"):
                lines.append("")
                lines.append("Attachments: " + ", ".join(message.get("files") or []))
            lines.append("")
        safe_name = secure_filename(session.get("title") or session.get("id") or "chat-source") or "chat-source"
        target_path = folder_source_dir() / f"{folder_id}_{session.get('id')}_{safe_name}.md"
        target_path.write_text("\n".join(lines), encoding="utf-8")
        updated_sources = merge_unique_strings((folder.get("source_docs") or []), [str(target_path.resolve())])
        updated_workspace_roots = merge_unique_strings(
            folder.get("workspace_roots") or [],
            folder_workspace_roots_for_docs(updated_sources),
        )
        stored = write_folder({
            "id": folder["id"],
            "title": folder["title"],
            "created": folder.get("created"),
            "updated": datetime.now().isoformat(),
            "source_docs": updated_sources,
            "workspace_roots": updated_workspace_roots,
        })
        summary = next((item for item in folder_summaries() if item["id"] == stored["id"]), None)
        return jsonify({"ok": True, "folder": summary or stored, "source_path": str(target_path.resolve())})

    @app.route("/api/chat/sessions", methods=["POST"])
    @require_token
    def api_chat_sessions_create():
        data = request.get_json() or {}
        requested_profile = normalize_profile_name(data.get("profile") or "")
        if requested_profile and requested_profile not in available_profile_names():
            return jsonify({"error": "Invalid profile"}), 400
        session = get_or_create_chat_session(profile_name=requested_profile)
        context_update, errors = parse_chat_context_update(data)
        transport_preference, transport_notice = validated_transport_preference(data.get("transport_preference"))
        folder_id = context_update.get("folder_id") or ""
        if folder_id:
            ensured = ensure_folder_exists(folder_id)
            context_update["folder_id"] = ensured["id"] if ensured else folder_id
        if errors:
            delete_session_from_disk(session["id"])
            return jsonify({"error": "Invalid chat context", "details": errors}), 400
        session.update(context_update)
        session["transport_preference"] = transport_preference
        if transport_notice:
            session["transport_notice"] = transport_notice
        session["updated"] = datetime.now().isoformat()
        write_session(session)
        return jsonify({
            "ok": True,
            "session_id": session["id"],
            "title": session.get("title", ""),
            "session": chat_session_meta(session),
        })

    @app.route("/api/chat/sessions/<session_id>/messages", methods=["GET"])
    @require_token
    def api_chat_messages(session_id):
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        if trim_trailing_empty_chat_segments(session):
            write_session(session)
            session = load_session(session_id) or session
        return jsonify({"messages": session["messages"], "title": session.get("title", ""), "session": chat_session_meta(session)})

    @app.route("/api/chat/sessions/<session_id>/rename", methods=["POST"])
    @require_token
    def api_chat_rename(session_id):
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        data = request.get_json() or {}
        new_title = data.get("title", "").strip()
        if new_title:
            session["title"] = new_title
            session["updated"] = datetime.now().isoformat()
            write_session(session)
        return jsonify({"ok": True, "title": session.get("title", "")})

    @app.route("/api/chat/sessions/<session_id>/context", methods=["PUT"])
    @require_token
    def api_chat_context_update(session_id):
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        context_update, errors = parse_chat_context_update(request.get_json() or {})
        folder_id = context_update.get("folder_id") or ""
        if folder_id:
            ensured = ensure_folder_exists(folder_id)
            context_update["folder_id"] = ensured["id"] if ensured else folder_id
        if errors:
            return jsonify({"error": "Invalid chat context", "details": errors}), 400
        session.update(context_update)
        session["updated"] = datetime.now().isoformat()
        write_session(session)
        return jsonify({"ok": True, "session": chat_session_meta(session)})

    @app.route("/api/chat/sessions/<session_id>/transport", methods=["PUT"])
    @require_token
    def api_chat_session_transport_update(session_id):
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        data = request.get_json() or {}
        requested = str(data.get("transport_preference") or "").strip().lower()
        if requested not in ("", "auto", chat_transport_cli, chat_transport_api):
            return jsonify({"error": "Invalid transport preference"}), 400
        session["transport_preference"], session["transport_notice"] = validated_transport_preference(requested)
        session["updated"] = datetime.now().isoformat()
        write_session(session)
        return jsonify({"ok": True, "session": chat_session_meta(session)})

    @app.route("/api/chat/sessions/<session_id>/profile", methods=["PUT"])
    @require_token
    def api_chat_session_profile_update(session_id):
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        data = request.get_json() or {}
        requested_profile = normalize_profile_name(data.get("profile"))
        if requested_profile not in available_profile_names():
            return jsonify({"error": "Invalid profile"}), 400
        selected = requested_profile
        segment = append_chat_segment(session, selected)
        segment["hermes_session_id"] = None
        session["profile"] = selected
        session["transport_mode"] = None
        session["continuity_mode"] = None
        session["hermes_session_id"] = None
        session["transport_notice"] = (
            f"Switched to Hermes profile {selected}. "
            "Next messages in this chat will use that profile with a fresh Hermes turn."
        )
        session["updated"] = datetime.now().isoformat()
        write_session(session)
        return jsonify({"ok": True, "selected": selected, "session": chat_session_meta(session)})

    @app.route("/api/chat/sessions/<session_id>/folder", methods=["PUT"])
    @require_token
    def api_chat_session_folder_update(session_id):
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        data = request.get_json() or {}
        folder_id = str(data.get("folder_id") or "").strip()
        if folder_id:
            ensured = ensure_folder_exists(folder_id)
            folder_id = ensured["id"] if ensured else folder_id
        session["folder_id"] = folder_id
        session["updated"] = datetime.now().isoformat()
        write_session(session)
        return jsonify({"ok": True, "session": chat_session_meta(session)})

    @app.route("/api/chat/sessions/<session_id>/delete", methods=["POST"])
    @require_token
    def api_chat_delete(session_id):
        if not load_session(session_id):
            return jsonify({"ok": False, "error": "Session not found"}), 404
        delete_session_from_disk(session_id)
        return jsonify({"ok": True})

    @app.route("/api/chat/sessions/<session_id>/clear", methods=["POST"])
    @require_token
    def api_chat_clear(session_id):
        session = load_session(session_id)
        if not session:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        session["messages"] = []
        session["updated"] = datetime.now().isoformat()
        session["hermes_session_id"] = None
        for segment in session.get("segments") or []:
            if isinstance(segment, dict):
                segment["hermes_session_id"] = None
        session["transport_mode"] = None
        session["continuity_mode"] = None
        session["transport_notice"] = ""
        write_session(session)
        return jsonify({"ok": True, "session": chat_session_meta(session)})

    @app.route("/api/chat/status", methods=["GET"])
    @require_token
    def api_chat_status():
        request_id = str(request.args.get("request_id") or "").strip()
        if request_id:
            payload = read_request_control(request_id)
            if payload is None:
                return jsonify({"error": "Request not found", "request_id": request_id}), 404
            response = jsonify({
                "request_id": request_id,
                "status": payload.get("status") or "running",
                "transport": payload.get("transport") or "",
                "cancel_supported": bool(payload.get("cancel_supported")),
                "session_id": payload.get("session_id") or "",
                "created_at": payload.get("created_at") or "",
                "updated_at": payload.get("updated_at") or "",
                "progress_lines": filter_live_progress_lines(request_progress_lines(request_id)),
                "error": payload.get("error") or "",
            })
            response.headers["Cache-Control"] = "no-store, no-cache, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response
        api_server = check_api_server()
        default_api_ok, default_api_reason, default_api_probe = api_server_probe(timeout=2)
        image_support, image_reason = image_attachment_support_status()
        vision_ready, vision_reason = vision_configured()
        vision_target = resolve_api_target(prefer_vision=True)
        runtime = chat_runtime_status()
        api_selectable = api_server and not runtime.get("requires_cli")
        return jsonify({
            "api_server": api_server,
            "api_url": effective_hermes_api_url(default_hermes_api_url()),
            "profile": selected_profile_name(),
            "api_probe": {
                "reachable": default_api_ok,
                "reason": default_api_reason,
                "probe": default_api_probe,
            },
            "capabilities": {
                "text_attachments": True,
                "image_attachments": image_support,
                "audio_attachments": False,
            },
            "capability_reasons": {
                "image_attachments": image_reason,
            },
            "request_lifecycle": {
                "chat_timeout_seconds": chat_request_timeout(),
                "server_timeout_seconds": chat_server_timeout(),
                "cancel_supported": {
                    chat_transport_cli: True,
                    chat_transport_api: False,
                },
                "continuity": {
                    chat_transport_cli: chat_continuity_hermes,
                    chat_transport_api: chat_continuity_local,
                },
            },
            "limits": {
                "max_upload_bytes": max_upload_size(),
                "max_request_body_bytes": max_request_body_size(),
            },
            "debug": {
                "persist_trace": chat_persist_debug_trace(),
            },
            "transport_policy": {
                "requires_cli": runtime.get("requires_cli"),
                "api_selectable": api_selectable,
                "reason": runtime.get("cli_reason") or "",
                "reasons": runtime.get("reasons") or [],
            },
            "runtime": runtime,
            "readiness": {
                "screenshots_ready": image_support,
                "vision_sidecar_ready": image_support,
                "vision_configured": vision_ready,
                "vision_reason": vision_reason,
                "vision_api_url": vision_target.get("base_url") or effective_hermes_api_url(default_hermes_api_url()),
                "vision_model": vision_target.get("model") if vision_ready else "",
                "api_reachable": default_api_ok,
                "api_reason": default_api_reason,
            },
        })