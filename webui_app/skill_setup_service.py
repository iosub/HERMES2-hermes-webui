from __future__ import annotations

import json


def discover_skill_entries(
    *,
    skills_dir,
    os_module,
    path_class,
    skill_frontmatter_fn,
    read_skill_source_metadata_fn,
    skill_setup_readiness_fn,
) -> list[dict]:
    skills = []
    if not skills_dir.exists():
        return skills

    for root, dirs, files in os_module.walk(str(skills_dir)):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if "SKILL.md" not in files:
            continue
        skill_md = path_class(root) / "SKILL.md"
        frontmatter = skill_frontmatter_fn(skill_md)
        rel_path = path_class(root).relative_to(skills_dir)
        dir_name = str(rel_path)
        skill = {
            "name": frontmatter.get("name", rel_path.name),
            "category": frontmatter.get("category", ""),
            "description": frontmatter.get("description", ""),
            "path": str(rel_path),
            "enabled": not dir_name.endswith(".disabled"),
            "frontmatter": frontmatter,
        }
        skill["source"] = read_skill_source_metadata_fn(path_class(root))
        skill["setup"] = skill_setup_readiness_fn(skill)
        skills.append(skill)
    return skills


def configured_hook_keys(raw: dict | None = None, *, cfg_get_raw, integration_config_is_configured_fn) -> list[str]:
    raw = raw if raw is not None else cfg_get_raw()
    hooks_cfg = raw.get("hooks")
    if not isinstance(hooks_cfg, dict):
        return []
    return [
        str(key)
        for key, value in hooks_cfg.items()
        if integration_config_is_configured_fn(value)
    ]


def skill_wants_integration_setup(skill: dict, env_blockers: list[dict]) -> bool:
    if any(str(blocker.get("group") or "").strip() == "Channel" for blocker in env_blockers):
        return True
    haystack = " ".join(
        str(value or "").strip().lower()
        for value in (
            skill.get("name"),
            skill.get("path"),
            skill.get("category"),
            ((skill.get("source") or {}).get("source_repo") if isinstance(skill.get("source"), dict) else ""),
        )
    )
    return any(hint in haystack for hint in ("discord", "whatsapp", "slack", "telegram", "matrix", "webhook"))


def skill_setup_details(
    skill: dict,
    *,
    normalize_capability_env_var_fn,
    clean_string_list_fn,
    normalize_capability_credential_file_fn,
    normalize_capability_required_command_fn,
) -> dict:
    frontmatter = skill.get("frontmatter") if isinstance(skill.get("frontmatter"), dict) else {}
    metadata = frontmatter.get("metadata") if isinstance(frontmatter.get("metadata"), dict) else {}
    ui_setup = {}
    if isinstance(metadata.get("hermes_web_ui"), dict):
        ui_setup = metadata["hermes_web_ui"].get("setup") if isinstance(metadata["hermes_web_ui"].get("setup"), dict) else {}

    env_vars = []
    seen_env = set()
    for entry in ui_setup.get("env_vars") if isinstance(ui_setup.get("env_vars"), list) else []:
        normalized = normalize_capability_env_var_fn(entry)
        key = normalized.get("key")
        if not key or key in seen_env:
            continue
        seen_env.add(key)
        env_vars.append(normalized)
    prerequisites = frontmatter.get("prerequisites")
    legacy_env_vars = clean_string_list_fn(prerequisites.get("env_vars")) if isinstance(prerequisites, dict) else []
    for env_key in legacy_env_vars:
        normalized = normalize_capability_env_var_fn(env_key)
        key = normalized.get("key")
        if not key or key in seen_env:
            continue
        seen_env.add(key)
        env_vars.append(normalized)

    credential_files = []
    seen_paths = set()
    for entry in ui_setup.get("credential_files") if isinstance(ui_setup.get("credential_files"), list) else []:
        normalized = normalize_capability_credential_file_fn(entry)
        rel_path = normalized.get("path")
        if not rel_path or rel_path in seen_paths:
            continue
        seen_paths.add(rel_path)
        credential_files.append(normalized)
    required_files = frontmatter.get("required_credential_files")
    if isinstance(required_files, list):
        for entry in required_files:
            normalized = normalize_capability_credential_file_fn(entry)
            rel_path = normalized.get("path")
            if not rel_path or rel_path in seen_paths:
                continue
            seen_paths.add(rel_path)
            credential_files.append(normalized)

    required_commands = []
    seen_commands = set()
    for entry in ui_setup.get("required_commands") if isinstance(ui_setup.get("required_commands"), list) else []:
        normalized = normalize_capability_required_command_fn(entry)
        name = normalized.get("name")
        if not name or name in seen_commands:
            continue
        seen_commands.add(name)
        required_commands.append(normalized)
    openclaw_meta = metadata.get("openclaw") if isinstance(metadata.get("openclaw"), dict) else {}
    legacy_bins = clean_string_list_fn(((openclaw_meta.get("requires") or {}).get("bins"))) if isinstance(openclaw_meta, dict) else []
    for binary in legacy_bins:
        normalized = normalize_capability_required_command_fn(binary)
        name = normalized.get("name")
        if not name or name in seen_commands:
            continue
        seen_commands.add(name)
        required_commands.append(normalized)

    return {
        "env_vars": env_vars,
        "credential_files": credential_files,
        "required_commands": required_commands,
    }


def skill_setup_readiness(
    skill: dict,
    *,
    skill_absolute_path_fn,
    skill_setup_details_fn,
    path_class,
    runtime_env_value_fn,
    classify_env_key_fn,
    shutil_module,
    skill_wants_integration_setup_fn,
) -> dict:
    skill_dir = skill_absolute_path_fn(skill)
    issues = []
    blockers = []
    actions = []
    details = skill_setup_details_fn(skill)

    for entry in details.get("credential_files") or []:
        rel_path = str(entry.get("path") or "").strip()
        if not rel_path or not skill_dir:
            continue
        target = (skill_dir / rel_path).resolve()
        if not target.exists():
            message = f"missing credential file {rel_path}"
            issues.append(message)
            blockers.append({
                "kind": "credential_file",
                "path": rel_path,
                "label": str(entry.get("label") or path_class(rel_path).name).strip(),
                "description": str(entry.get("description") or "").strip(),
                "absolute_path": str(target),
                "message": message,
            })

    env_blockers = []
    for env_entry in details.get("env_vars") or []:
        env_key = str(env_entry.get("key") or "").strip()
        if not env_key:
            continue
        if not runtime_env_value_fn(env_key, ""):
            message = f"missing env var {env_key}"
            issues.append(message)
            blocker = {
                "kind": "env_var",
                "key": env_key,
                "group": env_entry.get("group") or classify_env_key_fn(env_key),
                "label": env_entry.get("label") or env_key,
                "description": env_entry.get("description") or "",
                "default_value": str(env_entry.get("default_value") or "").strip(),
                "secret": bool(env_entry.get("secret")),
                "message": message,
            }
            blockers.append(blocker)
            env_blockers.append(blocker)

    for command_entry in details.get("required_commands") or []:
        binary = str(command_entry.get("name") or "").strip()
        if not binary:
            continue
        if shutil_module.which(binary) is None:
            message = f"missing command {binary}"
            issues.append(message)
            blockers.append({
                "kind": "command",
                "name": binary,
                "description": str(command_entry.get("description") or "").strip(),
                "message": message,
            })

    for blocker in env_blockers:
        actions.append({
            "type": "env_var",
            "key": blocker.get("key"),
            "group": blocker.get("group"),
            "description": blocker.get("description"),
            "default_value": blocker.get("default_value"),
            "secret": blocker.get("secret"),
            "label": f"Set {blocker.get('label') or blocker.get('key')}",
        })

    if skill_wants_integration_setup_fn(skill, env_blockers):
        actions.append({
            "type": "screen",
            "screen": "channels",
            "label": "Open Apps & Integrations",
        })

    unique_actions = []
    seen_actions = set()
    for action in actions:
        token = json.dumps(action, sort_keys=True)
        if token in seen_actions:
            continue
        seen_actions.add(token)
        unique_actions.append(action)

    return {
        "ready": not issues,
        "issues": issues,
        "blockers": blockers,
        "actions": unique_actions,
        "requirements": details,
    }


def skill_env_var_presets(
    skills: list[dict] | None = None,
    *,
    discover_skill_entries_fn,
    normalize_capability_env_var_fn,
    env_var_metadata_fn,
) -> dict[str, dict]:
    catalog = {}
    for skill in skills if skills is not None else discover_skill_entries_fn():
        requirements = ((skill.get("setup") or {}).get("requirements") if isinstance(skill.get("setup"), dict) else {}) or {}
        for entry in requirements.get("env_vars") if isinstance(requirements.get("env_vars"), list) else []:
            normalized = normalize_capability_env_var_fn(entry)
            key = normalized.get("key")
            if not key:
                continue
            existing = catalog.get(key, {})
            merged = {
                **env_var_metadata_fn(key),
                **existing,
                **normalized,
            }
            catalog[key] = merged
    return catalog