import os

import pytest
from fastapi.testclient import TestClient

from app import config

pytestmark = pytest.mark.skipif(not config.DB_PATH.exists(), reason="warehouse not built (run the build pipeline)")
os.environ["USE_LLM"] = "0"

from app.llm import agent  # noqa: E402
from app.main import app  # noqa: E402
from tests.eval_questions import EVAL  # noqa: E402

c = TestClient(app)


def test_funnel_reconciles():
    f = c.get("/api/funnel").json()
    assert f["remain_on_therapy"] + f["discontinued"] + f["switched"] == f["evaluable_patients"]
    assert f["evaluable_patients"] + f["not_evaluable_censored"] == f["index_patients"]
    assert f["hospitalized"] <= f["evaluable_patients"]
    assert sum(f["switch_destinations"].values()) == f["switched"]


def test_calibration_tolerance():
    p = c.get("/api/funnel").json()["pct"]
    if c.get("/api/kpis").json()["calibration_profile"] == "literature":
        # US claims studies: ~45-70% persist at 12 mo, ~16-24% switch, ~6-23% discontinue
        assert 50 <= p["remain"] <= 72 and 12 <= p["discontinued"] <= 28 and 10 <= p["switched"] <= 25
    else:
        assert 70 <= p["remain"] <= 88 and 8 <= p["discontinued"] <= 20 and 4 <= p["switched"] <= 12
    assert 2 <= p["hospitalized"] <= 12


def test_no_duplicate_patients_and_attrition():
    steps = {s["step"]: s["n"] for s in c.get("/api/attrition").json()["steps"]}
    assert steps["Index cohort (Therapy A initiators)"] == c.get("/api/kpis").json()["index_patients"]


def test_all_metric_families_available():
    for url in ("/api/kpis", "/api/pathways", "/api/survival?event=persistence", "/api/survival?event=switch", "/api/survival?event=discontinuation",
                "/api/survival?event=persistence&group=age", "/api/utilization", "/api/pre-index", "/api/escalation",
                "/api/subgroups?by=sex", "/api/unmet-need"):
        assert c.get(url).status_code == 200, url
    pid = c.get("/api/patients/sample?kind=switch&n=1").json()[0]["person_id"]
    assert c.get(f"/api/patients/{pid}/journey").json()["timeline"]


def test_persistence_curve_monotone():
    y = c.get("/api/survival?event=persistence").json()["overall"]["y"]
    assert all(a >= b for a, b in zip(y, y[1:]))


@pytest.mark.parametrize("q,tool", EVAL)
def test_rule_router(q, tool):
    r = agent.rule_route(q)
    assert r and r[0] == tool


def test_hospitalized_after_leaving_is_time_ordered():
    f = c.get("/api/funnel").json()
    assert f["hospitalized_after_leaving_therapy"] <= f["discontinued"] + f["switched"]
    assert f["hospitalized_among_persistent"] + f["hospitalized_among_switched"] + f["hospitalized_among_discontinued"] == f["hospitalized"]
    assert 0 < f["adherence_pdc_mean"] <= 1


def test_chat_fallback_answers():
    r = c.post("/api/chat", json={"question": "What happens to patients after they initiate Therapy A?"}).json()
    assert r["tool"] == "get_funnel" and r["engine"] == "rules" and "remain on therapy" in r["answer"]


def test_numbers_grounding_guard():
    payload = {"a": 7816, "pct": 80.4}
    assert agent.numbers_grounded("80.4% remain, 7,816 patients", payload, "q")
    assert not agent.numbers_grounded("Exactly 91.3% remain", payload, "q")


def test_rule_router_unmatched_returns_none():
    assert agent.rule_route("tell me a joke") is None


def test_leave_drivers_reconcile():
    ld = c.get("/api/leave-drivers").json()
    f = c.get("/api/funnel").json()
    assert ld["n_switched"] == f["switched"] and ld["n_discontinued"] == f["discontinued"]
    assert sum(x["switch_n"] for x in ld["proxy_categories"]) == ld["n_switched"]
    assert sum(x["discontinue_n"] for x in ld["proxy_categories"]) == ld["n_discontinued"]
    assert sum(x["n"] for x in ld["post_discontinuation_fate"]) == ld["n_discontinued"]
    flare = next(x for x in ld["proxy_categories"] if x["category"] == "Inadequate response / flare")
    assert flare["switch_pct"] > flare["discontinue_pct"]


def test_second_line_moa_swap_vs_cycling():
    sl = c.get("/api/second-line").json()
    assert sum(a["n"] for a in sl["by_agent"]) == sl["n_switched"]
    assert sl["persistence_gain_12m_pp"] > 0


def test_risk_drivers_and_subgroup_funnel_and_low_ram():
    import time
    from app.metrics.core import wh

    w = wh()
    assert not hasattr(w, "visits") and not hasattr(w, "drugs")
    rd = c.get("/api/risk-drivers").json()
    hrs = {r["key"]: r["hr"] for r in rd["discontinuation_cox"]}
    assert hrs["high_copay"] > 1.2 and hrs["comorb"] > 1.05
    sf = c.get("/api/funnel?group_by=copay&group_val=%3E%3D%24250%20OOP").json()
    assert sf["remain_on_therapy"] + sf["discontinued"] + sf["switched"] == sf["evaluable_patients"]
    t0 = time.perf_counter()
    assert c.get("/api/survival?event=switch&group=copay").status_code == 200
    t1 = time.perf_counter()
    assert c.get("/api/survival?event=switch&group=copay").status_code == 200
    t2 = time.perf_counter()
    assert (t2 - t1) <= max((t1 - t0), 0.02)



def test_study_designer_endpoints():
    g = c.get("/api/study").json()
    keys = {f["key"] for s in g["sections"] for f in s["fields"]}
    assert {"era_gap_days", "switch_window_days", "washout_days", "restart_same_line"} <= keys
    assert g["defaults"]["era_gap_days"] == 60 and g["active"]["name"] == "default"
    bad = c.post("/api/study", json={"name": "x", "rules": {"not_a_rule": 5}})
    assert bad.status_code == 422
    bad2 = c.post("/api/study", json={"name": "x", "rules": {"funnel_window_days": 9999}})
    assert bad2.status_code == 422


def test_study_apply_changes_cohort_and_reset():
    before = c.get("/api/funnel").json()
    try:
        r = c.post("/api/study", json={"name": "lenient", "rules": {"era_gap_days": 90, "switch_window_days": 120}})
        assert r.status_code == 200, r.text
        after = c.get("/api/funnel").json()
        # a longer permissible gap + wider switch window can only reduce discontinuation
        assert after["discontinued"] <= before["discontinued"]
        assert after["switched"] >= before["switched"]
        assert after["evaluable_patients"] + after["not_evaluable_censored"] == after["index_patients"]
        assert c.get("/api/kpis").json()["study_name"] == "lenient"
    finally:
        c.post("/api/study/reset")
    assert c.get("/api/kpis").json()["study_name"] == "default"
    back = c.get("/api/funnel").json()
    assert back["discontinued"] == before["discontinued"] and back["switched"] == before["switched"]
