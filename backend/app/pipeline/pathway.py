"""Pathway engine: raw OMOP drug_exposure -> drug eras -> lines of therapy -> patient events/journeys.

Pure pandas, no I/O, so it can be unit-tested with hand-built patients.
"""
import numpy as np
import pandas as pd

D = pd.Timedelta(days=1)
COMORB_RANGE = (2000001100, 2000001110)


def build_eras(drug_exposure: pd.DataFrame, gap_days: int) -> pd.DataFrame:
    """Collapse fills into continuous eras per person+drug (merge when gap <= gap_days)."""
    df = drug_exposure.copy()
    df["s"] = pd.to_datetime(df["drug_exposure_start_date"])
    df["e"] = pd.to_datetime(df["drug_exposure_end_date"])
    df = df.sort_values(["person_id", "drug_concept_id", "s"])
    g = df.groupby(["person_id", "drug_concept_id"], sort=False)
    prev_max_end = g["e"].transform(lambda x: x.cummax().shift())
    new = prev_max_end.isna() | (df["s"] > prev_max_end + pd.Timedelta(days=gap_days))
    df["era_id"] = new.cumsum()
    out = df.groupby("era_id").agg(person_id=("person_id", "first"), drug_concept_id=("drug_concept_id", "first"),
                                   era_start=("s", "min"), era_end=("e", "max"), n_fills=("s", "size"))
    return out.reset_index(drop=True).sort_values(["person_id", "era_start"]).reset_index(drop=True)


def derive_lines(eras, obs, drugs: dict, therapy_a: str, rules: dict):
    """Returns (index_cohort_table, events_df, attrition list).

    drugs: name -> {id, class}. obs: observation_period df.
    """
    id2name = {v["id"]: k for k, v in drugs.items()}
    id2cls = {v["id"]: v["class"] for v in drugs.values()}
    a_id = drugs[therapy_a]["id"]
    obs = obs.assign(s=pd.to_datetime(obs["observation_period_start_date"]),
                     e=pd.to_datetime(obs["observation_period_end_date"])).set_index("person_id")
    gap, sw_win, ovl = rules["era_gap_days"], rules["switch_window_days"], rules["combo_overlap_days"]
    fu, wash = rules["followup_days"], rules["washout_days"]
    eras = eras.assign(cls=eras["drug_concept_id"].map(id2cls))
    adv_all = eras[eras["cls"] == "advanced"]
    esc_all = eras[eras["cls"] == "escalation"]
    esc_by_p = {p: g for p, g in esc_all.groupby("person_id")}
    attr = {"n_with_advanced_therapy": adv_all["person_id"].nunique()}
    rows, events = [], []
    n_first_not_a = n_washout = 0
    for pid, g in adv_all.groupby("person_id", sort=False):
        g = g.sort_values("era_start")
        first = g.iloc[0]
        if first.drug_concept_id != a_id:
            n_first_not_a += 1
            continue
        index = first.era_start
        o = obs.loc[pid]
        if o.s > index - pd.Timedelta(days=wash):
            n_washout += 1
            continue
        cens = min(o.e, index + pd.Timedelta(days=fu))
        horizon = index + pd.Timedelta(days=fu)
        regimen = {first.drug_concept_id}
        main = first.drug_concept_id
        reg_end = first.era_end
        lines = [main]
        line_ends = {}
        first_leave = None
        ev = []

        def leave(kind, date):
            nonlocal first_leave
            if first_leave is None and len(lines) == 1:
                first_leave = (kind, date)

        for e in g.iloc[1:].itertuples():
            if e.era_start > horizon:
                break
            if e.drug_concept_id in regimen and e.drug_concept_id == main:
                ev.append(("restart", e.era_start, main, main, len(lines)))
                reg_end = max(reg_end, e.era_end)
                continue
            overlap = (reg_end - e.era_start).days
            if overlap > ovl:
                ev.append(("add_on_advanced", e.era_start, main, e.drug_concept_id, len(lines)))
                regimen.add(e.drug_concept_id)
                reg_end = max(reg_end, e.era_end)
            elif (e.era_start - reg_end).days <= sw_win:
                ev.append(("switch", e.era_start, main, e.drug_concept_id, len(lines)))
                leave("switch", e.era_start)
                line_ends[len(lines)] = reg_end
                main, regimen, reg_end = e.drug_concept_id, {e.drug_concept_id}, e.era_end
                lines.append(main)
            else:
                ev.append(("discontinue", reg_end, main, None, len(lines)))
                leave("discontinue", reg_end)
                line_ends[len(lines)] = reg_end
                ev.append(("new_line_after_gap", e.era_start, main, e.drug_concept_id, len(lines) + 1))
                main, regimen, reg_end = e.drug_concept_id, {e.drug_concept_id}, e.era_end
                lines.append(main)
        terminal = "ongoing"
        if reg_end + pd.Timedelta(days=gap) <= cens:
            terminal = "discontinued"
            ev.append(("discontinue", reg_end, main, None, len(lines)))
            leave("discontinue", reg_end)
        line_ends[len(lines)] = reg_end

        # escalation: conventional add-on starting while on line 1
        l1_end = line_ends.get(1, reg_end)
        esc_dates = [x[1] for x in ev if x[0] == "add_on_advanced" and x[4] == 1]
        if pid in esc_by_p:
            for x in esc_by_p[pid].itertuples():
                if index < x.era_start <= l1_end and x.era_start <= horizon:
                    esc_dates.append(x.era_start)
                    ev.append(("add_on_conventional", x.era_start, a_id, x.drug_concept_id, 1))
        esc_dates = [d for d in esc_dates if d <= cens]
        for kind, date, frm, to, ln in ev:
            events.append((pid, kind, date, id2name.get(frm), id2name.get(to), ln))
        events.append((pid, "index", index, None, therapy_a, 1))

        if first_leave and first_leave[1] <= cens:
            ltype, ldate = first_leave
            lev, ldays = 1, (ldate - index).days
        else:
            ltype, lev = "persistent", 0
            ldays = (cens - index).days
            ldate = None
        seq_ids = lines
        rows.append(dict(
            person_id=pid, index_date=index, obs_end=o.e, censor_date=cens,
            followup_days=(cens - index).days,
            first_leave=ltype, first_leave_date=ldate, leave_event=lev, leave_days=ldays,
            switch_event=int(ltype == "switch"), discontinue_event=int(ltype == "discontinue"),
            switch_days=ldays if ltype == "switch" else (cens - index).days,
            discontinue_days=ldays if ltype == "discontinue" else (cens - index).days,
            escalated=int(len(esc_dates) > 0),
            escalation_days=((min(esc_dates) - index).days if esc_dates else None),
            n_lines=len(lines), sequence=" > ".join(id2name[i] for i in seq_ids),
            terminal_status=terminal,
            second_line=id2name[lines[1]] if len(lines) > 1 else None,
        ))
    attr.update(n_first_advanced_not_therapy_a=n_first_not_a, n_failed_washout=n_washout, n_index=len(rows))
    cols = ["person_id", "event_type", "event_date", "from_therapy", "to_therapy", "line_no"]
    return pd.DataFrame(rows), pd.DataFrame(events, columns=cols), attr
