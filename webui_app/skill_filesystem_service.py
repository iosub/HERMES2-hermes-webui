from __future__ import annotations


def safe_skill_rel_path(value, *, normalize_skill_rel_path_fn, pure_posix_path_class) -> str:
    text = normalize_skill_rel_path_fn(value)
    if not text:
        return ""
    parts = []
    for part in pure_posix_path_class(text).parts:
        if part in ("", "."):
            continue
        if part == "..":
            return ""
        parts.append(part)
    return "/".join(parts)


def skill_request_paths(requested, *, safe_skill_rel_path_fn, skills_dir) -> dict:
    requested_rel = safe_skill_rel_path_fn(requested)
    if not requested_rel:
        return {}

    requested_parts = requested_rel.split("/")
    base_name = requested_parts[-1]
    while base_name.endswith(".disabled"):
        base_name = base_name[:-9]
    if not base_name:
        return {}

    base_rel = "/".join(requested_parts[:-1] + [base_name])
    base_path = skills_dir / base_rel
    disabled_rel = base_rel + ".disabled"
    disabled_path = skills_dir / disabled_rel

    variants = []
    seen = set()
    for rel in (requested_rel, base_rel, disabled_rel):
        if rel and rel not in seen:
            variants.append(skills_dir / rel)
            seen.add(rel)

    parent_dir = base_path.parent
    if parent_dir.exists():
        disabled_prefix = base_path.name + ".disabled"
        for sibling in sorted(parent_dir.iterdir(), key=lambda item: item.name):
            if not sibling.is_dir() or not sibling.name.startswith(disabled_prefix):
                continue
            sibling_rel = safe_skill_rel_path_fn(sibling.relative_to(skills_dir))
            if sibling_rel and sibling_rel not in seen:
                variants.append(sibling)
                seen.add(sibling_rel)

    return {
        "requested_rel": requested_rel,
        "base_rel": base_rel,
        "disabled_rel": disabled_rel,
        "base_path": base_path,
        "disabled_path": disabled_path,
        "variants": variants,
    }


def replace_skill_dir(src, dst, *, shutil_module) -> None:
    if dst.exists() and dst != src:
        shutil_module.rmtree(dst)
    shutil_module.move(str(src), str(dst))


def skill_apply_action(requested, action: str, *, skill_request_paths_fn, replace_skill_dir_fn, safe_skill_rel_path_fn, skills_dir, shutil_module) -> dict:
    info = skill_request_paths_fn(requested)
    if not info:
        return {"found": False, "error": "Skill path is required"}

    base_path = info["base_path"]
    disabled_path = info["disabled_path"]
    existing_variants = [path for path in info["variants"] if path.exists()]
    action_name = str(action or "").strip().lower()

    if action_name == "enable":
        if base_path.exists():
            return {"found": True, "changed": False, "enabled": True, "path": info["base_rel"]}
        candidate = next((path for path in existing_variants if path != base_path), None)
        if not candidate:
            return {"found": False, "error": f"Skill '{requested}' not found"}
        replace_skill_dir_fn(candidate, base_path)
        return {"found": True, "changed": True, "enabled": True, "path": info["base_rel"]}

    if action_name == "disable":
        if base_path.exists():
            replace_skill_dir_fn(base_path, disabled_path)
            return {"found": True, "changed": True, "enabled": False, "path": info["disabled_rel"]}
        candidate = next((path for path in existing_variants if path != base_path), None)
        if candidate:
            candidate_rel = safe_skill_rel_path_fn(candidate.relative_to(skills_dir))
            return {"found": True, "changed": False, "enabled": False, "path": candidate_rel}
        return {"found": False, "error": f"Skill '{requested}' not found"}

    if action_name == "remove":
        target = base_path if base_path.exists() else next(iter(existing_variants), None)
        if not target:
            return {"found": False, "error": f"Skill '{requested}' not found"}
        removed_rel = safe_skill_rel_path_fn(target.relative_to(skills_dir))
        shutil_module.rmtree(target)
        return {"found": True, "changed": True, "removed": True, "path": removed_rel}

    return {"found": False, "error": f"Unsupported action '{action}'"}