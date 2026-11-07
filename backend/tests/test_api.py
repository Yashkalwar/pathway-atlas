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
