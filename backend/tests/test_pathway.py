import pandas as pd

from app.pipeline.pathway import build_eras, derive_lines

DRUGS = {"mtx": {"id": 1, "class": "csDMARD"}, "ada": {"id": 2, "class": "advanced"}, "eta": {"id": 3, "class": "advanced"},
         "tcz": {"id": 4, "class": "advanced"}, "lef": {"id": 5, "class": "escalation"}}
RULES = dict(era_gap_days=60, switch_window_days=90, combo_overlap_days=28, followup_days=730, washout_days=365)
T0 = pd.Timestamp("2020-01-01")


def fills(pid, drug, start_day, n, gap=0):
    rows, d = [], start_day
    for _ in range(n):
        s = T0 + pd.Timedelta(days=d)
        rows.append((pid, DRUGS[drug]["id"], s, s + pd.Timedelta(days=27), 28))
        d += 28 + gap
    return rows


def run(rows, obs_end_day=1500, obs_start_day=-1000, rules=None):
    rules = rules or RULES
    de = pd.DataFrame(rows, columns=["person_id", "drug_concept_id", "drug_exposure_start_date", "drug_exposure_end_date", "days_supply"])
    pids = sorted(de.person_id.unique())
    obs = pd.DataFrame({"person_id": pids, "observation_period_start_date": T0 + pd.Timedelta(days=obs_start_day),
                        "observation_period_end_date": T0 + pd.Timedelta(days=obs_end_day)})
    eras = build_eras(de, rules["era_gap_days"], rules.get("min_era_days", 0))
    j, ev, attr = derive_lines(eras, obs, DRUGS, "ada", rules)
    return eras, (j.set_index("person_id") if len(j) else j), ev, attr


def test_short_gaps_merge_long_gaps_split():
    eras, *_ = run(fills(1, "ada", 0, 3, gap=10) + fills(1, "ada", 400, 2))
    assert len(eras) == 2 and eras.n_fills.tolist() == [3, 2]


def test_switch_discontinue_persistent_and_addon():
    rows = (fills(1, "ada", 0, 6) + fills(1, "eta", 6 * 28 + 10, 20)          # switch, remains on B
            + fills(2, "ada", 0, 6)                                              # discontinues (long observed gap)
            + fills(3, "ada", 0, 50)                                             # persistent to end of obs
            + fills(4, "ada", 0, 12) + fills(4, "lef", 100, 3)                   # conventional add-on escalation
            + fills(5, "ada", 0, 12) + fills(5, "tcz", 100, 5))                  # advanced add-on (overlap)
    _, j, ev, attr = run(rows, obs_end_day=1400)
    assert j.loc[1, "first_leave"] == "switch" and j.loc[1, "second_line"] == "eta" and j.loc[1, "switch_days"] == 6 * 28 + 10
    assert j.loc[1, "sequence"] == "ada > eta"
    assert j.loc[2, "first_leave"] == "discontinue" and j.loc[2, "discontinue_days"] == 5 * 28 + 27
    assert j.loc[2, "terminal_status"] == "discontinued"
    assert j.loc[3, "first_leave"] == "persistent" and j.loc[3, "leave_event"] == 0
    assert j.loc[4, "escalated"] == 1 and j.loc[4, "first_leave"] != "switch"
    assert j.loc[5, "escalated"] == 1 and j.loc[5, "n_lines"] == 1
    assert attr["n_index"] == 5


def test_restart_is_not_discontinuation():
    rows = fills(1, "ada", 0, 4) + fills(1, "ada", 400, 30)
    _, j, ev, _ = run(rows, obs_end_day=1500)
    assert j.loc[1, "first_leave"] == "persistent"
    assert (ev.event_type == "restart").sum() == 1


def test_censoring_when_gap_not_observable():
    rows = fills(1, "ada", 0, 3)
    _, j, *_ = run(rows, obs_end_day=3 * 28 + 30)
    assert j.loc[1, "first_leave"] == "persistent" and j.loc[1, "terminal_status"] == "ongoing"


def test_washout_and_not_first_line_excluded():
    rows = fills(1, "ada", 0, 3) + fills(2, "eta", 0, 3) + fills(2, "ada", 200, 3)
    _, j, _, attr = run(rows, obs_start_day=-100)
    assert attr["n_index"] == 0 and attr["n_failed_washout"] == 1 and attr["n_first_advanced_not_therapy_a"] == 1


def test_restart_as_new_episode():
    """restart_same_line=False: resuming the same drug after the gap = discontinuation + new episode."""
    rows = fills(1, "ada", 0, 4) + fills(1, "ada", 400, 30)
    _, j, ev, _ = run(rows, rules={**RULES, "restart_same_line": False})
    r = j.loc[1]
    assert r["first_leave"] == "discontinue" and r["n_lines"] == 2 and r["sequence"] == "ada > ada"
    assert r["had_restart"] == 0
    assert (ev.event_type == "discontinue").sum() == 1


def test_disc_date_expected_refill():
    """disc_date_rule=expected_refill: discontinuation is dated when the permissible gap ran out."""
    rows = fills(1, "ada", 0, 6)  # era ends 5*28+27=167; expected_refill -> 167+60=227
    _, j, _, _ = run(rows, obs_end_day=1400, rules={**RULES, "disc_date_rule": "expected_refill"})
    assert j.loc[1, "first_leave"] == "discontinue" and j.loc[1, "discontinue_days"] == 167 + 60


def test_min_era_days_drops_short_eras():
    """A single short course (e.g. one infusion) is dropped before line derivation."""
    rows = fills(1, "ada", 0, 1) + fills(2, "ada", 0, 6)
    _, j, _, attr = run(rows, rules={**RULES, "min_era_days": 90})
    assert attr["n_index"] == 1 and 2 in j.index and 1 not in j.index
