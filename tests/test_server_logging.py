import logging


def _flush_server_handlers():
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_pawflow_server_file_handler", False):
            handler.flush()


def _remove_server_handlers():
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_pawflow_server_file_handler", False):
            root.removeHandler(handler)
            handler.close()


def test_server_logging_writes_rotating_info_and_error_logs(tmp_path, monkeypatch):
    from core.server_logging import configure_server_logging

    log_dir = tmp_path / "runtime" / "logs"
    monkeypatch.setenv("PAWFLOW_SERVER_LOG_DIR", str(log_dir))
    monkeypatch.setenv("PAWFLOW_SERVER_LOG_MAX_BYTES", "256")
    monkeypatch.setenv("PAWFLOW_SERVER_LOG_BACKUP_COUNT", "2")
    monkeypatch.setenv("PAWFLOW_SERVER_ERROR_LOG_BACKUP_COUNT", "1")

    try:
        configure_server_logging(logging.INFO)
        logger = logging.getLogger("pawflow.test.server_logging")
        for i in range(40):
            logger.info("segmented server log line %03d %s", i, "x" * 40)
        logger.error("segmented error log marker")
        _flush_server_handlers()

        server_logs = sorted(log_dir.glob("server.log*"))
        error_logs = sorted(log_dir.glob("server.error.log*"))

        assert (log_dir / "server.log").exists()
        assert (log_dir / "server.error.log").exists()
        assert len(server_logs) <= 3
        assert len(error_logs) <= 2

        combined_errors = "\n".join(
            path.read_text(encoding="utf-8") for path in error_logs)
        assert "segmented error log marker" in combined_errors
    finally:
        _remove_server_handlers()


def test_cli_start_uses_rotating_server_logging():
    src = __import__("pathlib").Path("cli.py").read_text(encoding="utf-8")
    block = src[src.index("def cmd_start"):
                src.index("logger = logging.getLogger(\"pawflow\")")]
    assert "from core.server_logging import configure_server_logging" in block
    assert "configure_server_logging(logging.INFO)" in block
