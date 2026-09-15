"""Central configuration loader. No secrets or magic numbers live outside here + config/*.yaml."""
from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
CONFIG_DIR = PACKAGE_ROOT / "config"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


_load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = Path(os.environ.get("RADAR_DATA_DIR", str(PROJECT_ROOT / "data")))
DB_PATH = Path(os.environ.get("RADAR_DB_PATH", str(DATA_DIR / "radar.db")))
RUNS_DIR = DATA_DIR / "runs"
DESK_SHEETS_DIR = DATA_DIR / "desk_sheets"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"

for _dir in (DATA_DIR, RUNS_DIR, DESK_SHEETS_DIR, SNAPSHOTS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=None)
def _load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def sources() -> list[dict]:
    return _load_yaml("sources.yaml")["sources"]


def source_tiers() -> dict:
    return _load_yaml("sources.yaml")["source_tiers"]


def provenance_config() -> dict:
    return _load_yaml("sources.yaml").get("provenance", {})


def collection_config() -> dict:
    return _load_yaml("sources.yaml").get("collection", {"lookback_days": 21})


def categories() -> list[dict]:
    return _load_yaml("categories.yaml")["categories"]


def clusters() -> list[dict]:
    return _load_yaml("clusters.yaml")["clusters"]


def scoring_weights() -> dict:
    return _load_yaml("scoring_weights.yaml")


def feature_flags() -> dict:
    flags = _load_yaml("scoring_weights.yaml").get("feature_flags", {})
    if flags.get("auto_publish"):
        raise RuntimeError("auto_publish must stay false in v1 (Section 30). Human approval is a hard gate.")
    return flags


def content_rules() -> dict:
    return _load_yaml("content_rules.yaml")


def publishing_rules() -> dict:
    return _load_yaml("publishing.yaml")


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")  # unused in v1 hybrid mode

# Optional: UN Comtrade requires a free registered subscription key
# (https://comtradeplus.un.org/) for its data API. Exposure lookups work
# without one — they fall back to a manual-lookup link — but auto-retrieve a
# real trade-value figure when a key is present.
COMTRADE_API_KEY = os.environ.get("COMTRADE_API_KEY")

# WTO ePing (radar/collectors/eping.py) has no RSS/API — its SPS/TBT
# notification alerts are only delivered as email digests to a registered
# address, so collection means polling a dedicated inbox over IMAP. Requires
# a Gmail App Password (not the account's normal password) when the inbox is
# Gmail, since Gmail refuses plain-password IMAP login with 2-Step
# Verification on. See .env.example.
EPING_IMAP_HOST = os.environ.get("EPING_IMAP_HOST", "imap.gmail.com")
EPING_IMAP_PORT = int(os.environ.get("EPING_IMAP_PORT", "993"))
EPING_IMAP_USER = os.environ.get("EPING_IMAP_USER")
EPING_IMAP_PASSWORD = os.environ.get("EPING_IMAP_PASSWORD")
EPING_SENDER_DOMAIN = os.environ.get("EPING_SENDER_DOMAIN", "epingalert.org")
