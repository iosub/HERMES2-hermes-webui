from __future__ import annotations

from flask import jsonify, request


def register_config_routes(
    app,
    *,
    require_token,
    http_error,
    cfg_get,
    cfg_get_raw,
    cfg_update,
    cfg_load,
    preserve_masked_secret_updates,
    selected_hermes_profile_payload,
    set_selected_hermes_profile_name,
    profile_api_token_metadata,
    normalize_hermes_profile_name,
    available_hermes_profile_names,
    profile_api_gateway_url,
    api_url_port,
    repo_env_path,
    dotenv_values_fn,
    api_token_repo_keys_for_port,
    unset_key_fn,
    set_env_value,
) -> None:
    @app.route("/api/config", methods=["GET"])
    @require_token
    def api_config_get():
        try:
            return jsonify(cfg_get())
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/config/<section>", methods=["GET"])
    @require_token
    def api_config_get_section(section):
        try:
            return jsonify(cfg_get(section))
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/config/<section>", methods=["PUT"])
    @require_token
    def api_config_put_section(section):
        try:
            data = request.get_json(force=True)
            current = cfg_get_raw(section)
            data = preserve_masked_secret_updates(current, data)
            cfg_update(section, data)
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"ok": False, "errors": [str(exc)]}), 500

    @app.route("/api/config/reload", methods=["POST"])
    @require_token
    def api_config_reload():
        try:
            cfg_load()
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/runtime/profiles", methods=["GET"])
    @require_token
    def api_runtime_profiles_get():
        try:
            cfg_load()
            return jsonify(selected_hermes_profile_payload())
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/runtime/profiles", methods=["PUT"])
    @require_token
    def api_runtime_profiles_put():
        try:
            data = request.get_json(force=True) or {}
            selected = set_selected_hermes_profile_name(data.get("profile") or "default")
            cfg_load()
            return jsonify({"ok": True, **selected_hermes_profile_payload(), "selected": selected})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/runtime/profiles/<profile_name>/api-token", methods=["GET"])
    @require_token
    def api_runtime_profile_api_token_get(profile_name):
        try:
            return jsonify({"ok": True, **profile_api_token_metadata(profile_name)})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/runtime/profiles/<profile_name>/api-token", methods=["PUT"])
    @require_token
    def api_runtime_profile_api_token_put(profile_name):
        try:
            normalized = normalize_hermes_profile_name(profile_name)
            if normalized not in available_hermes_profile_names():
                raise ValueError(f"Unknown Hermes profile: {normalized}")
            data = request.get_json(force=True) or {}
            token = str(data.get("token") or "").strip()
            api_url = profile_api_gateway_url(normalized)
            port = api_url_port(api_url)
            env_path = repo_env_path()
            env_path.parent.mkdir(parents=True, exist_ok=True)
            raw = dotenv_values_fn(str(env_path)) if env_path.exists() else {}
            for key in api_token_repo_keys_for_port(port):
                if raw.get(key) is not None:
                    unset_key_fn(str(env_path), key)
            if token:
                key_name = f"HERMES_API_TOKEN_PORT_{port}" if port else "HERMES_API_TOKEN"
                set_env_value(env_path, key_name, token)
            return jsonify({"ok": True, **profile_api_token_metadata(normalized)})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return http_error(str(exc))