from __future__ import annotations

from flask import jsonify, request


def register_skill_routes(
    app,
    *,
    require_token,
    http_error,
    discover_skill_entries,
    skill_request_paths,
    skill_apply_action,
    safe_skill_rel_path,
) -> None:
    @app.route("/api/skills", methods=["GET"])
    @require_token
    def api_skills_get():
        try:
            return jsonify({"skills": discover_skill_entries()})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/skills/<path:name>/toggle", methods=["POST"])
    @require_token
    def api_skill_toggle(name):
        try:
            info = skill_request_paths(name)
            if not info:
                return jsonify({"ok": False, "error": "Skill path is required"}), 400
            action = "disable" if info["base_path"].exists() else "enable"
            result = skill_apply_action(name, action)
            if not result.get("found"):
                return jsonify({"ok": False, "error": result.get("error") or f"Skill '{name}' not found"}), 404
            return jsonify({
                "ok": True,
                "enabled": bool(result.get("enabled")),
                "changed": bool(result.get("changed")),
                "path": result.get("path") or info.get("requested_rel"),
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/skills/bulk", methods=["POST"])
    @require_token
    def api_skill_bulk():
        try:
            data = request.get_json(force=True) or {}
            action = str(data.get("action") or "").strip().lower()
            if action not in {"enable", "disable", "remove"}:
                return jsonify({"ok": False, "error": "Unsupported bulk action"}), 400

            raw_paths = data.get("paths") if isinstance(data.get("paths"), list) else []
            paths = []
            seen = set()
            for entry in raw_paths:
                rel_path = safe_skill_rel_path(entry)
                if rel_path and rel_path not in seen:
                    paths.append(rel_path)
                    seen.add(rel_path)
            if not paths:
                return jsonify({"ok": False, "error": "At least one skill path is required"}), 400

            results = []
            changed_paths = []
            missing_paths = []
            removed_paths = []
            for rel_path in paths:
                result = skill_apply_action(rel_path, action)
                result["requested"] = rel_path
                results.append(result)
                if not result.get("found"):
                    missing_paths.append(rel_path)
                    continue
                if result.get("changed"):
                    changed_paths.append(result.get("path") or rel_path)
                if result.get("removed"):
                    removed_paths.append(result.get("path") or rel_path)

            return jsonify({
                "ok": True,
                "action": action,
                "results": results,
                "changed_count": len(changed_paths),
                "changed_paths": changed_paths,
                "removed_paths": removed_paths,
                "missing_paths": missing_paths,
                "skills": discover_skill_entries(),
            })
        except Exception as exc:
            return http_error(str(exc))