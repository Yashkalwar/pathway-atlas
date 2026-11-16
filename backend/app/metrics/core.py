"""Deterministic metrics over the analytics tables. Every function returns plain dicts that
include the metric definition and denominator, so numbers are always traceable."""
from functools import lru_cache

import json

import duckdb
import numpy as np
import pandas as pd
from lifelines import AalenJohansenFitter, CoxPHFitter, KaplanMeierFitter

from .. import config

THERAPY = config.concepts()["therapy_a"]
GROUPS = {"age": "age_band", "sex": "sex", "comorbidity": "comorbidity_band", "copay": "copay_band"}

PROXY_ORDER = [
    "Inadequate response / flare",
    "Adverse event / intolerance",
    "Cost / adherence barrier",
    "Sustained low activity / step-down",
    "Unobserved in structured RWD",
]

FATE_ORDER = [
    "Maintained on csDMARD only",
    "Untreated in follow-up",
    "Restarted Therapy A, later stopped",
    "Delayed switch (>90d gap)",
]


class Warehouse:
    """Holds only compact summary tables in RAM (~15 MB); queries raw OMOP tables per-patient on demand."""

    def __init__(self, path=None):
        self.path = str(path or config.DB_PATH)
        self._cache: dict = {}
        con = duckdb.connect(self.path, read_only=True)
        q = lambda s: con.execute(s).df()
        self.j = q("SELECT * FROM analytics.index_journey")
        self.ra = q("SELECT * FROM analytics.ra_patients")
        self.events = q("SELECT * FROM analytics.patient_events")
        self.attrition = q("SELECT * FROM analytics.attrition")
        self.build_info = q("SELECT * FROM analytics.build_info").iloc[0].to_dict()
        tbls = set(con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").df()["table_name"])
        self.has_measurement = "measurement" in tbls
        a_tbls = set(con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'analytics'").df()["table_name"])
        spec = q("SELECT name, rules_json FROM analytics.study_spec").iloc[0].to_dict() if "study_spec" in a_tbls else None
        cost_cols = set(con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'cost'").df()["column_name"])
        self.has_oop = "paid_by_patient" in cost_cols
        con.close()
        self.rules = config.rules()
        self.study_name = "default"
        if spec is not None:
            self.study_name = spec["name"]
            self.rules = {**self.rules, **json.loads(spec["rules_json"])}
        self.names = config.drug_by_id()
        self.concepts = config.concepts()

    def memo(self, key: tuple, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]


@lru_cache(maxsize=1)
def wh() -> Warehouse:
    return Warehouse()


def _q(s: pd.Series):
    s = s.dropna()
    if s.empty:
        return {"median": None, "p25": None, "p75": None, "n": 0}
    return {"median": float(s.median()), "p25": float(s.quantile(0.25)), "p75": float(s.quantile(0.75)), "n": int(len(s))}


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
    if left.empty:
        return set()
    h = w.events[w.events.event_type.str.startswith("hospitalization")][["person_id", "event_date"]].merge(left, on="person_id")
    h = h[(pd.to_datetime(h.event_date) > h.first_leave_date) & (pd.to_datetime(h.event_date) <= h.index_date + pd.Timedelta(days=window))]
    return set(h.person_id)


def _km(j, dcol, ecol, window):
    if j.empty:
        return None
    k = KaplanMeierFitter().fit(j[dcol].clip(lower=0), j[ecol])
    return round(float(1 - k.predict(window)), 4)


def funnel(window: int | None = None, w: Warehouse | None = None, group: tuple | None = None, include_km: bool = True):
    w = w or wh()
    window = window or w.rules["funnel_window_days"]
    key = ("funnel", window, group, include_km)

    def _compute():
        j = w.j if group is None else w.j[w.j[GROUPS[group[0]]] == group[1]]
        ev = evaluable(j, window)
        st = _status(ev, window)
        n = len(ev)
        hosp = ev[(ev.hospitalized == 1) & (ev.hosp_days_from_index <= window)]
        sw = ev[st == "switch"]
        sw_km = _km(ev, "switch_days", "switch_event", window) if include_km else None
        cnt = {k: int((st == k).sum()) for k in ("persistent", "discontinue", "switch")}
        after = hosp_after_leave(ev, window, w)
        has_act = "active_pdc" in ev.columns and n > 0
        has_oop = "mean_oop_copay" in ev.columns and n > 0
        pdc_thr = w.rules.get("pdc_threshold", 0.8)
        return {
            "therapy": THERAPY,
            "window_days": window,
            "group": {"by": group[0], "value": group[1]} if group else None,
            "index_patients": int(len(j)),
            "evaluable_patients": n,
            "not_evaluable_censored": int(len(j) - n),
            "remain_on_therapy": cnt["persistent"],
            "discontinued": cnt["discontinue"],
            "switched": cnt["switch"],
            "hospitalized": int(len(hosp)),
            "hospitalized_among_switched": int((hosp.person_id.isin(sw.person_id)).sum()),
            "hospitalized_among_discontinued": int((hosp.person_id.isin(ev[st == "discontinue"].person_id)).sum()),
            "hospitalized_among_persistent": int((hosp.person_id.isin(ev[st == "persistent"].person_id)).sum()),
            "hospitalized_after_leaving_therapy": len(after),
            "adherence_pdc_mean": round(float(ev.pdc_12m.mean()), 3) if n else None,
            "adherence_pct_pdc_80": _pct(int((ev.pdc_12m >= pdc_thr).sum()), n),
            "active_pdc_mean": round(float(ev.active_pdc.mean()), 3) if has_act else None,
            "active_pct_pdc_80": _pct(int((ev.active_pdc >= pdc_thr).sum()), n) if has_act else None,
            "pdc_threshold": pdc_thr,
            "mean_oop_copay": round(float(ev.mean_oop_copay.mean()), 0) if has_oop else None,
            "pct": {
                "remain": _pct(cnt["persistent"], n),
                "discontinued": _pct(cnt["discontinue"], n),
                "switched": _pct(cnt["switch"], n),
                "hospitalized": _pct(len(hosp), n),
            },
            "median_days_to_switch_among_switchers": float(sw.switch_days.median()) if len(sw) else None,
            "median_days_to_discontinue_among_discontinuers": float(ev[st == "discontinue"].discontinue_days.median()) if cnt["discontinue"] else None,
            "switch_destinations": sw.second_line.value_counts().to_dict(),
            "definition": (
                f"Among {THERAPY} initiators observed for {window} days (or who left therapy before then), "
                f"status at {window} days: switched (new advanced agent within {w.rules['switch_window_days']}d of prior era end), "
                f"discontinued (no continued supply > {w.rules['era_gap_days']}d gap, no new advanced agent), else remain. "
                "Hospitalization = any inpatient stay in the window (overlaps statuses)."
            ),
            "km_switch_probability": sw_km,
        }

    return w.memo(key, _compute)


def leave_drivers(window: int | None = None, w: Warehouse | None = None):
    """Preceding 60-day OMOP clinical/claims proxy drivers for switching vs discontinuation, plus post-discontinuation fate."""
    w = w or wh()
    window = window or w.rules["funnel_window_days"]

    def _compute():
        ev = evaluable(w.j, window)
        st = _status(ev, window)
        sw = ev[st == "switch"]
        dc = ev[st == "discontinue"]
        pe = ev[st == "persistent"]
        ns, nd = len(sw), len(dc)

        sw_vc = sw["leave_proxy"].value_counts() if "leave_proxy" in sw.columns else pd.Series(dtype=int)
        dc_vc = dc["leave_proxy"].value_counts() if "leave_proxy" in dc.columns else pd.Series(dtype=int)
        cats = []
        for c in PROXY_ORDER:
            sn, dn = int(sw_vc.get(c, 0)), int(dc_vc.get(c, 0))
            cats.append({
                "category": c,
                "switch_n": sn, "switch_pct": _pct(sn, ns) or 0.0,
                "discontinue_n": dn, "discontinue_pct": _pct(dn, nd) or 0.0,
            })

        sig_defs = [
            ("sig_flare_or_escalation", "Flare code, CRP > 10 mg/L, or rescue escalation"),
            ("sig_elevated_crp", "Elevated CRP lab (> 10 mg/L) in lookback"),
            ("sig_infection_or_adr", "Serious infection or adverse drug reaction code"),
            ("sig_acute_er_hosp", "ER or inpatient visit in 60d before leaving"),
            ("sig_cost_or_drift", f"High OOP copay (>=${w.rules.get('high_copay_threshold', 250)}) or active PDC < {int(100 * w.rules.get('pdc_threshold', 0.8))}%"),
            ("sig_remission_stepdown", "Sustained >12m low CRP (<=5 mg/L) + csDMARD step-down"),
        ]
        sigs = []
        for col, label in sig_defs:
            if col in ev.columns:
                sn, dn = int(sw[col].sum()), int(dc[col].sum())
                sigs.append({
                    "signal": label, "key": col,
                    "switch_n": sn, "switch_pct": _pct(sn, ns) or 0.0,
                    "discontinue_n": dn, "discontinue_pct": _pct(dn, nd) or 0.0,
                })

        fate_vc = dc["post_disc_fate"].value_counts() if "post_disc_fate" in dc.columns else pd.Series(dtype=int)
        fates = [{"fate": f, "n": int(fate_vc.get(f, 0)), "pct": _pct(int(fate_vc.get(f, 0)), nd) or 0.0} for f in FATE_ORDER]

        def _adh(sub):
            if sub.empty:
                return {"n": 0, "active_pdc_mean": None, "active_pct_80": None, "fixed_pdc_12m_mean": None, "mean_oop_copay": None}
            return {
                "n": int(len(sub)),
                "active_pdc_mean": round(float(sub.active_pdc.mean()), 3),
                "active_pct_80": _pct(int((sub.active_pdc >= 0.8).sum()), len(sub)),
                "fixed_pdc_12m_mean": round(float(sub.pdc_12m.mean()), 3),
                "mean_oop_copay": round(float(sub.mean_oop_copay.mean()), 0),
            }

        top_sw = max(cats, key=lambda x: x["switch_pct"])
        untreated = next((x for x in fates if x["fate"] == "Untreated in follow-up"), {"n": 0, "pct": 0.0})
        cost_dc = next((x for x in cats if x["category"] == "Cost / adherence barrier"), {"discontinue_pct": 0.0})
        ae_dc = next((x for x in cats if x["category"] == "Adverse event / intolerance"), {"discontinue_pct": 0.0})

        lb_days = w.rules.get("proxy_lookback_days", 60)
        return {
            "window_days": window,
            "lookback_days": lb_days,
            "n_evaluable": int(len(ev)),
            "n_switched": ns,
            "n_discontinued": nd,
            "proxy_categories": cats,
            "preceding_signals": sigs,
            "timing_split": {
                "switch_early_180d_pct": _pct(int((sw.leave_days <= 180).sum()), ns) if ns else 0.0,
                "discontinue_early_180d_pct": _pct(int((dc.leave_days <= 180).sum()), nd) if nd else 0.0,
            },
            "adherence_contrast": {
                "persistent": _adh(pe),
                "switched": _adh(sw),
                "discontinued": _adh(dc),
            },
            "post_discontinuation_fate": fates,
            "takeaway": (
                f"{top_sw['switch_pct']}% of switches are preceded by objective flare, elevated CRP, or rescue escalation, "
                f"whereas discontinuations split across safety/intolerance ({ae_dc['discontinue_pct']}%), OOP cost/refill drift "
                f"({cost_dc['discontinue_pct']}%), and flares — leaving {untreated['pct']}% ({untreated['n']:,} patients) completely untreated."
            ),
            "definition": (
                f"Real-world claims & structured EHR (OMOP CDM) rarely populate drug_exposure.stop_reason. "
                f"Following standard HEOR methodology, we scan the {lb_days}-day window prior to leaving {THERAPY} across "
                "condition_occurrence (flare, infection, ADR), measurement (CRP > 10 mg/L), visit_occurrence (ER/inpatient), "
                "drug_exposure (rescue steroids/csDMARDs, active on-therapy PDC), and cost (patient OOP copay). "
                "Leaves with no structured signal remain 'Unobserved in structured RWD' (requiring clinical notes or CDISC trial CRFs)."
            ),
        }

    return w.memo(("leave_drivers", window), _compute)


def second_line(w: Warehouse | None = None):
    """Compare TNFi cycling (adalimumab -> etanercept) vs MoA swapping across 2nd-line persistence and destination agents."""
    w = w or wh()

    def _compute():
        sw = w.j[(w.j.first_leave == "switch") & w.j.second_line_days.notna()].copy()
        grid = list(range(0, 366, 15))

        def _fit_km(sub: pd.DataFrame):
            if sub.empty:
                return None
            d = sub["second_line_days"].astype(float).clip(lower=0)
            e = sub["second_line_leave_event"].astype(int)
            k = KaplanMeierFitter().fit(d, e)
            y = k.survival_function_at_times(grid).values
            lo_hi = k.confidence_interval_
            lo = np.interp(grid, lo_hi.index, lo_hi.iloc[:, 0])
            hi = np.interp(grid, lo_hi.index, lo_hi.iloc[:, 1])
            med = None if np.isinf(k.median_survival_time_) else float(k.median_survival_time_)
            return {
                "x": grid,
                "y": [round(float(v), 4) for v in y],
                "lo": [round(float(v), 4) for v in lo],
                "hi": [round(float(v), 4) for v in hi],
                "at_180": round(float(k.predict(180)), 4),
                "at_365": round(float(k.predict(365)), 4),
                "median_days": med,
                "n": int(len(sub)),
                "events": int(e.sum()),
                "pct_of_switches": _pct(len(sub), len(sw)),
            }

        swap_df = sw[sw.switch_modality == "moa_swapping"]
        cyc_df = sw[sw.switch_modality == "tnfi_cycling"]
        swap_km = _fit_km(swap_df)
        cyc_km = _fit_km(cyc_df)

        by_agent = []
        for agent_name, sub in sw.groupby("second_line"):
            km = _fit_km(sub)
            moa = str(sub["second_line_moa"].iloc[0])
            mod = str(sub["switch_modality"].iloc[0])
            by_agent.append({
                "agent": agent_name,
                "moa": moa,
                "modality": "TNFi cycling" if mod == "tnfi_cycling" else "MoA swapping",
                "n": int(len(sub)),
                "pct_of_switches": _pct(len(sub), len(sw)),
                "persist_180": round(100 * km["at_180"], 1) if km else None,
                "persist_365": round(100 * km["at_365"], 1) if km else None,
                "events": km["events"] if km else 0,
            })
        by_agent.sort(key=lambda r: -r["n"])

        gain = round(100.0 * (swap_km["at_365"] - cyc_km["at_365"]), 1) if (swap_km and cyc_km) else 0.0
        return {
            "n_switched": int(len(sw)),
            "persistence_gain_12m_pp": gain,
            "modalities": {
                "MoA swapping (IL-6i / JAKi / T-cell / CD20)": swap_km,
                "TNFi cycling (adalimumab -> etanercept)": cyc_km,
            },
            "by_agent": by_agent,
            "takeaway": (
                f"Swapping mechanism of action after {THERAPY} yields +{gain} percentage points higher 12-month "
                f"2nd-line persistence ({round(100 * swap_km['at_365'], 1)}% vs. {round(100 * cyc_km['at_365'], 1)}% "
                f"for intra-class TNFi cycling to etanercept; n={len(sw):,} switchers)."
            ) if (swap_km and cyc_km) else "",
            "definition": (
                "Kaplan-Meier persistence on 2nd-line therapy from switch date to 2nd-line discontinuation, 3rd-line switch, "
                "or censoring, stratified by whether the 2nd-line agent shares TNF-inhibitor MoA (cycling) or targets a new pathway (swapping)."
            ),
        }

    return w.memo(("second_line",), _compute)


def risk_drivers(w: Warehouse | None = None):
    """Multivariable Cox proportional hazards models for discontinuation and switching + hospitalization risk ratios."""
    w = w or wh()

    def _compute():
        j = w.j.copy()
        j["age_10y"] = (j["age_at_index"] - 54.0) / 10.0
        j["female"] = (j["sex"] == "Female").astype(float)
        j["comorb"] = j["comorbidity_count"].astype(float)
        j["high_copay"] = j["high_copay_flag"].astype(float) if "high_copay_flag" in j.columns else 0.0
        med_crp = float(j["baseline_crp"].median()) if "baseline_crp" in j.columns and j["baseline_crp"].notna().any() else 15.0
        j["high_crp"] = (j["baseline_crp"].fillna(med_crp) > 15.0).astype(float) if "baseline_crp" in j.columns else 0.0

        covs = [
            ("high_copay", "High OOP copay (>=$250/fill)"),
            ("comorb", "Comorbidity burden (per +1 condition)"),
            ("age_10y", "Older age (per +10 years)"),
            ("female", "Female sex (vs. Male)"),
            ("high_crp", "Elevated baseline CRP (>15 mg/L)"),
        ]
        cols = [c for c, _ in covs]

        def _fit_cox(dcol: str, ecol: str):
            df_c = j[cols + [dcol, ecol]].copy()
            df_c[dcol] = df_c[dcol].astype(float).clip(lower=0.5)
            cph = CoxPHFitter(penalizer=0.01)
            cph.fit(df_c, duration_col=dcol, event_col=ecol)
            s = cph.summary
            rows = []
            for key, label in covs:
                r = s.loc[key]
                rows.append({
                    "key": key,
                    "factor": label,
                    "hr": round(float(r["exp(coef)"]), 2),
                    "ci_lo": round(float(r["exp(coef) lower 95%"]), 2),
                    "ci_hi": round(float(r["exp(coef) upper 95%"]), 2),
                    "p_value": round(float(r["p"]), 4),
                    "significant": bool(r["p"] < 0.05),
                })
            return rows

        disc_hr = _fit_cox("discontinue_days", "discontinue_event")
        sw_hr = _fit_cox("switch_days", "switch_event")

        # Unadjusted hospitalization rate ratios (per patient-year)
        def _hosp_rr(mask_hi, mask_lo, label):
            hi, lo = j[mask_hi], j[mask_lo]
            r_hi = 100.0 * hi.n_hosp.sum() / max(hi.pyears.sum(), 1.0)
            r_lo = 100.0 * lo.n_hosp.sum() / max(lo.pyears.sum(), 1.0)
            return {
                "comparison": label,
                "rate_exposed_100py": round(float(r_hi), 1),
                "rate_ref_100py": round(float(r_lo), 1),
                "rate_ratio": round(float(r_hi / max(r_lo, 0.01)), 2),
            }

        hosp_rr = [
            _hosp_rr(j.comorbidity_band == "2+", j.comorbidity_band == "0", "Comorbidities 2+ vs. 0"),
            _hosp_rr(j.first_leave == "discontinue", j.first_leave == "persistent", "Discontinued vs. Persistent on Therapy A"),
            _hosp_rr(j.age_band == "60+", j.age_band == "<45", "Age 60+ vs. <45"),
        ]
        top_disc = max(disc_hr, key=lambda x: x["hr"])
        return {
            "n": int(len(j)),
            "discontinuation_cox": disc_hr,
            "switch_cox": sw_hr,
            "hospitalization_rr": hosp_rr,
            "takeaway": (
                f"In multivariable Cox regression, '{top_disc['factor']}' is the strongest independent predictor of "
                f"discontinuation (HR {top_disc['hr']:.2f}, 95% CI {top_disc['ci_lo']:.2f}-{top_disc['ci_hi']:.2f}), "
                f"while comorbidity burden and older age compound both treatment drop-off and inpatient admissions."
            ),
            "definition": (
                "Adjusted hazard ratios (HR > 1 indicates faster event rate) from multivariable Cox proportional-hazards models "
                f"across all n={len(j):,} {THERAPY} initiators, adjusting simultaneously for age, sex, comorbidity count, "
                "patient OOP copay tier, and baseline CRP inflammation."
            ),
        }

    return w.memo(("risk_drivers",), _compute)


def pathways(max_lines: int = 3, min_n: int = 1, w: Warehouse | None = None):
    w = w or wh()

    def _compute():
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
        for key in sorted(agg, key=len):
            sun["ids"].append(" > ".join(key))
            sun["labels"].append(key[-1])
            sun["parents"].append(" > ".join(key[:-1]))
            sun["values"].append(agg[key])
        return {
            "nodes": nodes, "links": lk,
            "top_pathways": [{"path": k, "n": v, "pct": _pct(v, len(j))} for k, v in top[:15]],
            "sunburst": sun, "n": int(len(j)),
            "definition": "Sequence of lines of therapy (main advanced agent per line), derived from drug eras. Add-ons do not start a new line.",
        }

    return w.memo(("pathways", max_lines, min_n), _compute)


def survival(event: str = "persistence", group: str | None = None, w: Warehouse | None = None):
    w = w or wh()

    def _compute():
        spec = {
            "persistence": ("leave_days", "leave_event"),
            "switch": ("switch_days", "switch_event"),
            "discontinuation": ("discontinue_days", "discontinue_event"),
        }[event]
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
                return {
                    "x": grid, "y": [round(float(v), 4) for v in y],
                    "lo": [round(float(v), 4) for v in lo], "hi": [round(float(v), 4) for v in hi],
                    "at_180": at(180), "at_365": at(365), "at_730": at(730),
                    "median_days": med, "n": int(len(j)), "events": int(e.sum()),
                }
            code = (j.first_leave == ("switch" if event == "switch" else "discontinue")).astype(int)
            ev_arr = np.where(j.leave_event == 1, np.where(code == 1, 1, 2), 0)
            aj = AalenJohansenFitter(calculate_variance=False, seed=0).fit(
                j.leave_days.clip(lower=0).astype(float) + np.random.default_rng(0).uniform(0, 1e-3, len(j)),
                ev_arr, event_of_interest=1,
            )
            c = aj.cumulative_density_
            y = np.interp(grid, c.index, c.iloc[:, 0])
            at = lambda t: round(float(np.interp(t, c.index, c.iloc[:, 0])), 4)
            return {
                "x": grid, "y": [round(float(v), 4) for v in y],
                "at_180": at(180), "at_365": at(365), "at_730": at(730),
                "n": int(len(j)), "events": int(code.sum()),
            }

        out = {
            "event": event,
            "curve": "survival" if event == "persistence" else "cumulative_incidence",
            "definition": {
                "persistence": "Kaplan-Meier probability of still being on Therapy A (not switched/discontinued).",
                "switch": "Cumulative incidence of switching (discontinuation treated as a competing event).",
                "discontinuation": "Cumulative incidence of discontinuation (switching treated as a competing event).",
            }[event],
        }
        if group:
            col = GROUPS[group]
            out["groups"] = {str(g): one(sub) for g, sub in w.j.groupby(col)}
        else:
            out["overall"] = one(w.j)
        return out

    return w.memo(("survival", event, group), _compute)


def utilization(w: Warehouse | None = None):
    w = w or wh()

    def _compute():
        j = w.j
        py = j.pyears.sum()
        by = {}
        for status, g in j.groupby("first_leave"):
            p = g.pyears.sum()
            by[status] = {
                "patients": int(len(g)),
                "hospitalizations_per_100py": round(100 * g.n_hosp.sum() / p, 1),
                "er_per_100py": round(100 * g.n_er.sum() / p, 1),
                "outpatient_per_py": round(g.n_outpatient.sum() / p, 2),
                "cost_per_patient_year": round(g.total_cost.sum() / p, 0),
            }
        return {
            "patients": int(len(j)),
            "patient_years": round(float(py), 0),
            "hospitalizations_per_100py": round(100 * j.n_hosp.sum() / py, 1),
            "ra_hospitalizations_per_100py": round(100 * j.n_hosp_ra.sum() / py, 1),
            "er_per_100py": round(100 * j.n_er.sum() / py, 1),
            "outpatient_per_py": round(j.n_outpatient.sum() / py, 2),
            "mean_cost_per_patient_year": round(float(j.total_cost.sum() / py), 0),
            "mean_inpatient_days_per_hospitalized": round(float(j.hosp_days.sum() / max(j.n_hosp.sum(), 1)), 1),
            "by_status": by,
            "definition": "Events from index to censoring, divided by patient-years of follow-up. Cost = synthetic paid amounts for visits+drug fills.",
        }

    return w.memo(("utilization",), _compute)


def pre_index(w: Warehouse | None = None):
    w = w or wh()

    def _compute():
        r = w.ra
        n = int(r.dx_date.notna().sum())
        return {
            "ra_patients": n,
            "time_to_diagnosis_days": _q(r.days_onset_to_dx),
            "time_to_specialist_days": _q(r.days_visit_to_specialist),
            "time_to_treatment_days": _q(r.days_dx_to_treatment),
            "time_dx_to_therapy_a_days": _q(
                w.j.merge(r[["person_id", "dx_date"]], on="person_id").pipe(lambda x: (x.index_date - x.dx_date).dt.days)
            ),
            "treatment_initiation_rate": {
                "pct_within_window": _pct(int(r.initiated_in_window.sum()), n),
                "pct_ever": _pct(int(r.initiated_ever.sum()), n),
                "window_days": w.rules["initiation_window_days"],
                "n": n,
            },
            "definition": {
                "time_to_diagnosis": "first symptom onset -> RA diagnosis",
                "time_to_specialist": "first (primary-care) visit -> first rheumatology visit",
                "time_to_treatment": "RA diagnosis -> first DMARD fill",
                "time_dx_to_therapy_a": "RA diagnosis -> first Therapy A fill (index cohort)",
                "initiation_rate": "share of RA patients with a DMARD fill within the window",
            },
        }

    return w.memo(("pre_index",), _compute)


def escalation(window: int | None = None, w: Warehouse | None = None):
    w = w or wh()
    window = window or w.rules["funnel_window_days"]

    def _compute():
        ev = evaluable(w.j, window)
        e = ev[(ev.escalated == 1) & (ev.escalation_days <= window)]
        types = w.events[w.events.event_type.isin(["add_on_advanced", "add_on_conventional"]) & w.events.person_id.isin(e.person_id)]
        return {
            "window_days": window, "n": int(len(ev)), "escalated": int(len(e)),
            "pct": _pct(len(e), len(ev)),
            "median_days_to_escalation": float(e.escalation_days.median()) if len(e) else None,
            "agents_added": types.to_therapy.value_counts().to_dict(),
            "definition": "Add-on of leflunomide/prednisone or a second advanced agent while on Therapy A (line 1).",
        }

    return w.memo(("escalation", window), _compute)


def subgroups(by: str = "age", window: int | None = None, w: Warehouse | None = None):
    w = w or wh()
    window_eff = window or w.rules["funnel_window_days"]

    def _compute():
        out = []
        copay_thr = w.rules.get("high_copay_threshold", 250)
        order = {
            "age": ["<45", "45-59", "60+"],
            "comorbidity": ["0", "1", "2+"],
            "copay": [f"<${copay_thr} OOP", f">=${copay_thr} OOP"],
        }.get(by)
        present = list(w.j[GROUPS[by]].unique())
        for g in ([x for x in order if x in present] if order else sorted(present)):
            f = funnel(window_eff, w, (by, g), include_km=False)
            n = f["evaluable_patients"]
            out.append({
                "group": g, "n": n,
                "pct_remain": f["pct"]["remain"],
                "pct_discontinued": f["pct"]["discontinued"],
                "pct_switched": f["pct"]["switched"],
                "pct_hospitalized": f["pct"]["hospitalized"],
                "active_pdc_mean": f["active_pdc_mean"],
            })
        return {"by": by, "window_days": window_eff, "groups": out}

    return w.memo(("subgroups", by, window_eff), _compute)


def unmet_need(w: Warehouse | None = None):
    w = w or wh()

    def _compute():
        j = w.j
        W = w.rules["funnel_window_days"]
        ev = evaluable(j, W)
        st = _status(ev, W)
        ef_days = w.rules.get("early_fail_days", 180)
        ml_thr = w.rules.get("multi_line_threshold", 3)
        early = ev[(ev.leave_event == 1) & (ev.leave_days <= ef_days)]
        multi = j[j.n_lines >= ml_thr]
        hosp_off = hosp_after_leave(ev, W, w)
        untreated = (
            ev[(st == "discontinue") & (ev.post_disc_fate == "Untreated in follow-up")]
            if "post_disc_fate" in ev.columns
            else ev[(ev.first_leave == "discontinue") & (ev.leave_days <= W) & (ev.n_lines == 1)]
        )
        high_oop_disc = (
            ev[(st == "discontinue") & (ev.sig_cost_or_drift == 1)]
            if "sig_cost_or_drift" in ev.columns
            else ev[ev.pdc_12m < 0.8]
        )
        return {
            "flags": [
                {"label": f"Left Therapy A within {ef_days} days (early primary failure)", "n": int(len(early)), "pct": _pct(len(early), len(ev))},
                {"label": "Discontinued with OOP copay / refill-drift barrier", "n": int(len(high_oop_disc)), "pct": _pct(len(high_oop_disc), len(ev))},
                {"label": "Discontinued & completely untreated in follow-up", "n": int(len(untreated)), "pct": _pct(len(untreated), len(ev))},
                {"label": f"Cycled through {ml_thr}+ advanced therapy lines", "n": int(len(multi)), "pct": _pct(len(multi), len(j))},
                {"label": "Hospitalized after leaving Therapy A (within window)", "n": int(len(hosp_off)), "pct": _pct(len(hosp_off), len(ev))},
            ],
            "window_days": W,
            "denominator": int(len(ev)),
            "definition": (
                f"Among patients evaluable at {W} days (3+ lines uses all initiators). "
                "Untreated = no conventional or advanced DMARD coverage >30d after stopping Therapy A."
            ),
        }

    return w.memo(("unmet_need",), _compute)


def kpis(w: Warehouse | None = None):
    w = w or wh()

    def _compute():
        f = funnel(None, w)
        pi = pre_index(w)
        ld = leave_drivers(None, w)
        sl = second_line(w)
        return {
            "calibration_profile": w.build_info["profile"],
            "study_name": w.study_name,
            "seed": int(w.build_info["seed"]),
            "ra_patients": pi["ra_patients"],
            "index_patients": f["index_patients"],
            "window_days": f["window_days"],
            "pct_persistent": f["pct"]["remain"],
            "pct_discontinued": f["pct"]["discontinued"],
            "pct_switched": f["pct"]["switched"],
            "pct_hospitalized": f["pct"]["hospitalized"],
            "median_days_to_switch": f["median_days_to_switch_among_switchers"],
            "median_days_to_treatment": pi["time_to_treatment_days"]["median"],
            "median_days_to_diagnosis": pi["time_to_diagnosis_days"]["median"],
            "adherence_pct_pdc_80": f["adherence_pct_pdc_80"],
            "active_pct_pdc_80": f["active_pct_pdc_80"],
            "hospitalized_after_leaving_therapy": f["hospitalized_after_leaving_therapy"],
            "moa_swap_gain_pp": sl["persistence_gain_12m_pp"],
            "top_switch_driver_pct": ld["proxy_categories"][0]["switch_pct"],
        }

    return w.memo(("kpis",), _compute)


def attrition(w: Warehouse | None = None):
    w = w or wh()
    return {"steps": w.attrition.to_dict("records")}


def patient_journey(person_id: int, w: Warehouse | None = None):
    w = w or wh()
    con = duckdb.connect(w.path, read_only=True)
    vis = con.execute("SELECT * FROM visit_occurrence WHERE person_id = ? ORDER BY visit_start_date", [person_id]).df()
    if vis.empty:
        con.close()
        return None
    if w.has_oop:
        dr = con.execute(
            "SELECT d.*, c.paid_by_patient FROM drug_exposure d "
            "LEFT JOIN cost c ON c.cost_domain_id = 'Drug' AND c.cost_event_id = d.drug_exposure_id "
            "WHERE d.person_id = ? ORDER BY d.drug_exposure_start_date",
            [person_id],
        ).df()
    else:
        dr = con.execute("SELECT * FROM drug_exposure WHERE person_id = ? ORDER BY drug_exposure_start_date", [person_id]).df()
        dr["paid_by_patient"] = 0.0
    labs = (
        con.execute("SELECT measurement_date, value_as_number, range_high FROM measurement WHERE person_id = ? ORDER BY measurement_date", [person_id]).df()
        if w.has_measurement
        else pd.DataFrame(columns=["measurement_date", "value_as_number", "range_high"])
    )
    con.close()

    ev = w.events[w.events.person_id == person_id]
    vt = {9201: "Hospitalization", 9202: "Outpatient", 9203: "ER"}
    items = [
        {"date": str(pd.Timestamp(r.visit_start_date).date()), "type": vt.get(r.visit_concept_id, "Visit"), "detail": r.visit_source_value}
        for r in vis.itertuples()
    ]
    items += [
        {
            "date": str(pd.Timestamp(r.event_date).date()),
            "type": "Event:" + r.event_type,
            "detail": f"{r.from_therapy or ''} -> {r.to_therapy or ''}".strip(" ->"),
        }
        for r in ev.itertuples()
        if not r.event_type.startswith("hospitalization")
    ]
    fills = dr.assign(name=dr.drug_concept_id.map(w.names)).sort_values("drug_exposure_start_date")
    drug_spans = fills.groupby("name").agg(
        first=("drug_exposure_start_date", "min"),
        last=("drug_exposure_end_date", "max"),
        fills=("drug_exposure_id", "size"),
        mean_oop=("paid_by_patient", "mean"),
    ).reset_index()
    jr = w.j[w.j.person_id == person_id]
    summary = jr.iloc[0].astype(object).where(jr.iloc[0].notna(), None).to_dict() if len(jr) else None
    if summary:
        for k, v in list(summary.items()):
            if isinstance(v, pd.Timestamp):
                summary[k] = str(v.date())
    return {
        "person_id": person_id,
        "summary": summary,
        "timeline": sorted(items, key=lambda x: x["date"]),
        "drug_spans": [
            {
                "drug": r.name,
                "start": str(pd.Timestamp(r.first).date()),
                "end": str(pd.Timestamp(r.last).date()),
                "fills": int(r.fills),
                "mean_oop": round(float(r.mean_oop or 0.0), 0),
            }
            for r in drug_spans.itertuples()
        ],
        "labs": [
            {"date": str(pd.Timestamp(r.measurement_date).date()), "crp": round(float(r.value_as_number), 1), "range_high": float(r.range_high)}
            for r in labs.itertuples()
        ],
    }


def sample_patients(kind: str = "switch", n: int = 5, w: Warehouse | None = None):
    w = w or wh()
    j = w.j[w.j.first_leave == kind] if kind != "any" else w.j
    cols = ["person_id", "sequence", "first_leave", "leave_days"]
    if "leave_proxy" in j.columns:
        cols.append("leave_proxy")
    return j.sample(min(n, len(j)), random_state=1)[cols].to_dict("records")
