import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    """Store rooted in a temp data dir with the real YAML rules loaded."""
    from scanner import config as cfg
    from scanner.data.store import Store
    real = cfg.load_settings()
    s = cfg.Settings(data_dir=tmp_path)
    s.rules = real.rules
    monkeypatch.setattr(cfg, "_cached", s)
    return Store()

