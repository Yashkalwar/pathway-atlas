"""Build the DuckDB warehouse: simulate OMOP tables, derive analytics & HEOR proxy tables.

Usage: python -m app.pipeline.build [--seed N] [--n N]
"""
import argparse
import json
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .. import config
from .pathway import COMORB_RANGE, build_eras, derive_lines
from .simulate import simulate


def concept_table(cn: dict) -> pd.DataFrame:
    rows = [(v["id"], k, "Drug", "Ingredient", v["class"]) for k, v in cn["drugs"].items()]
    cond = cn["conditions"]
    rows += [
        (cond["rheumatoid_arthritis"], "Rheumatoid arthritis", "Condition", "Clinical Finding", "SNOMED"),
        (cond["joint_pain_symptom"], "Joint pain (symptom onset)", "Condition", "Clinical Finding", "local"),
        (cond["other_inpatient_cause"], "Other inpatient cause", "Condition", "Clinical Finding", "local"),
    ]
    if "acute_ra_flare" in cond:
        rows.append((cond["acute_ra_flare"], "Acute rheumatoid arthritis flare", "Condition", "Clinical Finding", "local"))
    if "serious_infection" in cond:
        rows.append((cond["serious_infection"], "Serious / opportunistic infection", "Condition", "Clinical Finding", "SNOMED"))
    if "adverse_drug_reaction" in cond:
        rows.append((cond["adverse_drug_reaction"], "Adverse drug reaction / intolerance", "Condition", "Clinical Finding", "SNOMED"))
    if "measurements" in cn and "crp" in cn["measurements"]:
        crp = cn["measurements"]["crp"]
        rows += [
            (crp["id"], crp["name"], "Measurement", "Lab Test", "LOINC"),
            (crp["unit_id"], crp["unit"], "Unit", "Unit", "UCUM"),
            (32856, "Lab", "Type Concept", "Type Concept", "standard"),
        ]
    rows += [(2000001100 + k, f"Comorbidity {k + 1}", "Condition", "Clinical Finding", "local") for k in range(6)]
    rows += [
        (9201, "Inpatient Visit", "Visit", "Visit", "standard"),
        (9202, "Outpatient Visit", "Visit", "Visit", "standard"),
        (9203, "Emergency Room Visit", "Visit", "Visit", "standard"),
        (8507, "MALE", "Gender", "Gender", "standard"),
        (8532, "FEMALE", "Gender", "Gender", "standard"),
        (32810, "Claim", "Type Concept", "Type Concept", "standard"),
        (32869, "Pharmacy claim", "Type Concept", "Type Concept", "standard"),
    ]
    return pd.DataFrame(rows, columns=["concept_id", "concept_name", "domain_id", "concept_class_id", "vocabulary_id"])


def pdc(dexp: pd.DataFrame, j: pd.DataFrame, drug_id: int, horizon: int) -> pd.Series:
    """Proportion of days covered by Therapy A fills over min(horizon, follow-up) days from index."""
    d = dexp[dexp.drug_concept_id == drug_id][["person_id", "drug_exposure_start_date", "drug_exposure_end_date"]].merge(
        j[["person_id", "index_date", "followup_days"]], on="person_id"
    )
    d["win"] = d.followup_days.clip(upper=horizon)
    s = (pd.to_datetime(d.drug_exposure_start_date) - d.index_date).dt.days.clip(lower=0)
    e = (pd.to_datetime(d.drug_exposure_end_date) - d.index_date).dt.days + 1
    d["covered"] = (np.minimum(e, d.win) - s).clip(lower=0)
    cov = d.groupby("person_id").agg(covered=("covered", "sum"), win=("win", "first"))
    return (cov.covered.clip(upper=cov.win) / cov.win.clip(lower=1)).rename("pdc_12m")


def active_pdc(dexp: pd.DataFrame, j: pd.DataFrame, drug_id: int) -> pd.Series:
    """Proportion of days covered by Therapy A WHILE actively on Line 1 [index_date, line1_era_end].
    Separates refill regularity while on therapy from post-discontinuation drop-off."""
    d = dexp[dexp.drug_concept_id == drug_id][["person_id", "drug_exposure_start_date", "drug_exposure_end_date"]].merge(
        j[["person_id", "index_date", "line1_era_end"]], on="person_id"
    )
    span = ((d.line1_era_end - d.index_date).dt.days + 1).clip(lower=1)
    s = (pd.to_datetime(d.drug_exposure_start_date) - d.index_date).dt.days.clip(lower=0)
    e = (pd.to_datetime(d.drug_exposure_end_date) - d.index_date).dt.days + 1
    d["covered"] = (np.minimum(e, span) - s).clip(lower=0)
    d["span"] = span
    cov = d.groupby("person_id").agg(covered=("covered", "sum"), span=("span", "first"))
    return (cov.covered.clip(upper=cov.span) / cov.span).rename("active_pdc")


def _derive_proxies_and_fate(j: pd.DataFrame, raw: dict, rules: dict, cn: dict) -> pd.DataFrame:
    """Derive 60-day pre-leave clinical/claims proxy signals and post-discontinuation fate from raw OMOP events."""
    drugs, A = cn["drugs"], cn["therapy_a"]
    a_id = drugs[A]["id"]
    cond_cfg = cn["conditions"]
    V = cn["visits"]
    lb = pd.Timedelta(days=rules.get("proxy_lookback_days", 60))
    grace = pd.Timedelta(days=7)
    copay_thr = rules.get("high_copay_threshold", 250)
    crp_hi = rules.get("elevated_crp_mg_l", 10.0)
    pdc_thr = rules.get("pdc_threshold", 0.80)

    dexp, vis, cond, cost = raw["drug_exposure"], raw["visit_occurrence"], raw["condition_occurrence"], raw["cost"]
    meas = raw.get("measurement")

    # 1. Patient OOP copay on Therapy A fills
    if "paid_by_patient" in cost.columns:
        a_fills = dexp[dexp.drug_concept_id == a_id][["drug_exposure_id", "person_id", "drug_exposure_start_date"]].merge(
            j[["person_id", "index_date", "censor_date"]], on="person_id"
        )
        s_dt = pd.to_datetime(a_fills.drug_exposure_start_date)
        a_fills = a_fills[(s_dt >= a_fills.index_date) & (s_dt <= a_fills.censor_date)]
        oop = a_fills.merge(
            cost[cost.cost_domain_id == "Drug"][["cost_event_id", "paid_by_patient"]],
            left_on="drug_exposure_id", right_on="cost_event_id",
        ).groupby("person_id")["paid_by_patient"].mean().rename("mean_oop_copay")
        j = j.merge(oop.reset_index(), on="person_id", how="left")
    else:
        j["mean_oop_copay"] = 0.0
    j["mean_oop_copay"] = j["mean_oop_copay"].fillna(0.0).round(1)
    j["high_copay_flag"] = (j["mean_oop_copay"] >= copay_thr).astype(int)
    j["copay_band"] = np.where(j["high_copay_flag"] == 1, f">=${copay_thr} OOP", f"<${copay_thr} OOP")

    # 2. Baseline CRP & pre-leave CRP
    crp_id = cn.get("measurements", {}).get("crp", {}).get("id")
    if meas is not None and not meas.empty and crp_id is not None:
        crp = meas[meas.measurement_concept_id == crp_id][["person_id", "measurement_date", "value_as_number"]].copy()
        crp["md"] = pd.to_datetime(crp["measurement_date"])
        base = crp.merge(j[["person_id", "index_date"]], on="person_id")
        base = base[(base.md <= base.index_date) & (base.md >= base.index_date - pd.Timedelta(days=240))]
        base_last = base.sort_values("md").groupby("person_id")["value_as_number"].last().rename("baseline_crp")
        j = j.merge(base_last.reset_index(), on="person_id", how="left")
    else:
        crp = None
        j["baseline_crp"] = np.nan

    leavers = j[j.leave_event == 1][["person_id", "index_date", "first_leave_date"]].copy()
    leavers["win_start"] = np.maximum(leavers["index_date"], leavers["first_leave_date"] - lb)
    leavers["win_end"] = leavers["first_leave_date"] + grace

    if crp is not None and not leavers.empty:
        cl = crp.merge(leavers, on="person_id")
        cl = cl[(cl.md >= cl.win_start) & (cl.md <= cl.win_end)].sort_values("md")
        crp_agg = cl.groupby("person_id").agg(max_pre_crp=("value_as_number", "max"), last_pre_crp=("value_as_number", "last"))
        j = j.merge(crp_agg.reset_index(), on="person_id", how="left")
    else:
        j["max_pre_crp"] = np.nan
        j["last_pre_crp"] = np.nan

    # 3. Pre-leave acute conditions (flare, infection, ADR) and ER/inpatient encounters
    flare_id = cond_cfg.get("acute_ra_flare")
    ae_ids = [x for x in (cond_cfg.get("serious_infection"), cond_cfg.get("adverse_drug_reaction")) if x is not None]
    if not leavers.empty:
        cd = cond[["person_id", "condition_concept_id", "condition_start_date"]].copy()
        cd["cd_dt"] = pd.to_datetime(cd["condition_start_date"])
        cd = cd.merge(leavers, on="person_id")
        cd = cd[(cd.cd_dt >= cd.win_start) & (cd.cd_dt <= cd.win_end)]
        flare_p = set(cd[cd.condition_concept_id == flare_id].person_id) if flare_id else set()
        ae_p = set(cd[cd.condition_concept_id.isin(ae_ids)].person_id) if ae_ids else set()

        vd = vis[vis.visit_concept_id.isin([V["emergency"], V["inpatient"]])][["person_id", "visit_start_date"]].copy()
        vd["vd_dt"] = pd.to_datetime(vd["visit_start_date"])
        vd = vd.merge(leavers, on="person_id")
        vd = vd[(vd.vd_dt >= vd.win_start) & (vd.vd_dt <= vd.first_leave_date)]
        er_hosp_p = set(vd.person_id)
    else:
        flare_p, ae_p, er_hosp_p = set(), set(), set()

    # 4. Conventional DMARD continuation >30d after leaving Therapy A
    conv_ids = [v["id"] for k, v in drugs.items() if v["class"] == "csDMARD" or k == "leflunomide"]
    disc_l = j[j.first_leave == "discontinue"][["person_id", "first_leave_date", "censor_date"]]
    if not disc_l.empty:
        cv = dexp[dexp.drug_concept_id.isin(conv_ids)][["person_id", "drug_exposure_end_date"]].merge(disc_l, on="person_id")
        ce_dt = pd.to_datetime(cv.drug_exposure_end_date)
        csdmard_p = set(cv[(ce_dt > cv.first_leave_date + pd.Timedelta(days=30)) & (ce_dt <= cv.censor_date + pd.Timedelta(days=30))].person_id)
    else:
        csdmard_p = set()

    left_mask = j.leave_event == 1
    sig_flare_cond = left_mask & j.person_id.isin(flare_p)
    sig_elev_crp = left_mask & (j.max_pre_crp.fillna(0) > crp_hi)
    sig_esc_before = left_mask & (j.escalated == 1) & (j.escalation_days.fillna(99999) <= j.leave_days)
    sig_ae_cond = left_mask & j.person_id.isin(ae_p)
    sig_er_hosp = left_mask & j.person_id.isin(er_hosp_p)

    j["sig_flare_or_escalation"] = (sig_flare_cond | sig_elev_crp | sig_esc_before).astype(int)
    j["sig_elevated_crp"] = sig_elev_crp.astype(int)
    j["sig_infection_or_adr"] = sig_ae_cond.astype(int)
    j["sig_acute_er_hosp"] = sig_er_hosp.astype(int)
    j["sig_safety_event"] = (sig_ae_cond | sig_er_hosp).astype(int)
    j["sig_cost_or_drift"] = (left_mask & ((j.high_copay_flag == 1) | (j.active_pdc < pdc_thr))).astype(int)
    csdmard_cont = j.person_id.isin(csdmard_p)
    j["sig_remission_stepdown"] = (
        (j.first_leave == "discontinue") & (j.leave_days > 365) & (j.last_pre_crp.fillna(99.0) <= 5.0)
        & (j.sig_flare_or_escalation == 0) & (j.sig_safety_event == 0) & csdmard_cont
    ).astype(int)

    # Hierarchical primary proxy phenotype among leavers
    proxy = np.full(len(j), None, dtype=object)
    rem = left_mask.to_numpy().copy()
    for label, cond_arr in (
        ("Adverse event / intolerance", sig_ae_cond.to_numpy()),
        ("Inadequate response / flare", (j["sig_flare_or_escalation"] == 1).to_numpy()),
        ("Adverse event / intolerance", sig_er_hosp.to_numpy()),
        ("Sustained low activity / step-down", (j["sig_remission_stepdown"] == 1).to_numpy()),
        ("Cost / adherence barrier", (j["sig_cost_or_drift"] == 1).to_numpy()),
    ):
        hit = rem & cond_arr
        proxy[hit] = label
        rem &= ~hit
    proxy[rem] = "Unobserved in structured RWD"
    j["leave_proxy"] = proxy

    # Post-discontinuation trajectory among discontinuers
    fate = np.full(len(j), None, dtype=object)
    d_rem = (j.first_leave == "discontinue").to_numpy().copy()
    for label, cond_arr in (
        ("Delayed switch (>90d gap)", (j.n_lines >= 2).to_numpy()),
        ("Restarted Therapy A, later stopped", (j.had_restart == 1).to_numpy()),
        ("Maintained on csDMARD only", csdmard_cont.to_numpy()),
    ):
        hit = d_rem & cond_arr
        fate[hit] = label
        d_rem &= ~hit
    fate[d_rem] = "Untreated in follow-up"
    j["post_disc_fate"] = fate
    return j


def build_analytics(raw: dict, rules: dict, cn: dict):
    drugs, A = cn["drugs"], cn["therapy_a"]
    ra_id = cn["conditions"]["rheumatoid_arthritis"]
    sym_id = cn["conditions"]["joint_pain_symptom"]
    V = cn["visits"]
    dexp, vis, cond, cost, person = (raw[k] for k in ("drug_exposure", "visit_occurrence", "condition_occurrence", "cost", "person"))
    obs = raw["observation_period"]
    eras = build_eras(dexp, rules["era_gap_days"], rules.get("min_era_days", 0))
    journey, events, attr = derive_lines(eras, obs, drugs, A, rules)

    # ---- RA patient pre-index metrics (all RA patients)
    ra = cond[cond.condition_concept_id == ra_id].groupby("person_id")["condition_start_date"].min().rename("dx_date")
    onset = cond[cond.condition_concept_id == sym_id].groupby("person_id")["condition_start_date"].min().rename("symptom_onset")
    vv = vis.assign(d=pd.to_datetime(vis.visit_start_date))
    first_visit = vv[vv.visit_source_value == "primary_care_symptom"].groupby("person_id")["d"].min().rename("first_visit")
    rheum = vv[vv.visit_source_value == "rheumatology_first"].groupby("person_id")["d"].min().rename("rheum_visit")
    dmard_ids = [v["id"] for k, v in drugs.items() if k != "prednisone"]
    de = dexp[dexp.drug_concept_id.isin(dmard_ids)].assign(s=lambda x: pd.to_datetime(x.drug_exposure_start_date))
    first_dmard = de.groupby("person_id")["s"].min().rename("first_dmard")
    rap = pd.concat([ra, onset, first_visit, rheum, first_dmard], axis=1).reset_index().rename(columns={"index": "person_id"})
    for c in ("dx_date", "symptom_onset"):
        rap[c] = pd.to_datetime(rap[c])
    rap["days_onset_to_dx"] = (rap.dx_date - rap.symptom_onset).dt.days
    rap["days_visit_to_specialist"] = (rap.rheum_visit - rap.first_visit).dt.days
    rap["days_dx_to_treatment"] = (rap.first_dmard - rap.dx_date).dt.days
    rap["initiated_in_window"] = (rap.days_dx_to_treatment.between(0, rules["initiation_window_days"])).astype(int)
    rap["initiated_ever"] = rap.first_dmard.notna().astype(int)

    # ---- covariates + outcomes for index cohort
    cm = cond[cond.condition_concept_id.between(*COMORB_RANGE)].groupby("person_id")["condition_concept_id"].nunique().rename("comorbidity_count")
    j = journey.merge(person, on="person_id").merge(cm, on="person_id", how="left")
    j["comorbidity_count"] = j["comorbidity_count"].fillna(0).astype(int)
    j["age_at_index"] = j.index_date.dt.year - j.year_of_birth
    j["age_band"] = pd.cut(j.age_at_index, [0, 44, 59, 200], labels=["<45", "45-59", "60+"]).astype(str)
    j["sex"] = np.where(j.gender_concept_id == 8532, "Female", "Male")
    j["comorbidity_band"] = pd.cut(j.comorbidity_count, [-1, 0, 1, 99], labels=["0", "1", "2+"]).astype(str)

    w = j[["person_id", "index_date", "censor_date"]]
    v = vv.merge(w, on="person_id")
    v = v[(v.d >= v.index_date) & (v.d <= v.censor_date)]
    ra_vis = set(cond[(cond.condition_concept_id == ra_id) & cond.visit_occurrence_id.notna()].visit_occurrence_id.astype(int))
    inp = v[v.visit_concept_id == V["inpatient"]].copy()
    inp["ra_related"] = inp.visit_occurrence_id.isin(ra_vis)
    inp["los"] = (pd.to_datetime(inp.visit_end_date) - inp.d).dt.days
    hosp = inp.groupby("person_id").agg(
        n_hosp=("visit_occurrence_id", "size"), first_hosp=("d", "min"),
        n_hosp_ra=("ra_related", "sum"), hosp_days=("los", "sum"),
    )
    for name, code in (("n_outpatient", V["outpatient"]), ("n_er", V["emergency"])):
        j = j.merge(v[v.visit_concept_id == code].groupby("person_id").size().rename(name).reset_index(), on="person_id", how="left")
    j = j.merge(hosp.reset_index(), on="person_id", how="left")
    for c in ("n_outpatient", "n_er", "n_hosp", "n_hosp_ra", "hosp_days"):
        j[c] = j[c].fillna(0).astype(int)
    j["hospitalized"] = (j.n_hosp > 0).astype(int)
    j["hosp_days_from_index"] = (j.first_hosp - j.index_date).dt.days
    j["pyears"] = j.followup_days.clip(lower=1) / 365.25

    vc = cost[cost.cost_domain_id == "Visit"].merge(v[["visit_occurrence_id"]], left_on="cost_event_id", right_on="visit_occurrence_id")
    ds = dexp.assign(s=pd.to_datetime(dexp.drug_exposure_start_date)).merge(w, on="person_id")
    ds = ds[(ds.s >= ds.index_date) & (ds.s <= ds.censor_date)]
    dc = cost[cost.cost_domain_id == "Drug"].merge(ds[["drug_exposure_id"]], left_on="cost_event_id", right_on="drug_exposure_id")
    tot = pd.concat([vc[["person_id", "total_paid"]], dc[["person_id", "total_paid"]]]).groupby("person_id")["total_paid"].sum().rename("total_cost")
    j = j.merge(tot.reset_index(), on="person_id", how="left")
    j["total_cost"] = j.total_cost.fillna(0.0)
    j = j.merge(pdc(dexp, j, drugs[A]["id"], 365).reset_index(), on="person_id", how="left")
    j = j.merge(active_pdc(dexp, j, drugs[A]["id"]).reset_index(), on="person_id", how="left")
    j = _derive_proxies_and_fate(j, raw, rules, cn)

    hosp_events = inp[["person_id", "visit_start_date", "ra_related"]].rename(columns={"visit_start_date": "event_date"})
    hosp_events = hosp_events.assign(
        event_type=np.where(hosp_events.ra_related, "hospitalization_ra", "hospitalization_other"),
        from_therapy=None, to_therapy=None, line_no=None,
    )[events.columns]
    events = pd.concat([events, hosp_events], ignore_index=True).sort_values(["person_id", "event_date"], kind="mergesort")
    attr["n_ra_patients"] = int(rap.dx_date.notna().sum())
    drop_cols = ["gender_concept_id", "year_of_birth", "race_concept_id", "ethnicity_concept_id", "person_source_value"]
    return dict(
        drug_era=eras, ra_patients=rap,
        index_journey=j.drop(columns=drop_cols),
        patient_events=events,
    ), attr


def attrition_steps(attr: dict) -> list[tuple[str, int]]:
    return [
        ("RA patients in warehouse", attr["n_ra_patients"]),
        ("Received any advanced therapy", attr["n_with_advanced_therapy"]),
        ("Excluded: first advanced therapy was not Therapy A", attr["n_first_advanced_not_therapy_a"]),
        ("Excluded: < washout observation before index", attr["n_failed_washout"]),
        ("Index cohort (Therapy A initiators)", attr["n_index"]),
    ]


def write_analytics(con: duckdb.DuckDBPyConnection, analytics: dict, attr: dict):
    """Replace all analytics.* tables in-place (raw OMOP tables untouched)."""
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    for name, df in analytics.items():
        con.register("_t", df)
        con.execute(f"CREATE OR REPLACE TABLE analytics.{name} AS SELECT * FROM _t")
        con.unregister("_t")
    con.execute("CREATE OR REPLACE TABLE analytics.attrition AS SELECT * FROM (VALUES ('x', 0)) t(step, n)")
    con.execute("DELETE FROM analytics.attrition")
    con.executemany("INSERT INTO analytics.attrition VALUES (?, ?)", attrition_steps(attr))


def main(argv=None):
    t0 = time.perf_counter()
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--db", default=str(config.DB_PATH))
    a = ap.parse_args(argv)
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)
    print("simulating OMOP tables...")
    raw = simulate(a.seed, a.n)
    raw["concept"] = concept_table(config.concepts())
    print(f"deriving eras/lines/metrics tables... ({time.perf_counter() - t0:.2f}s)")
    analytics, attr = build_analytics(raw, config.rules(), config.concepts())
    con = duckdb.connect(a.db)
    for name, df in raw.items():
        con.register("_t", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
        con.unregister("_t")
    write_analytics(con, analytics, attr)
    cal = config.calibration()
    con.execute(
        "CREATE OR REPLACE TABLE analytics.build_info AS SELECT ? AS profile, ? AS seed, ? AS n_patients, now() AS built_at",
        [cal["profile"], a.seed if a.seed is not None else cal["seed"], a.n or cal["n_patients"]],
    )
    con.close()
    attr["build_seconds"] = round(time.perf_counter() - t0, 2)
    print(json.dumps(attr, indent=2, default=int))


if __name__ == "__main__":
    main()
