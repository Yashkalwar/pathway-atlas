# Methodology

## Prior art used for definitions
OHDSI/DARWIN-EU TreatmentPatterns and OHDSI/TxPathways (era collapse, gap, switch, interruption metrics),
Merck line-of-therapy algorithm and RWEdnesdays LOT notes (index, regimen window, advancement, gap rules).

## Pipeline
1. **Drug eras**: fills per person+drug merged when the gap is <= `era_gap_days` (60).
2. **Index**: first advanced-therapy era must be Therapy A (adalimumab); >= 365 days observation before index.
3. **Line logic** (per patient, over advanced-therapy eras in time order):
   - same drug after a gap = *restart* (not a discontinuation);
   - new agent overlapping the current regimen by > 28 days = *add-on / escalation* (no new line);
   - new agent starting within 90 days of the regimen end = *switch* (new line);
   - otherwise *discontinue* then *new line after gap*.
   - Conventional escalation: leflunomide or prednisone started while on line 1.
   - Final regimen is *discontinued* only if the gap is fully observable before censoring.
4. **Censoring**: min(observation end, index + 730 days).
5. **Funnel at window W**: patients followed for W days or who left therapy within W (others reported as not evaluable).
   Status = switched / discontinued / remain; hospitalization overlaps statuses.
6. **Time to event**: persistence = Kaplan-Meier; switch and discontinuation = Aalen-Johansen cumulative incidence (competing risks).
7. **Hospitalization after leaving therapy**: an inpatient admission dated after the switch/stop date and within the window
   (time-ordered, not just "hospitalized at some point").
8. **Adherence**: PDC = days covered by Therapy A fills in the first min(365, follow-up) days / that window (fixed-window PDC,
   so days after stopping count as uncovered, as in the MarketScan adherence literature).

## Simulator caveats
Seeded (`config/calibration.yaml: seed`), generates raw OMOP events only. Hazards depend on age, sex and comorbidity count.
Hospitalizations follow a piecewise Poisson process: off-therapy rate before index and after the last supply, on-therapy rate in between.
The `literature` profile is tuned toward published RA claims studies (docs/calibration_sources.md); hospitalization, ER, cost and
covariate effects are uncalibrated assumptions.

## Deviation from the original plan
The plan proposed the Synthea RA-10k OMOP dataset as the base population. The public file is ~840 MB, OMOP CDM 5.3 and,
per prior art, contains no switching behaviour, so the POC uses a fully self-contained simulator for persons and events instead.
A Synthea loader remains a possible extension.
