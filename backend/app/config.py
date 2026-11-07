import os
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DB_PATH = ROOT / "data" / "warehouse.duckdb"


def _load(name):
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def rules():
    return _load("rules.yaml")


def calibration():
    cal = _load("calibration.yaml")
    profiles = cal.pop("profiles", {}) or {}
    cal["profile"] = os.getenv("CALIBRATION_PROFILE", cal.get("profile", ""))
    return {**cal, **(profiles.get(cal["profile"]) or {})}


def concepts():
    return _load("concepts.yaml")


def drug_by_id():
    return {v["id"]: k for k, v in concepts()["drugs"].items()}
