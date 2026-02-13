import os
import sys
import traceback
import re
from typing import Optional
import csv
import requests
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from brainboost_data_source_logger_package.Notifications import Notifications


from brainboost_data_source_logger_package.BBLogEntry import BBLogEntry  # Replace with actual import path
from brainboost_configuration_package.BBConfig import BBConfig

class BBLogger:
    _process_name: Optional[str] = None
    _last_time: Optional[datetime] = None
    _delta: Optional[datetime] = None
    _config_disabled: bool = False
    _log_file_path: Optional[str] = None
    _default_config = {
        'log_debug_mode': True,
        'log_enable_files': False,
        'log_enable_terminal_output': True,
        'log_enable_database': False,
        'log_sqlite3_path': os.path.join('logs', 'brainboost_logs.sqlite3'),
        'log_columns': ['timestamp', 'log_type', 'process', 'code_location', 'message', 'processing_time'],
        'log_path': 'logs',
        'log_prefix': 'brainboost',
        'log_delimiter': ',',
        'log_page_size': 100,
        'log_notification_slack': '',
        'log_notification_url': '',
        'log_file_naming': 'daily',
        'log_file_name_convention': 'YYYY_MM_DD_HH_MM_SS-[process]-log.log'
    }

    @classmethod
    def _normalize_bool(cls, value):
        """
        Normalize various boolean representations to actual bool values.
        Accepts: true/false strings (case-insensitive), 1/0, yes/no, on/off.
        Returns actual bools unchanged.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        if isinstance(value, str):
            lower_val = value.lower().strip()
            if lower_val in ('true', '1', 'yes', 'on'):
                return True
            if lower_val in ('false', '0', 'no', 'off'):
                return False
        return value

    @classmethod
    def _get_config(cls, key: str):
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
    def _format_log_file_name(cls, convention: str, now: datetime, log_prefix: str) -> str:
        file_name = convention
        file_name = file_name.replace('YYYY_MM_DD_HH_MM_SS', now.strftime('%Y_%m_%d_%H_%M_%S'))
        file_name = file_name.replace('YYYY_MM_DD', now.strftime('%Y_%m_%d'))
        file_name = file_name.replace('[process]', cls._get_process_name())
        file_name = file_name.replace('${LOG_PREFIX}', str(log_prefix))
        return file_name

    @classmethod
    def _get_log_file_path(cls, date: Optional[str] = None) -> str:
        log_path = cls._get_config('log_path')
        log_prefix = cls._get_config('log_prefix')
        if date:
            return os.path.join(log_path, f"{log_prefix}_log_{date}.log")

        naming = str(cls._get_config('log_file_naming') or 'daily').lower()
        if naming == 'per_run':
            if cls._log_file_path:
                return cls._log_file_path
            convention = cls._get_config('log_file_name_convention') or 'YYYY_MM_DD_HH_MM_SS-[process]-log.log'
            now = cls._last_time or datetime.now()
            file_name = cls._format_log_file_name(convention, now, log_prefix)
            cls._log_file_path = os.path.join(log_path, file_name)
            return cls._log_file_path

        current_date = (cls._last_time or datetime.now()).strftime('%Y_%m_%d')
        return os.path.join(log_path, f"{log_prefix}_log_{current_date}.log")

    @classmethod
    def _initialize_database(cls):
        db_path = cls._get_config('log_sqlite3_path')
        if not db_path:
            return
        cls._ensure_parent_dir(db_path)
        if not os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            columns = cls._get_config('log_columns')
            columns_str = ", ".join([f"{col} TEXT" for col in columns])
            cursor.execute(f"CREATE TABLE logs ({columns_str});")
            conn.commit()
            conn.close()

    @classmethod
    def _write_to_database(cls, log_entry: BBLogEntry):
        db_path = cls._get_config('log_sqlite3_path')
        if not db_path:
            return
        cls._ensure_parent_dir(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        columns = cls._get_config('log_columns')
        cursor.execute(
            f"INSERT INTO logs ({', '.join(columns)}) VALUES ({', '.join(['?' for _ in columns])});",
            [
                log_entry.timestamp,
                log_entry.log_type,
                log_entry.process,
                log_entry.code_location,
                log_entry.message,
                log_entry.processing_time
            ]
        )
        conn.commit()
        conn.close()

    @classmethod
    def _write_to_log_file(cls, log_entry: BBLogEntry):
        log_path = cls._get_config('log_path')
        log_file_path = cls._get_log_file_path()
        if log_path and not os.path.exists(log_path):
            os.makedirs(log_path, exist_ok=True)
        file_exists = os.path.isfile(log_file_path)

        try:
            with open(log_file_path, 'a+', encoding='utf-8', newline='') as log_file:
                writer = csv.writer(
                    log_file,
                    delimiter=cls._get_config('log_delimiter'),
                    quotechar="'",
                    quoting=csv.QUOTE_MINIMAL
                )
                if not file_exists:
                    writer.writerow(cls._get_config('log_columns'))
                writer.writerow([
                    log_entry.timestamp,
                    log_entry.log_type,
                    log_entry.process,
                    log_entry.code_location,
                    log_entry.message,
                    log_entry.processing_time
                ])
        except IOError as e:
            print(f'Failed to write to log file: {e}')

    @classmethod
    def get_page(cls, page_num: int) -> pd.DataFrame:
        """
        Retrieve a specific page of log entries from today's log file as a pandas DataFrame.

        :param page_num: The page number to retrieve (1-based).
        :return: pandas DataFrame containing log entries for the specified page.
        :raises FileNotFoundError: If today's log file does not exist.
        :raises ValueError: If the page number is invalid.
        """
        # Retrieve page size from configuration
        page_size = cls._get_config('log_page_size')
        
        # Construct the log file path
        log_file_path = cls._get_log_file_path()

        # Check if the log file exists
        if not os.path.exists(log_file_path):
            raise FileNotFoundError(f"Log file for today does not exist: {log_file_path}")

        try:
            # Open and read the log file
            with open(log_file_path, 'r', encoding='utf-8') as log_file:
                reader = csv.reader(
                    log_file,
                    delimiter=cls._get_config('log_delimiter'),
                    quotechar="'"
                )
                logs = list(reader)

                # Extract headers if present
                if logs and logs[0] == cls._get_config('log_columns'):
                    headers = logs[0]
                    logs = logs[1:]
                else:
                    headers = cls._get_config('log_columns')

                total_logs = len(logs)
                total_pages = (total_logs + page_size - 1) // page_size  # Ceiling division

                # Validate page number
                if page_num < 1 or page_num > total_pages:
                    raise ValueError(f"Invalid page number: {page_num}. Total pages available: {total_pages}.")

                # Calculate start and end indices for slicing
                start_index = (page_num - 1) * page_size
                end_index = start_index + page_size

                # Slice the logs for the requested page
                selected_logs = logs[start_index:end_index]

                # Convert the selected logs to a pandas DataFrame
                df = pd.DataFrame(selected_logs, columns=headers)
                return df

        except IOError as e:
            print(f"Failed to read log file: {e}")
            return pd.DataFrame()

    @classmethod
    def get_logs_in_range(cls, date: str, start_line: int, end_line: int):
        """
        Retrieve log lines for a given date within a specified range.

        :param date: The date of the log file in YYYY_MM_DD format.
        :param start_line: The starting line number (1-based, inclusive).
        :param end_line: The ending line number (inclusive).
        :return: Pandas DataFrame of log entries within the specified range.
        """
        log_file_path = os.path.join(cls._get_config('log_path'), f"{cls._get_config('log_prefix')}_log_{date}.log")

        if not os.path.exists(log_file_path):
            raise FileNotFoundError(f"Log file for {date} does not exist: {log_file_path}")

        try:
            with open(log_file_path, 'r', encoding='utf-8') as log_file:
                reader = csv.reader(
                    log_file,
                    delimiter=cls._get_config('log_delimiter'),
                    quotechar="'"
                )
                logs = list(reader)

                # Skip header row if it exists
                if logs and logs[0] == cls._get_config('log_columns'):
                    headers = logs[0]
                    logs = logs[1:]
                else:
                    headers = None

                if start_line < 1 or end_line > len(logs) or start_line > end_line:
                    raise ValueError(f"Invalid range: start_line={start_line}, end_line={end_line}, total_lines={len(logs)}")

                selected_logs = logs[start_line - 1:end_line]
                return pd.DataFrame(selected_logs, columns=headers)
        except IOError as e:
            print(f"Failed to read log file: {e}")
            return pd.DataFrame()

    @classmethod
    def get_total_amount_of_pages(cls, date: Optional[str] = None) -> int:
        """
        Calculate the total number of pages available in a log file based on the page size.

        :param date: The date of the log file in YYYY_MM_DD format. If not provided, today's log is used.
        :param page_size: The number of log entries per page. Defaults to 100.
        :return: Total number of pages available.
        :raises Exception: If no logs are available for today and no date is provided.
        """
        page_size = cls._get_config('log_page_size')
        if date is None:
            date = datetime.now().strftime('%Y_%m_%d')
            is_today = True
        else:
            is_today = False
        if is_today:
            log_file_path = cls._get_log_file_path()
        else:
            log_file_path = os.path.join(cls._get_config('log_path'), f"{cls._get_config('log_prefix')}_log_{date}.log")

        if not os.path.exists(log_file_path):
            if is_today:
                raise Exception("No logs available")
            else:
                return 0  # Or you can choose to raise an exception for missing dates as well

        try:
            with open(log_file_path, 'r', encoding='utf-8') as log_file:
                reader = csv.reader(
                    log_file,
                    delimiter=cls._get_config('log_delimiter'),
                    quotechar="'"
                )
                logs = list(reader)

                # Remove header row if it exists
                if logs and logs[0] == cls._get_config('log_columns'):
                    logs = logs[1:]

                total_entries = len(logs)
                total_pages = (total_entries + page_size - 1) // page_size  # Ceiling division
                return total_pages
        except IOError as e:
            if is_today:
                raise Exception("No logs available")
            else:
                return 0  # Or handle the error as needed
            
    @classmethod
    def read_logs_from_date(cls, date: str) -> pd.DataFrame:
        """
        Read log lines from a specific date and return them as a pandas DataFrame.

        :param date: The date of the log file in 'YYYYMMDD' format, e.g., '20240110'.
        :return: pandas DataFrame containing the log entries.
        :raises ValueError: If the date format is incorrect.
        :raises FileNotFoundError: If the log file for the given date does not exist.
        """
        # Validate date format
        if not isinstance(date, str) or len(date) != 8 or not date.isdigit():
            raise ValueError("Date must be a string in 'YYYYMMDD' format, e.g., '20240110'.")

        # Convert to 'YYYY_MM_DD'
        formatted_date = f"{date[:4]}_{date[4:6]}_{date[6:]}"

        # Construct log file path
        log_file_path = os.path.join(
            cls._get_config('log_path'),
            f"{cls._get_config('log_prefix')}_log_{formatted_date}.log"
        )

        if not os.path.exists(log_file_path):
            raise FileNotFoundError(f"Log file for date {date} does not exist: {log_file_path}")

        try:
            # Determine if the log file has a header
            with open(log_file_path, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
                has_header = first_line == ",".join(cls._get_config('log_columns'))

            # Read the log file into a pandas DataFrame
            df = pd.read_csv(
                log_file_path,
                delimiter=cls._get_config('log_delimiter'),
                quotechar="'",
                encoding='utf-8',
                header=0 if has_header else None,
                names=cls._get_config('log_columns') if not has_header else None
            )

            return df
        except Exception as e:
            print(f"Failed to read log file: {e}")
            return pd.DataFrame()
        
    @classmethod
    def get_logs_between_timestampt_and_timestampt(cls, t1: str, t2: str) -> pd.DataFrame:
        """
        Retrieve all log entries between two timestamps across multiple log files.

        :param t1: The start timestamp in 'YYYYMMDDHHMMSS' format.
        :param t2: The end timestamp in 'YYYYMMDDHHMMSS' format.
        :return: pandas DataFrame containing log entries between t1 and t2.
        :raises ValueError: If the timestamp formats are incorrect or t1 > t2.
        """
        # Validate and parse timestamps
        try:
            dt1 = datetime.strptime(t1, '%Y%m%d%H%M%S')
            dt2 = datetime.strptime(t2, '%Y%m%d%H%M%S')
        except ValueError as ve:
            raise ValueError("Timestamps must be in 'YYYYMMDDHHMMSS' format.") from ve

        if dt1 > dt2:
            raise ValueError("Start timestamp t1 must be less than or equal to end timestamp t2.")

        # Generate list of dates between dt1 and dt2 inclusive
        date_list = []
        current_date = dt1.date()
        end_date = dt2.date()
        while current_date <= end_date:
            date_str = current_date.strftime('%Y%m%d')  # 'YYYYMMDD'
            date_list.append(date_str)
            current_date += timedelta(days=1)

        # Initialize list to collect DataFrames
        log_dfs = []
        for date_str in date_list:
            try:
                df = cls.read_logs_from_date(date_str)
                log_dfs.append(df)
            except FileNotFoundError:
                print(f"Log file for date {date_str} does not exist. Skipping.")
            except Exception as e:
                print(f"Failed to read log file for date {date_str}: {e}")

        if not log_dfs:
            print("No log entries found between the specified timestamps.")
            return pd.DataFrame()

        # Concatenate all DataFrames
        all_logs_df = pd.concat(log_dfs, ignore_index=True)

        # Convert 'timestamp' to datetime
        try:
            all_logs_df['timestamp'] = pd.to_datetime(all_logs_df['timestamp'], format='%Y%m%d%H%M%S')
        except Exception as e:
            print(f"Failed to convert 'timestamp' to datetime: {e}")
            return pd.DataFrame()

        # Filter logs between t1 and t2
        mask = (all_logs_df['timestamp'] >= dt1) & (all_logs_df['timestamp'] <= dt2)
        filtered_logs_df = all_logs_df.loc[mask].reset_index(drop=True)

        return filtered_logs_df

        
    @classmethod
    def log(cls, message, telegram: bool = False, slack: bool = False, url_notification: bool = False):
        if cls._normalize_bool(cls._get_config('log_debug_mode')):

            def is_error_message(message):
                error_pattern = re.compile(r'\b(error|errors|exception|exceptions|failed|missing)\b', re.IGNORECASE)
                return bool(error_pattern.search(message))

            def is_warning_message(message):
                warning_pattern = re.compile(r'\b(warning|aware|careful)\b', re.IGNORECASE)
                return bool(warning_pattern.search(message))

            log_type = 'error' if is_error_message(message) else 'warning' if is_warning_message(message) else 'message'

            cls._delta = datetime.now() - cls._last_time if cls._last_time else None
            cls._last_time = datetime.now()
            current_date = cls._last_time.strftime('%Y_%m_%d')

            stack = traceback.extract_stack()
            caller = stack[-2] if len(stack) >= 2 else None
            code_location = f"{os.path.basename(caller.filename)}:{caller.lineno}" if caller else 'Unknown'

            log_entry = BBLogEntry(
                process=cls._get_process_name(),
                timestamp=cls._last_time.strftime('%Y%m%d%H%M%S'),
                log_type=log_type,
                message=message,
                processing_time=str(cls._delta.total_seconds()) if cls._delta else '0',
                code_location=code_location
            )

            if cls._normalize_bool(cls._get_config('log_enable_files')):
                cls._write_to_log_file(log_entry)

            if cls._normalize_bool(cls._get_config('log_enable_terminal_output')):
                cls._safe_print(log_entry)

            if cls._normalize_bool(cls._get_config('log_enable_database')):
                cls._initialize_database()
                cls._write_to_database(log_entry)

            def send_notification(url, log_entry):
                try:
                    response = requests.post(url, json=log_entry.__dict__)
                    response.raise_for_status()
                except requests.RequestException as e:
                    print(f"Failed to send log to {url}: {e}")

            if telegram:
                Notifications.send_telegram_message(message=str(log_entry))
            if slack:
                slack_url = cls._get_config('log_notification_slack')
                if slack_url:
                    send_notification(slack_url, log_entry)
            if url_notification:
                url = cls._get_config('log_notification_url')
                if url:
                    send_notification(url, log_entry)
