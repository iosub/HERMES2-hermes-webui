from __future__ import annotations

import copy

from flask import jsonify, request


def register_agent_routes(
    app,
    *,
    require_token,
    http_error,
    cfg_get_raw,
    cfg_set,
    cfg_mask_secrets,
    agent_defaults,
    agent_personality_entries,
    personality_entry_for_api,
    normalize_personality_value,
    deep_merge,
) -> None:
    @app.route("/api/agents", methods=["GET"])
    @require_token
    def api_agents_get():
        try:
            raw = cfg_get_raw()
            agent_cfg = agent_defaults(raw)
            personalities, _ = agent_personality_entries(raw)
            entries = [
                personality_entry_for_api(name, value)
                for name, value in sorted(personalities.items(), key=lambda item: str(item[0]).casefold())
            ]
            result = {
                "defaults": cfg_mask_secrets({key: value for key, value in agent_cfg.items() if key != "personalities"}),
                "personalities": cfg_mask_secrets(personalities),
                "entries": entries,
            }
            return jsonify(result)
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/agents", methods=["POST"])
    @require_token
    def api_agents_add():
        try:
            data = request.get_json(force=True)
            name = data.get("name")
            if not name:
                return jsonify({"ok": False, "error": "name is required"}), 400

            raw = cfg_get_raw()
            personalities, _ = agent_personality_entries(raw)
            if name in personalities:
                return jsonify({"ok": False, "error": f"Agent '{name}' already exists"}), 409

            agent_cfg = raw.get("agent", {})
            if not isinstance(agent_cfg, dict):
                agent_cfg = {}
            nested = agent_cfg.get("personalities", {})
            if not isinstance(nested, dict):
                nested = {}
            nested[name] = normalize_personality_value({key: value for key, value in data.items() if key != "name"})
            agent_cfg["personalities"] = nested
            cfg_set("agent", agent_cfg)
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/agents/<name>", methods=["PUT"])
    @require_token
    def api_agents_update(name):
        try:
            data = request.get_json(force=True)
            raw = cfg_get_raw()
            personalities, storage = agent_personality_entries(raw)
            if name not in personalities:
                return jsonify({"ok": False, "error": f"Agent '{name}' not found"}), 404

            if storage.get(name) == "legacy":
                legacy = raw.get("personalities", {})
                if not isinstance(legacy, dict):
                    legacy = {}
                current = legacy.get(name, "")
                merged = deep_merge(current, data) if isinstance(current, dict) else {
                    **data,
                    "system_prompt": str(data.get("system_prompt") or data.get("prompt") or current or ""),
                }
                legacy[name] = normalize_personality_value(merged)
                cfg_set("personalities", legacy)
            else:
                agent_cfg = raw.get("agent", {})
                if not isinstance(agent_cfg, dict):
                    agent_cfg = {}
                nested = agent_cfg.get("personalities", {})
                if not isinstance(nested, dict):
                    nested = {}
                current = nested.get(name, "")
                merged = deep_merge(current, data) if isinstance(current, dict) else {
                    **data,
                    "system_prompt": str(data.get("system_prompt") or data.get("prompt") or current or ""),
                }
                nested[name] = normalize_personality_value(merged)
                agent_cfg["personalities"] = nested
                cfg_set("agent", agent_cfg)
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/agents/<name>", methods=["DELETE"])
    @require_token
    def api_agents_delete(name):
        try:
            raw = cfg_get_raw()
            personalities, storage = agent_personality_entries(raw)
            if name not in personalities:
                return jsonify({"ok": False, "error": f"Agent '{name}' not found"}), 404

            if storage.get(name) == "legacy":
                legacy = raw.get("personalities", {})
                if not isinstance(legacy, dict):
                    legacy = {}
                legacy.pop(name, None)
                cfg_set("personalities", legacy)
            else:
                agent_cfg = raw.get("agent", {})
                if not isinstance(agent_cfg, dict):
                    agent_cfg = {}
                nested = agent_cfg.get("personalities", {})
                if not isinstance(nested, dict):
                    nested = {}
                nested.pop(name, None)
                agent_cfg["personalities"] = nested
                cfg_set("agent", agent_cfg)
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/agents/<name>/duplicate", methods=["POST"])
    @require_token
    def api_agents_duplicate(name):
        try:
            data = request.get_json(force=True)
            new_name = data.get("new_name")
            if not new_name:
                return jsonify({"ok": False, "error": "new_name is required"}), 400

            raw = cfg_get_raw()
            personalities, storage = agent_personality_entries(raw)
            if name not in personalities:
                return jsonify({"ok": False, "error": f"Agent '{name}' not found"}), 404
            if new_name in personalities:
                return jsonify({"ok": False, "error": f"Agent '{new_name}' already exists"}), 409

            if storage.get(name) == "legacy":
                legacy = raw.get("personalities", {})
                if not isinstance(legacy, dict):
                    legacy = {}
                legacy[new_name] = copy.deepcopy(legacy.get(name))
                cfg_set("personalities", legacy)
            else:
                agent_cfg = raw.get("agent", {})
                if not isinstance(agent_cfg, dict):
                    agent_cfg = {}
                nested = agent_cfg.get("personalities", {})
                if not isinstance(nested, dict):
                    nested = {}
                nested[new_name] = copy.deepcopy(nested.get(name))
                agent_cfg["personalities"] = nested
                cfg_set("agent", agent_cfg)
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))