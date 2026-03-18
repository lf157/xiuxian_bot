from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import sys
import threading
import time
from collections import Counter
from logging.handlers import RotatingFileHandler
from typing import Any

_CONFIGURED_SERVICES: set[str] = set()


class _StatsState:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.lock = threading.Lock()
        self.level_counts: Counter[str] = Counter()
        self.total_records = 0
        self.total_exceptions = 0

    def ingest(self, record: logging.LogRecord) -> None:
        with self.lock:
            self.total_records += 1
            self.level_counts[record.levelname] += 1
            if record.exc_info:
                self.total_exceptions += 1

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "uptime_s": int(time.time() - self.started_at),
                "total_records": int(self.total_records),
                "total_exceptions": int(self.total_exceptions),
                "levels": dict(self.level_counts),
            }


class _StatsHandler(logging.Handler):
    def __init__(self, state: _StatsState) -> None:
        super().__init__(level=logging.DEBUG)
        self._state = state

    def emit(self, record: logging.LogRecord) -> None:
        self._state.ingest(record)


def _project_root_from_module() -> str:
    # core/utils/runtime_logging.py -> project_root/core/utils/runtime_logging.py
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _ensure_dirs(project_root: str) -> tuple[str, str]:
    logs_dir = os.path.join(project_root, "logs")
    log_dir = os.path.join(project_root, "log")
    os.makedirs(logs_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)
    return logs_dir, log_dir


def _rotating_file_handler(path: str, level: int) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path,
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    return handler


def _install_uncaught_exception_hooks(service_name: str) -> None:
    logger = logging.getLogger(service_name)

    previous_sys_hook = sys.excepthook

    def _sys_excepthook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            previous_sys_hook(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
        previous_sys_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = _sys_excepthook

    if hasattr(threading, "excepthook"):
        previous_thread_hook = threading.excepthook

        def _thread_excepthook(args):
            logger.critical(
                "Uncaught thread exception thread=%s",
                getattr(getattr(args, "thread", None), "name", "unknown"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            previous_thread_hook(args)

        threading.excepthook = _thread_excepthook


def install_asyncio_exception_logging(service_name: str) -> None:
    try:
        import asyncio
    except Exception:
        return

    try:
        loop = asyncio.get_event_loop()
    except Exception:
        return

    previous_handler = loop.get_exception_handler()
    logger = logging.getLogger(service_name)

    def _handler(current_loop, context):
        exc = context.get("exception")
        message = context.get("message", "Asyncio loop exception")
        if exc is not None:
            logger.error("Asyncio loop exception: %s", message, exc_info=exc)
        else:
            logger.error("Asyncio loop exception: %s context=%s", message, context)

        if previous_handler is not None:
            previous_handler(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)


def setup_runtime_logging(
    service_name: str,
    *,
    project_root: str | None = None,
    level: int = logging.INFO,
    emit_stats: bool = True,
    stats_interval_seconds: int = 300,
) -> logging.Logger:
    if service_name in _CONFIGURED_SERVICES:
        return logging.getLogger(service_name)

    project_root = project_root or _project_root_from_module()
    logs_dir, log_dir = _ensure_dirs(project_root)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    logging.captureWarnings(True)

    # Keep historical compatibility (`logs/`) and add explicit debug-focused files (`log/`).
    root_logger.addHandler(_rotating_file_handler(os.path.join(logs_dir, f"{service_name}.log"), logging.INFO))
    root_logger.addHandler(_rotating_file_handler(os.path.join(log_dir, f"{service_name}.log"), logging.INFO))
    root_logger.addHandler(_rotating_file_handler(os.path.join(log_dir, f"{service_name}_errors.log"), logging.ERROR))

    stats_state = _StatsState()
    root_logger.addHandler(_StatsHandler(stats_state))

    # Persist fatal native crashes (segfault, abort) to file for post-mortem debug.
    try:
        fatal_path = os.path.join(log_dir, f"{service_name}_fatal.log")
        fatal_stream = open(fatal_path, "a", encoding="utf-8")
        faulthandler.enable(fatal_stream)
        atexit.register(fatal_stream.close)
    except Exception:
        pass

    _install_uncaught_exception_hooks(service_name)

    if emit_stats:
        stats_logger = logging.getLogger(f"{service_name}.runtime")
        stop_event = threading.Event()

        def _stats_loop() -> None:
            while not stop_event.wait(max(30, int(stats_interval_seconds))):
                snapshot = stats_state.snapshot()
                stats_logger.info(
                    "runtime_stats uptime_s=%s total_records=%s total_exceptions=%s levels=%s",
                    snapshot["uptime_s"],
                    snapshot["total_records"],
                    snapshot["total_exceptions"],
                    snapshot["levels"],
                )

        stats_thread = threading.Thread(target=_stats_loop, name=f"{service_name}-log-stats", daemon=True)
        stats_thread.start()
        atexit.register(stop_event.set)

    _CONFIGURED_SERVICES.add(service_name)
    service_logger = logging.getLogger(service_name)
    service_logger.info(
        "runtime_logging_enabled pid=%s cwd=%s python=%s",
        os.getpid(),
        os.getcwd(),
        sys.version.split()[0],
    )
    return service_logger
