from __future__ import annotations

import copy


def normalized_model_config(*, cfg_get_raw, auxiliary_model_keys) -> dict:
    raw = cfg_get_raw()
    model_cfg = raw.get("model", {}) or {}
    auxiliary_cfg = raw.get("auxiliary", {}) or {}

    if isinstance(model_cfg, str):
        normalized = {"default_model": model_cfg.strip()}
    elif isinstance(model_cfg, dict):
        normalized = copy.deepcopy(model_cfg)
    else:
        normalized = {}

    model_cfg = normalized
    if not isinstance(auxiliary_cfg, dict):
        auxiliary_cfg = {}

    if "default_model" not in normalized and model_cfg.get("default"):
        normalized["default_model"] = model_cfg.get("default")
    if "default_provider" not in normalized and model_cfg.get("provider"):
        normalized["default_provider"] = model_cfg.get("provider")
    for aux_key in auxiliary_model_keys:
        if aux_key not in normalized and aux_key in auxiliary_cfg:
            normalized[aux_key] = copy.deepcopy(auxiliary_cfg.get(aux_key))
    return normalized


def provider_display_name(provider_type: str, *, provider_type_labels) -> str:
    normalized = str(provider_type or "").strip().lower()
    return provider_type_labels.get(normalized, normalized or "Custom")


def infer_provider_type(name: str = "", base_url: str = "", *, re_module) -> str:
    haystack = f"{name} {base_url}".lower()
    if "openrouter" in haystack:
        return "openrouter"
    if "api.openai.com" in haystack or re_module.search(r"\bopenai\b", haystack):
        return "openai"
    if "azure" in haystack:
        return "azure"
    if "anthropic" in haystack or "claude" in haystack:
        return "anthropic"
    if "groq" in haystack:
        return "groq"
    if "google" in haystack or "gemini" in haystack:
        return "google"
    if "mistral" in haystack:
        return "mistral"
    if "together" in haystack:
        return "together"
    if "fireworks" in haystack:
        return "fireworks"
    if "deepseek" in haystack:
        return "deepseek"
    if "cohere" in haystack:
        return "cohere"
    return "auto"


def normalize_provider_type(value: str = "", *, name: str = "", base_url: str = "", infer_provider_type_fn) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in ("", "default", "custom", "generic", "local", "openai-compatible"):
        return infer_provider_type_fn(name=name, base_url=base_url)
    return normalized


def provider_default_base_url(provider: str = "", *, normalize_provider_type_fn, provider_default_base_urls) -> str:
    normalized = normalize_provider_type_fn(provider)
    return provider_default_base_urls.get(normalized, "")


def normalize_provider_profile(entry, *, normalize_provider_type_fn, provider_default_base_url_fn) -> dict:
    if isinstance(entry, str):
        entry = {"name": entry}
    elif not isinstance(entry, dict):
        entry = {}

    normalized = copy.deepcopy(entry)
    normalized["name"] = str(normalized.get("name") or "").strip()
    normalized["base_url"] = str(normalized.get("base_url") or "").strip()
    normalized["model"] = str(normalized.get("model") or "").strip()
    normalized["provider"] = normalize_provider_type_fn(
        normalized.get("provider", ""),
        name=normalized.get("name", ""),
        base_url=normalized.get("base_url", ""),
    )
    if not normalized["base_url"]:
        normalized["base_url"] = provider_default_base_url_fn(normalized.get("provider", ""))
    api_key = normalized.get("api_key")
    normalized["api_key"] = str(api_key or "").strip() if api_key is not None else ""
    normalized["implicit"] = bool(normalized.get("implicit"))
    return normalized


def custom_provider_profiles(*, raw, normalize_provider_profile_fn) -> list[dict]:
    return [normalize_provider_profile_fn(item) for item in (raw.get("custom_providers", []) or [])]


def role_routing_provider(role: str, *, model_cfg: dict | None = None) -> str:
    model_cfg = model_cfg if model_cfg is not None else {}
    if role == "primary":
        return str(model_cfg.get("routing_provider") or "").strip()
    if role == "fallback":
        return str(model_cfg.get("fallback_routing_provider") or "").strip()
    if role == "vision":
        vision_cfg = model_cfg.get("vision")
        if isinstance(vision_cfg, dict):
            return str(vision_cfg.get("routing_provider") or "").strip()
    return ""


def raw_role_profile_candidate(
    role: str,
    *,
    model_cfg: dict,
    raw,
    normalize_provider_type_fn,
    provider_default_base_url_fn,
    role_routing_provider_fn,
    normalize_provider_profile_fn,
) -> dict | None:
    if role == "primary":
        explicit_profile = str(model_cfg.get("default_profile") or "").strip()
        declared_provider = str(model_cfg.get("default_provider") or "").strip()
        provider = normalize_provider_type_fn(declared_provider)
        model = str(model_cfg.get("default_model") or "").strip()
        base_url = str(model_cfg.get("base_url") or provider_default_base_url_fn(provider) or "").strip()
        api_key = str(model_cfg.get("api_key") or "").strip()
        routing_provider = role_routing_provider_fn("primary", model_cfg=model_cfg)
    elif role == "fallback":
        explicit_profile = str(model_cfg.get("fallback_profile") or "").strip()
        declared_provider = str(model_cfg.get("fallback_provider") or "").strip()
        provider = normalize_provider_type_fn(declared_provider)
        model = str(model_cfg.get("fallback_model") or "").strip()
        base_url = str(model_cfg.get("fallback_base_url") or provider_default_base_url_fn(provider) or "").strip()
        api_key = str(model_cfg.get("fallback_api_key") or "").strip()
        routing_provider = role_routing_provider_fn("fallback", model_cfg=model_cfg)
    elif role == "vision":
        vision_cfg = model_cfg.get("vision")
        if isinstance(vision_cfg, str):
            explicit_profile = ""
            declared_provider = str(model_cfg.get("default_provider") or "").strip()
            provider = normalize_provider_type_fn(declared_provider)
            model = vision_cfg.strip()
            base_url = str(model_cfg.get("base_url") or provider_default_base_url_fn(provider) or "").strip()
            api_key = str(model_cfg.get("api_key") or "").strip()
            routing_provider = ""
        elif isinstance(vision_cfg, dict):
            explicit_profile = str(vision_cfg.get("profile") or "").strip()
            declared_provider = str(vision_cfg.get("provider") or "").strip()
            provider = normalize_provider_type_fn(declared_provider, base_url=vision_cfg.get("base_url", ""))
            model = str(vision_cfg.get("model") or "").strip()
            base_url = str(vision_cfg.get("base_url") or provider_default_base_url_fn(provider) or "").strip()
            api_key = str(vision_cfg.get("api_key") or "").strip()
            routing_provider = role_routing_provider_fn("vision", model_cfg=model_cfg)
        else:
            return None
    else:
        return None

    if not any((explicit_profile, provider, model, base_url, api_key)):
        return None
    if not explicit_profile and provider in ("", "auto") and not any((model, base_url, api_key)):
        return None
    name = explicit_profile or declared_provider or provider or role
    return normalize_provider_profile_fn({
        "name": name,
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "routing_provider": routing_provider,
        "implicit": True,
        "source_role": role,
    })


def available_provider_profiles(
    *,
    raw,
    model_cfg,
    custom_provider_profiles_fn,
    raw_role_profile_candidate_fn,
    model_role_labels,
    normalize_provider_profile_fn,
) -> list[dict]:
    profiles = []
    by_name: dict[str, dict] = {}

    def add_profile(profile: dict | None):
        if not profile:
            return
        normalized = normalize_provider_profile_fn(profile)
        name = normalized.get("name", "")
        if not name:
            return
        existing = by_name.get(name)
        if existing:
            same_target = existing.get("provider") == normalized.get("provider") and existing.get("base_url") == normalized.get("base_url")
            if same_target:
                if not existing.get("model") and normalized.get("model"):
                    existing["model"] = normalized["model"]
                return
            suffix_name = f"{name}-{normalized.get('source_role') or 'profile'}"
            normalized["name"] = suffix_name
            name = suffix_name
            if name in by_name:
                return
        by_name[name] = normalized
        profiles.append(normalized)

    for profile in custom_provider_profiles_fn(raw=raw):
        add_profile(profile)
    for role in model_role_labels:
        candidate = raw_role_profile_candidate_fn(role, model_cfg=model_cfg, raw=raw)
        explicit_name = candidate.get("name", "") if candidate else ""
        if explicit_name and explicit_name in by_name:
            continue
        add_profile(candidate)
    return profiles


def get_provider_profile(name: str, *, available_provider_profiles_fn, raw) -> dict | None:
    normalized_name = str(name or "").strip()
    if not normalized_name:
        return None
    for profile in available_provider_profiles_fn(raw=raw):
        if profile.get("name") == normalized_name:
            return profile
    return None


def role_linked_profile_name(
    role: str,
    *,
    model_cfg,
    raw,
    custom_provider_profiles_fn,
    raw_role_profile_candidate_fn,
) -> str:
    profile_names = {item.get("name") for item in custom_provider_profiles_fn(raw=raw)}

    if role == "primary":
        explicit = str(model_cfg.get("default_profile") or "").strip()
        fallback = str(model_cfg.get("default_provider") or "").strip()
    elif role == "fallback":
        explicit = str(model_cfg.get("fallback_profile") or "").strip()
        fallback = str(model_cfg.get("fallback_provider") or "").strip()
    elif role == "vision":
        vision_cfg = model_cfg.get("vision")
        explicit = str(vision_cfg.get("profile") or "").strip() if isinstance(vision_cfg, dict) else ""
        fallback = str(vision_cfg.get("provider") or "").strip() if isinstance(vision_cfg, dict) else ""
    else:
        return ""

    if explicit:
        return explicit
    if fallback in profile_names:
        return fallback
    candidate = raw_role_profile_candidate_fn(role, model_cfg=model_cfg, raw=raw)
    if candidate:
        return candidate.get("name", "")
    return ""


def provider_usage_map(*, raw, model_cfg, model_role_labels, role_linked_profile_name_fn) -> dict[str, list[str]]:
    usage: dict[str, list[str]] = {}
    for role, label in model_role_labels.items():
        profile_name = role_linked_profile_name_fn(role, model_cfg=model_cfg, raw=raw)
        if not profile_name:
            continue
        usage.setdefault(profile_name, []).append(label)
    return usage


def resolve_role_target(
    role: str,
    *,
    raw,
    model_cfg,
    normalize_provider_type_fn,
    provider_default_base_url_fn,
    role_linked_profile_name_fn,
    profile_api_gateway_url_fn,
    effective_hermes_api_url_fn,
    default_hermes_api_url,
    role_routing_provider_fn,
    get_provider_profile_fn,
    resolve_runtime_template_fn,
    resolved_target_api_key_fn,
) -> dict:
    default_provider = normalize_provider_type_fn(
        model_cfg.get("default_provider", ""),
        base_url=model_cfg.get("base_url", ""),
    )
    default_target = {
        "base_url": (model_cfg.get("base_url") or provider_default_base_url_fn(default_provider) or effective_hermes_api_url_fn("") or default_hermes_api_url).strip(),
        "api_key": str(model_cfg.get("api_key") or "").strip(),
        "model": str(model_cfg.get("default_model") or "").strip(),
        "provider": default_provider,
        "profile": role_linked_profile_name_fn("primary", model_cfg=model_cfg, raw=raw),
        "routing_provider": role_routing_provider_fn("primary", model_cfg=model_cfg),
    }

    primary_profile = get_provider_profile_fn(default_target.get("profile"), raw=raw)
    if primary_profile:
        default_target["provider"] = primary_profile.get("provider") or default_target["provider"]
        default_target["base_url"] = primary_profile.get("base_url") or default_target["base_url"]
        if primary_profile.get("api_key"):
            default_target["api_key"] = primary_profile.get("api_key")
        if not default_target["model"]:
            default_target["model"] = primary_profile.get("model") or default_target["model"]
    default_target["base_url"] = resolve_runtime_template_fn(default_target.get("base_url") or "").strip()
    default_target["api_key"] = resolved_target_api_key_fn(default_target)

    if role == "primary":
        return default_target

    if role == "fallback":
        fallback_provider = normalize_provider_type_fn(
            model_cfg.get("fallback_provider", ""),
            base_url=model_cfg.get("fallback_base_url", ""),
        )
        fallback_target = {
            "base_url": str(model_cfg.get("fallback_base_url") or provider_default_base_url_fn(fallback_provider) or "").strip(),
            "api_key": str(model_cfg.get("fallback_api_key") or "").strip(),
            "model": str(model_cfg.get("fallback_model") or "").strip(),
            "provider": fallback_provider,
            "profile": role_linked_profile_name_fn("fallback", model_cfg=model_cfg, raw=raw),
            "routing_provider": role_routing_provider_fn("fallback", model_cfg=model_cfg),
        }
        fallback_profile = get_provider_profile_fn(fallback_target.get("profile"), raw=raw)
        if fallback_profile:
            fallback_target["provider"] = fallback_profile.get("provider") or fallback_target["provider"]
            fallback_target["base_url"] = fallback_profile.get("base_url") or fallback_target["base_url"]
            if fallback_profile.get("api_key"):
                fallback_target["api_key"] = fallback_profile.get("api_key")
            if not fallback_target.get("model"):
                fallback_target["model"] = fallback_profile.get("model") or fallback_target["model"]
        fallback_target["base_url"] = resolve_runtime_template_fn(fallback_target.get("base_url") or "").strip()
        fallback_target["api_key"] = resolved_target_api_key_fn(fallback_target)
        return fallback_target

    if role == "vision":
        merged = dict(default_target)
        vision_cfg = model_cfg.get("vision")
        if isinstance(vision_cfg, str) and vision_cfg.strip():
            merged["model"] = vision_cfg.strip()
            merged["profile"] = ""
            merged["routing_provider"] = ""
            merged["api_key"] = resolved_target_api_key_fn(merged)
            return merged
        if isinstance(vision_cfg, dict):
            merged["profile"] = role_linked_profile_name_fn("vision", model_cfg=model_cfg, raw=raw)
            merged["routing_provider"] = role_routing_provider_fn("vision", model_cfg=model_cfg)
            vision_profile = get_provider_profile_fn(merged.get("profile"), raw=raw)
            if vision_profile:
                merged["provider"] = vision_profile.get("provider") or merged["provider"]
                merged["base_url"] = vision_profile.get("base_url") or merged["base_url"]
                if vision_profile.get("api_key"):
                    merged["api_key"] = vision_profile.get("api_key")
            for key in ("base_url", "api_key", "model", "provider"):
                if isinstance(vision_cfg.get(key), str) and vision_cfg.get(key).strip():
                    merged[key] = vision_cfg.get(key).strip()
            merged["base_url"] = resolve_runtime_template_fn(merged.get("base_url") or "").strip()
            merged["provider"] = normalize_provider_type_fn(
                merged.get("provider", ""),
                base_url=merged.get("base_url", ""),
            )
            merged["api_key"] = resolved_target_api_key_fn(merged)
        return merged

    raise ValueError(f"Unknown model role: {role}")


def model_role_enabled(role: str, *, target: dict | None, resolve_role_target_fn) -> bool:
    if role == "primary":
        return True
    target = target if target is not None else resolve_role_target_fn(role)
    return bool(str(target.get("model") or "").strip())


def model_role_info(
    role: str,
    *,
    resolve_role_target_fn,
    model_role_labels,
    provider_display_name_fn,
    model_role_enabled_fn,
) -> dict:
    target = resolve_role_target_fn(role)
    linked_profile = str(target.get("profile") or "").strip()
    return {
        "role": role,
        "label": model_role_labels.get(role, role.title()),
        "profile": linked_profile,
        "provider": str(target.get("provider") or "").strip(),
        "provider_label": provider_display_name_fn(target.get("provider", "")),
        "model": str(target.get("model") or "").strip(),
        "base_url": str(target.get("base_url") or "").strip(),
        "routing_provider": str(target.get("routing_provider") or "").strip(),
        "enabled": model_role_enabled_fn(role, target=target),
        "supports_live_discovery": str(target.get("provider") or "").strip().lower() == "openrouter",
    }


def profile_payload_for_role(profile_name: str, model_name: str, routing_provider: str = "", *, get_provider_profile_fn, chat_backend_error_cls) -> dict:
    profile = get_provider_profile_fn(profile_name)
    if not profile:
        raise chat_backend_error_cls(f"Provider profile '{profile_name}' was not found", status_code=404)
    return {
        "profile": profile.get("name", ""),
        "provider": profile.get("provider", ""),
        "base_url": profile.get("base_url", ""),
        "api_key": profile.get("api_key", ""),
        "model": str(model_name or "").strip(),
        "routing_provider": str(routing_provider or "").strip(),
    }


def sync_linked_provider_roles(
    profile_name: str,
    profile: dict,
    *,
    cfg_get_raw,
    normalized_model_config_fn,
    role_linked_profile_name_fn,
    cfg_update,
) -> None:
    raw = cfg_get_raw()
    model_cfg = normalized_model_config_fn()
    model_updates = {}
    if role_linked_profile_name_fn("primary", model_cfg=model_cfg, raw=raw) == profile_name:
        model_updates.update({
            "default_profile": profile_name,
            "default_provider": profile.get("provider", ""),
            "base_url": profile.get("base_url", ""),
            "api_key": profile.get("api_key", ""),
        })
    if role_linked_profile_name_fn("fallback", model_cfg=model_cfg, raw=raw) == profile_name:
        model_updates.update({
            "fallback_profile": profile_name,
            "fallback_provider": profile.get("provider", ""),
            "fallback_base_url": profile.get("base_url", ""),
            "fallback_api_key": profile.get("api_key", ""),
        })
    if model_updates:
        cfg_update("model", model_updates)

    vision_cfg = model_cfg.get("vision")
    if role_linked_profile_name_fn("vision", model_cfg=model_cfg, raw=raw) == profile_name:
        cfg_update("auxiliary", {
            "vision": {
                "profile": profile_name,
                "provider": profile.get("provider", ""),
                "base_url": profile.get("base_url", ""),
                "api_key": profile.get("api_key", ""),
                "model": str((vision_cfg or {}).get("model") or "").strip() if isinstance(vision_cfg, dict) else "",
                "routing_provider": str((vision_cfg or {}).get("routing_provider") or "").strip() if isinstance(vision_cfg, dict) else "",
            }
        })