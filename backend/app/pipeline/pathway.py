"""Pathway engine: raw OMOP drug_exposure -> drug eras -> lines of therapy -> patient events/journeys.

Pure pandas, no I/O, so it can be unit-tested with hand-built patients.
"""
import pandas as pd

D = pd.Timedelta(days=1)
COMORB_RANGE = (2000001100, 2000001110)


def build_eras(drug_exposure: pd.DataFrame, gap_days: int, min_era_days: int = 0) -> pd.DataFrame:
    """Collapse fills into continuous eras per person+drug (merge when gap <= gap_days).

    Eras shorter than min_era_days (e.g. a single infusion or a steroid burst) are dropped.
    """
    if drug_exposure.empty:
        return pd.DataFrame(columns=["person_id", "drug_concept_id", "era_start", "era_end", "n_fills"])
    df = drug_exposure[["person_id", "drug_concept_id", "drug_exposure_start_date", "drug_exposure_end_date"]].copy()
    df["s"] = pd.to_datetime(df["drug_exposure_start_date"])
    df["e"] = pd.to_datetime(df["drug_exposure_end_date"])
    df = df.sort_values(["person_id", "drug_concept_id", "s"], kind="mergesort")
    same_grp = (df["person_id"] == df["person_id"].shift(1)) & (df["drug_concept_id"] == df["drug_concept_id"].shift(1))
    cum_e = df.groupby(["person_id", "drug_concept_id"], sort=False)["e"].cummax()
    prev_max_end = cum_e.shift(1).where(same_grp)
    new = prev_max_end.isna() | (df["s"] > prev_max_end + pd.Timedelta(days=gap_days))
    df["era_id"] = new.cumsum()
    out = df.groupby("era_id", sort=False).agg(
        person_id=("person_id", "first"), drug_concept_id=("drug_concept_id", "first"),
        era_start=("s", "min"), era_end=("e", "max"), n_fills=("s", "size"),
    )
    if min_era_days:
        out = out[(out.era_end - out.era_start).dt.days + 1 >= min_era_days]
    return out.reset_index(drop=True).sort_values(["person_id", "era_start"], kind="mergesort").reset_index(drop=True)


def derive_lines(eras: pd.DataFrame, obs: pd.DataFrame, drugs: dict, therapy_a: str, rules: dict):
    """Returns (index_cohort_table, events_df, attrition dict).

    drugs: name -> {id, class, moa?}. obs: observation_period df.
    """
    id2name = {v["id"]: k for k, v in drugs.items()}
    id2cls = {v["id"]: v["class"] for v in drugs.values()}
    id2moa = {v["id"]: v.get("moa", k) for k, v in drugs.items()}
    a_id = drugs[therapy_a]["id"]
    a_moa = id2moa[a_id]

    obs_s = dict(zip(obs["person_id"], pd.to_datetime(obs["observation_period_start_date"])))
    obs_e = dict(zip(obs["person_id"], pd.to_datetime(obs["observation_period_end_date"])))

    gap = rules["era_gap_days"]
    sw_win = rules["switch_window_days"]
    ovl = rules["combo_overlap_days"]
    fu = rules["followup_days"]
    wash = rules["washout_days"]
    restart_same = rules.get("restart_same_line", True)
    disc_extra = pd.Timedelta(days=gap) if rules.get("disc_date_rule") == "expected_refill" else pd.Timedelta(0)
    td_gap = pd.Timedelta(days=gap)
    td_fu = pd.Timedelta(days=fu)
    td_wash = pd.Timedelta(days=wash)

    def disc_dt(reg_end, cap):
        """Discontinuation date: last supply end (default) or the day the permissible gap ran out."""
        d = reg_end + disc_extra
        if cap is not None and d > cap:
            d = cap
        return d if d >= reg_end else reg_end

    eras_cls = eras["drug_concept_id"].map(id2cls)
    adv_all = eras[eras_cls == "advanced"].sort_values(["person_id", "era_start"], kind="mergesort")
    esc_all = eras[eras_cls == "escalation"].sort_values(["person_id", "era_start"], kind="mergesort")

    esc_by_p: dict[int, list[tuple]] = {}
    for row in esc_all[["person_id", "drug_concept_id", "era_start", "era_end"]].itertuples(index=False):
        esc_by_p.setdefault(row.person_id, []).append(row)

    attr = {"n_with_advanced_therapy": int(adv_all["person_id"].nunique())}
    rows, events = [], []
    n_first_not_a = n_washout = 0

    for pid, g in adv_all.groupby("person_id", sort=False):
        it = list(g[["drug_concept_id", "era_start", "era_end", "n_fills"]].itertuples(index=False))
        first = it[0]
        if first.drug_concept_id != a_id:
            n_first_not_a += 1
            continue
        index = first.era_start
        o_s, o_e = obs_s[pid], obs_e[pid]
        if o_s > index - td_wash:
            n_washout += 1
            continue
        horizon = index + td_fu
        cens = o_e if o_e < horizon else horizon
        regimen = {first.drug_concept_id}
        main = first.drug_concept_id
        reg_end = first.era_end
        lines = [main]
        line_starts = {1: index}
        line_ends = {}
        first_leave = None
        l2_leave = None
        had_restart = 0
        ev = []

        def mark_leave(kind, date):
            nonlocal first_leave
            if first_leave is None and len(lines) == 1:
                first_leave = (kind, date)

        for e in it[1:]:
            if e.era_start > horizon:
                break
            if e.drug_concept_id in regimen and e.drug_concept_id == main:
                cur_ln = len(lines)
                if restart_same:
                    ev.append(("restart", e.era_start, main, main, cur_ln))
                    if cur_ln == 1:
                        had_restart = 1
                    if e.era_end > reg_end:
                        reg_end = e.era_end
                else:
                    dd = disc_dt(reg_end, e.era_start - D)
                    ev.append(("discontinue", dd, main, None, cur_ln))
                    mark_leave("discontinue", dd)
                    if cur_ln == 2 and l2_leave is None:
                        l2_leave = ("discontinue", dd)
                    line_ends[cur_ln] = dd
                    ev.append(("restart", e.era_start, main, e.drug_concept_id, cur_ln + 1))
                    main, regimen, reg_end = e.drug_concept_id, {e.drug_concept_id}, e.era_end
                    lines.append(main)
                    line_starts[len(lines)] = e.era_start
                continue
            overlap = (reg_end - e.era_start).days
            if overlap > ovl:
                ev.append(("add_on_advanced", e.era_start, main, e.drug_concept_id, len(lines)))
                regimen.add(e.drug_concept_id)
                if e.era_end > reg_end:
                    reg_end = e.era_end
            elif (e.era_start - reg_end).days <= sw_win:
                cur_ln = len(lines)
                ev.append(("switch", e.era_start, main, e.drug_concept_id, cur_ln))
                mark_leave("switch", e.era_start)
                if cur_ln == 2 and l2_leave is None:
                    l2_leave = ("switch", e.era_start)
                line_ends[cur_ln] = reg_end
                main, regimen, reg_end = e.drug_concept_id, {e.drug_concept_id}, e.era_end
                lines.append(main)
                line_starts[len(lines)] = e.era_start
            else:
                cur_ln = len(lines)
                dd = disc_dt(reg_end, e.era_start - D)
                ev.append(("discontinue", dd, main, None, cur_ln))
                mark_leave("discontinue", dd)
                if cur_ln == 2 and l2_leave is None:
                    l2_leave = ("discontinue", dd)
                line_ends[cur_ln] = dd
                ev.append(("new_line_after_gap", e.era_start, main, e.drug_concept_id, cur_ln + 1))
                main, regimen, reg_end = e.drug_concept_id, {e.drug_concept_id}, e.era_end
                lines.append(main)
                line_starts[len(lines)] = e.era_start

        terminal = "ongoing"
        if reg_end + td_gap <= cens:
            terminal = "discontinued"
            cur_ln = len(lines)
            dd = disc_dt(reg_end, cens)
            ev.append(("discontinue", dd, main, None, cur_ln))
            mark_leave("discontinue", dd)
            if cur_ln == 2 and l2_leave is None:
                l2_leave = ("discontinue", dd)
        line_ends[len(lines)] = reg_end

        # Also evaluate 2nd-line persistence across full observation up to 365d from 2nd-line start
        l1_end = line_ends.get(1, reg_end)
        esc_dates = [x[1] for x in ev if x[0] == "add_on_advanced" and x[4] == 1]
        if pid in esc_by_p:
            for x in esc_by_p[pid]:
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

        sec_id = lines[1] if len(lines) > 1 else None
        sec_name = id2name[sec_id] if sec_id is not None else None
        sec_moa = id2moa[sec_id] if sec_id is not None else None
        sw_mod = None
        sec_start = line_starts.get(2)
        sec_days = None
        sec_leave_ev = None
        sec_leave_type = None
        if ltype == "switch" and sec_id is not None and sec_start is not None:
            sw_mod = "tnfi_cycling" if sec_moa == a_moa else "moa_swapping"
            l2_cens = cens
            if l2_leave and l2_leave[1] <= l2_cens:
                sec_leave_ev = 1
                sec_leave_type = l2_leave[0]
                sec_days = max(0, (l2_leave[1] - sec_start).days)
            else:
                sec_leave_ev = 0
                sec_leave_type = "persistent"
                sec_days = max(0, (l2_cens - sec_start).days)

        rows.append(dict(
            person_id=pid, index_date=index, obs_end=o_e, censor_date=cens,
            followup_days=(cens - index).days,
            line1_era_end=min(l1_end, cens),
            had_restart=had_restart,
            first_leave=ltype, first_leave_date=ldate, leave_event=lev, leave_days=ldays,
            switch_event=int(ltype == "switch"), discontinue_event=int(ltype == "discontinue"),
            switch_days=ldays if ltype == "switch" else (cens - index).days,
            discontinue_days=ldays if ltype == "discontinue" else (cens - index).days,
            escalated=int(len(esc_dates) > 0),
            escalation_days=((min(esc_dates) - index).days if esc_dates else None),
            n_lines=len(lines), sequence=" > ".join(id2name[i] for i in lines),
            terminal_status=terminal,
            second_line=sec_name,
            second_line_moa=sec_moa,
            switch_modality=sw_mod,
            second_line_start=sec_start,
            second_line_days=sec_days,
            second_line_leave_event=sec_leave_ev,
            second_line_leave_type=sec_leave_type,
        ))
    attr.update(n_first_advanced_not_therapy_a=n_first_not_a, n_failed_washout=n_washout, n_index=len(rows))
    cols = ["person_id", "event_type", "event_date", "from_therapy", "to_therapy", "line_no"]
    jdf = pd.DataFrame(rows)
    for dt_col in ("index_date", "obs_end", "censor_date", "line1_era_end", "first_leave_date", "second_line_start"):
        if dt_col in jdf.columns:
            jdf[dt_col] = pd.to_datetime(jdf[dt_col])
    return jdf, pd.DataFrame(events, columns=cols), attr
