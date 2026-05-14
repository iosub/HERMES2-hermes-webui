from __future__ import annotations

import copy
import json


def skill_matches_terms(skill: dict, terms: tuple[str, ...]) -> bool:
    needles = {
        str(term or "").strip().lower()
        for term in terms or ()
        if str(term or "").strip()
    }
    if not needles:
        return False

    haystack = set()
    for value in (
        skill.get("name"),
        skill.get("path"),
        ((skill.get("frontmatter") or {}).get("name") if isinstance(skill.get("frontmatter"), dict) else ""),
    ):
        text = str(value or "").strip().lower().replace("\\", "/")
        if not text:
            continue
        haystack.add(text)
        haystack.update(part for part in text.split("/") if part)
    return bool(haystack & needles)


def joined_labels(values: list[str]) -> str:
    items = [str(value).strip() for value in values if str(value).strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def starter_pack_skill_group(item_id: str, *, starter_pack_skill_groups) -> dict | None:
    needle = str(item_id or "").strip()
    if not needle:
        return None
    for group in starter_pack_skill_groups:
        if group.get("id") == needle:
            return group
    return None


def starter_pack_install_candidates(group: dict) -> list[dict]:
    candidates = []
    for candidate in group.get("install_candidates") or ():
        if not isinstance(candidate, dict):
            continue
        candidates.append({
            "identifier": str(candidate.get("identifier") or "").strip(),
            "label": str(candidate.get("label") or candidate.get("identifier") or "").strip(),
            "source": str(candidate.get("source") or "").strip(),
            "description": str(candidate.get("description") or "").strip(),
            "recommended": bool(candidate.get("recommended")),
        })
    return [candidate for candidate in candidates if candidate.get("identifier")]


def starter_pack_candidate_matches_enabled_skill(candidate: dict, enabled_skills: list[dict], *, skill_matches_terms_fn) -> bool:
    terms = set()
    for value in (candidate.get("identifier"), candidate.get("label")):
        text = str(value or "").strip().lower().replace("\\", "/")
        if not text:
            continue
        terms.add(text)
        terms.update(part for part in text.split("/") if part)
    if not terms:
        return False
    return any(skill_matches_terms_fn(skill, tuple(terms)) for skill in enabled_skills)


def starter_pack_item_from_group(
    group: dict,
    enabled_skills: list[dict],
    *,
    skill_matches_terms_fn,
    starter_pack_install_candidates_fn,
    starter_pack_candidate_matches_enabled_skill_fn,
    safe_skill_rel_path_fn,
    skill_setup_readiness_fn,
    joined_labels_fn,
) -> dict:
    terms = tuple(str(term).lower() for term in group.get("terms") or ())
    matches = [
        skill for skill in enabled_skills
        if skill_matches_terms_fn(skill, terms)
    ]
    install_candidates = starter_pack_install_candidates_fn(group)
    installed_candidates = [
        candidate for candidate in install_candidates
        if starter_pack_candidate_matches_enabled_skill_fn(candidate, enabled_skills)
    ]
    install_available = bool(install_candidates) and not bool(installed_candidates)
    install_action_label = "Install" if not matches else "Install Recommended"
    match_names = [str(skill.get("name") or skill.get("path") or "").strip() for skill in matches]
    match_paths = [safe_skill_rel_path_fn(skill.get("path") or "") for skill in matches if safe_skill_rel_path_fn(skill.get("path") or "")]
    readiness_checks = [skill_setup_readiness_fn(skill) for skill in matches]
    readiness_issues = []
    readiness_actions = []
    for check in readiness_checks:
        readiness_issues.extend(check.get("issues") or [])
        readiness_actions.extend(check.get("actions") or [])
    readiness_issues = list(dict.fromkeys(readiness_issues))
    deduped_actions = []
    seen_actions = set()
    for action in readiness_actions:
        token = json.dumps(action, sort_keys=True)
        if token in seen_actions:
            continue
        seen_actions.add(token)
        deduped_actions.append(action)
    if matches and readiness_issues and not deduped_actions and match_paths:
        deduped_actions.append({
            "type": "skill_setup",
            "path": match_paths[0],
            "label": "Open Setup",
        })

    if matches and not readiness_issues:
        status = "ready"
        detail = f"Installed via {joined_labels_fn(match_names)}."
        ready = True
    elif matches:
        status = "attention"
        detail = (
            f"Installed via {joined_labels_fn(match_names)}, but setup is still needed: "
            f"{joined_labels_fn(readiness_issues)}."
        )
        ready = False
    else:
        status = "missing"
        detail = group.get("description", "").strip() + " Not installed yet."
        ready = False

    if matches and install_available:
        preferred_candidate = next((candidate for candidate in install_candidates if candidate.get("recommended")), None)
        preferred_label = str((preferred_candidate or install_candidates[0]).get("label") or "").strip()
        if preferred_label:
            detail = detail.rstrip(".") + f". The recommended {preferred_label} starter-pack install is still available."

    return {
        "id": group.get("id"),
        "label": group.get("label"),
        "kind": "skill",
        "status": status,
        "ready": ready,
        "detail": detail,
        "matches": match_names,
        "matched_skill_paths": match_paths,
        "query": str(group.get("query") or "").strip(),
        "install_candidates": install_candidates,
        "installed_candidates": installed_candidates,
        "install_available": install_available,
        "install_action_label": install_action_label,
        "setup_notes": [str(note).strip() for note in (group.get("setup_notes") or []) if str(note).strip()],
        "supports_install": bool(install_candidates),
        "issues": readiness_issues,
        "setup_actions": deduped_actions,
    }


def memory_runtime_status(raw: dict | None = None, *, cfg_get_raw, clean_string_list_fn, runtime_env_source_fn) -> dict:
    raw = raw if raw is not None else cfg_get_raw()
    memory_cfg = raw.get("memory") if isinstance(raw.get("memory"), dict) else {}
    cli_toolsets = set(clean_string_list_fn(((raw.get("platform_toolsets") or {}).get("cli"))))
    openai_key_source = runtime_env_source_fn("OPENAI_API_KEY")
    memory_enabled = bool(memory_cfg.get("memory_enabled"))
    user_profile_enabled = bool(memory_cfg.get("user_profile_enabled"))
    cli_tool_enabled = "memory" in cli_toolsets
    openai_api_key_present = bool(openai_key_source)
    semantic_search_ready = memory_enabled and cli_tool_enabled and openai_api_key_present

    if not memory_enabled:
        detail = "Hermes memory is disabled."
    elif not cli_tool_enabled:
        detail = "Memory is enabled in config, but the CLI memory tool is not active for chats."
    elif not openai_api_key_present:
        detail = "Add OPENAI_API_KEY to the Hermes environment to enable OpenAI-backed memory search."
    else:
        detail = "Hermes memory is enabled and can use your OpenAI API key for semantic recall."

    return {
        "enabled": memory_enabled,
        "user_profile_enabled": user_profile_enabled,
        "cli_tool_enabled": cli_tool_enabled,
        "openai_api_key_present": openai_api_key_present,
        "openai_api_key_source": openai_key_source,
        "semantic_search_ready": semantic_search_ready,
        "detail": detail,
    }


def chat_runtime_status(
    raw: dict | None = None,
    *,
    skills: list[dict] | None = None,
    cfg_get_raw,
    discover_skill_entries_fn,
    integration_entries_fn,
    configured_hook_keys_fn,
    clean_string_list_fn,
    memory_runtime_status_fn,
    starter_pack_skill_groups,
    starter_pack_item_from_group_fn,
    joined_labels_fn,
) -> dict:
    raw = raw if raw is not None else cfg_get_raw()
    skills = copy.deepcopy(skills) if skills is not None else discover_skill_entries_fn()
    enabled_skills = [skill for skill in skills if skill.get("enabled") is not False]
    integrations = integration_entries_fn(raw)
    configured_integrations = [item for item in integrations if item.get("configured")]
    hook_keys = configured_hook_keys_fn(raw)
    cli_toolsets = set(clean_string_list_fn(((raw.get("platform_toolsets") or {}).get("cli"))))
    memory = memory_runtime_status_fn(raw)

    active_features = []
    reasons = []
    blocking_features = []
    if memory.get("enabled") and memory.get("cli_tool_enabled"):
        active_features.append("memory")
        reasons.append("Hermes memory is enabled for chat sessions.")
    if enabled_skills and "skills" in cli_toolsets:
        active_features.append("skills")
        reasons.append(f"{len(enabled_skills)} Hermes skill{'s are' if len(enabled_skills) != 1 else ' is'} enabled.")
    if configured_integrations:
        active_features.append("integrations")
        reasons.append(
            f"{len(configured_integrations)} integration{'s are' if len(configured_integrations) != 1 else ' is'} configured."
        )
    if hook_keys:
        active_features.append("hooks")
        reasons.append(f"Hooks are configured: {joined_labels_fn(hook_keys)}.")
        blocking_features.append("hooks")

    requires_cli = bool(blocking_features)
    if requires_cli:
        cli_reason = "Hermes CLI is required because " + joined_labels_fn(blocking_features) + " " + (
            "is active."
            if len(blocking_features) == 1
            else "are active."
        )
    else:
        cli_reason = ""

    starter_items = []
    for group in starter_pack_skill_groups:
        item = starter_pack_item_from_group_fn(group, enabled_skills)
        if item.get("status") == "ready":
            continue
        starter_items.append(item)

    return {
        "requires_cli": requires_cli,
        "cli_reason": cli_reason,
        "reasons": reasons,
        "active_features": active_features,
        "blocking_features": blocking_features,
        "memory": memory,
        "skills": {
            "detected_count": len(skills),
            "enabled_count": len(enabled_skills),
            "tool_enabled": "skills" in cli_toolsets,
        },
        "integrations": {
            "configured_count": len(configured_integrations),
            "configured_names": [item.get("label") or item.get("name") for item in configured_integrations],
        },
        "hooks": {
            "configured": bool(hook_keys),
            "keys": hook_keys,
        },
        "starter_pack": {
            "items": starter_items,
        },
    }