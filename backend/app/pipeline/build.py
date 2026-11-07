"""Build the DuckDB warehouse: simulate OMOP tables, derive analytics tables.

Usage: python -m app.pipeline.build [--seed N] [--n N]
"""
import argparse
import json

import duckdb
import numpy as np
import pandas as pd

from .. import config
from .pathway import COMORB_RANGE, build_eras, derive_lines
from .simulate import simulate


def concept_table(cn: dict) -> pd.DataFrame:
    rows = [(v["id"], k, "Drug", "Ingredient", v["class"]) for k, v in cn["drugs"].items()]
    rows += [(cn["conditions"]["rheumatoid_arthritis"], "Rheumatoid arthritis", "Condition", "Clinical Finding", "SNOMED"),
             (cn["conditions"]["joint_pain_symptom"], "Joint pain (symptom onset)", "Condition", "Clinical Finding", "local"),
             (cn["conditions"]["other_inpatient_cause"], "Other inpatient cause", "Condition", "Clinical Finding", "local")]
    rows += [(2000001100 + k, f"Comorbidity {k + 1}", "Condition", "Clinical Finding", "local") for k in range(6)]
    rows += [(9201, "Inpatient Visit", "Visit", "Visit", "standard"), (9202, "Outpatient Visit", "Visit", "Visit", "standard"),
             (9203, "Emergency Room Visit", "Visit", "Visit", "standard"), (8507, "MALE", "Gender", "Gender", "standard"),
             (8532, "FEMALE", "Gender", "Gender", "standard"), (32810, "Claim", "Type Concept", "Type Concept", "standard"),
             (32869, "Pharmacy claim", "Type Concept", "Type Concept", "standard")]
    return pd.DataFrame(rows, columns=["concept_id", "concept_name", "domain_id", "concept_class_id", "vocabulary_id"])


def pdc(dexp: pd.DataFrame, j: pd.DataFrame, drug_id: int, horizon: int) -> pd.Series:
    """Proportion of days covered by Therapy A fills over min(horizon, follow-up) days from index."""
    d = dexp[dexp.drug_concept_id == drug_id][["person_id", "drug_exposure_start_date", "drug_exposure_end_date"]].merge(
        j[["person_id", "index_date", "followup_days"]], on="person_id")
    d["win"] = d.followup_days.clip(upper=horizon)
    s = (pd.to_datetime(d.drug_exposure_start_date) - d.index_date).dt.days.clip(lower=0)
    e = (pd.to_datetime(d.drug_exposure_end_date) - d.index_date).dt.days + 1
    d["covered"] = (np.minimum(e, d.win) - s).clip(lower=0)
    # fills never overlap within one drug in this data model; cap at window to be safe
    cov = d.groupby("person_id").agg(covered=("covered", "sum"), win=("win", "first"))
    return (cov.covered.clip(upper=cov.win) / cov.win.clip(lower=1)).rename("pdc_12m")


def build_analytics(raw: dict, rules: dict, cn: dict):
    drugs, A = cn["drugs"], cn["therapy_a"]
    ra_id = cn["conditions"]["rheumatoid_arthritis"]
    sym_id = cn["conditions"]["joint_pain_symptom"]
    V = cn["visits"]
    dexp, vis, cond, cost, person = (raw[k] for k in ("drug_exposure", "visit_occurrence", "condition_occurrence", "cost", "person"))
    obs = raw["observation_period"]
    eras = build_eras(dexp, rules["era_gap_days"])
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
    hosp = inp.groupby("person_id").agg(n_hosp=("visit_occurrence_id", "size"), first_hosp=("d", "min"),
                                        n_hosp_ra=("ra_related", "sum"), hosp_days=("visit_end_date", lambda s: 0))
    inp["los"] = (pd.to_datetime(inp.visit_end_date) - inp.d).dt.days
    hosp["hosp_days"] = inp.groupby("person_id")["los"].sum()
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

    # hospital timing per window handled in metrics via hosp_days_from_index
    hosp_events = inp[["person_id", "visit_start_date", "ra_related"]].rename(columns={"visit_start_date": "event_date"})
    hosp_events = hosp_events.assign(event_type=np.where(hosp_events.ra_related, "hospitalization_ra", "hospitalization_other"),
                                     from_therapy=None, to_therapy=None, line_no=None)[events.columns]
    events = pd.concat([events, hosp_events], ignore_index=True).sort_values(["person_id", "event_date"])
    attr["n_ra_patients"] = int(rap.dx_date.notna().sum())
    return dict(drug_era=eras, ra_patients=rap, index_journey=j.drop(columns=["gender_concept_id", "year_of_birth", "race_concept_id", "ethnicity_concept_id", "person_source_value"]),
                patient_events=events), attr


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--db", default=str(config.DB_PATH))
    a = ap.parse_args(argv)
    print("simulating OMOP tables...")
    raw = simulate(a.seed, a.n)
    raw["concept"] = concept_table(config.concepts())
    print("deriving eras/lines/metrics tables...")
    analytics, attr = build_analytics(raw, config.rules(), config.concepts())
    con = duckdb.connect(a.db)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    for name, df in raw.items():
        con.register("_t", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
        con.unregister("_t")
    for name, df in analytics.items():
        con.register("_t", df)
        con.execute(f"CREATE OR REPLACE TABLE analytics.{name} AS SELECT * FROM _t")
        con.unregister("_t")
    con.execute("CREATE OR REPLACE TABLE analytics.attrition AS SELECT * FROM (VALUES (?, ?)) t(step, n)", ["__json__", 0])
    con.execute("DELETE FROM analytics.attrition")
    steps = [("RA patients in warehouse", attr["n_ra_patients"]),
             ("Received any advanced therapy", attr["n_with_advanced_therapy"]),
             ("Excluded: first advanced therapy was not Therapy A", attr["n_first_advanced_not_therapy_a"]),
             ("Excluded: < washout observation before index", attr["n_failed_washout"]),
             ("Index cohort (Therapy A initiators)", attr["n_index"])]
    con.executemany("INSERT INTO analytics.attrition VALUES (?, ?)", steps)
    cal = config.calibration()
    con.execute("CREATE OR REPLACE TABLE analytics.build_info AS SELECT ? AS profile, ? AS seed, ? AS n_patients, now() AS built_at",
                [cal["profile"], a.seed if a.seed is not None else cal["seed"], a.n or cal["n_patients"]])
    con.close()
    print(json.dumps(attr, indent=2, default=int))


if __name__ == "__main__":
    main()
