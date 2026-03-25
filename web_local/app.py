import os
import json
import sys
import io
import subprocess
import psutil
import logging
import datetime
import re
import functools
import hashlib
import secrets
import psycopg2
import tempfile
import ipaddress
from core.database.connection import (
    connect_db,
    create_tables,
    fetch_one,
    fetch_all,
    execute,
)
from core.config import config as app_config
from core.utils.runtime_logging import setup_runtime_logging
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for

BASE_DIR    = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR    = os.path.abspath(os.path.join(BASE_DIR, '..'))
CONFIG_PATH = os.path.join(ROOT_DIR, 'config.json')
I18N_DIR    = os.path.join(BASE_DIR, 'i18n')
LOG_DIR     = os.path.join(ROOT_DIR, 'logs')
DEFAULT_LANG = "zh"
ADAPTER_STOP_TIMEOUT_SECONDS = max(15, int(os.getenv("ADAPTER_STOP_TIMEOUT_SECONDS", "15")))

os.makedirs(LOG_DIR, exist_ok=True)
logger = setup_runtime_logging("web_local", project_root=ROOT_DIR, stats_interval_seconds=180)

app = Flask(__name__,
            static_folder=os.path.join(BASE_DIR, 'static'),
            template_folder=os.path.join(BASE_DIR, 'templates'))
app.secret_key = secrets.token_hex(32)

running_processes = {
    'core': None,
    'adapters': {}
}


# ---- CSRF ----

def _ensure_csrf_token() -> str:
    token = session.get("csrf_token")
    if token:
        return token
    token = secrets.token_urlsafe(32)
    session["csrf_token"] = token
    return token


def _csrf_required() -> bool:
    return request.method in {"POST", "PUT", "PATCH", "DELETE"}


def _csrf_valid() -> bool:
    expected = str(session.get("csrf_token") or "")
    if not expected:
        return False
    provided = request.headers.get("X-CSRF-Token")
    if not provided:
        if request.is_json:
            provided = (request.get_json(silent=True) or {}).get("csrf_token")
        else:
            provided = request.form.get("csrf_token")
    if not provided:
        return False
    return secrets.compare_digest(str(provided), expected)


def _is_loopback_addr(addr: str) -> bool:
    if not addr:
        return False
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return addr in ("127.0.0.1", "::1", "localhost")


def _is_service_call() -> bool:
    marker = str(request.headers.get("X-Service-Request") or "").strip().lower()
    if marker not in ("1", "true", "yes", "service"):
        return False
    forwarded = str(request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    remote = forwarded or str(request.remote_addr or "")
    return _is_loopback_addr(remote)


# ---- 管理面板认证 ----

def require_admin_auth(f):
    """装饰器：要求管理面板密码认证。

    支持两种认证方式：
    1. Session 登录（浏览器访问）
    2. Header/JSON api_password（API 调用）
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        admin_pw = app_config.admin_password
        # 如果未设置密码或配置关闭了密码验证，直接放行
        if not admin_pw:
            return f(*args, **kwargs)
        pw_required = app_config.get_nested("admin_panel", "api_with_password", default=True)
        if not pw_required:
            return f(*args, **kwargs)

        # 方式1: session 登录
        if session.get("admin_authed"):
            _ensure_csrf_token()
            if _csrf_required() and not _csrf_valid():
                return jsonify({"status": "error", "message": "CSRF 校验失败"}), 403
            return f(*args, **kwargs)

        # 方式2: 请求头或 JSON
        provided = (
            request.headers.get("X-Admin-Password")
            or (request.get_json(silent=True) or {}).get("api_password")
        )
        if provided and provided == admin_pw:
            if _csrf_required():
                if _csrf_valid() or _is_service_call():
                    return f(*args, **kwargs)
                return jsonify({"status": "error", "message": "CSRF 校验失败"}), 403
            return f(*args, **kwargs)

        # 浏览器访问 → 重定向到登录页
        if request.accept_mimetypes.accept_html and not request.is_json:
            return redirect(url_for("admin_login"))

        return jsonify({"status": "error", "message": "未授权"}), 401
    return decorated


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return render_template("login.html") if os.path.exists(
            os.path.join(BASE_DIR, "templates", "login.html")
        ) else (
            '<form method="POST"><input name="password" type="password" placeholder="管理员密码">'
            '<button type="submit">登录</button></form>'
        )
    pw = (request.form.get("password") or "").strip()
    if pw and pw == app_config.admin_password:
        session["admin_authed"] = True
        _ensure_csrf_token()
        return redirect(url_for("index"))
    return redirect(url_for("admin_login"))

def load_config():
    """兼容函数：返回统一配置的 raw dict。"""
    return app_config.raw

cfg = load_config()
db_path = app_config.db_path
connect_db()
create_tables()

def save_config(cfg):
    if not isinstance(cfg, dict):
        raise ValueError("配置必须是 JSON 对象")
    cfg_dir = os.path.dirname(CONFIG_PATH) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="config.", suffix=".tmp", dir=cfg_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def load_translations():
    path = os.path.join(I18N_DIR, f"{DEFAULT_LANG}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"未找到翻译文件：{path}")
        return {}

def is_process_running(pid):
    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False

def start_core():
    if running_processes['core'] and is_process_running(running_processes['core'].pid):
        logger.info("Core server is already running")
        return True
    
    try:
        logger.info("Starting core server")
        
        try:
            from core.database.connection import connect_db, create_tables
            from core.game.mechanics import initialize_game_mechanics

            connect_db()
            create_tables()

            initialize_game_mechanics()

            logger.info("Core components initialized successfully")
        except Exception as init_error:
            logger.error(f"Failed to initialize core components: {init_error}")
        
        server_path = os.path.join(ROOT_DIR, 'core', 'server.py')
        process = subprocess.Popen(
            [sys.executable, server_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True
        )
        running_processes['core'] = process
        logger.info(f"Core server started with PID {process.pid}")
        return True
    except Exception as e:
        logger.error(f"Failed to start core server: {e}")
        return False

def stop_core():
    if not running_processes['core'] or not is_process_running(running_processes['core'].pid):
        logger.info("Core server is not running")
        return True
    
    try:
        logger.info(f"Stopping core server (PID {running_processes['core'].pid})")
        running_processes['core'].terminate()
        try:
            running_processes['core'].wait(timeout=5)
        except subprocess.TimeoutExpired:
            running_processes['core'].kill()
        
        running_processes['core'] = None
        
        for adapter_name in list(running_processes['adapters'].keys()):
            stop_adapter(adapter_name)
        
        logger.info("Core server stopped")
        return True
    except Exception as e:
        logger.error(f"Failed to stop core server: {e}")
        return False

def start_adapter(adapter_name):
    if not app_config.is_adapter_enabled(adapter_name):
        logger.info(f"Adapter {adapter_name} is disabled in config")
        return False

    if not running_processes['core'] or not is_process_running(running_processes['core'].pid):
        logger.warning("Cannot start adapter: Core server is not running")
        return False
    
    if adapter_name in running_processes['adapters'] and is_process_running(running_processes['adapters'][adapter_name].pid):
        logger.info(f"{adapter_name} adapter is already running")
        return True
    
    try:
        logger.info(f"Starting {adapter_name} adapter")
        adapter_path = os.path.join(ROOT_DIR, 'adapters', adapter_name, 'bot.py')
        
        if not os.path.exists(adapter_path):
            process = subprocess.Popen(
                [sys.executable, '-c', f'import time; print("{adapter_name} adapter started"); time.sleep(3600)'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True
            )
        else:
            process = subprocess.Popen(
                [sys.executable, adapter_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True
            )
        
        running_processes['adapters'][adapter_name] = process
        logger.info(f"{adapter_name} adapter started with PID {process.pid}")
        return True
    except Exception as e:
        logger.error(f"Failed to start {adapter_name} adapter: {e}")
        return False

def stop_adapter(adapter_name):
    if adapter_name not in running_processes['adapters'] or not is_process_running(running_processes['adapters'][adapter_name].pid):
        logger.info(f"{adapter_name} adapter is not running")
        return True
    
    try:
        logger.info(f"Stopping {adapter_name} adapter (PID {running_processes['adapters'][adapter_name].pid})")
        running_processes['adapters'][adapter_name].terminate()
        try:
            running_processes['adapters'][adapter_name].wait(timeout=ADAPTER_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning(
                f"{adapter_name} adapter did not exit within {ADAPTER_STOP_TIMEOUT_SECONDS}s; forcing kill"
            )
            running_processes['adapters'][adapter_name].kill()
        
        del running_processes['adapters'][adapter_name]
        logger.info(f"{adapter_name} adapter stopped")
        return True
    except Exception as e:
        logger.error(f"Failed to stop {adapter_name} adapter: {e}")
        return False

@app.context_processor
def inject_i18n():
    trans = load_translations()
    return dict(trans=trans, current_lang=DEFAULT_LANG)


@app.context_processor
def inject_versions():
    return dict(
        core_version=os.getenv("CORE_VERSION", "dev"),
        web_version=os.getenv("WEB_VERSION", "dev"),
        telegram_version=os.getenv("TELEGRAM_VERSION", "dev"),
    )


@app.context_processor
def inject_security_tokens():
    token = session.get("csrf_token")
    if session.get("admin_authed") and not token:
        token = _ensure_csrf_token()
    return dict(csrf_token=token or "")

@app.route('/')
def index():
    cfg = load_config()
    return render_template('index.html', config=cfg)

@app.route('/config', methods=['GET'])
@require_admin_auth
def config_get():
    cfg = load_config()
    return render_template('config.html', config_data=cfg)

@app.route('/config', methods=['POST'])
@require_admin_auth
def config_post():
    trans = load_translations()

    old_cfg = load_config()
    new_cfg = request.get_json(silent=True)
    if not isinstance(new_cfg, dict):
        return jsonify(
            status='error',
            message=trans.get('config_invalid', '配置格式错误：必须提交 JSON 对象'),
        ), 400
    try:
        save_config(new_cfg)
        app_config.reload()
        full_restart    = old_cfg.get('db')             != new_cfg.get('db') \
                       or old_cfg.get('admin_panel')    != new_cfg.get('admin_panel')
        adapter_restart = old_cfg.get('adapters')       != new_cfg.get('adapters') \
                       or old_cfg.get('tokens')         != new_cfg.get('tokens')
        return jsonify(
            status='ok',
            message=trans.get('config_saved', 'Config saved successfully!'),
            full_restart=full_restart,
            adapter_restart=adapter_restart
        )
    except Exception as e:
        return jsonify(status='error', message=str(e)), 500

@app.route('/servers')
@require_admin_auth
def servers():
    cfg = load_config()
    return render_template('servers.html', config=cfg)

@app.route('/logs')
@require_admin_auth
def logs():
    return render_template('logs.html')

@app.route('/admin')
@require_admin_auth
def admin():
    return render_template('admin.html')

@app.route('/database')
@require_admin_auth
def database():
    return render_template('database.html')

@app.route('/admin/search_user')
@require_admin_auth
def search_user():
    try:
        query = request.args.get('query', '')
        logger.info(f"Search user request with query: {query}")
        
        if not query:
            logger.warning("No query provided in search_user")
            return jsonify({'status': 'error', 'message': '未提供查询条件'}), 400
        
        from core.admin.user_management import search_users, get_user
        
        logger.info(f"Attempting to find user by ID: {query}")
        user = get_user(query)
        if user:
            logger.info(
                f"User found by ID: {user.get('user_id')} / {user.get('in_game_username')}"
            )
            return jsonify({'status': 'ok', 'user': user})
        
        logger.info(f"User not found by ID, searching by username regex: {query}")
        users = search_users({'in_game_username': {'$regex': query, '$options': 'i'}}, limit=10)
        if users:
            logger.info(f"Found {len(users)} users by username search")
            return jsonify({'status': 'ok', 'users': users})
        
        logger.warning(f"No users found for query: {query}")
        return jsonify({'status': 'error', 'message': '未找到用户'}), 404
    except Exception as e:
        logger.error(f"Error in search_user: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'服务器错误：{str(e)}'}), 500


@app.route('/admin/field_options')
@require_admin_auth
def admin_field_options():
    try:
        from core.admin.user_management import get_modifiable_fields

        return jsonify({
            'status': 'ok',
            'fields': get_modifiable_fields(),
        })
    except Exception as e:
        logger.error(f"Error in admin_field_options: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/admin/modify_user', methods=['POST'])
@require_admin_auth
def modify_user():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': '未提供数据'}), 400
        
        user_id = data.get('user_id')
        field = data.get('field')
        action = data.get('action')
        value = data.get('value')
        
        if not all([user_id, field, action, value is not None]):
            return jsonify({'status': 'error', 'message': '缺少必填字段'}), 400
        
        from core.admin.user_management import modify_user_field, get_user

        success, message = modify_user_field(user_id, field, action, value)

        if success:
            user = get_user(user_id)
            return jsonify({'status': 'ok', 'message': message, 'user': user})
        else:
            return jsonify({'status': 'error', 'message': message}), 400
    except Exception as e:
        logger.error(f"Error in modify_user: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/admin/get_inventory/<user_id>')
@require_admin_auth
def get_inventory(user_id):
    try:
        page = int(request.args.get('page', 1))
        items_per_page = 10
        
        from core.admin.user_management import get_user, get_user_inventory
        
        user = get_user(user_id)
        if not user:
            logger.warning(f"User not found in get_inventory: {user_id}")
            return jsonify({'status': 'error', 'message': '未找到用户'}), 404
        
        if 'copper' not in user:
            user['copper'] = 0
        if 'gold' not in user:
            user['gold'] = 0
        
        
        if 'inventory' in user and isinstance(user['inventory'], list):
            logger.info(f"Using embedded inventory for user {user_id}")
            all_items = user['inventory']
            total_items = len(all_items)
            total_pages = (total_items + items_per_page - 1) // items_per_page
            
            start_idx = (page - 1) * items_per_page
            end_idx = min(start_idx + items_per_page, total_items)
            items = all_items[start_idx:end_idx]
            
        else:
            logger.info(f"Using items table for user {user_id}")
            items, total_pages = get_user_inventory(user_id, page, items_per_page)
        
        return jsonify({
            'status': 'ok',
            'user': user,
            'items': items,
            'page': page,
            'total_pages': max(1, total_pages)
        })
    except Exception as e:
        logger.error(f"Error in get_inventory: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

def _normalize_log_source(source: str) -> str:
    source = (source or "all").strip().lower()
    if source == "web":
        return "web-interface"
    return source


def _resolve_existing_log_path(candidates):
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0] if candidates else ""


def _log_file_map():
    return {
        "core": _resolve_existing_log_path([
            os.path.join(LOG_DIR, "core.log"),
            os.path.join(LOG_DIR, "xiuxianbot.log"),
        ]),
        "web-interface": _resolve_existing_log_path([os.path.join(LOG_DIR, "web_local.log")]),
        "telegram": _resolve_existing_log_path([
            os.path.join(LOG_DIR, "telegram.log"),
            os.path.join(ROOT_DIR, "adapters", "telegram", "telegram.log"),
        ]),
    }


def _parse_log_line(line: str):
    parts = line.rstrip("\r\n").split(" - ", 3)
    if len(parts) < 4:
        return None
    timestamp_str, _logger_name, log_level, message = parts
    ts = timestamp_str.replace(",", ".")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(ts, fmt), log_level.lower(), message.strip()
        except ValueError:
            continue
    return None


def _time_filter_match(timestamp: datetime.datetime, time_filter: str) -> bool:
    tf = (time_filter or "all").lower()
    if tf == "all":
        return True
    age_seconds = (datetime.datetime.now() - timestamp).total_seconds()
    if tf == "hour":
        return age_seconds <= 3600
    if tf == "day":
        return age_seconds <= 86400
    if tf == "week":
        return age_seconds <= 604800
    return True


def _collect_logs(source: str = "all", level: str = "all", time_filter: str = "all", max_lines_per_file: int = 1000):
    normalized_source = _normalize_log_source(source)
    normalized_level = (level or "all").lower()
    logs = []
    for source_name, log_file in _log_file_map().items():
        if normalized_source != "all" and normalized_source != source_name:
            continue
        if not os.path.exists(log_file):
            continue
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            logger.error(f"Error reading log file {log_file}: {e}")
            continue
        if max_lines_per_file > 0:
            lines = lines[-max_lines_per_file:]
        for line in lines:
            parsed = _parse_log_line(line)
            if not parsed:
                continue
            timestamp, log_level, message = parsed
            if normalized_level != "all" and normalized_level != log_level:
                continue
            if not _time_filter_match(timestamp, time_filter):
                continue
            logs.append({
                "timestamp": timestamp.isoformat(),
                "source": source_name,
                "level": log_level,
                "message": message,
                "_sort_ts": timestamp,
            })
    logs.sort(key=lambda x: x["_sort_ts"], reverse=True)
    for entry in logs:
        entry.pop("_sort_ts", None)
    return logs


@app.route('/logs/download')
@require_admin_auth
def download_logs():
    try:
        source = _normalize_log_source(request.args.get('source', 'all'))
        level = request.args.get('level', 'all')
        time_filter = request.args.get('time', 'all')
        logs = _collect_logs(source=source, level=level, time_filter=time_filter, max_lines_per_file=20000)
        content_lines = [
            f"XiuXianBot 日志 - 生成时间 {datetime.datetime.now().isoformat()}",
            "=" * 80,
            "",
        ]
        for entry in logs:
            content_lines.append(
                f"{entry['timestamp']} [{entry['source']}] [{entry['level'].upper()}] {entry['message']}"
            )
        if not logs:
            content_lines.append("没有匹配筛选条件的日志。")
        payload = "\n".join(content_lines).encode("utf-8")
        output = io.BytesIO(payload)
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name=f"xiuxianbot_logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            mimetype='text/plain'
        )
    except Exception as e:
        logger.error(f"Error in download_logs: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/logs/data')
@require_admin_auth
def logs_data():
    try:
        source = _normalize_log_source(request.args.get('source', 'all'))
        level = request.args.get('level', 'all')
        time_filter = request.args.get('time', 'all')
        logs = _collect_logs(source=source, level=level, time_filter=time_filter, max_lines_per_file=1000)
        return jsonify({'logs': logs})
    except Exception as e:
        logger.error(f"Error in logs_data: {e}")
        return jsonify({'logs': [], 'error': str(e)})

@app.route('/servers/status')
@require_admin_auth
def servers_status():
    cfg = load_config()
    
    core_running = running_processes['core'] is not None and is_process_running(running_processes['core'].pid)
    
    adapter_statuses = {}
    for adapter_name, enabled in cfg.get('adapters', {}).items():
        if enabled:
            adapter_running = (
                adapter_name in running_processes['adapters'] and 
                is_process_running(running_processes['adapters'][adapter_name].pid)
            )
            adapter_statuses[adapter_name] = {
                'running': adapter_running,
                'enabled': True
            }
    
    return jsonify({
        'core': {
            'running': core_running
        },
        'adapters': adapter_statuses
    })

@app.route('/servers/start/core', methods=['POST'])
@require_admin_auth
def start_core_route():
    trans = load_translations()
    
    if start_core():
        return jsonify({
            'status': 'ok',
            'message': trans.get('core_started', '核心服务器已启动')
        })
    else:
        return jsonify({
            'status': 'error',
            'message': trans.get('core_start_failed', '核心服务器启动失败')
        }), 500

@app.route('/servers/stop/core', methods=['POST'])
@require_admin_auth
def stop_core_route():
    trans = load_translations()
    
    if stop_core():
        return jsonify({
            'status': 'ok',
            'message': trans.get('core_stopped', '核心服务器已停止')
        })
    else:
        return jsonify({
            'status': 'error',
            'message': trans.get('core_stop_failed', '核心服务器停止失败')
        }), 500

@app.route('/servers/start/<adapter_name>', methods=['POST'])
@require_admin_auth
def start_adapter_route(adapter_name):
    trans = load_translations()

    if not app_config.is_adapter_enabled(adapter_name):
        return jsonify({
            'status': 'error',
            'message': trans.get('adapter_disabled', '适配器已在配置中禁用').replace('{adapter}', adapter_name)
        }), 400

    if start_adapter(adapter_name):
        return jsonify({
            'status': 'ok',
            'message': trans.get('adapter_started', '适配器已启动').replace('{adapter}', adapter_name)
        })
    else:
        return jsonify({
            'status': 'error',
            'message': trans.get('adapter_start_failed', '适配器启动失败').replace('{adapter}', adapter_name)
        }), 500

@app.route('/servers/stop/<adapter_name>', methods=['POST'])
@require_admin_auth
def stop_adapter_route(adapter_name):
    trans = load_translations()
    
    if stop_adapter(adapter_name):
        return jsonify({
            'status': 'ok',
            'message': trans.get('adapter_stopped', '适配器已停止').replace('{adapter}', adapter_name)
        })
    else:
        return jsonify({
            'status': 'error',
            'message': trans.get('adapter_stop_failed', '适配器停止失败').replace('{adapter}', adapter_name)
        }), 500

@app.route('/database/query/<collection_name>', methods=['POST'])
@require_admin_auth
def database_query(collection_name):
    try:
        from core.database.validators import validate_table, validate_column

        # 表名白名单验证
        collection_name = validate_table(collection_name)

        data = request.get_json()
        query = data.get('query', {})
        page = int(request.args.get('page', 1))
        items_per_page = int(request.args.get('items_per_page', 20))

        conditions = []
        params = []
        for field, value in query.items():
            # 列名白名单验证
            field = validate_column(field, collection_name)

            if isinstance(value, dict) and '$regex' in value:
                conditions.append(f"{field} LIKE %s")
                params.append(f"%{value['$regex']}%")
            elif isinstance(value, dict) and '$ne' in value:
                conditions.append(f"{field} != %s")
                params.append(value['$ne'])
            elif isinstance(value, dict) and '$gt' in value:
                conditions.append(f"{field} > %s")
                params.append(value['$gt'])
            elif isinstance(value, dict) and '$gte' in value:
                conditions.append(f"{field} >= %s")
                params.append(value['$gte'])
            elif isinstance(value, dict) and '$lt' in value:
                conditions.append(f"{field} < %s")
                params.append(value['$lt'])
            elif isinstance(value, dict) and '$lte' in value:
                conditions.append(f"{field} <= %s")
                params.append(value['$lte'])
            else:
                conditions.append(f"{field} = %s")
                params.append(value)

        where_clause = ''
        if conditions:
            where_clause = 'WHERE ' + ' AND '.join(conditions)

        count_row = fetch_one(
            f"SELECT COUNT(*) as c FROM {collection_name} {where_clause}",
            tuple(params),
        )
        total_items = count_row['c'] if count_row else 0
        total_pages = (total_items + items_per_page - 1) // items_per_page

        params.extend([items_per_page, (page - 1) * items_per_page])
        results = fetch_all(
            f"SELECT * FROM {collection_name} {where_clause} LIMIT %s OFFSET %s",
            tuple(params),
        )
        for r in results:
            for k in list(r.keys()):
                if k.strip().lower() == 'actions':
                    r.pop(k, None)

        return jsonify({
            'status': 'ok',
            'results': results,
            'count': total_items,
            'page': page,
            'total_pages': max(1, total_pages)
        })
    except Exception as e:
        logger.error(f"Error in database_query: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# 所有包含 user_id 的子表（级联删除用）
_USER_CHILD_TABLES = [
    ("items", ("user_id",)),
    ("timings", ("user_id",)),
    ("battle_logs", ("user_id",)),
    ("battle_sessions", ("user_id",)),
    ("breakthrough_logs", ("user_id",)),
    ("user_skills", ("user_id",)),
    ("user_quests", ("user_id",)),
    ("user_realm_trials", ("user_id",)),
    ("user_growth_snapshots", ("user_id",)),
    ("codex_monsters", ("user_id",)),
    ("codex_items", ("user_id",)),
    ("drop_pity", ("user_id",)),
    ("pvp_records", ("challenger_id", "defender_id", "winner_id")),
    ("friends", ("user_id", "friend_id")),
    ("friend_requests", ("from_user_id", "to_user_id")),
    ("social_chat_requests", ("from_user_id", "to_user_id")),
    ("sect_members", ("user_id",)),
    ("sect_quest_claims", ("user_id",)),
    ("sect_branch_members", ("user_id",)),
    ("sect_branch_requests", ("applicant_user_id", "decided_by")),
    ("sect_branches", ("leader_user_id",)),
    ("sects", ("leader_id",)),
    ("alchemy_logs", ("user_id",)),
    ("gacha_pity", ("user_id",)),
    ("gacha_logs", ("user_id",)),
    ("user_achievements", ("user_id",)),
    ("event_claims", ("user_id",)),
    ("event_points", ("user_id",)),
    ("event_point_logs", ("user_id",)),
    ("event_exchange_claims", ("user_id",)),
    ("world_boss_attacks", ("user_id",)),
    ("shop_purchase_limits", ("user_id",)),
    ("request_dedup", ("user_id",)),
    ("event_logs", ("user_id",)),
    ("economy_ledger", ("user_id",)),
]

@app.route('/database/delete/<user_id>', methods=['POST'])
@require_admin_auth
def database_delete_user(user_id):
    try:
        from core.database.connection import db_transaction

        with db_transaction() as cur:
            # 先删除所有子表数据
            for table, columns in _USER_CHILD_TABLES:
                where_clause = " OR ".join([f"{col} = %s" for col in columns])
                params = tuple([user_id] * len(columns))
                try:
                    cur.execute(f"DELETE FROM {table} WHERE {where_clause}", params)
                except psycopg2.OperationalError as exc:
                    logger.warning(f"跳过删除 {table}：{exc}")

            # 最后删除用户主表
            cur.execute('DELETE FROM users WHERE user_id = %s', (user_id,))

        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error in database_delete_user: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/database/user/<user_id>')
@require_admin_auth
def database_get_user(user_id):
    try:
        user = fetch_one('SELECT * FROM users WHERE user_id = %s', (user_id,))
        if not user:
            return jsonify({'status': 'error', 'message': '未找到用户'}), 404
        return jsonify({'status': 'ok', 'user': user})
    except Exception as e:
        logger.error(f"Error in database_get_user: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    cfg  = load_config()
    port = cfg.get('admin_panel', {}).get('port', 11451)
    app.run(host='127.0.0.1', port=port, debug=True)
