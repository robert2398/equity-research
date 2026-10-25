"""Configuration loading: .env secrets + versioned YAML threshold files.

Thresholds in YAML are initial heuristics, not optimized trading rules.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Missing required config file: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a YAML mapping")
    return data


@dataclass
class Settings:
    # kite credentials (from .env, never committed)
    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_redirect_url: str = "http://127.0.0.1:8765/kite/callback"

    timezone: str = "Asia/Kolkata"
    data_dir: Path = PROJECT_ROOT / "data"
    history_years: int = 3
    data_start_date: str | None = None   # explicit start; overrides history_years when set

    # universe filters
    min_stock_price: float = 50.0
    min_avg_daily_turnover: float = 20_000_000.0
    sector_top_n: int = 5
    stocks_per_sector: int = 10
    early_stage2_max_age_weeks: int = 6
    enable_order_placement: bool = False   # always false; orders are out of scope by design

    # yaml rule blocks
    rules: dict = field(default_factory=dict)

    # derived paths
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def session_file(self) -> Path:
        return self.data_dir / "session" / "access_token.json"

    @property
    def reports_dir(self) -> Path:
        return PROJECT_ROOT / "reports"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.raw_dir, self.processed_dir, self.snapshots_dir,
                  self.session_file.parent, self.reports_dir):
            p.mkdir(parents=True, exist_ok=True)


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or (PROJECT_ROOT / ".env"), override=False)

    s = Settings()
    s.kite_api_key = os.getenv("KITE_API_KEY", "").strip()
    s.kite_api_secret = os.getenv("KITE_API_SECRET", "").strip()
    s.kite_redirect_url = os.getenv("KITE_REDIRECT_URL", s.kite_redirect_url).strip()
    s.timezone = os.getenv("APP_TIMEZONE", s.timezone).strip()

    s.history_years = int(os.getenv("HISTORY_YEARS", str(s.history_years)))
    s.data_start_date = os.getenv("DATA_START_DATE") or None
    s.data_dir = (PROJECT_ROOT / os.getenv("DATA_DIR", "data")).resolve()

    s.min_stock_price = float(os.getenv("MIN_STOCK_PRICE", s.min_stock_price))
    s.min_avg_daily_turnover = float(os.getenv("MIN_AVG_DAILY_TURNOVER_INR", s.min_avg_daily_turnover))
    s.sector_top_n = int(os.getenv("SECTOR_TOP_N", s.sector_top_n))
    s.stocks_per_sector = int(os.getenv("STOCKS_PER_SECTOR", s.stocks_per_sector))
    s.enable_order_placement = os.getenv("ENABLE_ORDER_PLACEMENT", "false").lower() == "true"

    s.rules = {
        "settings": _load_yaml(CONFIG_DIR / "settings.yaml"),
        "sectors": _load_yaml(CONFIG_DIR / "sectors.yaml"),
        "stage_rules": _load_yaml(CONFIG_DIR / "stage_rules.yaml"),
        "setup_rules": _load_yaml(CONFIG_DIR / "setup_rules.yaml"),
        "ranking_rules": _load_yaml(CONFIG_DIR / "ranking_rules.yaml"),
    }
    return s


_cached: Settings | None = None


def get_settings(force_reload: bool = False) -> Settings:
    global _cached
    if _cached is None or force_reload:
        _cached = load_settings()
    return _cached
