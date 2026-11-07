"""Seeded, calibrated simulator producing OMOP-shaped (CDM 5.4 subset) RA claims+EHR tables.

The simulator writes only raw OMOP events (visits, conditions, drug fills, costs). All journey
logic (eras, lines, switches...) is re-derived from those events by the pathway engine.
"""
import numpy as np
import pandas as pd

from .. import config

FILL_DAYS = 28


def _lognormal_days(rng, median, sigma=0.6):
    return max(1, int(round(rng.lognormal(np.log(median), sigma))))


def _exp_time(rng, hazard):
    return rng.exponential(1.0 / hazard) if hazard > 0 else np.inf


class _Sink:
    def __init__(self):
        self.visits, self.conds, self.drugs, self.costs = [], [], [], []
        self.vid = 0
        self.did = 0

    def visit(self, pid, vtype, start, end, source, cost, conds=()):
        self.vid += 1
        self.visits.append((self.vid, pid, vtype, start, end, source))
        self.costs.append((pid, "Visit", self.vid, cost))
        for c in conds:
            self.conds.append((pid, c, start, self.vid))
        return self.vid

    def fill(self, pid, drug_id, start, cost):
        self.did += 1
        self.drugs.append((self.did, pid, drug_id, start, start + pd.Timedelta(days=FILL_DAYS - 1), FILL_DAYS))
        self.costs.append((pid, "Drug", self.did, cost))


DRUG_COST = {"methotrexate": 40, "hydroxychloroquine": 60, "leflunomide": 150, "prednisone": 25,
             "adalimumab": 3400, "etanercept": 3200, "tocilizumab": 3600, "tofacitinib": 3300,
             "abatacept": 3500, "rituximab": 4200}


def _fills(rng, sink, pid, drug, drug_id, start, last_start, obs_end):
    """Monthly fills from `start` until the next fill would begin after `last_start`.
    Returns the date the last fill's supply ends (+1)."""
    d, end = start, start
    while d <= last_start and d <= obs_end:
        sink.fill(pid, drug_id, d, DRUG_COST[drug])
        end = d + pd.Timedelta(days=FILL_DAYS)
        gap = 0 if rng.random() < 0.75 else int(rng.integers(3, 25))
        d = end + pd.Timedelta(days=gap)
    return end


def simulate(seed=None, n_patients=None):
    cal, cn = config.calibration(), config.concepts()
    if n_patients:
        cal["n_patients"] = n_patients
    rng = np.random.default_rng(cal["seed"] if seed is None else seed)
    drugs = {k: v["id"] for k, v in cn["drugs"].items()}
    A = cn["therapy_a"]
    data_end = pd.Timestamp(cal["data_end"])
    dx0, dx1 = pd.Timestamp(cal["dx_start"]), pd.Timestamp(cal["dx_end"])
    span = (dx1 - dx0).days
    RA, SYM, OTH = (cn["conditions"][k] for k in ("rheumatoid_arthritis", "joint_pain_symptom", "other_inpatient_cause"))
    V = cn["visits"]
    sink = _Sink()
    persons, obs = [], []
    targets = list(cal["switch_targets"])

    for pid in range(1, cal["n_patients"] + 1):
        female = rng.random() < 0.72
        age = int(np.clip(rng.normal(54, 13), 20, 88))
        comorb = int(min(rng.poisson(1.2), 5))
        dx = dx0 + pd.Timedelta(days=int(rng.integers(0, span)))
        mult_age = np.exp(cal["beta_age_per_10y"] * (age - 54) / 10)
        mult_c = np.exp(cal["beta_comorbidity"] * (comorb - 1.2))
        m_disc = mult_age * mult_c * (np.exp(cal["beta_female_discontinue"]) if female else 1.0)
        m_sw, m_hosp = mult_age * mult_c, np.exp(cal["beta_hosp_comorbidity"] * (comorb - 1.2)) * mult_age

        rheum = dx - pd.Timedelta(days=_lognormal_days(rng, cal["days_rheum_to_dx_median"]))
        first_visit = rheum - pd.Timedelta(days=_lognormal_days(rng, cal["days_first_visit_to_rheum_median"]))
        onset = first_visit - pd.Timedelta(days=_lognormal_days(rng, cal["days_onset_to_first_visit_median"]))
        persons.append((pid, 8532 if female else 8507, dx.year - age))
        for k in range(comorb):
            sink.conds.append((pid, 2000001100 + k, dx - pd.Timedelta(days=int(rng.integers(100, 1000))), None))

        sink.visit(pid, V["outpatient"], first_visit, first_visit, "primary_care_symptom", 160, [SYM])
        sink.conds.append((pid, SYM, onset, None))
        sink.visit(pid, V["outpatient"], rheum, rheum, "rheumatology_first", 260)
        sink.visit(pid, V["outpatient"], dx, dx, "rheumatology_dx", 240, [RA])

        obs_end = data_end
        mtx_start = None
        index = None
        if rng.random() < cal["p_start_csdmard"]:
            mtx_start = dx + pd.Timedelta(days=_lognormal_days(rng, cal["days_dx_to_csdmard_median"], 0.8))
        elif rng.random() < 0.3:
            mtx_start = dx + pd.Timedelta(days=int(rng.integers(120, 400)))
        if mtx_start is not None and rng.random() < cal["p_advanced_given_csdmard"]:
            index = mtx_start + pd.Timedelta(days=_lognormal_days(rng, cal["days_csdmard_to_a_median"], 0.5))
        if index is not None and index > data_end - pd.Timedelta(days=60):
            index = None
        if index is not None and rng.random() < cal["p_early_censor"]:
            obs_end = min(data_end, index + pd.Timedelta(days=int(rng.integers(60, 700))))
        elif index is None and rng.random() < cal["p_early_censor"]:
            obs_end = min(data_end, dx + pd.Timedelta(days=int(rng.integers(200, 900))))
        obs.append((pid, dx - pd.Timedelta(days=3 * 365), obs_end))

        # background csDMARD
        if mtx_start is not None and mtx_start <= obs_end:
            last = (index + pd.Timedelta(days=150)) if index is not None else mtx_start + pd.Timedelta(days=int(rng.integers(180, 700)))
            _fills(rng, sink, pid, "methotrexate", drugs["methotrexate"], mtx_start, last, obs_end)

        on_end = None
        if index is not None:
            # line 1: Therapy A
            tD = _exp_time(rng, cal["a_hazard_discontinue"] * m_disc)
            tS = _exp_time(rng, cal["a_hazard_switch"] * m_sw)
            tE = _exp_time(rng, cal["a_hazard_escalate"] * m_sw)
            leave = min(tD, tS)
            last_start = index + pd.Timedelta(days=min(leave, 4000))
            a_end = _fills(rng, sink, pid, A, drugs[A], index, last_start, obs_end)
            on_end = a_end
            if tE < leave and index + pd.Timedelta(days=tE) < obs_end:
                es = index + pd.Timedelta(days=tE)
                if rng.random() < 0.6:
                    _fills(rng, sink, pid, "leflunomide", drugs["leflunomide"], es, es + pd.Timedelta(days=150), obs_end)
                else:
                    _fills(rng, sink, pid, "prednisone", drugs["prednisone"], es, es + pd.Timedelta(days=28), obs_end)
            cur_end = a_end
            if tS < tD:
                # later lines: switch chain (B, then optionally C)
                used = {A}
                for _line in range(2):
                    opts = [t for t in targets if t not in used]
                    p = np.array([cal["switch_targets"][t] for t in opts]); p /= p.sum()
                    nxt = opts[int(rng.choice(len(opts), p=p))]
                    used.add(nxt)
                    gap = int(rng.integers(0, 22))
                    b_start = cur_end + pd.Timedelta(days=gap)
                    if b_start > obs_end:
                        break
                    tDb = _exp_time(rng, cal["b_hazard_discontinue"] * m_disc)
                    tSb = _exp_time(rng, cal["b_hazard_switch"] * m_sw)
                    b_last = b_start + pd.Timedelta(days=min(min(tDb, tSb), 4000))
                    cur_end = _fills(rng, sink, pid, nxt, drugs[nxt], b_start, b_last, obs_end)
                    on_end = cur_end
                    if not (tSb < tDb):
                        break
            elif tD <= tS and rng.random() < cal["p_restart_after_discontinue"]:
                rs = a_end + pd.Timedelta(days=int(rng.integers(75, 240)))
                if rs <= obs_end:
                    on_end = _fills(rng, sink, pid, A, drugs[A], rs, rs + pd.Timedelta(days=int(rng.integers(60, 300))), obs_end)

        # follow-up outpatient visits, ER visits, hospitalizations
        d = dx + pd.Timedelta(days=90)
        while d <= obs_end:
            on = on_end is not None and d <= on_end
            sink.visit(pid, V["outpatient"], d, d, "rheumatology_followup", 220)
            d += pd.Timedelta(days=int(rng.integers(75, 105) if on else rng.integers(150, 210)))
        yrs_total = max((obs_end - dx).days, 1) / 365.0
        for _ in range(rng.poisson(cal["er_rate_per_year"] * yrs_total * m_hosp)):
            ed = dx + pd.Timedelta(days=int(rng.integers(0, max((obs_end - dx).days, 1))))
            sink.visit(pid, V["emergency"], ed, ed, "emergency", 1400)
        # hospitalization: piecewise Poisson process - off-rate before index, on-rate while on advanced
        # therapy (index -> end of last supply), off-rate after stopping
        on_rate, off_rate = cal["hosp_rate_on_therapy_per_year"], cal["hosp_rate_off_therapy_per_year"]
        if index is None:
            segments = [(dx, obs_end, off_rate)]
        else:
            stop = min(on_end, obs_end)
            segments = [(dx, min(index, obs_end), off_rate), (index, stop, on_rate), (stop, obs_end, off_rate)]
        hosp_dates = []
        for s, e, rate in segments:
            days = (e - s).days
            if days > 0:
                hosp_dates += [s + pd.Timedelta(days=int(x)) for x in rng.uniform(0, days, rng.poisson(rate * m_hosp * days / 365.0))]
        for hd in hosp_dates:
            los = int(rng.integers(2, 10))
            ra_rel = rng.random() < cal["hosp_ra_related_fraction"]
            sink.visit(pid, V["inpatient"], hd, hd + pd.Timedelta(days=los), "inpatient",
                       9000 + 1800 * los, [RA if ra_rel else OTH])

    # OMOP CDM 5.4 column names; type concepts: 32810 = Claim, 32869 = Pharmacy claim; 44818668 = USD
    person = pd.DataFrame(persons, columns=["person_id", "gender_concept_id", "year_of_birth"])
    person = person.assign(race_concept_id=0, ethnicity_concept_id=0, person_source_value=person.person_id.map("SIM{:06d}".format))
    obsp = pd.DataFrame(obs, columns=["person_id", "observation_period_start_date", "observation_period_end_date"])
    obsp.insert(0, "observation_period_id", range(1, len(obsp) + 1))
    obsp["period_type_concept_id"] = 32810
    visits = pd.DataFrame(sink.visits, columns=["visit_occurrence_id", "person_id", "visit_concept_id",
                                                "visit_start_date", "visit_end_date", "visit_source_value"])
    visits["visit_type_concept_id"] = 32810
    conds = pd.DataFrame(sink.conds, columns=["person_id", "condition_concept_id", "condition_start_date", "visit_occurrence_id"])
    conds.insert(0, "condition_occurrence_id", range(1, len(conds) + 1))
    conds["condition_type_concept_id"] = 32810
    conds["visit_occurrence_id"] = conds["visit_occurrence_id"].astype("Int64")
    drug = pd.DataFrame(sink.drugs, columns=["drug_exposure_id", "person_id", "drug_concept_id",
                                             "drug_exposure_start_date", "drug_exposure_end_date", "days_supply"])
    drug = drug.assign(drug_type_concept_id=32869, quantity=1.0)
    cost = pd.DataFrame(sink.costs, columns=["person_id", "cost_domain_id", "cost_event_id", "total_paid"])
    cost.insert(0, "cost_id", range(1, len(cost) + 1))
    cost = cost.assign(cost_type_concept_id=32810, currency_concept_id=44818668)
    return dict(person=person, observation_period=obsp, visit_occurrence=visits,
                condition_occurrence=conds, drug_exposure=drug, cost=cost)
