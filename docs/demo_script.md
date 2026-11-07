# 5-minute demo script

Setup: `./tasks.ps1 serve`, open http://localhost:8000, pre-warm the LLM by asking one question on Consult.

1. **01 The cohort (45 s).** Read the headline: "10,182 patients started adalimumab. A year later, ~62% are still on it."
   Point at the status strip (remain / discontinue / switch, hatched = hospitalized) and the median-day journey line
   (symptom -> diagnosis -> DMARD -> Therapy A -> switch). Close on the unmet-need signals list.
2. **02 After initiation (90 s).** The answer to the brief's demo question. Walk the flow left to right:
   initiators -> remain / discontinue / switch -> switch destinations. Toggle 6 / 12 / 24 months.
   Call out the median time to switch, admissions after leaving therapy, and adherence (PDC).
   Open "Method & denominator" to show every number is defined and traceable.
3. **03 Sequencing (45 s).** Sankey of lines of therapy; explain switch vs add-on rules (configurable in `rules.yaml`).
4. **04 Persistence (30 s).** KM curve; switch to "Age" to show older patients drop off faster.
5. **05 Who struggles (30 s).** Segment outcomes, diagnostic delay, escalation, utilization and cost per patient-year by status.
6. **06 One journey (30 s).** Pick a switcher; show the raw fills/encounters and the events the engine derived.
7. **07 Consult (45 s).** Ask "What happens to patients after they initiate Therapy A?" then a free-form question.
   Point at the provenance footer: analysis used, routing, local model, numbers verified.

Talking points
- Engine reads only raw OMOP events (drug_exposure, visit_occurrence, condition_occurrence, cost), so it runs unchanged on real
  claims/EHR mapped to OMOP. Swapping the data is a config change, not a rewrite.
- Definitions follow OHDSI TreatmentPatterns / TxPathways conventions (era gap, switch window, add-on overlap).
- Everything runs on a laptop: DuckDB, FastAPI, React, a 1.7B local model. No data leaves the machine.
- Data is synthetic; rates are tuned to published RA claims studies (docs/calibration_sources.md).

Next steps to propose
- Point the pipeline at a real OMOP instance (or the Synthea RA dataset) and validate against OHDSI TreatmentPatterns.
- Add comparative views (Therapy A vs B), cohort builder, PDF export.
