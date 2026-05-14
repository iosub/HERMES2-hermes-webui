from __future__ import annotations

from flask import jsonify, request


def register_model_role_routes(
    app,
    *,
    require_token,
    http_error,
    cfg_mask_secrets,
    cfg_update,
    provider_usage_map,
    available_provider_profiles,
    provider_env_api_key,
    provider_display_name,
    model_role_info,
    model_role_labels,
    profile_payload_for_role,
    chat_backend_error_cls,
) -> None:
    @app.route("/api/model-roles", methods=["GET"])
    @require_token
    def api_model_roles_get():
        try:
            profiles = []
            usage_map = provider_usage_map()
            for profile in available_provider_profiles():
                safe = cfg_mask_secrets(profile)
                safe["used_by"] = usage_map.get(profile.get("name", ""), [])
                safe["has_api_key"] = bool(profile.get("api_key") or provider_env_api_key(profile.get("provider")))
                safe["provider_label"] = provider_display_name(profile.get("provider", ""))
                profiles.append(safe)
            return jsonify({
                "profiles": profiles,
                "roles": {
                    "primary": model_role_info("primary"),
                    "fallback": model_role_info("fallback"),
                    "vision": model_role_info("vision"),
                },
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/model-roles/<role>", methods=["PUT"])
    @require_token
    def api_model_roles_update(role):
        try:
            role = str(role or "").strip().lower()
            if role not in model_role_labels:
                return jsonify({"ok": False, "error": f"Unknown role '{role}'"}), 404

            data = request.get_json(force=True) or {}
            profile_name = str(data.get("profile") or "").strip()
            model_name = str(data.get("model") or "").strip()
            routing_provider = str(data.get("routing_provider") or "").strip()

            if role == "primary":
                if not profile_name or not model_name:
                    return jsonify({"ok": False, "error": "Primary Chat requires both a provider profile and a model"}), 400
                profile_payload = profile_payload_for_role(profile_name, model_name, routing_provider)
                cfg_update("model", {
                    "default_profile": profile_payload["profile"],
                    "default_provider": profile_payload["provider"],
                    "default_model": profile_payload["model"],
                    "base_url": profile_payload["base_url"],
                    "api_key": profile_payload["api_key"],
                    "routing_provider": profile_payload["routing_provider"],
                })
                return jsonify({"ok": True})

            if not profile_name or not model_name:
                if role == "fallback":
                    cfg_update("model", {
                        "fallback_profile": "",
                        "fallback_provider": "",
                        "fallback_model": "",
                        "fallback_base_url": "",
                        "fallback_api_key": "",
                        "fallback_routing_provider": "",
                    })
                    return jsonify({"ok": True})
                if role == "vision":
                    cfg_update("auxiliary", {
                        "vision": {
                            "profile": "",
                            "provider": "auto",
                            "model": "",
                            "base_url": "",
                            "api_key": "",
                            "routing_provider": "",
                        }
                    })
                    return jsonify({"ok": True})

            profile_payload = profile_payload_for_role(profile_name, model_name, routing_provider)
            if role == "fallback":
                cfg_update("model", {
                    "fallback_profile": profile_payload["profile"],
                    "fallback_provider": profile_payload["provider"],
                    "fallback_model": profile_payload["model"],
                    "fallback_base_url": profile_payload["base_url"],
                    "fallback_api_key": profile_payload["api_key"],
                    "fallback_routing_provider": profile_payload["routing_provider"],
                })
                return jsonify({"ok": True})

            cfg_update("auxiliary", {
                "vision": {
                    "profile": profile_payload["profile"],
                    "provider": profile_payload["provider"],
                    "model": profile_payload["model"],
                    "base_url": profile_payload["base_url"],
                    "api_key": profile_payload["api_key"],
                    "routing_provider": profile_payload["routing_provider"],
                }
            })
            return jsonify({"ok": True})
        except chat_backend_error_cls as exc:
            return jsonify({"ok": False, "error": str(exc)}), exc.status_code
        except Exception as exc:
            return http_error(str(exc))