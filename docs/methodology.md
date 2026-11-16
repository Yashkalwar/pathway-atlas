## Prior art used for definitions
OHDSI/DARWIN-EU TreatmentPatterns and OHDSI/TxPathways (era collapse, gap, switch, interruption metrics),
Merck line-of-therapy algorithm and RWEdnesdays LOT notes (index, regimen window, advancement, gap rules),
and published MarketScan / IQVIA HEOR proxy-attribution studies in rheumatoid arthritis.

## OMOP RWD vs. CDISC Clinical Trial Data on "Why Patients Stop/Switch"
In clinical trials (**CDISC SDTM `DS` / `AE` / `ADaM ADTTE`**), structured discontinuation reasons (`DSDECOD`: *Lack of Efficacy*,
*Adverse Event*, *Cost*, *Withdrawal*) are mandatory on Case Report Forms. In routine claims + structured EHR (**OMOP CDM 5.4**),
`drug_exposure.stop_reason` is unpopulated in >95% of records. Rather than fabricating a `stop_reason` column, this pipeline models
standard OMOP clinical and cost domains (`visit_occurrence`, `condition_occurrence`, `drug_exposure`, `measurement` CRP labs, and
`cost.paid_by_patient` out-of-pocket copays) and derives **preceding 60-day clinical/claims proxy phenotypes** downstream.

## Pipeline
1. **Drug eras**: fills per person+drug merged when the gap is <= `era_gap_days` (60).
2. **Index**: first advanced-therapy era must be Therapy A (adalimumab); >= 365 days observation before index.
3. **Line logic** (per patient, over advanced-therapy eras in time order):
   - same drug after a gap = *restart* (not an immediate discontinuation);
   - new agent overlapping the current regimen by > 28 days = *add-on / escalation* (no new line);
   - new agent starting within 90 days of the regimen end = *switch* (new line);
   - otherwise *discontinue* then *new line after gap* (>90d).
   - Conventional escalation: leflunomide or prednisone started while on line 1.
   - Final regimen is *discontinued* only if the gap is fully observable before censoring.
4. **2nd-line MoA Cycling vs. Swapping**: switchers from adalimumab (TNFi) are classified as *TNFi cycling* (`etanercept`) vs.
   *MoA swapping* (`IL-6i`, `JAKi`, `T-cell costimulation`, `anti-CD20`), with 2nd-line persistence estimated via Kaplan-Meier from the
   switch date.
5. **Preceding 60-day OMOP Proxy Phenotypes** (among switchers & discontinuers):
   - *Adverse event / intolerance*: serious infection or ADR code, or acute ER/inpatient admission within 60 days before leaving.
   - *Inadequate response / flare*: acute RA flare code, elevated CRP (`> 10 mg/L`) in lookback, or prior rescue escalation.
   - *Sustained low activity / step-down*: `> 365` days on Therapy A, latest CRP `<= 5 mg/L`, no flare/AE, continuing background csDMARD.
   - *Cost / adherence barrier*: patient OOP copay `>= $250/fill` or active on-therapy refill regularity (`active_pdc < 80%`).
   - *Unobserved in structured RWD*: leaves with no structured claim/lab proxy in the 60-day window.
6. **Post-discontinuation trajectory**: classifies discontinuers into *Delayed switch (>90d gap)*, *Restarted Therapy A, later stopped*,
   *Maintained on csDMARD only*, or *Untreated in follow-up*.
7. **Adherence**: separates **Active On-Therapy PDC** (`[index_date, line1_era_end]`, isolating dose-taking regularity while on therapy)
   from **Fixed 12-Month PDC** (`min(365, follow-up)`, which drops mechanically after stopping).
8. **Multivariable Risk Drivers**: Cox proportional-hazards models (`CoxPHFitter`) estimate adjusted hazard ratios simultaneously across
   OOP copay tier, comorbidity count, age, sex, and baseline CRP.

## Study designer (client-parameterized definitions)
Every definition above is a client-tunable rule. `GET /api/study` returns the field spec (labels, ranges, help text);
`POST /api/study {name, rules}` re-runs the full derivation — `build_eras` → `derive_lines` → covariates/proxies/fates —
on the **same raw OMOP events** and replaces `analytics.*` in place (~10s for 13.8k patients). Raw tables are never touched.
`POST /api/study/reset` restores file defaults. The active spec is stored in `analytics.study_spec` and merged into
`Warehouse.rules`, so metric `definition` strings, labels and the LLM answers all reflect the applied study.
Exposed rules (mapped to prior-art parameters):

| Rule | Meaning (prior art) |
|---|---|
| `washout_days` | New-user washout / clean window (ACNU design; 180-365 typical) |
| `initiation_window_days` | Dx -> first DMARD counts as "initiated" |
| `era_gap_days` | Permissible gap / grace period merging fills into one era (TreatmentPatterns `eraCollapseSize`) |
| `min_era_days` | Minimum era duration (TreatmentPatterns `minEraDuration`) |
| `combo_overlap_days` | Regimen-defining window: overlapping different agent = add-on (Merck 28d; TreatmentPatterns `combinationWindow`) |
| `switch_window_days` | New agent within N days of era end = switch; beyond = new line after gap |
| `restart_same_line` | Same drug after gap = restart (on) vs. discontinuation + new episode (off, stricter "anniversary" convention) |
| `disc_date_rule` | `supply_end` (last covered day) vs. `expected_refill` (day the gap ran out) |
| `followup_days` / `funnel_window_days` | Max follow-up / status landmark (6/12/24-month reporting) |
| `pdc_threshold` | Adherence cutoff (standard 0.80) |
| `early_fail_days`, `multi_line_threshold` | Unmet-need flag thresholds |
| `proxy_lookback_days`, `high_copay_threshold`, `elevated_crp_mg_l` | Leave-driver signal window + thresholds |

## Simulator caveats
Seeded (`config/calibration.yaml: seed`), generates raw OMOP events only using integer epoch-day arithmetic. The `literature` profile is
tuned toward published RA claims studies (`docs/calibration_sources.md`); hospitalization, ER, cost, CRP, and covariate hazard ratios are
illustrative assumptions for the POC.
