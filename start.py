import os
import sys
import subprocess
import threading
import importlib.util
import logging
import signal
import time
from pathlib import Path
from core.utils.runtime_logging import setup_runtime_logging

CORE_VERSION = "OSBETADocker0.1.52"
WEB_VERSION = "0.3.2"
TELEGRAM_VERSION = "0.1.0"
ADAPTER_STOP_TIMEOUT_SECONDS = max(15, int(os.getenv("ADAPTER_STOP_TIMEOUT_SECONDS", "15")))
CAPTURED_STDIO_ENV = "XIUXIAN_CAPTURED_STDIO"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = setup_runtime_logging("xiuxianbot", project_root=BASE_DIR, stats_interval_seconds=180)

# 模块级进程/线程状态。仅在主线程中访问（信号处理 & 启动流程），无需加锁。
adapter_processes = {}
adapter_log_handles = {}
core_process = None
web_thread = None
web_local_module = None
public_thread = None
web_public_module = None
shutdown_requested = threading.Event()

# 使用统一配置模块
from core.config import config


def _is_subprocess_running(process) -> bool:
    return isinstance(process, subprocess.Popen) and process.poll() is None


def _version_env() -> dict:
    """返回版本环境变量字典，用于注入子进程或当前进程。"""
    return {
        "CORE_VERSION": CORE_VERSION,
        "WEB_VERSION": WEB_VERSION,
        "TELEGRAM_VERSION": TELEGRAM_VERSION,
    }

def start_web_local():
    try:
        port = config.admin_panel_port
        logger.info(f"Starting local admin dashboard on port {port}")
        
        web_local_path = os.path.join(BASE_DIR, 'web_local', 'app.py')
        
        if not os.path.exists(web_local_path):
            logger.error(f"Web local app not found: {web_local_path}")
            return None
        
        web_local_dir = os.path.dirname(web_local_path)
        if web_local_dir not in sys.path:
            sys.path.insert(0, web_local_dir)
            
        os.environ.update(_version_env())

        spec = importlib.util.spec_from_file_location("app", web_local_path)
        web_local = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(web_local)

        global web_local_module
        web_local_module = web_local

        if core_process is not None and core_process.poll() is None:
            web_local.running_processes['core'] = core_process
        for adapter_name, process in adapter_processes.items():
            if isinstance(process, subprocess.Popen) and process.poll() is None:
                web_local.running_processes['adapters'][adapter_name] = process
        
        def run_flask_app():
            web_local.app.run(host='127.0.0.1', port=port, debug=False)
        
        flask_thread = threading.Thread(target=run_flask_app, daemon=True)
        flask_thread.start()
        global web_thread
        web_thread = flask_thread
        
        logger.info(f"Local admin dashboard started at http://127.0.0.1:{port}")
        return flask_thread
    except Exception as e:
        logger.error(f"Failed to start local admin dashboard: {e}")
        return None

def start_web_public():
    try:
        public_cfg = config.get("public_web", {}) or {}
        if not bool(public_cfg.get("enabled", False)):
            logger.info("Public web is disabled in config")
            return None

        port = int(public_cfg.get("port", 11452))
        host = str(public_cfg.get("host", "127.0.0.1"))
        logger.info(f"Starting public web on {host}:{port}")

        web_public_path = os.path.join(BASE_DIR, 'web_public', 'app.py')
        if not os.path.exists(web_public_path):
            logger.error(f"Web public app not found: {web_public_path}")
            return None

        web_public_dir = os.path.dirname(web_public_path)
        if web_public_dir not in sys.path:
            sys.path.insert(0, web_public_dir)

        os.environ.update(_version_env())

        spec = importlib.util.spec_from_file_location("web_public_app", web_public_path)
        web_public = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(web_public)

        global web_public_module
        web_public_module = web_public

        def run_flask_app():
            web_public.app.run(host=host, port=port, debug=False)

        flask_thread = threading.Thread(target=run_flask_app, daemon=True)
        flask_thread.start()
        global public_thread
        public_thread = flask_thread

        logger.info(f"Public web started at http://{host}:{port}")
        return flask_thread
    except Exception as e:
        logger.error(f"Failed to start public web: {e}")
        return None

def start_core():
    global core_process
    if core_process is not None and core_process.poll() is None:
        logger.info("Core server already running")
        return core_process

    server_path = os.path.join(BASE_DIR, 'core', 'server.py')
    if not os.path.exists(server_path):
        logger.error(f"Core server path not found: {server_path}")
        return None

    env = {**os.environ, **_version_env()}

    process = subprocess.Popen(
        [sys.executable, server_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env
    )
    core_process = process
    logger.info(f"Core server started with PID {process.pid}")
    return process

def stop_core():
    global core_process
    if core_process is None:
        return True
    if core_process.poll() is not None:
        return True
    logger.info(f"Stopping core server (PID {core_process.pid})")
    core_process.terminate()
    try:
        core_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        core_process.kill()
    return True

def start_adapter(adapter_name):
    try:
        if not config.is_adapter_enabled(adapter_name):
            logger.info(f"Adapter {adapter_name} is disabled in config")
            return None

        existing = adapter_processes.get(adapter_name)
        if _is_subprocess_running(existing):
            logger.info(f"{adapter_name} adapter already running with PID {existing.pid}")
            return existing
        if existing is not None and not isinstance(existing, threading.Thread):
            adapter_processes.pop(adapter_name, None)
        
        logger.info(f"Starting {adapter_name} adapter")
        adapter_path = os.path.join(BASE_DIR, 'adapters', adapter_name, 'bot.py')
        
        if not os.path.exists(adapter_path):
            logger.error(f"Adapter path not found: {adapter_path}")
            return None
        
        env = {**os.environ, **_version_env(), CAPTURED_STDIO_ENV: "1"}

        log_path = os.path.join(LOG_DIR, f"{adapter_name}.log")
        log_file = open(log_path, "a", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, adapter_path],
            stdout=log_file,
            stderr=log_file,
            text=True,
            env=env
        )
        adapter_log_handles[adapter_name] = log_file
        
        adapter_processes[adapter_name] = process
        if web_local_module is not None:
            web_local_module.running_processes['adapters'][adapter_name] = process
        logger.info(f"{adapter_name} adapter started with PID {process.pid}")
        # 短时存活检查：若 adapter 启动后 2 秒内即退出则告警
        time.sleep(2)
        if process.poll() is not None:
            logger.warning(
                f"{adapter_name} adapter (PID {process.pid}) exited immediately "
                f"(returncode={process.returncode}). Check logs/{adapter_name}.log for details."
            )
        return process
    except Exception as e:
        logger.error(f"Failed to start {adapter_name} adapter: {e}")
        return None

def stop_adapter(adapter_name):
    if adapter_name in adapter_processes:
        process = adapter_processes[adapter_name]
        if isinstance(process, threading.Thread):
            logger.info(f"Adapter {adapter_name} runs in-thread; skipping terminate")
            del adapter_processes[adapter_name]
            log_handle = adapter_log_handles.pop(adapter_name, None)
            if log_handle is not None:
                try:
                    log_handle.close()
                except Exception:
                    pass
            return True
        if process.poll() is not None:
            del adapter_processes[adapter_name]
            log_handle = adapter_log_handles.pop(adapter_name, None)
            if log_handle is not None:
                try:
                    log_handle.close()
                except Exception:
                    pass
            if web_local_module is not None:
                web_local_module.running_processes['adapters'].pop(adapter_name, None)
            logger.info(f"{adapter_name} adapter already stopped")
            return True
        logger.info(f"Stopping {adapter_name} adapter (PID {process.pid})")
        process.terminate()
        try:
            process.wait(timeout=ADAPTER_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning(
                f"{adapter_name} adapter did not exit within {ADAPTER_STOP_TIMEOUT_SECONDS}s; forcing kill"
            )
            process.kill()
        
        del adapter_processes[adapter_name]
        log_handle = adapter_log_handles.pop(adapter_name, None)
        if log_handle is not None:
            try:
                log_handle.close()
            except Exception:
                pass
        if web_local_module is not None:
            web_local_module.running_processes['adapters'].pop(adapter_name, None)
        logger.info(f"{adapter_name} adapter stopped")
        return True
    return False


def shutdown_all():
    for adapter_name in list(adapter_processes.keys()):
        stop_adapter(adapter_name)
    stop_core()


def _handle_shutdown_signal(signum, _frame):
    if shutdown_requested.is_set():
        return
    try:
        signal_name = signal.Signals(signum).name
    except Exception:
        signal_name = str(signum)
    logger.info(f"Received {signal_name}. Shutting down...")
    shutdown_requested.set()


def _install_signal_handlers():
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle_shutdown_signal)
        except Exception:
            logger.debug("Unable to register signal handler for %s", sig_name, exc_info=True)

def main():
    logger.info("Starting XiuXianBot")

    os.environ.update(_version_env())
    _install_signal_handlers()

    if not start_core():
        logger.error("Failed to start core server. Exiting.")
        return

    for adapter_name, enabled in (config.get("adapters", {}) or {}).items():
        if enabled:
            start_adapter(adapter_name)

    web_thread = start_web_local()
    if not web_thread:
        logger.error("Failed to start local admin dashboard. Exiting.")
        return

    public_cfg = config.get("public_web", {}) or {}
    if public_cfg.get("enabled", False):
        start_web_public()
    
    try:
        logger.info("XiuXianBot is running. Press Ctrl+C to stop.")
        while not shutdown_requested.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt. Shutting down...")
        shutdown_requested.set()
    finally:
        shutdown_all()
        logger.info("XiuXianBot stopped")

if __name__ == "__main__":
    main()
