"""Structured logging for print operations — daily rotating file + cleanup."""

import logging
import datetime
import os
import time
from config import settings


def cleanup_old_logs(keep=90):
    """Delete old log files, keeping the most recent `keep` files."""
    log_dir = settings.log_dir_resolved
    if not os.path.isdir(log_dir):
        return
    log_files = sorted(
        [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".log")],
        key=os.path.getmtime,
        reverse=True,
    )
    for old_file in log_files[keep:]:
        try:
            os.remove(old_file)
        except OSError:
            pass


class PrintLogger:
    """Logger for print operations — one structured line per request, daily file."""

    def __init__(self):
        log_dir = settings.log_dir_resolved
        os.makedirs(log_dir, exist_ok=True)

        today = datetime.date.today().strftime("%Y%m%d")
        log_file = os.path.join(log_dir, f"print_{today}.log")

        self._logger = logging.getLogger("print_app")
        self._logger.setLevel(logging.DEBUG)
        # Avoid duplicate handlers on reload
        self._logger.handlers.clear()
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        self._logger.addHandler(handler)

    def log_print(self, username: str, company: str, doc_type: str,
                  doc_no: str, template: str, status: str,
                  latency_ms: int = 0, error: str = ""):
        """Log a structured print request line."""
        parts = [
            username or "-",
            company or "-",
            doc_type or "-",
            doc_no or "-",
            template or "-",
            status,
            f"{latency_ms}ms",
        ]
        if error:
            parts.append(error)
        self._logger.info(" | ".join(parts))

    def info(self, message: str):
        self._logger.info(message)

    def error(self, message: str):
        self._logger.error(message)

    def warning(self, message: str):
        self._logger.warning(message)


# Module-level singleton
print_logger = PrintLogger()
