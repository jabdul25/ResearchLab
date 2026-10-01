from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

@dataclass(frozen=True)
class Settings:
    database_url: str
    min_turnover_24h: float = 500_000.0
    history_months: int = 12
    reports_dir: Path = Path("reports")
    request_pause_seconds: float = .08
    request_retries: int = 5

    @classmethod
    def load(cls, config_path: str = "config/system.yaml") -> "Settings":
        load_dotenv()
        data = yaml.safe_load(Path(config_path).read_text()) if Path(config_path).exists() else {}
        data = data or {}
        url = os.getenv("DATABASE_URL", data.get("database_url", ""))
        if not url:
            raise RuntimeError("DATABASE_URL is required; add it to .env")
        return cls(url, float(data.get("filters", {}).get("min_turnover_24h", 500000)),
                   int(data.get("collection", {}).get("history_months", 12)),
                   Path(data.get("reports", {}).get("directory", "reports")),
                   float(data.get("collection", {}).get("request_pause_seconds", .08)),
                   int(data.get("collection", {}).get("request_retries", 5)))
