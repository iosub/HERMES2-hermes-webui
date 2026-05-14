from __future__ import annotations

from flask import jsonify, request


def register_capability_routes(
    app,
    *,
    require_token,
    http_error,
    preview_skill_capability,
    apply_skill_capability,
    preview_integration_capability,
    apply_integration_capability,
    preview_agent_preset_capability,
    apply_agent_preset_capability,
) -> None:
    @app.route("/api/capabilities/preview", methods=["POST"])
    @require_token
    def api_capabilities_preview():
        try:
            data = request.get_json(force=True) or {}
            capability_type = str(data.get("type") or "").strip().lower()
            draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
            if capability_type == "skill":
                payload, status = preview_skill_capability(draft)
                return jsonify(payload), status
            if capability_type == "integration":
                payload, status = preview_integration_capability(draft)
                return jsonify(payload), status
            if capability_type == "agent_preset":
                payload, status = preview_agent_preset_capability(draft)
                return jsonify(payload), status
            return jsonify({"ok": False, "error": "Capability type is required"}), 400
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/capabilities/apply", methods=["POST"])
    @require_token
    def api_capabilities_apply():
        try:
            data = request.get_json(force=True) or {}
            capability_type = str(data.get("type") or "").strip().lower()
            draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
            preview_token = str(data.get("preview_token") or "").strip()
            if capability_type == "skill":
                payload, status = apply_skill_capability(draft, preview_token)
                return jsonify(payload), status
            if capability_type == "integration":
                payload, status = apply_integration_capability(draft, preview_token)
                return jsonify(payload), status
            if capability_type == "agent_preset":
                payload, status = apply_agent_preset_capability(draft, preview_token)
                return jsonify(payload), status
            return jsonify({"ok": False, "error": "Capability type is required"}), 400
        except Exception as exc:
            return http_error(str(exc))