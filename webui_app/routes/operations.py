from __future__ import annotations

import glob
import os
from datetime import datetime
import uuid

from dotenv import dotenv_values
from flask import jsonify, request


def register_operations_routes(app, *, require_token, http_error, deps) -> None:
    integration_entries = deps["integration_entries"]
    cfg_get_raw = deps["cfg_get_raw"]
    cfg_get = deps["cfg_get"]
    cfg_set = deps["cfg_set"]
    preserve_masked_secret_updates = deps["preserve_masked_secret_updates"]
    integration_section_labels = deps["integration_section_labels"]
    sessions_dir = deps["sessions_dir"]
    log_file_keys = deps["log_file_keys"]
    resolve_log_path = deps["resolve_log_path"]
    read_log_file = deps["read_log_file"]
    selected_hermes_home = deps["selected_hermes_home"]
    crontab_available = deps["crontab_available"]
    load_cron_jobs = deps["load_cron_jobs"]
    validate_cron_job_payload = deps["validate_cron_job_payload"]
    write_cron_jobs = deps["write_cron_jobs"]
    sync_cron_jobs_to_system = deps["sync_cron_jobs_to_system"]
    chat_backend_error = deps["chat_backend_error"]
    run_hermes = deps["run_hermes"]
    selected_hermes_bin = deps["selected_hermes_bin"]
    selected_gateway_log_path = deps["selected_gateway_log_path"]
    gateway_status = deps["gateway_status"]
    selected_hermes_home_for_service = deps["selected_hermes_home_for_service"]
    time_module = deps["time_module"]
    popen = deps["popen"]
    timeout_expired = deps["timeout_expired"]
    normalized_model_config = deps["normalized_model_config"]
    env_path = deps["env_path"]
    secret_patterns = deps["secret_patterns"]

    @app.route("/api/channels", methods=["GET"])
    @require_token
    def api_channels_get():
        try:
            integrations = integration_entries()
            return jsonify({"channels": integrations, "integrations": integrations})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/channels/<name>", methods=["PUT"])
    @require_token
    def api_channels_update(name):
        try:
            data = request.get_json(force=True)
            raw = cfg_get_raw()
            channels_cfg = raw.get("channels", {})
            if not isinstance(data, dict):
                return jsonify({"ok": False, "error": "Integration config must be a JSON object"}), 400

            if name in channels_cfg and isinstance(channels_cfg.get(name), dict):
                current = channels_cfg.get(name, {})
                channels_cfg[name] = preserve_masked_secret_updates(current, data)
                cfg_set("channels", channels_cfg)
                return jsonify({"ok": True})

            if name in integration_section_labels and isinstance(raw.get(name), dict):
                current = raw.get(name, {})
                cfg_set(name, preserve_masked_secret_updates(current, data))
                return jsonify({"ok": True})

            return jsonify({"ok": False, "error": f"Integration '{name}' not found"}), 404
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/sessions", methods=["GET"])
    @require_token
    def api_sessions_get():
        try:
            sessions = []
            session_root = sessions_dir()
            if session_root.is_dir():
                files = sorted(glob.glob(str(session_root / "*.json")), key=os.path.getmtime, reverse=True)[:50]
                for path in files:
                    name = os.path.splitext(os.path.basename(path))[0]
                    sessions.append({"id": name, "title": name})
            return jsonify({"sessions": sessions})
        except Exception:
            return jsonify({"sessions": []})

    @app.route("/api/sessions/config", methods=["GET"])
    @require_token
    def api_sessions_config_get():
        try:
            return jsonify(cfg_get("session_reset") or {})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/sessions/config", methods=["PUT"])
    @require_token
    def api_sessions_config_put():
        try:
            cfg_set("session_reset", request.get_json(force=True))
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/hooks", methods=["GET"])
    @require_token
    def api_hooks_get():
        try:
            hooks = cfg_get("hooks")
            return jsonify({"config": hooks if isinstance(hooks, dict) else {}})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/hooks", methods=["PUT"])
    @require_token
    def api_hooks_put():
        try:
            cfg_set("hooks", request.get_json(force=True))
            return jsonify({"ok": True})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/logs")
    @require_token
    def api_logs_get():
        try:
            lines = request.args.get("lines", 200, type=int)
            lines = max(10, min(lines, 2000))
            file_key = request.args.get("file", "").strip().lower()

            log_text = ""
            if file_key and file_key in log_file_keys:
                path = resolve_log_path(file_key)
                if path and path.exists():
                    log_text = read_log_file(path, lines) or ""
            else:
                log_candidates = [
                    selected_hermes_home() / "logs" / "agent.log",
                    selected_hermes_home() / "logs" / "gateway.log",
                    selected_hermes_home() / "logs" / "errors.log",
                    selected_hermes_home() / "gateway.log",
                ]
                for candidate in log_candidates:
                    content = read_log_file(candidate, lines)
                    if content:
                        log_text = content
                        break

            return jsonify({
                "logs": log_text,
                "source": "log_files",
                "source_detail": "Tail of Hermes log files under ~/.hermes/logs when present.",
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/logs/clear", methods=["POST"])
    @require_token
    def api_logs_clear():
        try:
            body = request.get_json(silent=True) or {}
            file_key = (body.get("file") or "").strip().lower()
            if file_key not in log_file_keys:
                return jsonify({"error": "Invalid log file key. Allowed: " + ", ".join(log_file_keys)}), 400
            path = resolve_log_path(file_key)
            if path is None:
                return jsonify({"error": "Could not resolve log path"}), 400
            if path.exists():
                path.write_text("", encoding="utf-8")
            return jsonify({"ok": True, "file": file_key})
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/cron/jobs", methods=["GET"])
    @require_token
    def api_cron_jobs():
        try:
            if not crontab_available():
                return jsonify({"available": False, "jobs": [], "error": "crontab is not installed"}), 501
            jobs = sorted(load_cron_jobs().values(), key=lambda job: job.get("updated", ""), reverse=True)
            return jsonify({"available": True, "jobs": jobs})
        except chat_backend_error as exc:
            return jsonify({"error": str(exc)}), exc.status_code

    @app.route("/api/cron/jobs", methods=["POST"])
    @require_token
    def api_cron_jobs_create():
        try:
            payload, errors = validate_cron_job_payload(request.get_json() or {})
            if errors:
                return jsonify({"error": "Invalid cron job", "details": errors}), 400
            jobs = load_cron_jobs()
            now = datetime.now().isoformat()
            job_id = str(uuid.uuid4())[:8]
            jobs[job_id] = {
                "id": job_id,
                "name": payload["name"],
                "schedule": payload["schedule"],
                "command": payload["command"],
                "enabled": payload["enabled"],
                "created": now,
                "updated": now,
            }
            write_cron_jobs(jobs)
            sync_cron_jobs_to_system(jobs)
            return jsonify({"ok": True, "job": jobs[job_id]})
        except chat_backend_error as exc:
            return jsonify({"error": str(exc)}), exc.status_code

    @app.route("/api/cron/jobs/<job_id>", methods=["PUT"])
    @require_token
    def api_cron_job_update(job_id):
        try:
            jobs = load_cron_jobs()
            job = jobs.get(job_id)
            if not job:
                return jsonify({"error": "Cron job not found"}), 404
            payload, errors = validate_cron_job_payload(request.get_json() or {})
            if errors:
                return jsonify({"error": "Invalid cron job", "details": errors}), 400
            job.update(payload)
            job["updated"] = datetime.now().isoformat()
            jobs[job_id] = job
            write_cron_jobs(jobs)
            sync_cron_jobs_to_system(jobs)
            return jsonify({"ok": True, "job": job})
        except chat_backend_error as exc:
            return jsonify({"error": str(exc)}), exc.status_code

    @app.route("/api/cron/jobs/<job_id>", methods=["DELETE"])
    @require_token
    def api_cron_job_delete(job_id):
        try:
            jobs = load_cron_jobs()
            if job_id not in jobs:
                return jsonify({"error": "Cron job not found"}), 404
            jobs.pop(job_id, None)
            write_cron_jobs(jobs)
            sync_cron_jobs_to_system(jobs)
            return jsonify({"ok": True})
        except chat_backend_error as exc:
            return jsonify({"error": str(exc)}), exc.status_code

    @app.route("/api/tools", methods=["GET"])
    @require_token
    def api_tools_get():
        try:
            tools = []
            total_enabled = 0
            total_disabled = 0

            try:
                result = run_hermes("tools", "list", timeout=15)
                output = result.stdout if result.returncode == 0 else result.stderr
            except Exception:
                output = ""

            if output:
                for line in output.strip().splitlines():
                    line = line.strip()
                    if not line or line.startswith("-") or line.startswith("="):
                        continue
                    parts = line.split(None, 2)
                    if len(parts) >= 2:
                        tool_name = parts[0]
                        status = parts[1].lower()
                        desc = parts[2] if len(parts) > 2 else ""
                        is_enabled = status in ("enabled", "active", "on", "✓", "yes")
                        tools.append({
                            "name": tool_name,
                            "status": "enabled" if is_enabled else "disabled",
                            "description": desc,
                        })
                        if is_enabled:
                            total_enabled += 1
                        else:
                            total_disabled += 1

            return jsonify({
                "tools": tools,
                "total_enabled": total_enabled,
                "total_disabled": total_disabled,
                "source": "parsed_cli_output",
                "source_detail": "Parsed from `hermes tools list` text output.",
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/service/<action>", methods=["POST"])
    @require_token
    def api_service_action(action):
        try:
            action = action.lower()
            time_api = time_module()

            if action == "start":
                bin_path = selected_hermes_bin()
                env = {**os.environ, "HERMES_HOME": str(selected_hermes_home_for_service())}
                log_path = selected_gateway_log_path()
                log_path.parent.mkdir(exist_ok=True)
                run_hermes("gateway", "stop", timeout=10)
                time_api.sleep(1)
                with open(log_path, "a", encoding="utf-8") as log_file:
                    popen(
                        [str(bin_path), "gateway", "run"],
                        env=env,
                        stdout=log_file,
                        stderr=-2,
                        start_new_session=True,
                    )
                time_api.sleep(3)
                running = gateway_status()["running"]
                return jsonify({"ok": running, "output": "Gateway started", "gateway_running": running})

            cmd_map = {
                "stop": ["gateway", "stop"],
                "restart": ["gateway", "stop"],
                "doctor": ["doctor"],
            }
            if action not in cmd_map:
                return jsonify({"ok": False, "error": f"Unknown action: {action}"}), 400

            result = run_hermes(*cmd_map[action], timeout=30)
            output = (result.stdout + "\n" + result.stderr).strip()
            running_after = gateway_status()["running"]
            ok = result.returncode == 0

            if action == "restart":
                bin_path = selected_hermes_bin()
                env = {**os.environ, "HERMES_HOME": str(selected_hermes_home_for_service())}
                log_path = selected_gateway_log_path()
                log_path.parent.mkdir(exist_ok=True)
                time_api.sleep(1)
                with open(log_path, "a", encoding="utf-8") as log_file:
                    popen(
                        [str(bin_path), "gateway", "run"],
                        env=env,
                        stdout=log_file,
                        stderr=-2,
                        start_new_session=True,
                    )
                time_api.sleep(3)
                running_after = gateway_status()["running"]
                output = "Restarted (running: " + str(running_after) + ")"
                ok = running_after
            elif action == "stop":
                ok = not running_after
            elif action == "doctor":
                ok = result.returncode == 0

            return jsonify({"ok": ok, "output": output, "returncode": result.returncode, "gateway_running": running_after})
        except timeout_expired:
            return jsonify({"ok": False, "error": "Command timed out", "gateway_running": gateway_status()["running"]}), 500
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/onboarding", methods=["GET"])
    @require_token
    def api_onboarding_get():
        try:
            raw = cfg_get_raw()
            resolved_env_path = env_path()
            env_vars = dotenv_values(str(resolved_env_path)) if resolved_env_path.exists() else {}
            missing = []

            has_api_key = any(v for key, v in env_vars.items() if v and secret_patterns.search(key))
            model_cfg = normalized_model_config()
            if not has_api_key and not model_cfg.get("api_key"):
                missing.append("api_key")
            if not model_cfg.get("default_provider"):
                missing.append("default_provider")
            if not model_cfg.get("default_model"):
                missing.append("default_model")

            integrations = integration_entries(raw)
            if not any(item.get("configured") for item in integrations):
                missing.append("channel")

            return jsonify({
                "complete": len(missing) == 0,
                "missing": missing,
            })
        except Exception as exc:
            return http_error(str(exc))