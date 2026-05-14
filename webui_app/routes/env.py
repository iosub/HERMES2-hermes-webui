from __future__ import annotations

from flask import jsonify, request


def register_env_routes(
    app,
    *,
    require_token,
    http_error,
    selected_env_path,
    dotenv_values_fn,
    mask_value,
    discover_skill_entries,
    skill_env_var_presets,
    classify_env_key,
    env_var_metadata,
    env_presets_by_group,
    env_group_help,
    set_env_value,
    unset_key_fn,
) -> None:
    @app.route("/api/env", methods=["GET"])
    @require_token
    def api_env_get():
        try:
            env_path = selected_env_path()
            raw = dotenv_values_fn(str(env_path)) if env_path.exists() else {}
            masked = {k: mask_value(k, v) for k, v in raw.items() if v is not None}
            skills = discover_skill_entries()
            dynamic_presets = skill_env_var_presets(skills)

            groups: dict[str, list[str]] = {}
            for key in masked:
                group = classify_env_key(key)
                groups.setdefault(group, []).append(key)

            metadata = {
                key: dynamic_presets.get(key) or env_var_metadata(key)
                for key in masked
            }
            presets = env_presets_by_group()
            for key, meta in dynamic_presets.items():
                group = meta.get("group") or classify_env_key(key)
                bucket = presets.setdefault(group, [])
                if any(str(item.get("key") or "").strip() == key for item in bucket if isinstance(item, dict)):
                    continue
                bucket.append(meta)
            for values in presets.values():
                values.sort(key=lambda item: str(item.get("label") or item.get("key") or "").casefold())
            return jsonify({
                "vars": masked,
                "groups": groups,
                "metadata": metadata,
                "group_help": env_group_help,
                "presets": presets,
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/env", methods=["POST"])
    @require_token
    def api_env_set():
        try:
            data = request.get_json(force=True)
            key, value = data.get("key"), data.get("value")
            if not key:
                return jsonify({"ok": False, "error": "key is required"}), 400
            env_path = selected_env_path()
            env_path.parent.mkdir(parents=True, exist_ok=True)
            set_env_value(env_path, key, value or "")
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/env/<key>", methods=["PUT"])
    @require_token
    def api_env_update(key):
        try:
            data = request.get_json(force=True)
            value = data.get("value", "")
            env_path = selected_env_path()
            current = dotenv_values_fn(str(env_path)).get(key) if env_path.exists() else None
            if (
                isinstance(value, str)
                and isinstance(current, str)
                and current
                and value == mask_value(key, current)
            ):
                return jsonify({"ok": True})
            set_env_value(env_path, key, value)
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/env/<key>", methods=["DELETE"])
    @require_token
    def api_env_delete(key):
        try:
            unset_key_fn(str(selected_env_path()), key)
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))