from app.log import setup_logging


def test_setup_logging_creates_file(tmp_path):
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=log_dir)
    assert (log_dir / "app.log").exists()


def test_setup_logging_without_dir_ok():
    setup_logging(log_dir=None)  # 不抛异常
