from __future__ import annotations

import json
import time

from flask import jsonify, request


def register_provider_routes(
    app,
    *,
    require_token,
    rate_limit,
    http_error,
    cfg_get_raw,
    cfg_set,
    cfg_mask_secrets,
    normalized_model_config,
    custom_provider_profiles,
    role_linked_profile_name,
    provider_usage_map,
    provider_display_name,
    provider_presets,
    auxiliary_model_keys,
    normalize_provider_profile,
    preserve_masked_secret_updates,
    deep_merge,
    sync_linked_provider_roles,
    get_provider_profile,
    resolve_role_target,
    build_openai_api_url,
    api_server_headers,
    summarize_upstream_error_detail,
    model_role_info,
    openrouter_discovery_models,
    normalize_provider_type,
    openrouter_discovery_endpoints,
) -> None:
    def _get_providers_info():
        raw = cfg_get_raw()
        model_cfg = normalized_model_config()
        custom = custom_provider_profiles(raw)

        default = {
            "profile": role_linked_profile_name("primary", model_cfg=model_cfg, raw=raw),
            "provider": model_cfg.get("default_provider", ""),
            "model": model_cfg.get("default_model", ""),
            "base_url": model_cfg.get("base_url", ""),
            "routing_provider": model_cfg.get("routing_provider", ""),
        }

        auxiliary = {}
        for aux_key in auxiliary_model_keys:
            value = model_cfg.get(aux_key)
            if value:
                if isinstance(value, str):
                    auxiliary[aux_key] = {"model": value}
                elif isinstance(value, dict):
                    auxiliary[aux_key] = value

        return default, custom, auxiliary

    @app.route("/api/providers", methods=["GET"])
    @require_token
    def api_providers_get():
        try:
            default, custom, auxiliary = _get_providers_info()
            usage_map = provider_usage_map()
            safe_custom = []
            for profile in custom:
                safe = cfg_mask_secrets(profile)
                safe["used_by"] = usage_map.get(profile.get("name", ""), [])
                safe["has_api_key"] = bool(profile.get("api_key"))
                safe["provider_label"] = provider_display_name(profile.get("provider", ""))
                safe_custom.append(safe)
            safe_aux = cfg_mask_secrets(auxiliary)
            for cfg_value in safe_aux.values():
                if isinstance(cfg_value, dict):
                    cfg_value["provider_label"] = provider_display_name(cfg_value.get("provider", ""))
            return jsonify({
                "default": {
                    **default,
                    "provider_label": provider_display_name(default.get("provider", "")),
                },
                "custom": safe_custom,
                "auxiliary": safe_aux,
                "presets": provider_presets,
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/models", methods=["GET"])
    @require_token
    def api_models_get():
        try:
            raw = cfg_get_raw()
            model_cfg = normalized_model_config()
            custom = custom_provider_profiles(raw)

            all_models = []
            seen = set()

            def _add(provider_name, model_name):
                key = (provider_name, model_name)
                if model_name and key not in seen:
                    seen.add(key)
                    all_models.append({"provider": provider_name, "model": model_name})

            _add(model_cfg.get("default_provider", "default"), model_cfg.get("default_model", ""))
            _add(model_cfg.get("fallback_provider") or model_cfg.get("default_provider", "default"), model_cfg.get("fallback_model", ""))

            for profile in custom:
                _add(profile.get("name", ""), profile.get("model", ""))

            for aux_key in auxiliary_model_keys:
                value = model_cfg.get(aux_key)
                if isinstance(value, str):
                    _add(aux_key, value)
                elif isinstance(value, dict):
                    _add(aux_key, value.get("model", ""))

            return jsonify({
                "default_model": model_cfg.get("default_model", ""),
                "default_provider": model_cfg.get("default_provider", ""),
                "default_profile": model_cfg.get("default_profile", ""),
                "fallback_model": model_cfg.get("fallback_model", ""),
                "fallback_provider": model_cfg.get("fallback_provider", ""),
                "fallback_profile": model_cfg.get("fallback_profile", ""),
                "all_models": all_models,
                "roles": {
                    "primary": model_role_info("primary"),
                    "fallback": model_role_info("fallback"),
                    "vision": model_role_info("vision"),
                },
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/providers", methods=["POST"])
    @require_token
    def api_providers_add():
        try:
            data = normalize_provider_profile(request.get_json(force=True))
            name = data.get("name")
            if not name:
                return jsonify({"ok": False, "error": "name is required"}), 400

            raw = cfg_get_raw()
            custom = custom_provider_profiles(raw)
            for profile in custom:
                if profile.get("name") == name:
                    return jsonify({"ok": False, "error": f"Provider '{name}' already exists"}), 409

            custom.append(data)
            cfg_set("custom_providers", custom)
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/providers/<name>", methods=["PUT"])
    @require_token
    def api_providers_update(name):
        try:
            data = request.get_json(force=True)
            raw = cfg_get_raw()
            custom = custom_provider_profiles(raw)
            found = False
            for index, profile in enumerate(custom):
                if profile.get("name") == name:
                    sanitized = preserve_masked_secret_updates(profile, data)
                    merged = deep_merge(profile, sanitized)
                    merged["name"] = name
                    custom[index] = normalize_provider_profile(merged)
                    found = True
                    break
            if not found:
                return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404
            cfg_set("custom_providers", custom)
            sync_linked_provider_roles(name, custom[index])
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/providers/<name>", methods=["DELETE"])
    @require_token
    def api_providers_delete(name):
        try:
            raw = cfg_get_raw()
            usage_map = provider_usage_map(raw=raw)
            if usage_map.get(name):
                used_by = ", ".join(usage_map.get(name, []))
                return jsonify({"ok": False, "error": f"Provider '{name}' is still used by {used_by}"}), 409
            custom = custom_provider_profiles(raw)
            new_custom = [profile for profile in custom if profile.get("name") != name]
            if len(new_custom) == len(custom):
                return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404
            cfg_set("custom_providers", new_custom)
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/providers/<name>/test", methods=["POST"])
    @require_token
    @rate_limit
    def api_providers_test(name):
        try:
            import urllib.error
            import urllib.request

            raw = cfg_get_raw()
            model_cfg = normalized_model_config()
            provider_cfg = get_provider_profile(name, raw)

            if not provider_cfg:
                if role_linked_profile_name("primary", model_cfg=model_cfg, raw=raw) == name:
                    provider_cfg = resolve_role_target("primary")
                else:
                    return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404

            base_url = (provider_cfg.get("base_url") or "").rstrip("/")
            model = provider_cfg.get("model", "gpt-3.5-turbo")
            provider_type = provider_cfg.get("provider", "")
            if not base_url:
                return jsonify({"ok": False, "error": "Base URL is required to test this provider"}), 200
            if not model:
                return jsonify({"ok": False, "error": "Suggested model is required to test this provider"}), 200

            url = build_openai_api_url(base_url, "chat/completions")
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 5,
            }).encode("utf-8")

            headers = api_server_headers(provider_cfg.get("api_key"), provider_type)
            headers["Content-Type"] = "application/json"

            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            start = time.time()
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    latency = int((time.time() - start) * 1000)
                    return jsonify({"ok": True, "latency_ms": latency, "response": body[:200]})
            except urllib.error.HTTPError as exc:
                latency = int((time.time() - start) * 1000)
                body = exc.read().decode("utf-8", errors="replace")
                detail = summarize_upstream_error_detail(body, str(exc.reason))[:300]
                return jsonify({"ok": False, "error": f"HTTP {exc.code}: {detail}", "latency_ms": latency}), 200
            except urllib.error.URLError as exc:
                latency = int((time.time() - start) * 1000)
                return jsonify({"ok": False, "error": str(exc.reason), "latency_ms": latency}), 200

        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/api/providers/<name>/discovery/models", methods=["GET"])
    @require_token
    def api_provider_discovery_models(name):
        try:
            profile = get_provider_profile(name)
            if not profile:
                return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404
            vision_only = request.args.get("vision_only", "").lower() in ("1", "true", "yes")
            if str(profile.get("provider") or "").strip().lower() != "openrouter":
                return jsonify({
                    "supported": False,
                    "provider": profile.get("provider", ""),
                    "models": [],
                    "reason": "Live model discovery is only available for OpenRouter profiles right now",
                })
            return jsonify({
                "supported": True,
                "provider": profile.get("provider", ""),
                "models": openrouter_discovery_models(vision_only=vision_only),
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/provider-types/<provider>/discovery/models", methods=["GET"])
    @require_token
    def api_provider_type_discovery_models(provider):
        try:
            provider = normalize_provider_type(provider or "")
            vision_only = request.args.get("vision_only", "").lower() in ("1", "true", "yes")
            if provider != "openrouter":
                return jsonify({
                    "supported": False,
                    "provider": provider,
                    "models": [],
                    "reason": "Live model discovery is only available for OpenRouter right now",
                })
            return jsonify({
                "supported": True,
                "provider": provider,
                "models": openrouter_discovery_models(vision_only=vision_only),
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/providers/<name>/discovery/endpoints", methods=["GET"])
    @require_token
    def api_provider_discovery_endpoints(name):
        try:
            profile = get_provider_profile(name)
            if not profile:
                return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404
            model_id = str(request.args.get("model") or "").strip()
            if not model_id:
                return jsonify({"supported": False, "endpoints": [], "reason": "model is required"}), 400
            if str(profile.get("provider") or "").strip().lower() != "openrouter":
                return jsonify({
                    "supported": False,
                    "provider": profile.get("provider", ""),
                    "endpoints": [],
                    "reason": "Live endpoint discovery is only available for OpenRouter profiles right now",
                })
            return jsonify({
                "supported": True,
                "provider": profile.get("provider", ""),
                "endpoints": openrouter_discovery_endpoints(model_id),
            })
        except Exception as exc:
            return http_error(str(exc))