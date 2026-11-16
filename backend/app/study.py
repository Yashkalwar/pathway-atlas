"""Client study designer: apply rule overrides -> re-derive the cohort analytics tables.

The raw OMOP tables are never touched; only analytics.* is rebuilt. Mirrors how real
RWE pathway tools (OHDSI TreatmentPatterns, Merck LOT algorithm) parameterize studies.
"""
import json
import time
from typing import Optional

import duckdb

from . import config
from .pipeline.build import attrition_steps, build_analytics, write_analytics

# Field spec: (key, label, type, default-from-rules.yaml, min, max, unit, help text)
SECTIONS = [
    {
        "id": "cohort",
        "title": "1 · Index cohort",
        "blurb": "Who counts as a new Therapy A initiator.",
        "fields": [
            ("washout_days", "New-user washout", "int", "washout_days", 0, 730, "days",
             "Days of continuous observation with no advanced therapy required before the first Therapy A fill. Standard: 180-365."),
            ("initiation_window_days", "Diagnosis -> treatment window", "int", "initiation_window_days", 30, 365, "days",
             "RA diagnosis to first DMARD fill within this window counts as 'treatment initiated'."),
        ],
    },
    {
        "id": "eras",
        "title": "2 · Drug-era construction",
        "blurb": "When consecutive fills still count as one continuous course of therapy.",
        "fields": [
            ("era_gap_days", "Permissible gap (grace period)", "int", "era_gap_days", 15, 180, "days",
             "Fills within this many days after supply runs out are merged into one era. THE discontinuation definition: larger gap = fewer 'discontinuations'. Claims studies use 30-90d."),
            ("min_era_days", "Minimum era duration", "int", "min_era_days", 0, 180, "days",
             "Drop eras shorter than this (e.g. a single infusion or steroid burst) before deriving lines."),
        ],
    },
    {
        "id": "events",
        "title": "3 · Lines & event definitions",
        "blurb": "The exact semantics of 'switch', 'add-on', 'restart' and 'discontinuation'.",
        "fields": [
            ("combo_overlap_days", "Regimen-defining window", "int", "combo_overlap_days", 7, 90, "days",
             "A different agent overlapping line start by more than this is an add-on (combination), not a switch. Merck LOT algorithm uses ~28d."),
            ("switch_window_days", "Switch window", "int", "switch_window_days", 14, 365, "days",
             "A different agent starting within this many days after the era ends = switch; beyond it = new line after a treatment-free gap."),
            ("restart_same_line", "Restart continues same line", "bool", "restart_same_line", None, None, "",
             "On: same drug resuming after a gap is a restart (still line 1). Off (stricter): the gap already counts as discontinuation; resuming starts a new episode."),
            ("disc_date_rule", "Discontinuation date convention", "enum", "disc_date_rule", None, None, "",
             "supply_end: last covered day (default). expected_refill: the day the permissible gap ran out — shifts all 'days to discontinue' later by the gap."),
        ],
    },
    {
        "id": "followup",
        "title": "4 · Follow-up & landmark",
        "blurb": "How long each patient is observed and where status is reported.",
        "fields": [
            ("followup_days", "Maximum follow-up", "int", "followup_days", 180, 1460, "days",
             "Censoring horizon after index (365 / 730 typical)."),
            ("funnel_window_days", "Status landmark", "int", "funnel_window_days", 90, 730, "days",
             "The day at which remain / switch / discontinue / hospitalized status is reported (365 = 12 months)."),
        ],
    },
    {
        "id": "unmet",
        "title": "5 · Unmet-need & adherence",
        "blurb": "Thresholds behind the unmet-need flags and leave-driver signals.",
        "fields": [
            ("pdc_threshold", "Adherence PDC cutoff", "float", "pdc_threshold", 0.5, 0.95, "",
             "Proportion of days covered counted as 'good adherence' (standard: 0.80)."),
            ("early_fail_days", "Early-failure cutoff", "int", "early_fail_days", 30, 365, "days",
             "Leaving line 1 before this = early primary failure flag."),
            ("multi_line_threshold", "Heavy cycling threshold", "int", "multi_line_threshold", 2, 5, "lines",
             "Patients reaching this many advanced-therapy lines are flagged."),
            ("proxy_lookback_days", "Pre-leave signal window", "int", "proxy_lookback_days", 14, 120, "days",
             "Window before a leave event scanned for flare / CRP / cost signals."),
            ("high_copay_threshold", "High OOP copay flag", "int", "high_copay_threshold", 50, 1000, "USD",
             "Mean patient out-of-pocket cost per fill above this flags a cost barrier."),
            ("elevated_crp_mg_l", "Elevated CRP threshold", "float", "elevated_crp_mg_l", 3.0, 30.0, "mg/L",
             "CRP above this in the pre-leave window = inflammation signal."),
        ],
    },
]

ENUMS = {"disc_date_rule": ["supply_end", "expected_refill"]}

PRESETS = [
    {"name": "default", "label": "Default claims study", "rules": {},
     "description": "60d permissible gap, 365d washout, restart = same line, landmark 12 mo."},
    {"name": "strict_episode", "label": "Strict episode (anniversary)", "rules": {"restart_same_line": False, "disc_date_rule": "expected_refill"},
     "description": "Any gap beyond the permissible gap = discontinuation, dated at the expected refill; resuming drug = new episode. Lowers apparent persistence."},
    {"name": "lenient_gap", "label": "Lenient refill gap", "rules": {"era_gap_days": 90, "switch_window_days": 120},
     "description": "90d permissible gap and 120d switch window — common for biologics dosed every 8 weeks. Raises 'remain' and 'switch', lowers 'discontinue'."},
    {"name": "broad_cohort", "label": "Broad cohort", "rules": {"washout_days": 180, "followup_days": 1095, "funnel_window_days": 730},
     "description": "6-month washout (bigger cohort, some prevalent users) with a 24-month landmark."},
]

FIELD_KEYS = {k for s in SECTIONS for k, *_ in s["fields"]}

RAW_TABLES = ["person", "observation_period", "visit_occurrence", "condition_occurrence", "drug_exposure", "cost", "measurement"]


def field_spec() -> list[dict]:
    defaults = config.rules()
    out = []
    for s in SECTIONS:
        fields = []
        for key, label, typ, rules_key, lo, hi, unit, help_ in s["fields"]:
            f = {"key": key, "label": label, "type": typ, "default": defaults.get(rules_key),
                 "min": lo, "max": hi, "unit": unit, "help": help_}
            if key in ENUMS:
                f["options"] = ENUMS[key]
            fields.append(f)
        out.append({"id": s["id"], "title": s["title"], "blurb": s["blurb"], "fields": fields})
    return out


def validate(overrides: dict) -> dict:
    """Whitelist + type/range-check client overrides."""
    clean = {}
    for key, val in overrides.items():
        if key not in FIELD_KEYS:
            raise ValueError(f"unknown study parameter: {key}")
        if key in ENUMS:
            if val not in ENUMS[key]:
                raise ValueError(f"{key} must be one of {ENUMS[key]}")
        elif key == "restart_same_line":
            val = bool(val)
        elif key == "pdc_threshold" or key == "elevated_crp_mg_l":
            val = float(val)
        else:
            val = int(val)
        clean[key] = val
    rules = {**config.rules(), **clean}
    if rules["funnel_window_days"] > rules["followup_days"]:
        raise ValueError("status landmark cannot exceed maximum follow-up")
    if rules["switch_window_days"] < rules["combo_overlap_days"]:
        raise ValueError("switch window should be >= regimen-defining window")
    return clean


def current() -> dict:
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    tbls = set(con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='analytics'").df()["table_name"])
    if "study_spec" not in tbls:
        con.close()
        return {"name": "default", "rules": {}}
    r = con.execute("SELECT name, rules_json, applied_at FROM analytics.study_spec").df().iloc[0]
    con.close()
    return {"name": r["name"], "rules": json.loads(r["rules_json"]), "applied_at": str(r["applied_at"])}


def apply(overrides: dict, name: str = "custom") -> dict:
    """Re-derive analytics tables with overridden rules. Raw events untouched."""
    clean = validate(overrides)
    rules = {**config.rules(), **clean}
    t0 = time.perf_counter()
    path = str(config.DB_PATH)
    raw = {}
    con = duckdb.connect(path, read_only=True)
    for t in RAW_TABLES:
        try:
            raw[t] = con.execute(f"SELECT * FROM {t}").df()
        except duckdb.CatalogException:
            pass
    con.close()
    analytics, attr = build_analytics(raw, rules, config.concepts())

    con = None
    for _ in range(40):  # wait for any transient read_only connections to close
        try:
            con = duckdb.connect(path)
            break
        except duckdb.IOException:
            time.sleep(0.25)
    if con is None:
        raise RuntimeError("warehouse is locked by another reader")
    write_analytics(con, analytics, attr)
    con.execute("CREATE OR REPLACE TABLE analytics.study_spec AS SELECT ? AS name, ? AS rules_json, now() AS applied_at",
                [name, json.dumps(clean)])
    con.close()

    from .metrics import core as m
    m.wh.cache_clear()
    return {"name": name, "overrides": clean, "seconds": round(time.perf_counter() - t0, 1),
            "attrition": [{"step": s, "n": n} for s, n in attrition_steps(attr)]}


def reset() -> dict:
    """Re-derive with the file defaults and drop the active spec marker."""
    result = apply({}, "default")
    con = duckdb.connect(str(config.DB_PATH))
    con.execute("DROP TABLE IF EXISTS analytics.study_spec")
    con.close()
    from .metrics import core as m
    m.wh.cache_clear()
    return result
