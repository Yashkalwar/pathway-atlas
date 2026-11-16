"""Seeded, calibrated simulator producing OMOP-shaped (CDM 5.4 subset) RA claims+EHR tables.

The simulator writes only raw OMOP events (visits, conditions, drug fills, labs, costs) with NO
explicit `stop_reason` column. All journey logic, lines of therapy, and stop/switch proxy signals
are re-derived downstream from raw OMOP events by the pathway & analytics engine.
"""
import math

import numpy as np
import pandas as pd

from .. import config

FILL_DAYS = 28
EPOCH = pd.Timestamp("1970-01-01")


def _to_day(ts_str: str) -> int:
    return int((pd.Timestamp(ts_str) - EPOCH).days)


def _lognormal_days(rng, median, sigma=0.6) -> int:
    return max(1, int(round(rng.lognormal(math.log(median), sigma))))


def _exp_time(rng, hazard: float) -> float:
    return float(rng.exponential(1.0 / hazard)) if hazard > 0 else math.inf


class _Sink:
    __slots__ = ("visits", "conds", "drugs", "costs", "meas", "vid", "did", "mid")

    def __init__(self):
        self.visits, self.conds, self.drugs, self.costs, self.meas = [], [], [], [], []
        self.vid = 0
        self.did = 0
        self.mid = 0

    def visit(self, pid: int, vtype: int, start: int, end: int, source: str, cost: int, oop: int = 25, conds=()):
        self.vid += 1
        vid = self.vid
        self.visits.append((vid, pid, vtype, start, end, source))
        self.costs.append((pid, "Visit", vid, cost, oop))
        for c in conds:
            self.conds.append((pid, c, start, vid))
        return vid

    def fill(self, pid: int, drug_id: int, start: int, cost: int, oop: int):
        self.did += 1
        did = self.did
        self.drugs.append((did, pid, drug_id, start, start + FILL_DAYS - 1, FILL_DAYS))
        self.costs.append((pid, "Drug", did, cost, oop))

    def lab(self, pid: int, concept_id: int, day: int, val: float, unit_id: int, range_high: float, vid: int | None = None):
        self.mid += 1
        self.meas.append((self.mid, pid, concept_id, day, round(val, 1), unit_id, range_high, vid))


DRUG_COST = {
    "methotrexate": 40, "hydroxychloroquine": 60, "leflunomide": 150, "prednisone": 25,
    "adalimumab": 3400, "etanercept": 3200, "tocilizumab": 3600, "tofacitinib": 3300,
    "abatacept": 3500, "rituximab": 4200,
}


def _fills(rng, sink: _Sink, pid: int, drug: str, drug_id: int, start: int, last_start: int, obs_end: int,
           is_adv: bool = False, high_copay: bool = False) -> int:
    """Monthly fills from `start` (epoch days) until the next fill would begin after `last_start`.
    Returns the epoch day the last fill's supply ends (+1)."""
    d, end = start, start
    base_cost = DRUG_COST[drug]
    p_ontime = 0.42 if (is_adv and high_copay) else 0.78
    while d <= last_start and d <= obs_end:
        if is_adv:
            oop = int(rng.integers(260, 580)) if high_copay else int(rng.integers(10, 45))
        else:
            oop = int(rng.integers(5, 18))
        sink.fill(pid, drug_id, d, base_cost, oop)
        end = d + FILL_DAYS
        gap = 0 if rng.random() < p_ontime else int(rng.integers(8, 30) if (is_adv and high_copay) else rng.integers(3, 22))
        d = end + gap
    return end


def simulate(seed=None, n_patients=None):
    cal, cn = config.calibration(), config.concepts()
    if n_patients:
        cal["n_patients"] = n_patients
    rng = np.random.default_rng(cal["seed"] if seed is None else seed)
    drugs = {k: v["id"] for k, v in cn["drugs"].items()}
    moas = {k: v.get("moa", v["class"]) for k, v in cn["drugs"].items()}
    A = cn["therapy_a"]
    a_moa = moas[A]
    data_end = _to_day(cal["data_end"])
    dx0, dx1 = _to_day(cal["dx_start"]), _to_day(cal["dx_end"])
    span = dx1 - dx0
    dx_years = pd.date_range(cal["dx_start"], cal["dx_end"], freq="D").year.to_numpy()

    cond_cfg = cn["conditions"]
    RA, SYM, OTH = cond_cfg["rheumatoid_arthritis"], cond_cfg["joint_pain_symptom"], cond_cfg["other_inpatient_cause"]
    FLARE = cond_cfg["acute_ra_flare"]
    INF = cond_cfg["serious_infection"]
    ADR = cond_cfg["adverse_drug_reaction"]
    crp_cfg = cn["measurements"]["crp"]
    CRP_ID, CRP_UNIT, CRP_HI = crp_cfg["id"], crp_cfg["unit_id"], crp_cfg["range_high"]
    V = cn["visits"]

    sink = _Sink()
    persons, obs = [], []
    targets = list(cal["switch_targets"])
    target_weights = np.array([cal["switch_targets"][t] for t in targets], dtype=float)

    p_copay = cal.get("p_high_oop_copay", 0.24)
    beta_copay = cal.get("beta_high_copay_discontinue", 0.68)
    swap_mult_cfg = cal.get("b_moa_swap_hazard_mult", 0.68)
    log_crp_base = math.log(cal.get("crp_baseline_median", 16.0))
    log_crp_ctrl = math.log(cal.get("crp_controlled_median", 4.0))
    log_crp_flare = math.log(cal.get("crp_flare_median", 24.0))
    p_sw_flare = cal.get("p_switch_flare_episode", 0.66)
    p_sw_ae = cal.get("p_switch_ae_episode", 0.16)
    p_dc_ae = cal.get("p_disc_ae_episode", 0.26)
    p_dc_flare = cal.get("p_disc_flare_episode", 0.14)

    for pid in range(1, cal["n_patients"] + 1):
        female = rng.random() < 0.72
        age = int(np.clip(rng.normal(54, 13), 20, 88))
        comorb = int(min(rng.poisson(1.2), 5))
        high_copay = rng.random() < p_copay
        crp_base = float(np.clip(rng.lognormal(log_crp_base, 0.45), 1.5, 85.0))

        dx_off = int(rng.integers(0, span))
        dx = dx0 + dx_off
        dx_year = int(dx_years[dx_off])

        mult_age = math.exp(cal["beta_age_per_10y"] * (age - 54) / 10.0)
        mult_c = math.exp(cal["beta_comorbidity"] * (comorb - 1.2))
        mult_copay = math.exp(beta_copay * ((1.0 if high_copay else 0.0) - p_copay))
        mult_crp = math.exp(0.16 * (math.log(crp_base) - log_crp_base))

        m_disc = mult_age * mult_c * mult_copay * (math.exp(cal["beta_female_discontinue"]) if female else 1.0)
        m_sw = mult_age * mult_c * mult_crp
        m_hosp = math.exp(cal["beta_hosp_comorbidity"] * (comorb - 1.2)) * mult_age

        rheum = dx - _lognormal_days(rng, cal["days_rheum_to_dx_median"])
        first_visit = rheum - _lognormal_days(rng, cal["days_first_visit_to_rheum_median"])
        onset = first_visit - _lognormal_days(rng, cal["days_onset_to_first_visit_median"])
        persons.append((pid, 8532 if female else 8507, dx_year - age))
        for k in range(comorb):
            sink.conds.append((pid, 2000001100 + k, dx - int(rng.integers(100, 1000)), None))

        sink.visit(pid, V["outpatient"], first_visit, first_visit, "primary_care_symptom", 160, 25, (SYM,))
        sink.conds.append((pid, SYM, onset, None))
        sink.visit(pid, V["outpatient"], rheum, rheum, "rheumatology_first", 260, 40)
        dx_vid = sink.visit(pid, V["outpatient"], dx, dx, "rheumatology_dx", 240, 40, (RA,))
        sink.lab(pid, CRP_ID, dx, crp_base, CRP_UNIT, CRP_HI, dx_vid)

        obs_end = data_end
        mtx_start = None
        index = None
        if rng.random() < cal["p_start_csdmard"]:
            mtx_start = dx + _lognormal_days(rng, cal["days_dx_to_csdmard_median"], 0.8)
        elif rng.random() < 0.3:
            mtx_start = dx + int(rng.integers(120, 400))
        if mtx_start is not None and rng.random() < cal["p_advanced_given_csdmard"]:
            index = mtx_start + _lognormal_days(rng, cal["days_csdmard_to_a_median"], 0.5)
        if index is not None and index > data_end - 60:
            index = None
        if index is not None and rng.random() < cal["p_early_censor"]:
            obs_end = min(data_end, index + int(rng.integers(60, 700)))
        elif index is None and rng.random() < cal["p_early_censor"]:
            obs_end = min(data_end, dx + int(rng.integers(200, 900)))
        obs.append((pid, dx - 3 * 365, obs_end))

        mtx_end_target = None
        if mtx_start is not None and mtx_start <= obs_end:
            mtx_end_target = (index + 150) if index is not None else (mtx_start + int(rng.integers(180, 700)))

        on_end = None
        remission_stepdown = False
        if index is not None:
            # Pre-index CRP at Therapy A initiation
            idx_vid = sink.visit(pid, V["outpatient"], index, index, "rheumatology_initiation", 240, 40)
            sink.lab(pid, CRP_ID, index, max(2.0, crp_base * float(rng.uniform(0.85, 1.2))), CRP_UNIT, CRP_HI, idx_vid)

            tD = _exp_time(rng, cal["a_hazard_discontinue"] * m_disc)
            tS = _exp_time(rng, cal["a_hazard_switch"] * m_sw)
            tE = _exp_time(rng, cal["a_hazard_escalate"] * m_sw)
            leave = min(tD, tS)
            last_start = index + int(min(leave, 4000))
            a_end = _fills(rng, sink, pid, A, drugs[A], index, last_start, obs_end, is_adv=True, high_copay=high_copay)
            on_end = a_end
            escalated_l1 = False
            if tE < leave and index + int(tE) < obs_end:
                es = index + int(tE)
                escalated_l1 = True
                if rng.random() < 0.6:
                    _fills(rng, sink, pid, "leflunomide", drugs["leflunomide"], es, es + 150, obs_end)
                else:
                    _fills(rng, sink, pid, "prednisone", drugs["prednisone"], es, es + 28, obs_end)

            cur_end = a_end
            if tS < tD:
                gap = int(rng.integers(0, 22))
                b_start = cur_end + gap
                if b_start <= obs_end:
                    # Preceding clinical context before switch (no stop_reason column; raw OMOP visits/labs/conditions)
                    ev_day = max(index + 5, b_start - int(rng.integers(4, 38)))
                    u_sw = rng.random()
                    if u_sw < p_sw_flare:
                        fvid = sink.visit(pid, V["outpatient"], ev_day, ev_day, "rheumatology_flare", 250, 40, (FLARE,))
                        sink.lab(pid, CRP_ID, ev_day, float(np.clip(rng.lognormal(log_crp_flare, 0.35), 11.5, 92.0)), CRP_UNIT, CRP_HI, fvid)
                        if not escalated_l1 and rng.random() < 0.35:
                            _fills(rng, sink, pid, "prednisone", drugs["prednisone"], ev_day, ev_day + 14, obs_end)
                    elif u_sw < p_sw_flare + p_sw_ae:
                        ae_cond = INF if rng.random() < 0.42 else ADR
                        vtype = V["emergency"] if rng.random() < 0.30 else V["outpatient"]
                        vsrc = "emergency" if vtype == V["emergency"] else "adverse_event_visit"
                        sink.visit(pid, vtype, ev_day, ev_day, vsrc, 1400 if vtype == V["emergency"] else 240, 90 if vtype == V["emergency"] else 40, (ae_cond,))

                    # Later lines: switch chain (B, then optionally C) with MoA swap persistence effect
                    used = {A}
                    for _line in range(2):
                        mask = np.array([t not in used for t in targets])
                        opts = [t for t, keep in zip(targets, mask) if keep]
                        p = target_weights[mask]
                        p = p / p.sum()
                        nxt = opts[int(rng.choice(len(opts), p=p))]
                        used.add(nxt)
                        if _line > 0:
                            gap = int(rng.integers(0, 22))
                            b_start = cur_end + gap
                            if b_start > obs_end:
                                break
                        swap_mult = swap_mult_cfg if moas[nxt] != a_moa else 1.0
                        tDb = _exp_time(rng, cal["b_hazard_discontinue"] * m_disc * swap_mult)
                        tSb = _exp_time(rng, cal["b_hazard_switch"] * m_sw * swap_mult)
                        b_last = b_start + int(min(min(tDb, tSb), 4000))
                        cur_end = _fills(rng, sink, pid, nxt, drugs[nxt], b_start, b_last, obs_end, is_adv=True, high_copay=high_copay)
                        on_end = cur_end
                        if not (tSb < tDb):
                            break
            else:
                # Discontinuation branch (if observable before censoring)
                if a_end + 60 <= obs_end:
                    ev_day = max(index + 5, a_end - int(rng.integers(3, 35)))
                    if (a_end - index) > 365 and not high_copay and rng.random() < 0.36:
                        # Sustained low disease activity / step-down onto background csDMARD
                        remission_stepdown = True
                        rvid = sink.visit(pid, V["outpatient"], ev_day, ev_day, "rheumatology_followup", 220, 35)
                        sink.lab(pid, CRP_ID, ev_day, float(rng.uniform(0.8, 4.2)), CRP_UNIT, CRP_HI, rvid)
                        mtx_end_target = max(mtx_end_target or 0, min(obs_end, a_end + int(rng.integers(180, 420))))
                    else:
                        u_dc = rng.random()
                        if u_dc < p_dc_ae:
                            ae_cond = INF if rng.random() < 0.45 else ADR
                            vtype = V["emergency"] if rng.random() < 0.35 else V["outpatient"]
                            vsrc = "emergency" if vtype == V["emergency"] else "adverse_event_visit"
                            sink.visit(pid, vtype, ev_day, ev_day, vsrc, 1400 if vtype == V["emergency"] else 240, 90 if vtype == V["emergency"] else 40, (ae_cond,))
                        elif u_dc < p_dc_ae + p_dc_flare:
                            fvid = sink.visit(pid, V["outpatient"], ev_day, ev_day, "rheumatology_flare", 250, 40, (FLARE,))
                            sink.lab(pid, CRP_ID, ev_day, float(np.clip(rng.lognormal(log_crp_flare, 0.35), 11.0, 80.0)), CRP_UNIT, CRP_HI, fvid)

                u_post = rng.random()
                if u_post < cal["p_restart_after_discontinue"]:
                    rs = a_end + int(rng.integers(75, 220))
                    if rs <= obs_end:
                        on_end = _fills(rng, sink, pid, A, drugs[A], rs, rs + int(rng.integers(90, 340)), obs_end, is_adv=True, high_copay=high_copay)
                elif not remission_stepdown and u_post < cal["p_restart_after_discontinue"] + 0.09:
                    # Delayed new advanced line after >90d gap
                    ds = a_end + int(rng.integers(105, 260))
                    if ds <= obs_end:
                        p = target_weights / target_weights.sum()
                        nxt = targets[int(rng.choice(len(targets), p=p))]
                        on_end = _fills(rng, sink, pid, nxt, drugs[nxt], ds, ds + int(rng.integers(120, 380)), obs_end, is_adv=True, high_copay=high_copay)
                elif not remission_stepdown and rng.random() < 0.35 and mtx_start is not None:
                    # Maintained on conventional csDMARD only after stopping biologic
                    mtx_end_target = max(mtx_end_target or 0, min(obs_end, a_end + int(rng.integers(150, 400))))

        if mtx_start is not None and mtx_end_target is not None and mtx_start <= obs_end:
            _fills(rng, sink, pid, "methotrexate", drugs["methotrexate"], mtx_start, mtx_end_target, obs_end)

        # Follow-up outpatient visits, routine CRP monitoring, ER visits, hospitalizations
        d = dx + 90
        while d <= obs_end:
            on = on_end is not None and d <= on_end
            fvid = sink.visit(pid, V["outpatient"], d, d, "rheumatology_followup", 220, 35)
            if rng.random() < 0.55:
                mu = log_crp_ctrl if (on or remission_stepdown) else (log_crp_base - 0.35)
                sink.lab(pid, CRP_ID, d, float(np.clip(rng.lognormal(mu, 0.4), 0.6, 55.0)), CRP_UNIT, CRP_HI, fvid)
            d += int(rng.integers(75, 105) if on else rng.integers(150, 210))

        yrs_total = max(obs_end - dx, 1) / 365.0
        span_obs = max(obs_end - dx, 1)
        for _ in range(rng.poisson(cal["er_rate_per_year"] * yrs_total * m_hosp)):
            ed = dx + int(rng.integers(0, span_obs))
            sink.visit(pid, V["emergency"], ed, ed, "emergency", 1400, 120)

        on_rate, off_rate = cal["hosp_rate_on_therapy_per_year"], cal["hosp_rate_off_therapy_per_year"]
        if index is None:
            segments = ((dx, obs_end, off_rate),)
        else:
            stop = min(on_end, obs_end)
            segments = ((dx, min(index, obs_end), off_rate), (index, stop, on_rate), (stop, obs_end, off_rate))
        for s, e, rate in segments:
            days = e - s
            if days > 0:
                n_h = rng.poisson(rate * m_hosp * days / 365.0)
                if n_h:
                    for x in rng.uniform(0, days, n_h):
                        hd = s + int(x)
                        los = int(rng.integers(2, 10))
                        ra_rel = rng.random() < cal["hosp_ra_related_fraction"]
                        sink.visit(pid, V["inpatient"], hd, hd + los, "inpatient", 9000 + 1800 * los, 450, (RA if ra_rel else OTH,))

    # OMOP CDM 5.4 column names; convert integer epoch-days to datetime64[ns] in bulk
    person = pd.DataFrame(persons, columns=["person_id", "gender_concept_id", "year_of_birth"])
    person = person.assign(race_concept_id=0, ethnicity_concept_id=0, person_source_value=person.person_id.map("SIM{:06d}".format))

    obsp = pd.DataFrame(obs, columns=["person_id", "observation_period_start_date", "observation_period_end_date"])
    obsp["observation_period_start_date"] = pd.to_datetime(obsp["observation_period_start_date"], unit="D")
    obsp["observation_period_end_date"] = pd.to_datetime(obsp["observation_period_end_date"], unit="D")
    obsp.insert(0, "observation_period_id", range(1, len(obsp) + 1))
    obsp["period_type_concept_id"] = 32810

    visits = pd.DataFrame(sink.visits, columns=["visit_occurrence_id", "person_id", "visit_concept_id",
                                                "visit_start_date", "visit_end_date", "visit_source_value"])
    visits["visit_start_date"] = pd.to_datetime(visits["visit_start_date"], unit="D")
    visits["visit_end_date"] = pd.to_datetime(visits["visit_end_date"], unit="D")
    visits["visit_type_concept_id"] = 32810

    conds = pd.DataFrame(sink.conds, columns=["person_id", "condition_concept_id", "condition_start_date", "visit_occurrence_id"])
    conds["condition_start_date"] = pd.to_datetime(conds["condition_start_date"], unit="D")
    conds.insert(0, "condition_occurrence_id", range(1, len(conds) + 1))
    conds["condition_type_concept_id"] = 32810
    conds["visit_occurrence_id"] = conds["visit_occurrence_id"].astype("Int64")

    drug = pd.DataFrame(sink.drugs, columns=["drug_exposure_id", "person_id", "drug_concept_id",
                                             "drug_exposure_start_date", "drug_exposure_end_date", "days_supply"])
    drug["drug_exposure_start_date"] = pd.to_datetime(drug["drug_exposure_start_date"], unit="D")
    drug["drug_exposure_end_date"] = pd.to_datetime(drug["drug_exposure_end_date"], unit="D")
    drug = drug.assign(drug_type_concept_id=32869, quantity=1.0)

    cost = pd.DataFrame(sink.costs, columns=["person_id", "cost_domain_id", "cost_event_id", "total_paid", "paid_by_patient"])
    cost.insert(0, "cost_id", range(1, len(cost) + 1))
    cost = cost.assign(cost_type_concept_id=32810, currency_concept_id=44818668)

    meas = pd.DataFrame(sink.meas, columns=["measurement_id", "person_id", "measurement_concept_id", "measurement_date",
                                            "value_as_number", "unit_concept_id", "range_high", "visit_occurrence_id"])
    meas["measurement_date"] = pd.to_datetime(meas["measurement_date"], unit="D")
    meas["measurement_type_concept_id"] = 32856
    meas["visit_occurrence_id"] = meas["visit_occurrence_id"].astype("Int64")

    return dict(person=person, observation_period=obsp, visit_occurrence=visits,
                condition_occurrence=conds, drug_exposure=drug, cost=cost, measurement=meas)
