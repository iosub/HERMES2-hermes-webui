from __future__ import annotations

import subprocess

from flask import jsonify, request


def register_skill_routes(
    app,
    *,
    require_token,
    http_error,
    discover_skill_entries,
    skill_request_paths,
    skill_apply_action,
    safe_skill_rel_path,
    normalize_skill_rel_path,
    run_hermes,
    combined_process_output,
    hermes_skill_install_failed,
    install_skills_from_github_repo,
    record_skill_install_source,
    match_skill_paths_for_identifier,
    starter_pack_skill_group,
    starter_pack_install_candidates,
    chat_runtime_status,
) -> None:
    @app.route("/api/skills", methods=["GET"])
    @require_token
    def api_skills_get():
        try:
            return jsonify({"skills": discover_skill_entries()})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/skills/install", methods=["POST"])
    @require_token
    def api_skill_install():
        try:
            data = request.get_json(force=True) or {}
            identifier = str(data.get("identifier") or "").strip()
            if not identifier:
                return jsonify({"ok": False, "error": "identifier is required"}), 400

            before_paths = {
                normalize_skill_rel_path(skill.get("path") or "")
                for skill in discover_skill_entries()
                if normalize_skill_rel_path(skill.get("path") or "")
            }
            result = run_hermes("skills", "install", identifier, "--yes", timeout=300)
            combined_output = combined_process_output(result)
            fallback = None
            if hermes_skill_install_failed(result, combined_output):
                fallback = install_skills_from_github_repo(identifier)
                if not fallback:
                    message = combined_output or f"Hermes skills install exited with status {result.returncode}"
                    return jsonify({"ok": False, "error": message}), 502

            skills = discover_skill_entries()
            after_paths = {
                normalize_skill_rel_path(skill.get("path") or "")
                for skill in skills
                if normalize_skill_rel_path(skill.get("path") or "")
            }
            installed_skill_paths = sorted(after_paths - before_paths)
            already_present_paths = []
            annotated_skill_paths = []

            if fallback:
                installed_skill_paths = list(fallback.get("installed_paths") or [])
                already_present_paths = list(fallback.get("skipped_paths") or [])
                annotated_skill_paths = list(dict.fromkeys(installed_skill_paths + already_present_paths))
            else:
                if installed_skill_paths:
                    annotated_skill_paths = record_skill_install_source(
                        installed_skill_paths,
                        identifier=identifier,
                        install_mode="hermes",
                    )
                else:
                    already_present_paths = match_skill_paths_for_identifier(identifier, skills)
                    if already_present_paths:
                        annotated_skill_paths = record_skill_install_source(
                            already_present_paths,
                            identifier=identifier,
                            install_mode="hermes",
                        )

            return jsonify({
                "ok": True,
                "identifier": identifier,
                "output": combined_output,
                "install_mode": (fallback or {}).get("mode", "hermes"),
                "fallback": fallback,
                "installed_skill_paths": installed_skill_paths,
                "already_present_paths": already_present_paths,
                "annotated_skill_paths": annotated_skill_paths,
                "skills": skills,
            })
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "error": "Hermes skills install timed out"}), 504
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 502
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/starter-pack/<item_id>/install", methods=["POST"])
    @require_token
    def api_starter_pack_install(item_id):
        try:
            group = starter_pack_skill_group(item_id)
            if not group:
                return jsonify({"ok": False, "error": "Starter-pack item not found"}), 404

            candidates = starter_pack_install_candidates(group)
            if not candidates:
                return jsonify({"ok": False, "error": "This starter-pack item does not support installation"}), 400

            requested_identifier = str((request.get_json(silent=True) or {}).get("identifier") or "").strip()
            candidate_map = {candidate["identifier"]: candidate for candidate in candidates}
            if requested_identifier:
                if requested_identifier not in candidate_map:
                    return jsonify({"ok": False, "error": "Unsupported starter-pack install target"}), 400
                chosen = candidate_map[requested_identifier]
            else:
                chosen = next((candidate for candidate in candidates if candidate.get("recommended")), candidates[0])

            before_paths = {
                normalize_skill_rel_path(skill.get("path") or "")
                for skill in discover_skill_entries()
                if normalize_skill_rel_path(skill.get("path") or "")
            }
            result = run_hermes("skills", "install", chosen["identifier"], "--yes", timeout=300)
            combined_output = combined_process_output(result)
            if hermes_skill_install_failed(result, combined_output):
                message = combined_output or f"Hermes skills install exited with status {result.returncode}"
                return jsonify({"ok": False, "error": message}), 502

            after_skills = discover_skill_entries()
            after_paths = {
                normalize_skill_rel_path(skill.get("path") or "")
                for skill in after_skills
                if normalize_skill_rel_path(skill.get("path") or "")
            }
            installed_skill_paths = sorted(after_paths - before_paths)
            already_present_paths = []
            annotated_skill_paths = []
            if installed_skill_paths:
                annotated_skill_paths = record_skill_install_source(
                    installed_skill_paths,
                    identifier=chosen["identifier"],
                    install_mode="hermes",
                    catalog_source=chosen.get("source") or "",
                )
            else:
                already_present_paths = match_skill_paths_for_identifier(chosen["identifier"], after_skills)
                if already_present_paths:
                    annotated_skill_paths = record_skill_install_source(
                        already_present_paths,
                        identifier=chosen["identifier"],
                        install_mode="hermes",
                        catalog_source=chosen.get("source") or "",
                    )

            runtime = chat_runtime_status()
            item = next(
                (entry for entry in (runtime.get("starter_pack", {}).get("items") or []) if entry.get("id") == group.get("id")),
                None,
            )
            return jsonify({
                "ok": True,
                "installed": True,
                "candidate": chosen,
                "item": item,
                "output": combined_output,
                "installed_skill_paths": installed_skill_paths,
                "already_present_paths": already_present_paths,
                "annotated_skill_paths": annotated_skill_paths,
                "setup_notes": [str(note).strip() for note in (group.get("setup_notes") or []) if str(note).strip()],
            })
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "error": "Hermes skills install timed out"}), 504
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/skills/<path:name>/toggle", methods=["POST"])
    @require_token
    def api_skill_toggle(name):
        try:
            info = skill_request_paths(name)
            if not info:
                return jsonify({"ok": False, "error": "Skill path is required"}), 400
            action = "disable" if info["base_path"].exists() else "enable"
            result = skill_apply_action(name, action)
            if not result.get("found"):
                return jsonify({"ok": False, "error": result.get("error") or f"Skill '{name}' not found"}), 404
            return jsonify({
                "ok": True,
                "enabled": bool(result.get("enabled")),
                "changed": bool(result.get("changed")),
                "path": result.get("path") or info.get("requested_rel"),
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/skills/bulk", methods=["POST"])
    @require_token
    def api_skill_bulk():
        try:
            data = request.get_json(force=True) or {}
            action = str(data.get("action") or "").strip().lower()
            if action not in {"enable", "disable", "remove"}:
                return jsonify({"ok": False, "error": "Unsupported bulk action"}), 400

            raw_paths = data.get("paths") if isinstance(data.get("paths"), list) else []
            paths = []
            seen = set()
            for entry in raw_paths:
                rel_path = safe_skill_rel_path(entry)
                if rel_path and rel_path not in seen:
                    paths.append(rel_path)
                    seen.add(rel_path)
            if not paths:
                return jsonify({"ok": False, "error": "At least one skill path is required"}), 400

            results = []
            changed_paths = []
            missing_paths = []
            removed_paths = []
            for rel_path in paths:
                result = skill_apply_action(rel_path, action)
                result["requested"] = rel_path
                results.append(result)
                if not result.get("found"):
                    missing_paths.append(rel_path)
                    continue
                if result.get("changed"):
                    changed_paths.append(result.get("path") or rel_path)
                if result.get("removed"):
                    removed_paths.append(result.get("path") or rel_path)

            return jsonify({
                "ok": True,
                "action": action,
                "results": results,
                "changed_count": len(changed_paths),
                "changed_paths": changed_paths,
                "removed_paths": removed_paths,
                "missing_paths": missing_paths,
                "skills": discover_skill_entries(),
            })
        except Exception as exc:
            return http_error(str(exc))