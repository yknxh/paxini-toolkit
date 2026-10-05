import subprocess
import sys

from paxkit import paths


def test_data_dir_inside_repo():
    assert (paths.REPO_ROOT / "pyproject.toml").is_file()
    assert paths.DATA_DIR == paths.REPO_ROOT / "data"
    assert paths.data_path("logs", create=False) == paths.REPO_ROOT / "data" / "logs"


def test_data_dir_independent_of_cwd(tmp_path):
    # data/ must point inside the repo even when run from another directory
    out = subprocess.run(
        [sys.executable, "-c", "from paxkit import paths; print(paths.data_path('logs', create=False))"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == str(paths.REPO_ROOT / "data" / "logs")


def test_config_loads():
    from paxkit.config import Config

    cfg = Config.load()
    assert cfg.get("gauge.baudrate") == 2400
    assert cfg.get("gauge.record_regex") == r"[-+ \d]\d{3}\.\d"
    assert cfg.get("sensor_types.A.taxels") == 31
