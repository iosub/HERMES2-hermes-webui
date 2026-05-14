from __future__ import annotations

from flask import jsonify


def register_frontend_routes(app, *, index_path) -> None:
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def catch_all(path):
        if path.startswith("api/"):
            return jsonify({"error": "Not found"}), 404
        index = index_path()
        if index.exists():
            return index.read_text(encoding="utf-8")
        return jsonify({"error": "Frontend not built yet"}), 404