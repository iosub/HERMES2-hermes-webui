from __future__ import annotations

import copy
import json


def normalize_capability_env_var(entry, *, re_module, env_var_metadata_fn, classify_env_key_fn, env_group_help) -> dict:
    if isinstance(entry, str):
        entry = {"key": entry}
    if not isinstance(entry, dict):
        return {}
    raw_key = str(entry.get("key") or "").strip().upper()
    key = re_module.sub(r"[^A-Z0-9_]", "_", raw_key).strip("_")
    if not key:
        return {}
    base = env_var_metadata_fn(key)
    group = str(entry.get("group") or base.get("group") or classify_env_key_fn(key)).strip()
    if group not in env_group_help:
        group = classify_env_key_fn(key)
    label = str(entry.get("label") or base.get("label") or key).strip() or key
    description = str(entry.get("description") or base.get("description") or "").strip()
    default_value = str(entry.get("default_value") or base.get("default_value") or "").strip()
    secret = bool(entry.get("secret")) if "secret" in entry else bool(base.get("secret"))
    recommended = bool(entry.get("recommended")) if "recommended" in entry else bool(base.get("recommended"))
    return {
        "key": key,
        "group": group,
        "label": label,
        "description": description,
        "default_value": default_value,
        "secret": secret,
        "recommended": recommended,
    }


def normalize_capability_env_assignment(entry, *, normalize_capability_env_var_fn) -> dict:
    if isinstance(entry, str):
        entry = {"key": entry}
    if not isinstance(entry, dict):
        return {}
    normalized = normalize_capability_env_var_fn(entry)
    if not normalized:
        return {}
    value = entry.get("value")
    normalized["value"] = str(value) if value is not None else ""
    return normalized


def restore_text_file(path, previous_text: str | None) -> None:
    if previous_text is None:
        try:
            path.unlink()
        except FileNotFoundError:
            return
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(previous_text, encoding="utf-8")


def normalize_integration_capability_draft(
    data: dict | None,
    *,
    integration_config_templates,
    integration_section_labels,
    normalize_capability_env_assignment_fn,
) -> tuple[dict, list[str]]:
    payload = data if isinstance(data, dict) else {}
    kind = str(payload.get("kind") or payload.get("name") or "").strip().lower()
    config = payload.get("config")
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except Exception:
            config = {"__invalid_json__": True}
    if config is None:
        config = copy.deepcopy(integration_config_templates.get(kind) or {})
    env_vars = []
    seen_env_keys = set()
    for entry in payload.get("env_vars") if isinstance(payload.get("env_vars"), list) else []:
        normalized = normalize_capability_env_assignment_fn(entry)
        key = normalized.get("key")
        if not key or key in seen_env_keys:
            continue
        seen_env_keys.add(key)
        env_vars.append(normalized)

    normalized = {
        "kind": kind,
        "label": integration_section_labels.get(kind, kind.title()),
        "config": copy.deepcopy(config) if isinstance(config, dict) else config,
        "env_vars": env_vars,
    }
    errors = []
    if kind not in integration_section_labels:
        errors.append("Integration kind is required")
    if not isinstance(config, dict) or config.get("__invalid_json__"):
        errors.append("Integration config must be a JSON object")
    return normalized, errors


def integration_capability_conflicts(kind: str, *, cfg_get_raw, integration_config_is_configured_fn, config_path) -> list[str]:
    raw = cfg_get_raw()
    current = raw.get(kind)
    if isinstance(current, dict) and integration_config_is_configured_fn(current):
        return [str(config_path)]
    return []


def integration_capability_readiness(draft: dict, env_values: dict[str, str] | None = None, *, classify_env_key_fn, integration_config_is_configured_fn) -> dict:
    env_values = env_values or {}
    blockers = []
    issues = []
    for entry in draft.get("env_vars") or []:
        key = entry.get("key") or ""
        value = str(entry.get("value") or env_values.get(key) or "").strip()
        if value:
            continue
        blockers.append({
            "kind": "env_var",
            "key": key,
            "group": entry.get("group") or classify_env_key_fn(key),
            "message": f"missing env var {key}",
        })
        issues.append(f"missing env var {key}")
    if not integration_config_is_configured_fn(draft.get("config") or {}):
        blockers.append({
            "kind": "config",
            "message": "integration config is still empty",
        })
        issues.append("integration config is still empty")
    return {
        "ready": not blockers,
        "issues": issues,
        "blockers": blockers,
    }


def preview_integration_capability(
    data: dict | None,
    *,
    normalize_integration_capability_draft_fn,
    cfg_get_raw,
    env_path,
    dotenv_values_fn,
    integration_capability_conflicts_fn,
    integration_capability_readiness_fn,
    integration_entries_fn,
    integration_config_is_configured_fn,
    cfg_mask_secrets,
    config_path,
    mask_value_fn,
    capability_preview_token_fn,
) -> tuple[dict, int]:
    draft, errors = normalize_integration_capability_draft_fn(data)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}, 400

    raw = cfg_get_raw()
    env_values = dotenv_values_fn(str(env_path)) if env_path.exists() else {}
    current = raw.get(draft["kind"])
    exists = isinstance(current, dict)
    conflicts = integration_capability_conflicts_fn(draft["kind"], raw=raw)
    readiness = integration_capability_readiness_fn(draft, env_values=env_values)

    next_raw = copy.deepcopy(raw)
    next_raw[draft["kind"]] = copy.deepcopy(draft["config"])
    integration_entry = next(
        (entry for entry in integration_entries_fn(next_raw) if entry.get("name") == draft["kind"]),
        {
            "name": draft["kind"],
            "label": draft["label"],
            "kind": "integration",
            "configured": integration_config_is_configured_fn(draft["config"]),
            "config": cfg_mask_secrets(copy.deepcopy(draft["config"])),
            "source": "top_level",
        },
    )
    integration_entry["readiness"] = readiness

    writes = [{
        "kind": "file",
        "path": str(config_path),
        "action": "update" if exists else "create",
        "label": f"Set {draft['label']} integration block",
        "content": json.dumps(draft.get("config") or {}, indent=2, sort_keys=True),
    }]
    env_overrides = []
    for entry in draft.get("env_vars") or []:
        key = entry.get("key") or ""
        value = str(entry.get("value") or "")
        current_value = str(env_values.get(key) or "")
        if value:
            if current_value and current_value != value:
                env_overrides.append(key)
            writes.append({
                "kind": "file",
                "path": str(env_path),
                "action": "update" if env_path.exists() else "create",
                "label": f"Set env var {key}",
                "key": key,
                "content": mask_value_fn(key, value),
            })

    warnings = []
    if conflicts:
        warnings.append("This integration is already configured. Edit it from Apps & Integrations instead of creating it again.")
    if not integration_config_is_configured_fn(draft.get("config") or {}):
        warnings.append("The config block is still empty, so Apps & Integrations may continue to show it as Empty.")
    for key in env_overrides:
        warnings.append(f"{key} already exists in ~/.hermes/.env and will be overwritten.")
    if exists and not conflicts:
        warnings.append("This will fill an existing empty integration section in config.yaml.")

    preview_payload = {
        "draft": draft,
        "integration": {
            **integration_entry,
            "config_raw": copy.deepcopy(draft["config"]),
            "env_vars": [
                {key: value for key, value in entry.items() if key != "value"}
                for entry in (draft.get("env_vars") or [])
            ],
            "readiness": readiness,
        },
        "writes": writes,
        "conflicts": conflicts,
    }
    preview_token = capability_preview_token_fn("integration", preview_payload)
    return {
        "ok": True,
        "type": "integration",
        "phase": "Phase 2",
        "preview_token": preview_token,
        "can_apply": not conflicts,
        "draft": draft,
        "summary": {
            "name": draft.get("label") or draft.get("kind") or "Integration",
            "kind": draft.get("kind") or "",
            "target_dir": str(config_path),
            "env_var_count": len(draft.get("env_vars") or []),
            "env_write_count": len([entry for entry in (draft.get("env_vars") or []) if str(entry.get("value") or "").strip()]),
            "configured": bool(integration_entry.get("configured")),
            "conflict_count": len(conflicts),
        },
        "warnings": warnings,
        "conflicts": conflicts,
        "writes": writes,
        "manifest": {
            "integration": {key: value for key, value in preview_payload["integration"].items() if key != "config_raw"},
            "integration_config": copy.deepcopy(draft["config"]),
        },
    }, 200


def apply_integration_capability(
    data: dict | None,
    preview_token: str,
    *,
    preview_integration_capability_fn,
    config_path,
    env_path,
    set_env_value_fn,
    cfg_set,
    restore_text_file_fn,
    cfg_load,
    integration_entries_fn,
) -> tuple[dict, int]:
    preview, status = preview_integration_capability_fn(data)
    if status != 200:
        return preview, status
    if not preview_token or preview_token != preview.get("preview_token"):
        return {"ok": False, "error": "Preview has changed. Refresh the draft preview before approval."}, 409
    if not preview.get("can_apply"):
        return {"ok": False, "error": "This integration is already configured. Edit it from Apps & Integrations instead."}, 409

    draft = preview.get("draft") or {}
    config_before = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    env_before = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    try:
        for entry in draft.get("env_vars") or []:
            value = str(entry.get("value") or "")
            if not value:
                continue
            env_path.parent.mkdir(parents=True, exist_ok=True)
            set_env_value_fn(env_path, entry.get("key") or "", value)
        cfg_set(str(draft.get("kind") or ""), copy.deepcopy(draft.get("config") or {}))
    except Exception:
        restore_text_file_fn(env_path, env_before)
        restore_text_file_fn(config_path, config_before)
        cfg_load()
        raise

    cfg_load()
    created = next(
        (entry for entry in integration_entries_fn() if entry.get("name") == draft.get("kind")),
        None,
    )
    return {
        "ok": True,
        "type": "integration",
        "created": {
            "name": draft.get("label") or draft.get("kind") or "Integration",
            "kind": draft.get("kind") or "",
            "target_dir": str(config_path),
            "files": [str(config_path)] + ([str(env_path)] if any(str(item.get("value") or "").strip() for item in (draft.get("env_vars") or [])) else []),
            "integration": created,
        },
    }, 200