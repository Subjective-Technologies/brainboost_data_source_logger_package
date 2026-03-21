import atexit
import csv
import io
import os
import queue
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import requests

try:
    import pandas as pd
except Exception:
    pd = None

from brainboost_data_source_logger_package.BBLogEntry import BBLogEntry
from brainboost_data_source_logger_package.Notifications import Notifications
from brainboost_configuration_package.BBConfig import BBConfig


class _LogWriter(threading.Thread):
    """Background thread that drains queued log entries and performs I/O."""

    def __init__(self, write_fn):
        super().__init__(name="BBLogger-writer", daemon=True)
        self.queue = queue.Queue(maxsize=10000)
        self._write_fn = write_fn
        self._stop_event = threading.Event()

    def run(self):
        while True:
            try:
                entry = self.queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue

            try:
                if entry is None:
                    break
                self._write_fn(entry)
            except Exception as exc:
                try:
                    print(f"BBLogger writer error: {exc}")
                except Exception:
                    pass
            finally:
                self.queue.task_done()

    def stop(self):
        self._stop_event.set()
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except Exception:
                pass
            try:
                self.queue.put_nowait(None)
            except Exception:
                pass


_SECRET_KEY_PATTERN = re.compile(
    r"""(?x)
    (['"]?)                                  # optional opening quote
    (?:api_key|apikey|api_secret|secret|secret_key|private_key
       |token|access_token|refresh_token|auth_token|bearer_token
       |password|passwd|credentials|authorization
       |client_secret|consumer_secret|signing_key)
    \1                                       # matching closing quote
    \s*[:=]\s*                               # separator (colon or equals)
    (['"]?)                                  # optional value quote
    ([^\s'"<>,}{)\]]{4,})                    # the secret value (4+ non-whitespace chars)
    \2                                       # matching value closing quote
    """,
    re.IGNORECASE,
)


def _scrub_secrets(text: str) -> str:
    """Replace secret values in a log message string with ***REDACTED***.

    This is a safety net — callers should redact dicts before formatting,
    but this catches anything that slips through.
    """
    def _replace(m):
        key_quote = m.group(1)
        val_quote = m.group(2)
        key_end = m.end(1) if m.group(1) else m.start(2)
        # Reconstruct up to the value, then redact
        prefix = m.group(0)[:m.start(3) - m.start(0)]
        return f"{prefix}{val_quote}***REDACTED***{val_quote}"

    return _SECRET_KEY_PATTERN.sub(_replace, text)


class BBLogger:
    _process_name: Optional[str] = None
    _last_time: Optional[datetime] = None
    _delta: Optional[datetime] = None
    _config_disabled: bool = False
    _log_file_path: Optional[str] = None
    _log_file_process: Optional[str] = None
    _config_cache: Optional[dict[str, Any]] = None
    _config_cache_time: float = 0.0
    _config_cache_ttl: float = 5.0
    _config_lock = threading.Lock()
    _writer = None
    _writer_lock = threading.Lock()
    _RE_ERROR = re.compile(r"\b(error|errors|exception|exceptions|failed|fail|missing)\b", re.IGNORECASE)
    _RE_WARNING = re.compile(r"\b(warning|warn|aware|careful)\b", re.IGNORECASE)
    _default_config = {
        "log_debug_mode": True,
        "log_enable_files": False,
        "log_enable_terminal_output": True,
        "log_enable_database": False,
        "log_sqlite3_path": os.path.join("logs", "brainboost_logs.sqlite3"),
        "log_columns": ["timestamp", "log_type", "process", "code_location", "message", "processing_time"],
        "log_path": "logs",
        "log_prefix": "brainboost",
        "log_delimiter": ",",
        "log_page_size": 100,
        "log_notification_slack": "",
        "log_notification_url": "",
        "log_file_naming": "daily",
        "log_file_name_convention": "YYYY_MM_DD_HH_MM_SS-[process]-log.log",
    }

    @classmethod
    def _normalize_bool(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        if isinstance(value, str):
            lower_val = value.lower().strip()
            if lower_val in ("true", "1", "yes", "on"):
                return True
            if lower_val in ("false", "0", "no", "off"):
                return False
        return value

    @classmethod
    def _read_config_value(cls, key: str):
        if cls._config_disabled:
            return cls._default_config.get(key)
        try:
            try:
                value = BBConfig.get(key)
            except KeyError:
                value = None
            if value is None:
                alt_key = key.upper() if key == key.lower() else key.lower()
                try:
                    value = BBConfig.get(alt_key)
                except KeyError:
                    value = None
            if value is None:
                return cls._default_config.get(key)
            return value
        except FileNotFoundError:
            cls._config_disabled = True
            return cls._default_config.get(key)
        except Exception:
            return cls._default_config.get(key)

    @classmethod
    def _refresh_config_cache(cls):
        cache = {}
        for key in cls._default_config:
            cache[key] = cls._read_config_value(key)
        cls._config_cache = cache
        cls._config_cache_time = time.monotonic()
        return cache

    @classmethod
    def _get_config_snapshot(cls) -> dict[str, Any]:
        with cls._config_lock:
            if cls._config_cache is None or (time.monotonic() - cls._config_cache_time) > cls._config_cache_ttl:
                return dict(cls._refresh_config_cache())
            return dict(cls._config_cache)

    @classmethod
    def _get_config(cls, key: str):
        return cls._get_config_snapshot().get(key, cls._default_config.get(key))

    @classmethod
    def invalidate_config_cache(cls):
        with cls._config_lock:
            cls._config_cache = None
            cls._config_cache_time = 0.0

    @classmethod
    def _ensure_parent_dir(cls, path: str) -> None:
        dir_path = os.path.dirname(path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

    @classmethod
    def _get_process_name(cls) -> str:
        if not cls._process_name:
            cls._process_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
        return cls._process_name

    @classmethod
    def _safe_print(cls, value) -> None:
        try:
            print(value)
        except UnicodeEncodeError:
            encoding = sys.stdout.encoding or "utf-8"
            safe_text = str(value).encode(encoding, errors="backslashreplace").decode(encoding, errors="ignore")
            print(safe_text)

    @classmethod
    def _format_log_file_name(cls, convention: str, now: datetime, log_prefix: str, process_name: str) -> str:
        file_name = convention
        file_name = file_name.replace("YYYY_MM_DD_HH_MM_SS", now.strftime("%Y_%m_%d_%H_%M_%S"))
        file_name = file_name.replace("YYYY_MM_DD", now.strftime("%Y_%m_%d"))
        file_name = file_name.replace("[process]", process_name)
        file_name = file_name.replace("${LOG_PREFIX}", str(log_prefix))
        return file_name

    @classmethod
    def _get_log_file_path(
        cls,
        date: Optional[str] = None,
        *,
        config: Optional[dict[str, Any]] = None,
        now: Optional[datetime] = None,
        process_name: Optional[str] = None,
    ) -> str:
        config = config or cls._get_config_snapshot()
        log_path = config.get("log_path", cls._default_config["log_path"])
        log_prefix = config.get("log_prefix", cls._default_config["log_prefix"])
        if date:
            return os.path.join(log_path, f"{log_prefix}_log_{date}.log")

        naming = str(config.get("log_file_naming") or "daily").lower()
        current_now = now or cls._last_time or datetime.now()
        current_process = str(process_name or cls._get_process_name())
        if naming == "per_run":
            if not cls._log_file_path:
                cls._log_file_process = None
            if cls._log_file_path and cls._log_file_process == current_process:
                return cls._log_file_path
            convention = (
                config.get("log_file_name_convention")
                or "YYYY_MM_DD_HH_MM_SS-[process]-log.log"
            )
            file_name = cls._format_log_file_name(convention, current_now, str(log_prefix), current_process)
            cls._log_file_path = os.path.join(str(log_path), file_name)
            cls._log_file_process = current_process
            return cls._log_file_path

        current_date = current_now.strftime("%Y_%m_%d")
        return os.path.join(str(log_path), f"{log_prefix}_log_{current_date}.log")

    @classmethod
    def _initialize_database(cls, db_path: Optional[str], columns: list[str]) -> None:
        if not db_path:
            return
        cls._ensure_parent_dir(db_path)
        if not os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            try:
                cursor = conn.cursor()
                columns_str = ", ".join([f"{col} TEXT" for col in columns])
                cursor.execute(f"CREATE TABLE logs ({columns_str});")
                conn.commit()
            finally:
                conn.close()

    @classmethod
    def _write_to_database(cls, log_entry: BBLogEntry, db_path: Optional[str], columns: list[str]):
        if not db_path:
            return
        cls._ensure_parent_dir(db_path)
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"INSERT INTO logs ({', '.join(columns)}) VALUES ({', '.join(['?' for _ in columns])});",
                [
                    log_entry.timestamp,
                    log_entry.log_type,
                    log_entry.process,
                    log_entry.code_location,
                    log_entry.message,
                    log_entry.processing_time,
                ],
            )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def _write_to_log_file(
        cls,
        log_entry: BBLogEntry,
        log_file_path: Optional[str],
        delimiter: str,
        columns: list[str],
    ):
        if not log_file_path:
            return
        cls._ensure_parent_dir(log_file_path)
        file_exists = os.path.isfile(log_file_path)
        try:
            with open(log_file_path, "a+", encoding="utf-8", newline="") as log_file:
                writer = csv.writer(
                    log_file,
                    delimiter=delimiter,
                    quotechar="'",
                    quoting=csv.QUOTE_MINIMAL,
                )
                if not file_exists:
                    writer.writerow(columns)
                writer.writerow(
                    [
                        log_entry.timestamp,
                        log_entry.log_type,
                        log_entry.process,
                        log_entry.code_location,
                        log_entry.message,
                        log_entry.processing_time,
                    ]
                )
        except IOError as exc:
            print(f"Failed to write to log file: {exc}")

    @classmethod
    def _format_log_entry_text(cls, log_entry: BBLogEntry, delimiter: str) -> str:
        output = io.StringIO()
        writer = csv.writer(output, delimiter=delimiter, quotechar='"', quoting=csv.QUOTE_ALL)
        writer.writerow(
            [
                log_entry.timestamp,
                log_entry.log_type,
                log_entry.process,
                log_entry.code_location,
                log_entry.message,
                log_entry.processing_time,
            ]
        )
        return output.getvalue().strip()

    @classmethod
    def _get_caller_fast(cls) -> str:
        try:
            frame = sys._getframe(2)
            try:
                return f"{os.path.basename(frame.f_code.co_filename)}:{frame.f_lineno}"
            finally:
                del frame
        except Exception:
            return "Unknown"

    @classmethod
    def _write_entry(cls, entry: dict[str, Any]):
        log_entry = BBLogEntry(
            process=entry["process"],
            timestamp=entry["timestamp"],
            log_type=entry["log_type"],
            message=entry["message"],
            processing_time=entry["processing_time"],
            code_location=entry["code_location"],
        )
        delimiter = str(entry["log_delimiter"])

        if entry["log_enable_files"]:
            cls._write_to_log_file(
                log_entry,
                entry["log_file_path"],
                delimiter,
                list(entry["log_columns"]),
            )

        if entry["log_enable_terminal_output"]:
            cls._safe_print(cls._format_log_entry_text(log_entry, delimiter))

        if entry["log_enable_database"]:
            columns = list(entry["log_columns"])
            db_path = entry["log_sqlite3_path"]
            cls._initialize_database(db_path, columns)
            cls._write_to_database(log_entry, db_path, columns)

        def _send_notification(url: str):
            try:
                response = requests.post(url, json=log_entry.__dict__)
                response.raise_for_status()
            except requests.RequestException as exc:
                print(f"Failed to send log to {url}: {exc}")

        if entry["telegram"]:
            Notifications.send_telegram_message(message=cls._format_log_entry_text(log_entry, delimiter))
        if entry["slack"] and entry["log_notification_slack"]:
            _send_notification(str(entry["log_notification_slack"]))
        if entry["url_notification"] and entry["log_notification_url"]:
            _send_notification(str(entry["log_notification_url"]))

    @classmethod
    def _ensure_writer(cls):
        with cls._writer_lock:
            if cls._writer is None or not cls._writer.is_alive():
                cls._writer = _LogWriter(cls._write_entry)
                cls._writer.start()
            return cls._writer

    @classmethod
    def _wait_for_queue(cls, writer, timeout: Optional[float]) -> None:
        if writer is None:
            return
        if timeout is None:
            writer.queue.join()
            return

        deadline = time.monotonic() + timeout
        while writer.queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)

    @classmethod
    def flush(cls, timeout: Optional[float] = 2.0) -> None:
        writer = cls._ensure_writer()
        cls._wait_for_queue(writer, timeout)

    @classmethod
    def shutdown(cls, timeout: float = 2.0) -> None:
        with cls._writer_lock:
            writer = cls._writer
            cls._writer = None
        if writer is None:
            return
        cls._wait_for_queue(writer, timeout)
        writer.stop()
        writer.join(timeout=timeout)
        cls.invalidate_config_cache()

    @classmethod
    def _require_pandas(cls):
        if pd is None:
            raise ImportError("pandas is required for BBLogger dataframe helpers")

    @classmethod
    def get_page(cls, page_num: int):
        cls._require_pandas()
        cls.flush()
        page_size = int(cls._get_config("log_page_size") or 100)
        log_file_path = cls._get_log_file_path()
        if not os.path.exists(log_file_path):
            raise FileNotFoundError(f"Log file for today does not exist: {log_file_path}")

        try:
            with open(log_file_path, "r", encoding="utf-8") as log_file:
                reader = csv.reader(
                    log_file,
                    delimiter=str(cls._get_config("log_delimiter") or ","),
                    quotechar="'",
                )
                logs = list(reader)
        except IOError:
            return pd.DataFrame()

        headers = list(cls._get_config("log_columns") or cls._default_config["log_columns"])
        if logs and logs[0] == headers:
            logs = logs[1:]

        total_logs = len(logs)
        total_pages = (total_logs + page_size - 1) // page_size
        if page_num < 1 or page_num > max(total_pages, 1):
            raise ValueError(f"Invalid page number: {page_num}. Total pages available: {total_pages}.")

        start_index = (page_num - 1) * page_size
        end_index = start_index + page_size
        return pd.DataFrame(logs[start_index:end_index], columns=headers)

    @classmethod
    def get_logs_in_range(cls, date: str, start_line: int, end_line: int):
        cls._require_pandas()
        cls.flush()
        log_file_path = os.path.join(
            str(cls._get_config("log_path")),
            f"{cls._get_config('log_prefix')}_log_{date}.log",
        )
        if not os.path.exists(log_file_path):
            raise FileNotFoundError(f"Log file for {date} does not exist: {log_file_path}")

        try:
            with open(log_file_path, "r", encoding="utf-8") as log_file:
                reader = csv.reader(
                    log_file,
                    delimiter=str(cls._get_config("log_delimiter") or ","),
                    quotechar="'",
                )
                logs = list(reader)
        except IOError:
            return pd.DataFrame()

        headers = None
        configured_headers = list(cls._get_config("log_columns") or cls._default_config["log_columns"])
        if logs and logs[0] == configured_headers:
            headers = logs[0]
            logs = logs[1:]

        if start_line < 1 or end_line > len(logs) or start_line > end_line:
            raise ValueError(
                f"Invalid range: start_line={start_line}, end_line={end_line}, total_lines={len(logs)}"
            )
        return pd.DataFrame(logs[start_line - 1:end_line], columns=headers)

    @classmethod
    def get_total_amount_of_pages(cls, date: Optional[str] = None) -> int:
        cls.flush()
        page_size = int(cls._get_config("log_page_size") or 100)
        if date is None:
            date = datetime.now().strftime("%Y_%m_%d")
            is_today = True
        else:
            is_today = False
        if is_today:
            log_file_path = cls._get_log_file_path()
        else:
            log_file_path = os.path.join(
                str(cls._get_config("log_path")),
                f"{cls._get_config('log_prefix')}_log_{date}.log",
            )

        if not os.path.exists(log_file_path):
            if is_today:
                raise Exception("No logs available")
            return 0

        try:
            with open(log_file_path, "r", encoding="utf-8") as log_file:
                reader = csv.reader(
                    log_file,
                    delimiter=str(cls._get_config("log_delimiter") or ","),
                    quotechar="'",
                )
                logs = list(reader)
        except IOError:
            if is_today:
                raise Exception("No logs available")
            return 0

        headers = list(cls._get_config("log_columns") or cls._default_config["log_columns"])
        if logs and logs[0] == headers:
            logs = logs[1:]
        total_entries = len(logs)
        return (total_entries + page_size - 1) // page_size

    @classmethod
    def read_logs_from_date(cls, date: str):
        cls._require_pandas()
        cls.flush()
        if not isinstance(date, str) or len(date) != 8 or not date.isdigit():
            raise ValueError("Date must be a string in 'YYYYMMDD' format, e.g., '20240110'.")

        formatted_date = f"{date[:4]}_{date[4:6]}_{date[6:]}"
        log_file_path = os.path.join(
            str(cls._get_config("log_path")),
            f"{cls._get_config('log_prefix')}_log_{formatted_date}.log",
        )
        if not os.path.exists(log_file_path):
            raise FileNotFoundError(f"Log file for date {date} does not exist: {log_file_path}")

        try:
            with open(log_file_path, "r", encoding="utf-8") as handle:
                first_line = handle.readline().strip()
            headers = list(cls._get_config("log_columns") or cls._default_config["log_columns"])
            has_header = first_line == ",".join(headers)
            return pd.read_csv(
                log_file_path,
                delimiter=str(cls._get_config("log_delimiter") or ","),
                quotechar="'",
                encoding="utf-8",
                header=0 if has_header else None,
                names=None if has_header else headers,
            )
        except Exception:
            return pd.DataFrame()

    @classmethod
    def get_logs_between_timestampt_and_timestampt(cls, t1: str, t2: str):
        cls._require_pandas()
        cls.flush()
        try:
            dt1 = datetime.strptime(t1, "%Y%m%d%H%M%S")
            dt2 = datetime.strptime(t2, "%Y%m%d%H%M%S")
        except ValueError as exc:
            raise ValueError("Timestamps must be in 'YYYYMMDDHHMMSS' format.") from exc

        if dt1 > dt2:
            raise ValueError("Start timestamp t1 must be less than or equal to end timestamp t2.")

        date_list = []
        current_date = dt1.date()
        end_date = dt2.date()
        while current_date <= end_date:
            date_list.append(current_date.strftime("%Y%m%d"))
            current_date += timedelta(days=1)

        log_dfs = []
        for date_str in date_list:
            try:
                log_dfs.append(cls.read_logs_from_date(date_str))
            except FileNotFoundError:
                continue
            except Exception:
                continue

        if not log_dfs:
            return pd.DataFrame()

        all_logs_df = pd.concat(log_dfs, ignore_index=True)
        try:
            all_logs_df["timestamp"] = pd.to_datetime(all_logs_df["timestamp"], format="%Y%m%d%H%M%S")
        except Exception:
            return pd.DataFrame()
        mask = (all_logs_df["timestamp"] >= dt1) & (all_logs_df["timestamp"] <= dt2)
        return all_logs_df.loc[mask].reset_index(drop=True)

    @classmethod
    def log(cls, message, telegram: bool = False, slack: bool = False, url_notification: bool = False):
        config = cls._get_config_snapshot()
        if not cls._normalize_bool(config.get("log_debug_mode")):
            return

        now = datetime.now()
        delta = (now - cls._last_time).total_seconds() if cls._last_time else 0.0
        cls._delta = now - cls._last_time if cls._last_time else None
        cls._last_time = now

        message_text = _scrub_secrets(str(message))
        if cls._RE_ERROR.search(message_text):
            log_type = "error"
        elif cls._RE_WARNING.search(message_text):
            log_type = "warning"
        else:
            log_type = "message"

        process_name = cls._get_process_name()
        log_enable_files = bool(cls._normalize_bool(config.get("log_enable_files")))
        entry = {
            "timestamp": now.strftime("%Y%m%d%H%M%S"),
            "log_type": log_type,
            "process": process_name,
            "code_location": cls._get_caller_fast(),
            "message": message_text,
            "processing_time": str(delta),
            "log_enable_files": log_enable_files,
            "log_enable_terminal_output": bool(cls._normalize_bool(config.get("log_enable_terminal_output"))),
            "log_enable_database": bool(cls._normalize_bool(config.get("log_enable_database"))),
            "log_sqlite3_path": config.get("log_sqlite3_path"),
            "log_columns": list(config.get("log_columns") or cls._default_config["log_columns"]),
            "log_delimiter": str(config.get("log_delimiter") or ","),
            "log_file_path": cls._get_log_file_path(
                config=config,
                now=now,
                process_name=process_name,
            )
            if log_enable_files
            else None,
            "log_notification_slack": config.get("log_notification_slack") or "",
            "log_notification_url": config.get("log_notification_url") or "",
            "telegram": bool(telegram),
            "slack": bool(slack),
            "url_notification": bool(url_notification),
        }

        writer = cls._ensure_writer()
        try:
            writer.queue.put_nowait(entry)
        except queue.Full:
            pass


BBLogger._ensure_writer()
atexit.register(BBLogger.shutdown)
