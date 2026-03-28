"""
修仙游戏核心服务器 - 应用入口

职责：Flask app 创建、核心组件初始化、服务器生命周期管理。
所有 API 路由已迁移至 core/routes/ 下的 Blueprint 模块。
"""

import os
import sys
import time
import threading
import logging
import hmac
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server
from core.config import config
from core.utils.runtime_logging import setup_runtime_logging

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = setup_runtime_logging("core", project_root=PROJECT_ROOT, stats_interval_seconds=180)


def is_internal_request_authorized(provided_token: str) -> bool:
    expected = (config.internal_api_token or "").strip()
    if not expected:
        return True
    provided = (provided_token or "").strip()
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def create_app() -> Flask:
    """Flask 应用工厂函数。"""
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False

    @app.before_request
    def _verify_internal_api_token():
        if request.method == "OPTIONS":
            return None
        # Keep health probes accessible without token.
        if request.path in {"/health", "/api/health"}:
            return None
        if not is_internal_request_authorized(request.headers.get("X-Internal-Token", "")):
            return jsonify({
                "success": False,
                "code": "UNAUTHORIZED",
                "message": "Unauthorized request",
            }), 401
        return None

    @app.errorhandler(HTTPException)
    def _handle_http_exception(exc: HTTPException):
        logger.warning(
            "HTTP error path=%s method=%s status=%s description=%s",
            request.path,
            request.method,
            exc.code,
            exc.description,
        )
        return jsonify({
            "success": False,
            "code": str(getattr(exc, "name", "HTTP_ERROR")).upper().replace(" ", "_"),
            "message": exc.description,
            "status_code": int(exc.code or 500),
            "path": request.path,
        }), int(exc.code or 500)

    @app.errorhandler(Exception)
    def _handle_unexpected_exception(exc: Exception):
        logger.exception(
            "Unhandled core exception path=%s method=%s",
            request.path,
            request.method,
            exc_info=exc,
        )
        return jsonify({
            "success": False,
            "code": "INTERNAL_SERVER_ERROR",
            "message": "Internal server error",
            "status_code": 500,
            "path": request.path,
        }), 500

    from core.routes import register_blueprints
    register_blueprints(app)

    return app


core_app: Optional[Flask] = None

server_running = False
server_thread: Optional[threading.Thread] = None
_wsgi_server = None
_server_start_event: Optional[threading.Event] = None
_server_start_error: Optional[Exception] = None


def initialize_core_components() -> bool:
    try:
        from core.database.connection import connect_db, create_tables
        from core.database.migrations import run_migrations
        from core.game.mechanics import initialize_game_mechanics

        logger.info("Initialising core components ...")
        connect_db()
        create_tables()
        applied = run_migrations()
        if applied:
            logger.info(f"Applied migrations: {', '.join(applied)}")
        else:
            logger.info("No schema migrations pending")
        initialize_game_mechanics()
        logger.info("Core components initialised")
        return True
    except Exception as exc:
        logger.error(f"Failed to initialise core components: {exc}", exc_info=True)
        logger.critical("Aborting: cannot start on a broken schema or missing components.")
        sys.exit(1)


def load_config():
    """兼容函数：返回统一配置的 raw dict。"""
    return config.raw


def run_core_server():
    global server_running, _wsgi_server, core_app, _server_start_event, _server_start_error
    server_running = True
    try:
        port = config.core_server_port
        if core_app is None:
            core_app = create_app()
        logger.info(f"Starting core server on http://127.0.0.1:{port}")
        _wsgi_server = make_server("127.0.0.1", port, core_app)
        _server_start_error = None
        if _server_start_event is not None:
            _server_start_event.set()
        _wsgi_server.serve_forever()
    except Exception as exc:
        _server_start_error = exc
        if _server_start_event is not None:
            _server_start_event.set()
        logger.error(f"Core server error: {exc}")
    finally:
        server_running = False
        if _wsgi_server is not None:
            try:
                _wsgi_server.server_close()
            except Exception:
                pass
            _wsgi_server = None


def start_server() -> bool:
    global server_thread, server_running, _server_start_event, _server_start_error
    if server_running:
        logger.info("Core server already running")
        return True
    if not initialize_core_components():
        return False
    _server_start_error = None
    _server_start_event = threading.Event()
    server_thread = threading.Thread(target=run_core_server, daemon=True)
    server_thread.start()
    if not _server_start_event.wait(timeout=3):
        logger.error("Core server startup timed out before bind confirmation")
        return False
    if _server_start_error is not None or _wsgi_server is None:
        logger.error(f"Core server failed to bind: {_server_start_error}")
        return False
    logger.info("Core server started")
    return True


def stop_server():
    global server_running, _wsgi_server
    server_running = False
    if _wsgi_server is not None:
        try:
            _wsgi_server.shutdown()
        except Exception:
            pass
    logger.info("Core server stopped")


if __name__ == "__main__":
    if start_server():
        try:
            while server_running:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_server()
