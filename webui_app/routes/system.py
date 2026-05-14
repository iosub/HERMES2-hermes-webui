from __future__ import annotations

from pathlib import Path
import shutil
import threading

from flask import jsonify, request


def register_system_routes(
    app,
    *,
    require_token,
    http_error,
    gateway_status,
    find_gateway_pid,
    run_hermes,
    selected_hermes_home,
    selected_hermes_bin,
    selected_hermes_profile_name,
    build_hermes_update_payload,
    runtime_snapshot,
    invalidate_hermes_update_cache,
    set_update_runtime,
    utc_now_z,
    run_hermes_update_worker,
    threading_module,
) -> None:
    @app.route("/api/health")
    @require_token
    def api_health():
        try:
            gs = gateway_status()
            pid = gs["pid"]
            if pid is None:
                pid = find_gateway_pid()
            version = "unknown"
            try:
                result = run_hermes("--version", timeout=5)
                if result.returncode == 0:
                    version = result.stdout.strip() or result.stderr.strip()
            except Exception:
                pass
            return jsonify({
                "status": "running" if gs["running"] else "stopped",
                "gateway_pid": pid,
                "gateway_running": gs["running"],
                "version": version,
                "hermes_home": str(selected_hermes_home()),
                "hermes_bin": str(selected_hermes_bin()),
                "profile": selected_hermes_profile_name(),
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/system")
    @require_token
    def api_system():
        try:
            import platform

            disk = shutil.disk_usage(str(Path.home()))
            uptime = 0
            try:
                with open("/proc/uptime") as fh:
                    uptime = float(fh.read().split()[0])
            except Exception:
                pass
            return jsonify({
                "python_version": platform.python_version(),
                "os_info": f"{platform.system()} {platform.release()} ({platform.machine()})",
                "disk_free": disk.free,
                "uptime": uptime,
            })
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/hermes/update-status")
    @require_token
    def api_hermes_update_status():
        try:
            refresh = str(request.args.get("refresh") or "").strip().lower() in {"1", "true", "yes", "on"}
            return jsonify(build_hermes_update_payload(force_refresh=refresh))
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/hermes/update-check", methods=["POST"])
    @require_token
    def api_hermes_update_check():
        try:
            return jsonify(build_hermes_update_payload(force_refresh=True))
        except Exception as exc:
            return http_error(str(exc))

    @app.route("/api/hermes/update", methods=["POST"])
    @require_token
    def api_hermes_update():
        try:
            data = request.get_json(silent=True) or {}
            if not data.get("confirm"):
                return jsonify({"ok": False, "error": "Update confirmation required."}), 400

            current = build_hermes_update_payload(force_refresh=True)
            if not current.get("can_update"):
                return jsonify({
                    "ok": False,
                    "error": current.get("manual_reason") or "In-app Hermes updating is not supported for this install.",
                    "manual_command": current.get("manual_command") or "",
                    "status": current,
                }), 409

            runtime = runtime_snapshot()
            if runtime.get("status") == "update_in_progress":
                return jsonify({
                    "ok": True,
                    "message": runtime.get("summary") or "Hermes update already in progress.",
                    "status": build_hermes_update_payload(force_refresh=False),
                }), 202

            invalidate_hermes_update_cache(Path(current["project_root"]) if current.get("project_root") else None)
            set_update_runtime(
                status="update_in_progress",
                started_at=utc_now_z(),
                finished_at="",
                returncode=None,
                error="",
                summary=f"Starting Hermes update for {current.get('installed_version', {}).get('display') or 'the installed version'}...",
                logs=[],
                install_key=current.get("install_key") or "",
                installed_version_before=(current.get("installed_version") or {}).get("display") or "",
                installed_version_after="",
            )
            worker = threading_module().Thread(
                target=run_hermes_update_worker,
                args=(current,),
                daemon=True,
                name="hermes-update-worker",
            )
            worker.start()
            return jsonify({
                "ok": True,
                "message": "Hermes update started.",
                "status": build_hermes_update_payload(force_refresh=False),
            }), 202
        except Exception as exc:
            return http_error(str(exc))