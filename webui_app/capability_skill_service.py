from __future__ import annotations

import copy


def normalize_capability_credential_file(entry, *, safe_skill_rel_path_fn, path_class) -> dict:
    if isinstance(entry, str):
        entry = {"path": entry}
    if not isinstance(entry, dict):
        return {}
    rel_path = safe_skill_rel_path_fn(entry.get("path") or "")
    if not rel_path:
        return {}
    label = str(entry.get("label") or path_class(rel_path).name).strip() or path_class(rel_path).name
    description = str(entry.get("description") or "").strip()
    return {
        "path": rel_path,
        "label": label,
        "description": description,
    }


def normalize_capability_required_command(entry) -> dict:
    if isinstance(entry, str):
        entry = {"name": entry}
    if not isinstance(entry, dict):
        return {}
    name = str(entry.get("name") or "").strip()
    if not name:
        return {}
    description = str(entry.get("description") or "").strip()
    return {
        "name": name,
        "description": description,
    }


def normalize_skill_capability_draft(
    data: dict | None,
    *,
    slugify_capability_fn,
    normalize_capability_env_var_fn,
    normalize_capability_credential_file_fn,
    normalize_capability_required_command_fn,
) -> tuple[dict, list[str]]:
    payload = data if isinstance(data, dict) else {}
    name = str(payload.get("name") or "").strip()
    slug = slugify_capability_fn(str(payload.get("slug") or "").strip() or name)
    category = str(payload.get("category") or "").strip()
    description = str(payload.get("description") or "").strip()
    instructions = str(payload.get("instructions") or "").strip()
    include_scripts = bool(payload.get("include_scripts"))
    include_references = bool(payload.get("include_references"))

    env_vars = []
    seen_env_keys = set()
    for entry in payload.get("env_vars") if isinstance(payload.get("env_vars"), list) else []:
        normalized = normalize_capability_env_var_fn(entry)
        key = normalized.get("key")
        if not key or key in seen_env_keys:
            continue
        seen_env_keys.add(key)
        env_vars.append(normalized)

    credential_files = []
    seen_paths = set()
    for entry in payload.get("credential_files") if isinstance(payload.get("credential_files"), list) else []:
        normalized = normalize_capability_credential_file_fn(entry)
        rel_path = normalized.get("path")
        if not rel_path or rel_path in seen_paths:
            continue
        seen_paths.add(rel_path)
        credential_files.append(normalized)

    required_commands = []
    seen_commands = set()
    for entry in payload.get("required_commands") if isinstance(payload.get("required_commands"), list) else []:
        normalized = normalize_capability_required_command_fn(entry)
        command_name = normalized.get("name")
        if not command_name or command_name in seen_commands:
            continue
        seen_commands.add(command_name)
        required_commands.append(normalized)

    normalized = {
        "name": name[:120].rstrip(),
        "slug": slug,
        "category": category[:120].rstrip(),
        "description": description[:400].rstrip(),
        "instructions": instructions[:12000].rstrip(),
        "env_vars": env_vars,
        "credential_files": credential_files,
        "required_commands": required_commands,
        "include_scripts": include_scripts,
        "include_references": include_references,
    }
    errors = []
    if not normalized["name"]:
        errors.append("Skill name is required")
    if not normalized["slug"]:
        errors.append("Skill slug is required")
    return normalized, errors


def render_skill_capability_frontmatter(draft: dict) -> dict:
    frontmatter = {
        "name": draft.get("name") or "",
        "description": draft.get("description") or f"{draft.get('name') or 'Skill'} created in Hermes Web UI.",
    }
    if draft.get("category"):
        frontmatter["category"] = draft["category"]
    if draft.get("env_vars"):
        frontmatter["prerequisites"] = {
            "env_vars": [entry.get("key") for entry in draft["env_vars"] if entry.get("key")],
        }
    if draft.get("credential_files"):
        frontmatter["required_credential_files"] = [
            {key: value for key, value in entry.items() if value not in (None, "", [])}
            for entry in draft["credential_files"]
        ]

    metadata = {
        "hermes_web_ui": {
            "capability_type": "skill",
            "schema_version": 1,
            "created_via": "hermes-web-ui",
            "setup": {},
        }
    }
    if draft.get("required_commands"):
        metadata["openclaw"] = {
            "requires": {
                "bins": [entry.get("name") for entry in draft["required_commands"] if entry.get("name")],
            }
        }
    setup = metadata["hermes_web_ui"]["setup"]
    if draft.get("env_vars"):
        setup["env_vars"] = draft["env_vars"]
    if draft.get("credential_files"):
        setup["credential_files"] = draft["credential_files"]
    if draft.get("required_commands"):
        setup["required_commands"] = draft["required_commands"]
    if draft.get("include_scripts") or draft.get("include_references"):
        setup["folders"] = {
            "scripts": bool(draft.get("include_scripts")),
            "references": bool(draft.get("include_references")),
        }
    if setup:
        frontmatter["metadata"] = metadata
    return frontmatter


def render_skill_capability_markdown(draft: dict, frontmatter: dict, *, yaml_module) -> str:
    frontmatter_yaml = yaml_module.safe_dump(
        frontmatter,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    instructions = draft.get("instructions") or (
        f"Describe when to use the {draft.get('name') or 'skill'} capability, what steps it should follow, "
        "and any constraints or output expectations."
    )
    lines = [
        "---",
        frontmatter_yaml,
        "---",
        "",
        f"# {draft.get('name') or 'New Skill'}",
        "",
        draft.get("description") or "Reusable Hermes skill created in Hermes Web UI.",
        "",
        "## Instructions",
        instructions,
    ]

    env_vars = draft.get("env_vars") or []
    credential_files = draft.get("credential_files") or []
    required_commands = draft.get("required_commands") or []
    if env_vars or credential_files or required_commands:
        lines.extend(["", "## Setup Requirements"])
        if env_vars:
            lines.append("")
            lines.append("### Environment Variables")
            for entry in env_vars:
                detail = str(entry.get("description") or "").strip()
                label = str(entry.get("label") or entry.get("key") or "").strip()
                line = f"- `{entry.get('key')}`"
                if label and label != entry.get("key"):
                    line += f" - {label}"
                if detail:
                    line += f": {detail}"
                lines.append(line)
        if credential_files:
            lines.append("")
            lines.append("### Credential Files")
            for entry in credential_files:
                detail = str(entry.get("description") or "").strip()
                line = f"- `{entry.get('path')}`"
                if detail:
                    line += f": {detail}"
                lines.append(line)
        if required_commands:
            lines.append("")
            lines.append("### Required Commands")
            for entry in required_commands:
                detail = str(entry.get("description") or "").strip()
                line = f"- `{entry.get('name')}`"
                if detail:
                    line += f": {detail}"
                lines.append(line)

    included_folders = []
    if draft.get("include_scripts"):
        included_folders.append("`scripts/` for helper automation")
    if draft.get("include_references"):
        included_folders.append("`references/` for docs and examples")
    if included_folders:
        lines.extend(["", "## Included Folders"])
        lines.extend([f"- {item}" for item in included_folders])

    return "\n".join(lines).rstrip() + "\n"


def capability_skill_source_metadata(*, build_skill_source_record_fn) -> dict:
    return build_skill_source_record_fn(
        "hermes-web-ui/create-skill",
        install_mode="webui_create",
        display="Hermes Web UI",
        catalog_source="create-capability",
    )


def capability_skill_conflicts(slug: str, *, skill_request_paths_fn, path_class) -> list[str]:
    info = skill_request_paths_fn(slug)
    if not info:
        return []
    return [
        str(path)
        for path in info.get("variants") or []
        if isinstance(path, path_class) and path.exists()
    ]


def preview_skill_capability(
    data: dict | None,
    *,
    normalize_skill_capability_draft_fn,
    render_skill_capability_frontmatter_fn,
    render_skill_capability_markdown_fn,
    capability_skill_source_metadata_fn,
    skill_setup_readiness_fn,
    skills_dir,
    capability_skill_conflicts_fn,
    capability_preview_token_fn,
) -> tuple[dict, int]:
    draft, errors = normalize_skill_capability_draft_fn(data)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}, 400

    frontmatter = render_skill_capability_frontmatter_fn(draft)
    skill_md = render_skill_capability_markdown_fn(draft, frontmatter)
    source = capability_skill_source_metadata_fn()
    skill = {
        "name": frontmatter.get("name") or draft.get("name") or draft.get("slug") or "Skill",
        "category": frontmatter.get("category") or "",
        "description": frontmatter.get("description") or "",
        "path": draft.get("slug") or "",
        "enabled": True,
        "frontmatter": frontmatter,
        "source": source,
    }
    skill["setup"] = skill_setup_readiness_fn(skill)

    target_dir = skills_dir / draft["slug"]
    writes = [{
        "kind": "directory",
        "path": str(target_dir),
        "action": "create",
        "label": "Skill folder",
    }, {
        "kind": "file",
        "path": str(target_dir / "SKILL.md"),
        "action": "create",
        "label": "Skill instructions",
        "content": skill_md,
    }]
    if draft.get("include_scripts"):
        writes.append({
            "kind": "directory",
            "path": str(target_dir / "scripts"),
            "action": "create",
            "label": "Optional scripts folder",
        })
    if draft.get("include_references"):
        writes.append({
            "kind": "directory",
            "path": str(target_dir / "references"),
            "action": "create",
            "label": "Optional references folder",
        })

    conflicts = capability_skill_conflicts_fn(draft["slug"])
    warnings = []
    if conflicts:
        warnings.append("A skill already exists for this slug. Change the slug before approval.")

    preview_payload = {
        "draft": draft,
        "skill": {
            **skill,
            "setup": skill.get("setup") or {},
        },
        "writes": writes,
        "conflicts": conflicts,
    }
    preview_token = capability_preview_token_fn("skill", preview_payload)
    return {
        "ok": True,
        "type": "skill",
        "phase": "Phase 1",
        "preview_token": preview_token,
        "can_apply": not conflicts,
        "draft": draft,
        "summary": {
            "name": draft.get("name") or draft.get("slug") or "Skill",
            "slug": draft.get("slug") or "",
            "target_dir": str(target_dir),
            "description": frontmatter.get("description") or "",
            "conflict_count": len(conflicts),
            "env_var_count": len(draft.get("env_vars") or []),
            "credential_file_count": len(draft.get("credential_files") or []),
            "required_command_count": len(draft.get("required_commands") or []),
        },
        "warnings": warnings,
        "conflicts": conflicts,
        "writes": writes,
        "manifest": {
            "skill": skill,
        },
    }, 200


def apply_skill_capability(
    data: dict | None,
    preview_token: str,
    *,
    preview_skill_capability_fn,
    skills_dir,
    uuid_module,
    write_skill_source_metadata_fn,
    capability_skill_source_metadata_fn,
    discover_skill_entries_fn,
    shutil_module,
) -> tuple[dict, int]:
    preview, status = preview_skill_capability_fn(data)
    if status != 200:
        return preview, status
    if not preview_token or preview_token != preview.get("preview_token"):
        return {"ok": False, "error": "Preview has changed. Refresh the draft preview before approval."}, 409
    if not preview.get("can_apply"):
        return {"ok": False, "error": "This skill slug is already taken. Change the slug and preview again."}, 409

    draft = preview.get("draft") or {}
    target_dir = skills_dir / str(draft.get("slug") or "")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        return {"ok": False, "error": "This skill already exists on disk."}, 409
    tmp_dir = target_dir.parent / f".{target_dir.name}.tmp-{uuid_module.uuid4().hex[:8]}"
    skill_md_content = next(
        (entry.get("content") for entry in (preview.get("writes") or []) if entry.get("path") == str(target_dir / "SKILL.md")),
        "",
    )
    try:
        tmp_dir.mkdir(parents=False, exist_ok=False)
        (tmp_dir / "SKILL.md").write_text(skill_md_content, encoding="utf-8")
        if draft.get("include_scripts"):
            (tmp_dir / "scripts").mkdir(exist_ok=True)
        if draft.get("include_references"):
            (tmp_dir / "references").mkdir(exist_ok=True)
        write_skill_source_metadata_fn(tmp_dir, capability_skill_source_metadata_fn())
        tmp_dir.rename(target_dir)
    except Exception:
        if tmp_dir.exists():
            shutil_module.rmtree(tmp_dir, ignore_errors=True)
        raise

    try:
        created_skill = next(
            (entry for entry in discover_skill_entries_fn() if entry.get("path") == draft.get("slug")),
            None,
        )
    except Exception:
        created_skill = copy.deepcopy(((preview.get("manifest") or {}).get("skill") if isinstance(preview.get("manifest"), dict) else {}) or None)
    return {
        "ok": True,
        "type": "skill",
        "created": {
            "name": draft.get("name") or draft.get("slug") or "Skill",
            "slug": draft.get("slug") or "",
            "target_dir": str(target_dir),
            "files": [entry.get("path") for entry in (preview.get("writes") or []) if entry.get("path")],
            "skill": created_skill,
        },
    }, 200