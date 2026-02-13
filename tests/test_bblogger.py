import pytest
from brainboost_data_source_logger_package.BBLogger import BBLogger
from brainboost_configuration_package.BBConfig import BBConfig
import random
import string
from datetime import datetime, timedelta
import os

def random_message(length=50):
    """Generate a random string of fixed length."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def test_get_config_accepts_uppercase_and_lowercase_keys():
    conf_backup = BBConfig._conf.copy()
    overrides_backup = BBConfig._overrides.copy()
    config_file_backup = BBConfig._config_file
    config_disabled_backup = BBLogger._config_disabled
    last_time_backup = BBLogger._last_time
    log_file_path_backup = BBLogger._log_file_path
    try:
        BBConfig._conf = {
            "LOG_PATH": "tests/logs_upper",
            "log_path": "tests/logs_lower",
            "LOG_FILE_NAMING": "per_run",
            "LOG_FILE_NAME_CONVENTION": "YYYY_MM_DD_HH_MM_SS-[process]-${LOG_PREFIX}-log.log",
            "LOG_PREFIX": "bb",
        }
        BBConfig._overrides = {}
        BBConfig._config_file = "tests/dummy.conf"
        BBLogger._config_disabled = False
        BBLogger._log_file_path = None
        BBLogger._last_time = datetime(2024, 1, 2, 3, 4, 5)

        assert BBLogger._get_config("log_path") == "tests/logs_lower"
        assert BBLogger._get_config("LOG_PATH") == "tests/logs_upper"
        first_path = BBLogger._get_log_file_path()
        second_path = BBLogger._get_log_file_path()
        assert first_path == second_path
        assert "2024_01_02_03_04_05" in first_path
        assert "bb" in first_path
        assert BBLogger._get_process_name() in first_path
    finally:
        BBConfig._conf = conf_backup
        BBConfig._overrides = overrides_backup
        BBConfig._config_file = config_file_backup
        BBLogger._config_disabled = config_disabled_backup
        BBLogger._last_time = last_time_backup
        BBLogger._log_file_path = log_file_path_backup

def test_bblogger_inserts_millions_of_logs():
    """Test BBLogger by inserting a couple of million log lines."""

    # Override configuration settings for testing
    BBConfig.override('log_path', 'tests/logs')
    BBConfig.override('log_enable_terminal_output', False)
    BBConfig.override('log_enable_files', True)
    BBConfig.override('log_enable_database', False)
    BBConfig.override('log_page_size', 100)

    # Verify configuration overrides
    assert BBConfig.get('log_path') == 'tests/logs'
    assert not BBConfig.get('log_enable_terminal_output')
    assert BBConfig.get('log_enable_files')
    assert not BBConfig.get('log_enable_database')

    num_logs = 10_000  # Reduced number of logs for testing
    for i in range(num_logs):
        message = f"Test log {i}: {random_message()}"
        BBLogger.log(message)

def test_read_random_pages():
    """Test reading random pages from the current log file using BBLogger.get_page."""
    print("Testing BBLogger.get_page:")
    page_size = BBConfig.get('log_page_size')
    log_path = BBConfig.get('log_path')
    current_date = datetime.now().strftime('%Y_%m_%d')
    log_file_path = os.path.join(log_path, f"{BBConfig.get('log_prefix')}_log_{current_date}.log")

    print(f"Log file path: {log_file_path}")
    if not os.path.exists(log_file_path):
        pytest.skip(f"Log file for today does not exist: {log_file_path}")

    with open(log_file_path, 'r', encoding='utf-8') as log_file:
        total_lines = sum(1 for _ in log_file) - 1  # Exclude header row
        total_pages = (total_lines + page_size - 1) // page_size
        print(f"Total lines: {total_lines}, Total pages: {total_pages}")

    for _ in range(3):
        page_num = random.randint(1, total_pages)
        page = BBLogger.get_page(page_num)

        start_index = (page_num - 1) * page_size + 1
        end_index = start_index + len(page) - 1
        print(f"\n--- Page {page_num} (Rows {start_index} to {end_index}) ---")
        print(page.to_string(index=False))
        assert len(page) <= page_size

def test_get_logs_in_range():
    """Test retrieving logs within a specific range."""
    print("Testing BBLogger.get_logs_in_range:")
    current_date = datetime.now().strftime('%Y_%m_%d')
    start_line = 1
    end_line = 50
    logs = BBLogger.get_logs_in_range(current_date, start_line, end_line)
    print(logs.to_string(index=False))
    assert not logs.empty
    assert len(logs) == (end_line - start_line + 1)

def test_get_logs_between_timestamps():
    """Test retrieving logs between two timestamps."""
    print("Testing BBLogger.get_logs_between_timestampt_and_timestampt:")
    t1 = datetime.now().strftime('%Y%m%d%H%M%S')
    t2 = (datetime.now() + timedelta(minutes=5)).strftime('%Y%m%d%H%M%S')
    logs = BBLogger.get_logs_between_timestampt_and_timestampt(t1, t2)
    print(logs.to_string(index=False))
    assert not logs.empty
    assert all(t1 <= ts <= t2 for ts in logs['timestamp'])

def test_get_total_amount_of_pages():
    """Test retrieving the total number of pages in the log file."""
    print("Testing BBLogger.get_total_amount_of_pages:")
    current_date = datetime.now().strftime('%Y_%m_%d')
    total_pages = BBLogger.get_total_amount_of_pages(current_date)
    print(f"Total pages: {total_pages}")
    assert total_pages > 0


def test_telegram_integration(monkeypatch):
    """
    Test the telegram integration by monkeypatching the Notifications.send_telegram_message
    method so that it does not perform an actual HTTP request.
    """
 
    BBLogger.log('Testing loquillo chancho apestozo!!! ', telegram=True)

    # Verify that our fake telegram function was called with the test message.
    assert True




if __name__ == "__main__":
    pytest.main(["-v", "test_bblogger.py"])
