"""Deterministic metrics over the analytics tables. Every function returns plain dicts that
include the metric definition and denominator, so numbers are always traceable."""
from functools import lru_cache

import duckdb
import numpy as np
import pandas as pd
from lifelines import AalenJohansenFitter, KaplanMeierFitter

from .. import config

THERAPY = config.concepts()["therapy_a"]
GROUPS = {"age": "age_band", "sex": "sex", "comorbidity": "comorbidity_band"}


class Warehouse:
    def __init__(self, path=None):
        con = duckdb.connect(str(path or config.DB_PATH), read_only=True)
        q = lambda s: con.execute(s).df()
        self.j = q("SELECT * FROM analytics.index_journey")
        self.ra = q("SELECT * FROM analytics.ra_patients")
        self.events = q("SELECT * FROM analytics.patient_events")
        self.attrition = q("SELECT * FROM analytics.attrition")
        self.build_info = q("SELECT * FROM analytics.build_info").iloc[0].to_dict()
        self.visits = q("SELECT * FROM visit_occurrence")
        self.drugs = q("SELECT * FROM drug_exposure")
        con.close()
        self.rules = config.rules()
        self.names = config.drug_by_id()


@lru_cache(maxsize=1)
def wh() -> Warehouse:
    return Warehouse()


def _q(s: pd.Series):
    s = s.dropna()
    if s.empty:
        return {"median": None, "p25": None, "p75": None, "n": 0}
    return {"median": float(s.median()), "p25": float(s.quantile(.25)), "p75": float(s.quantile(.75)), "n": int(len(s))}


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def evaluable(j: pd.DataFrame, window: int) -> pd.DataFrame:
    """Patients whose status at `window` days is known: followed for the full window, or had a leave event inside it."""
    return j[(j.followup_days >= window) | ((j.leave_event == 1) & (j.leave_days <= window))]


def _status(j: pd.DataFrame, window: int) -> pd.Series:
    left = (j.leave_event == 1) & (j.leave_days <= window)
    return pd.Series(np.where(left, j.first_leave, "persistent"), index=j.index)


def hosp_after_leave(ev: pd.DataFrame, window: int, w: Warehouse) -> set:
    """person_ids with an inpatient admission after leaving Therapy A and within `window` days of index."""
    left = ev[(ev.leave_event == 1) & (ev.leave_days <= window)][["person_id", "index_date", "first_leave_date"]]
    h = w.events[w.events.event_type.str.startswith("hospitalization")][["person_id", "event_date"]].merge(left, on="person_id")
    h = h[(pd.to_datetime(h.event_date) > h.first_leave_date) & (pd.to_datetime(h.event_date) <= h.index_date + pd.Timedelta(days=window))]
    return set(h.person_id)


def funnel(window: int | None = None, w: Warehouse | None = None, group: tuple | None = None):
    w = w or wh()
    window = window or w.rules["funnel_window_days"]
    j = w.j if group is None else w.j[w.j[GROUPS[group[0]]] == group[1]]
    ev = evaluable(j, window)
    st = _status(ev, window)
    n = len(ev)
    hosp = ev[(ev.hospitalized == 1) & (ev.hosp_days_from_index <= window)]
    sw = ev[st == "switch"]
    sw_km = _km(ev, "switch_days", "switch_event", window)
    cnt = {k: int((st == k).sum()) for k in ("persistent", "discontinue", "switch")}
    after = hosp_after_leave(ev, window, w)
    return {
        "therapy": THERAPY, "window_days": window, "index_patients": int(len(j)), "evaluable_patients": n,
        "not_evaluable_censored": int(len(j) - n),
        "remain_on_therapy": cnt["persistent"], "discontinued": cnt["discontinue"], "switched": cnt["switch"],
        "hospitalized": int(len(hosp)),
        "hospitalized_among_switched": int((hosp.person_id.isin(sw.person_id)).sum()),
        "hospitalized_among_discontinued": int((hosp.person_id.isin(ev[st == "discontinue"].person_id)).sum()),
        "hospitalized_among_persistent": int((hosp.person_id.isin(ev[st == "persistent"].person_id)).sum()),
        "hospitalized_after_leaving_therapy": len(after),
        "adherence_pdc_mean": round(float(ev.pdc_12m.mean()), 3),
        "adherence_pct_pdc_80": _pct(int((ev.pdc_12m >= 0.8).sum()), n),
        "pct": {"remain": _pct(cnt["persistent"], n), "discontinued": _pct(cnt["discontinue"], n),
                "switched": _pct(cnt["switch"], n), "hospitalized": _pct(len(hosp), n)},
        "median_days_to_switch_among_switchers": float(sw.switch_days.median()) if len(sw) else None,
        "median_days_to_discontinue_among_discontinuers": float(ev[st == "discontinue"].discontinue_days.median()) if cnt["discontinue"] else None,
        "switch_destinations": sw.second_line.value_counts().to_dict(),
        "definition": (f"Among {THERAPY} initiators observed for {window} days (or who left therapy before then), "
                       f"status at {window} days: switched (new advanced agent within {w.rules['switch_window_days']}d of prior era end), "
                       f"discontinued (no continued supply > {w.rules['era_gap_days']}d gap, no new advanced agent), else remain. "
                       "Hospitalization = any inpatient stay in the window (overlaps statuses)."),
        "km_switch_probability": sw_km,
    }


def _km(j, dcol, ecol, window):
    if j.empty:
        return None
    k = KaplanMeierFitter().fit(j[dcol].clip(lower=0), j[ecol])
    return round(float(1 - k.predict(window)), 4)


def pathways(max_lines: int = 3, min_n: int = 1, w: Warehouse | None = None):
    w = w or wh()
    j = w.j
    seqs = j.assign(parts=j.sequence.str.split(" > "))
    links, nodes_cnt, paths = {}, {}, {}
    for r in seqs.itertuples():
        parts = r.parts[:max_lines]
        term = "Discontinued" if r.terminal_status == "discontinued" else "Ongoing / censored"
        if len(r.parts) > max_lines:
            term = "Further lines"
        nodes = [f"L{i + 1}: {p}" for i, p in enumerate(parts)] + [term]
        for a, b in zip(nodes, nodes[1:]):
            links[(a, b)] = links.get((a, b), 0) + 1
        for n_ in nodes[:-1]:
            nodes_cnt[n_] = nodes_cnt.get(n_, 0) + 1
        key = " > ".join(parts + [term])
        paths[key] = paths.get(key, 0) + 1
    nodes = sorted({n for ab in links for n in ab})
    idx = {n: i for i, n in enumerate(nodes)}
    lk = [{"source": idx[a], "target": idx[b], "value": v} for (a, b), v in links.items() if v >= min_n]
    top = sorted(paths.items(), key=lambda kv: -kv[1])
    sun = {"ids": [], "labels": [], "parents": [], "values": []}
    agg = {}
    for p, v in paths.items():
        steps = p.split(" > ")
        for i in range(len(steps)):
            agg[tuple(steps[: i + 1])] = agg.get(tuple(steps[: i + 1]), 0) + (v if i == len(steps) - 1 else 0)
    # sunburst values: leaf counts only (branchvalues=total handled client-side via 'remainder')
    for key in sorted(agg, key=len):
        sun["ids"].append(" > ".join(key))
        sun["labels"].append(key[-1])
        sun["parents"].append(" > ".join(key[:-1]))
        sun["values"].append(agg[key])
    return {"nodes": nodes, "links": lk, "top_pathways": [{"path": k, "n": v, "pct": _pct(v, len(j))} for k, v in top[:15]],
            "sunburst": sun, "n": int(len(j)),
            "definition": "Sequence of lines of therapy (main advanced agent per line), derived from drug eras. Add-ons do not start a new line."}


def survival(event: str = "persistence", group: str | None = None, w: Warehouse | None = None):
    w = w or wh()
    spec = {"persistence": ("leave_days", "leave_event"), "switch": ("switch_days", "switch_event"),
            "discontinuation": ("discontinue_days", "discontinue_event")}[event]
    grid = list(range(0, w.rules["followup_days"] + 1, 15))

    def one(j):
        d, e = j[spec[0]].clip(lower=0), j[spec[1]]
        if event == "persistence":
            k = KaplanMeierFitter().fit(d, e)
            y = k.survival_function_at_times(grid).values
            lo_hi = k.confidence_interval_
            lo = np.interp(grid, lo_hi.index, lo_hi.iloc[:, 0])
            hi = np.interp(grid, lo_hi.index, lo_hi.iloc[:, 1])
            at = lambda t: round(float(k.predict(t)), 4)
            med = None if np.isinf(k.median_survival_time_) else float(k.median_survival_time_)
            return {"x": grid, "y": [round(float(v), 4) for v in y], "lo": [round(float(v), 4) for v in lo], "hi": [round(float(v), 4) for v in hi],
                    "at_180": at(180), "at_365": at(365), "at_730": at(730), "median_days": med, "n": int(len(j)), "events": int(e.sum())}
        code = (j.first_leave == ("switch" if event == "switch" else "discontinue")).astype(int)
        ev = np.where(j.leave_event == 1, np.where(code == 1, 1, 2), 0)
        aj = AalenJohansenFitter(calculate_variance=False, seed=0).fit(j.leave_days.clip(lower=0).astype(float) + np.random.default_rng(0).uniform(0, 1e-3, len(j)), ev, event_of_interest=1)
        c = aj.cumulative_density_
        y = np.interp(grid, c.index, c.iloc[:, 0])
        at = lambda t: round(float(np.interp(t, c.index, c.iloc[:, 0])), 4)
        return {"x": grid, "y": [round(float(v), 4) for v in y], "at_180": at(180), "at_365": at(365), "at_730": at(730),
                "n": int(len(j)), "events": int(code.sum())}

    out = {"event": event, "curve": "survival" if event == "persistence" else "cumulative_incidence",
           "definition": {"persistence": "Kaplan-Meier probability of still being on Therapy A (not switched/discontinued).",
                          "switch": "Cumulative incidence of switching (discontinuation treated as a competing event).",
                          "discontinuation": "Cumulative incidence of discontinuation (switching treated as a competing event)."}[event]}
    if group:
        col = GROUPS[group]
        out["groups"] = {str(g): one(sub) for g, sub in w.j.groupby(col)}
    else:
        out["overall"] = one(w.j)
    return out


def utilization(w: Warehouse | None = None):
    w = w or wh()
    j = w.j
    py = j.pyears.sum()
    by = {}
    for status, g in j.groupby("first_leave"):
        p = g.pyears.sum()
        by[status] = {"patients": int(len(g)), "hospitalizations_per_100py": round(100 * g.n_hosp.sum() / p, 1),
                      "er_per_100py": round(100 * g.n_er.sum() / p, 1), "outpatient_per_py": round(g.n_outpatient.sum() / p, 2),
                      "cost_per_patient_year": round(g.total_cost.sum() / p, 0)}
    return {"patients": int(len(j)), "patient_years": round(float(py), 0),
            "hospitalizations_per_100py": round(100 * j.n_hosp.sum() / py, 1),
            "ra_hospitalizations_per_100py": round(100 * j.n_hosp_ra.sum() / py, 1),
            "er_per_100py": round(100 * j.n_er.sum() / py, 1), "outpatient_per_py": round(j.n_outpatient.sum() / py, 2),
            "mean_cost_per_patient_year": round(float(j.total_cost.sum() / py), 0),
            "mean_inpatient_days_per_hospitalized": round(float(j.hosp_days.sum() / max(j.n_hosp.sum(), 1)), 1),
            "by_status": by,
            "definition": "Events from index to censoring, divided by patient-years of follow-up. Cost = synthetic paid amounts for visits+drug fills."}


def pre_index(w: Warehouse | None = None):
    w = w or wh()
    r = w.ra
    n = int(r.dx_date.notna().sum())
    return {
        "ra_patients": n,
        "time_to_diagnosis_days": _q(r.days_onset_to_dx),
        "time_to_specialist_days": _q(r.days_visit_to_specialist),
        "time_to_treatment_days": _q(r.days_dx_to_treatment),
        "time_dx_to_therapy_a_days": _q((w.j.merge(r[["person_id", "dx_date"]], on="person_id")
                                         .pipe(lambda x: (x.index_date - x.dx_date).dt.days))),
        "treatment_initiation_rate": {"pct_within_window": _pct(int(r.initiated_in_window.sum()), n),
                                      "pct_ever": _pct(int(r.initiated_ever.sum()), n), "window_days": w.rules["initiation_window_days"], "n": n},
        "definition": {"time_to_diagnosis": "first symptom onset -> RA diagnosis", "time_to_specialist": "first (primary-care) visit -> first rheumatology visit",
                       "time_to_treatment": "RA diagnosis -> first DMARD fill",
                       "time_dx_to_therapy_a": "RA diagnosis -> first Therapy A fill (index cohort)", "initiation_rate": "share of RA patients with a DMARD fill within the window"},
    }


def escalation(window: int | None = None, w: Warehouse | None = None):
    w = w or wh()
    window = window or w.rules["funnel_window_days"]
    ev = evaluable(w.j, window)
    e = ev[(ev.escalated == 1) & (ev.escalation_days <= window)]
    types = w.events[w.events.event_type.isin(["add_on_advanced", "add_on_conventional"]) & w.events.person_id.isin(e.person_id)]
    return {"window_days": window, "n": int(len(ev)), "escalated": int(len(e)), "pct": _pct(len(e), len(ev)),
            "median_days_to_escalation": float(e.escalation_days.median()) if len(e) else None,
            "agents_added": types.to_therapy.value_counts().to_dict(),
            "definition": "Add-on of leflunomide/prednisone or a second advanced agent while on Therapy A (line 1)."}


def subgroups(by: str = "age", window: int | None = None, w: Warehouse | None = None):
    w = w or wh()
    out = []
    order = {"age": ["<45", "45-59", "60+"], "comorbidity": ["0", "1", "2+"]}.get(by)
    present = list(w.j[GROUPS[by]].unique())
    for g in ([x for x in order if x in present] if order else sorted(present)):
        f = funnel(window, w, (by, g))
        n = f["evaluable_patients"]
        out.append({"group": g, "n": n, "pct_remain": f["pct"]["remain"], "pct_discontinued": f["pct"]["discontinued"],
                    "pct_switched": f["pct"]["switched"], "pct_hospitalized": f["pct"]["hospitalized"]})
    return {"by": by, "window_days": window or w.rules["funnel_window_days"], "groups": out}


def unmet_need(w: Warehouse | None = None):
    w = w or wh()
    j = w.j
    W = w.rules["funnel_window_days"]
    ev = evaluable(j, W)
    early = ev[(ev.leave_event == 1) & (ev.leave_days <= 180)]
    multi = j[j.n_lines >= 3]
    hosp_off = hosp_after_leave(ev, W, w)
    nofurther = ev[(ev.first_leave == "discontinue") & (ev.leave_days <= W) & (ev.n_lines == 1)]
    return {"flags": [
        {"label": "Left Therapy A within 6 months (early failure)", "n": int(len(early)), "pct": _pct(len(early), len(ev))},
        {"label": "Cycled to 3+ advanced therapies", "n": int(len(multi)), "pct": _pct(len(multi), len(j))},
        {"label": "Hospitalized after leaving Therapy A (within window)", "n": int(len(hosp_off)), "pct": _pct(len(hosp_off), len(ev))},
        {"label": "Discontinued within window, no further advanced therapy", "n": len(nofurther), "pct": _pct(len(nofurther), len(ev))},
        {"label": "Low adherence on Therapy A (PDC < 80%)", "n": int((ev.pdc_12m < 0.8).sum()), "pct": _pct(int((ev.pdc_12m < 0.8).sum()), len(ev))}],
        "window_days": W, "denominator": int(len(ev)),
        "definition": f"Among patients evaluable at {W} days (3+ therapies uses all initiators). PDC = proportion of days covered by Therapy A fills in the first {W} days."}


def kpis(w: Warehouse | None = None):
    w = w or wh()
    f = funnel(None, w)
    pi = pre_index(w)
    return {"calibration_profile": w.build_info["profile"], "seed": int(w.build_info["seed"]), "ra_patients": pi["ra_patients"], "index_patients": f["index_patients"], "window_days": f["window_days"],
            "pct_persistent": f["pct"]["remain"], "pct_discontinued": f["pct"]["discontinued"], "pct_switched": f["pct"]["switched"],
            "pct_hospitalized": f["pct"]["hospitalized"], "median_days_to_switch": f["median_days_to_switch_among_switchers"],
            "median_days_to_treatment": pi["time_to_treatment_days"]["median"],
            "median_days_to_diagnosis": pi["time_to_diagnosis_days"]["median"],
            "adherence_pct_pdc_80": f["adherence_pct_pdc_80"], "hospitalized_after_leaving_therapy": f["hospitalized_after_leaving_therapy"]}


def attrition(w: Warehouse | None = None):
    w = w or wh()
    return {"steps": w.attrition.to_dict("records")}


def patient_journey(person_id: int, w: Warehouse | None = None):
    w = w or wh()
    ev = w.events[w.events.person_id == person_id]
    vis = w.visits[w.visits.person_id == person_id]
    dr = w.drugs[w.drugs.person_id == person_id]
    if vis.empty:
        return None
    vt = {9201: "Hospitalization", 9202: "Outpatient", 9203: "ER"}
    items = [{"date": str(pd.Timestamp(r.visit_start_date).date()), "type": vt.get(r.visit_concept_id, "Visit"),
              "detail": r.visit_source_value} for r in vis.itertuples()]
    items += [{"date": str(pd.Timestamp(r.event_date).date()), "type": "Event:" + r.event_type,
               "detail": f"{r.from_therapy or ''} -> {r.to_therapy or ''}".strip(" ->")} for r in ev.itertuples()
              if not r.event_type.startswith("hospitalization")]
    from_name = w.names
    fills = dr.assign(name=dr.drug_concept_id.map(from_name)).sort_values("drug_exposure_start_date")
    drug_spans = fills.groupby("name").agg(first=("drug_exposure_start_date", "min"), last=("drug_exposure_end_date", "max"),
                                          fills=("drug_exposure_id", "size")).reset_index()
    jr = w.j[w.j.person_id == person_id]
    return {"person_id": person_id, "summary": jr.iloc[0].astype(object).where(jr.iloc[0].notna(), None).to_dict() if len(jr) else None,
            "timeline": sorted(items, key=lambda x: x["date"]),
            "drug_spans": [{"drug": r.name, "start": str(pd.Timestamp(r.first).date()), "end": str(pd.Timestamp(r.last).date()), "fills": int(r.fills)}
                           for r in drug_spans.itertuples()]}


def sample_patients(kind: str = "switch", n: int = 5, w: Warehouse | None = None):
    w = w or wh()
    j = w.j[w.j.first_leave == kind] if kind != "any" else w.j
    return j.sample(min(n, len(j)), random_state=1)[["person_id", "sequence", "first_leave", "leave_days"]].to_dict("records")
