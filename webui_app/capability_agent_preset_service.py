from __future__ import annotations

import copy


def normalize_agent_preset_role(role: str, payload, profile_names: set[str], *, model_role_labels) -> tuple[dict, list[str]]:
    data = payload if isinstance(payload, dict) else {}
    profile = str(data.get("profile") or "").strip()
    model = str(data.get("model") or "").strip()
    routing_provider = str(data.get("routing_provider") or "").strip()
    enabled = role == "primary" or bool(data.get("enabled")) or bool(profile or model or routing_provider)
    normalized = {
        "enabled": enabled,
        "profile": profile,
        "model": model,
        "routing_provider": routing_provider,
    }
    errors = []
    if enabled and not profile:
        errors.append(f"{model_role_labels.get(role, role.title())} requires a provider profile")
    if enabled and not model:
        errors.append(f"{model_role_labels.get(role, role.title())} requires a model")
    if profile and profile not in profile_names:
        errors.append(f"{model_role_labels.get(role, role.title())} profile '{profile}' was not found")
    return normalized, errors


def render_agent_preset_fragment(name: str, personality: dict, *, yaml_module) -> str:
    fragment = {
        "agent": {
            "personalities": {
                name: personality,
            }
        }
    }
    return yaml_module.safe_dump(
        fragment,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).strip() + "\n"


def normalize_agent_preset_draft(
    data: dict | None,
    *,
    cfg_get_raw,
    available_provider_profiles_fn,
    discover_skill_entries_fn,
    capability_integration_options_fn,
    normalize_agent_preset_role_fn,
    model_role_labels,
    agent_reasoning_effort_options,
) -> tuple[dict, list[str]]:
    payload = data if isinstance(data, dict) else {}
    raw = cfg_get_raw()
    profile_names = {
        str(profile.get("name") or "").strip()
        for profile in available_provider_profiles_fn(raw)
        if str(profile.get("name") or "").strip()
    }
    skill_map = {
        str(skill.get("path") or "").strip(): skill
        for skill in discover_skill_entries_fn()
        if str(skill.get("path") or "").strip()
    }
    integration_names = {
        str(item.get("name") or "").strip()
        for item in capability_integration_options_fn(raw)
        if str(item.get("name") or "").strip()
    }

    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    system_prompt = str(payload.get("system_prompt") or payload.get("prompt") or "").strip()
    reasoning_effort = str(payload.get("reasoning_effort") or "").strip().lower()
    max_turns_raw = payload.get("max_turns")
    max_turns = None
    if max_turns_raw not in (None, "", False):
        try:
            max_turns = int(max_turns_raw)
        except (TypeError, ValueError):
            max_turns = "invalid"

    roles = {}
    errors = []
    role_payload = payload.get("roles") if isinstance(payload.get("roles"), dict) else {}
    for role in model_role_labels:
        normalized_role, role_errors = normalize_agent_preset_role_fn(role, role_payload.get(role), profile_names)
        roles[role] = normalized_role
        errors.extend(role_errors)

    skills = []
    seen_skills = set()
    for value in payload.get("skills") if isinstance(payload.get("skills"), list) else []:
        path = str(value or "").strip()
        if not path or path in seen_skills:
            continue
        seen_skills.add(path)
        if path not in skill_map:
            errors.append(f"Selected skill '{path}' was not found")
            continue
        skills.append(path)

    integrations = []
    seen_integrations = set()
    for value in payload.get("integrations") if isinstance(payload.get("integrations"), list) else []:
        name_value = str(value or "").strip()
        if not name_value or name_value in seen_integrations:
            continue
        seen_integrations.add(name_value)
        if name_value not in integration_names:
            errors.append(f"Selected integration '{name_value}' was not found")
            continue
        integrations.append(name_value)

    normalized = {
        "name": name[:120].rstrip(),
        "description": description[:400].rstrip(),
        "system_prompt": system_prompt[:12000].rstrip(),
        "roles": roles,
        "skills": skills,
        "integrations": integrations,
        "reasoning_effort": reasoning_effort,
        "max_turns": max_turns,
    }
    if not normalized["name"]:
        errors.append("Preset name is required")
    if normalized["max_turns"] == "invalid" or (isinstance(normalized["max_turns"], int) and normalized["max_turns"] <= 0):
        errors.append("Max turns must be a positive integer")
    if normalized["reasoning_effort"] and normalized["reasoning_effort"] not in agent_reasoning_effort_options:
        errors.append("Reasoning effort must be one of none, low, medium, high, xhigh, or minimal")
    return normalized, errors


def agent_preset_conflicts(name: str, *, agent_personality_entries_fn, config_path, raw: dict | None = None) -> list[str]:
    personalities, _ = agent_personality_entries_fn(raw)
    if str(name or "").strip() in personalities:
        return [str(config_path)]
    return []


def agent_preset_personality_manifest(draft: dict) -> dict:
    metadata = {
        "hermes_web_ui": {
            "capability_type": "agent_preset",
            "schema_version": 1,
            "created_via": "hermes-web-ui",
            "model_roles": copy.deepcopy(draft.get("roles") or {}),
            "skills": list(draft.get("skills") or []),
            "integrations": list(draft.get("integrations") or []),
            "agent_defaults": {},
        }
    }
    if draft.get("reasoning_effort"):
        metadata["hermes_web_ui"]["agent_defaults"]["reasoning_effort"] = draft["reasoning_effort"]
    if isinstance(draft.get("max_turns"), int):
        metadata["hermes_web_ui"]["agent_defaults"]["max_turns"] = draft["max_turns"]
    if not metadata["hermes_web_ui"]["agent_defaults"]:
        metadata["hermes_web_ui"].pop("agent_defaults", None)
    personality = {
        "description": draft.get("description") or f"{draft.get('name') or 'Preset'} created in Hermes Web UI.",
        "system_prompt": draft.get("system_prompt") or draft.get("description") or f"You are the {draft.get('name') or 'agent preset'} preset.",
        "metadata": metadata,
    }
    return personality


def preview_agent_preset_capability(
    data: dict | None,
    *,
    normalize_agent_preset_draft_fn,
    cfg_get_raw,
    agent_personality_entries_fn,
    discover_skill_entries_fn,
    capability_integration_options_fn,
    agent_preset_conflicts_fn,
    agent_preset_personality_manifest_fn,
    config_path,
    render_agent_preset_fragment_fn,
    capability_preview_token_fn,
) -> tuple[dict, int]:
    draft, errors = normalize_agent_preset_draft_fn(data)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}, 400

    raw = cfg_get_raw()
    personalities, storage = agent_personality_entries_fn(raw)
    skill_map = {
        str(skill.get("path") or "").strip(): skill
        for skill in discover_skill_entries_fn()
        if str(skill.get("path") or "").strip()
    }
    integration_map = {
        str(item.get("name") or "").strip(): item
        for item in capability_integration_options_fn(raw)
        if str(item.get("name") or "").strip()
    }

    conflicts = agent_preset_conflicts_fn(draft["name"], raw=raw)
    warnings = []
    if conflicts:
        existing_source = storage.get(draft["name"], "agent")
        warnings.append(f"A preset or personality already exists with this name in {existing_source}.")

    skill_details = []
    for path in draft.get("skills") or []:
        skill = skill_map.get(path) or {}
        if not skill.get("enabled"):
            warnings.append(f"Skill '{path}' is currently disabled.")
        elif not ((skill.get("setup") or {}).get("ready", True)):
            warnings.append(f"Skill '{path}' still needs setup.")
        skill_details.append({
            "path": path,
            "name": skill.get("name") or path,
            "enabled": bool(skill.get("enabled")),
            "ready": bool((skill.get("setup") or {}).get("ready", True)),
        })

    integration_details = []
    for name in draft.get("integrations") or []:
        item = integration_map.get(name) or {}
        if not item.get("configured"):
            warnings.append(f"Integration '{name}' is not configured yet.")
        integration_details.append({
            "name": name,
            "label": item.get("label") or name,
            "configured": bool(item.get("configured")),
        })

    personality = agent_preset_personality_manifest_fn(draft)
    writes = [{
        "kind": "file",
        "path": str(config_path),
        "action": "update",
        "label": f"Save preset {draft['name']} under agent.personalities",
        "content": render_agent_preset_fragment_fn(draft["name"], personality),
    }]
    preview_payload = {
        "draft": draft,
        "personality": personality,
        "writes": writes,
        "conflicts": conflicts,
    }
    preview_token = capability_preview_token_fn("agent_preset", preview_payload)
    return {
        "ok": True,
        "type": "agent_preset",
        "phase": "Phase 3",
        "preview_token": preview_token,
        "can_apply": not conflicts,
        "draft": draft,
        "summary": {
            "name": draft.get("name") or "Agent Preset",
            "target_dir": str(config_path),
            "skill_count": len(draft.get("skills") or []),
            "integration_count": len(draft.get("integrations") or []),
            "enabled_role_count": len([role for role, entry in (draft.get("roles") or {}).items() if entry.get("enabled")]),
            "conflict_count": len(conflicts),
        },
        "warnings": warnings,
        "conflicts": conflicts,
        "writes": writes,
        "manifest": {
            "personality": personality,
            "skills": skill_details,
            "integrations": integration_details,
            "storage_path": f"agent.personalities.{draft['name']}",
            "existing_personality": copy.deepcopy(personalities.get(draft["name"])) if draft["name"] in personalities else None,
        },
    }, 200


def apply_agent_preset_capability(
    data: dict | None,
    preview_token: str,
    *,
    preview_agent_preset_capability_fn,
    config_path,
    cfg_get_raw,
    cfg_set,
    restore_text_file_fn,
    cfg_load,
    agent_personality_entries_fn,
) -> tuple[dict, int]:
    preview, status = preview_agent_preset_capability_fn(data)
    if status != 200:
        return preview, status
    if not preview_token or preview_token != preview.get("preview_token"):
        return {"ok": False, "error": "Preview has changed. Refresh the draft preview before approval."}, 409
    if not preview.get("can_apply"):
        return {"ok": False, "error": "A preset or personality already exists with this name."}, 409

    draft = preview.get("draft") or {}
    personality = copy.deepcopy(((preview.get("manifest") or {}).get("personality")) or {})
    config_before = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    try:
        raw = cfg_get_raw()
        agent_cfg = raw.get("agent", {})
        if not isinstance(agent_cfg, dict):
            agent_cfg = {}
        personalities = agent_cfg.get("personalities", {})
        if not isinstance(personalities, dict):
            personalities = {}
        personalities[str(draft.get("name") or "")] = personality
        agent_cfg["personalities"] = personalities
        cfg_set("agent", agent_cfg)
    except Exception:
        restore_text_file_fn(config_path, config_before)
        cfg_load()
        raise

    cfg_load()
    merged, _ = agent_personality_entries_fn()
    return {
        "ok": True,
        "type": "agent_preset",
        "created": {
            "name": draft.get("name") or "Agent Preset",
            "target_dir": str(config_path),
            "files": [str(config_path)],
            "personality": copy.deepcopy(merged.get(str(draft.get("name") or ""))),
        },
    }, 200