# Pathway Atlas: Treatment Pathway & Unmet Need Intelligence (RA POC)

Answers "What happens to patients after they initiate Therapy A?" from OMOP-shaped claims + EHR data. It rebuilds each
patient's journey (symptom, diagnosis, specialist, csDMARD, Therapy A, then switch / add-on / discontinuation, hospitalization)
and measures time to diagnosis/treatment, initiation, persistence (KM), adherence (PDC), discontinuation, switching,
escalation, hospitalization, utilization/cost and treatment sequencing. Everything runs locally, including the LLM (Ollama).

**All data is synthetic.** A seeded simulator generates ~13,800 RA patients (~10,200 adalimumab initiators) as OMOP CDM 5.4
tables (`person`, `observation_period`, `visit_occurrence`, `condition_occurrence`, `drug_exposure`, `cost`, `concept`).
The pathway engine only sees those raw events, so it runs unchanged on real OMOP claims/EHR.

Calibration profiles (`config/calibration.yaml`, override with `CALIBRATION_PROFILE`):
- `literature` (default): tuned toward published US/AU claims studies, ~62% persist / ~21% discontinue / ~17% switch at 12 months
  (see `docs/calibration_sources.md`).
- `manager_example`: reproduces the illustrative funnel in the brief, ~77% / ~14% / ~9%.

## Quick start (Windows PowerShell)
```
./tasks.ps1 setup        # venv + pip + npm install
./tasks.ps1 build-data   # simulate + derive eras/lines/metrics -> data/warehouse.duckdb (~40s)
./tasks.ps1 build-ui     # builds frontend/dist (served by the API)
./tasks.ps1 serve        # http://localhost:8000
./tasks.ps1 test         # 41 tests
```
Dev UI with hot reload: `./tasks.ps1 dev-ui` (proxies `/api` to :8000).
Local LLM: `ollama pull qwen3:1.7b` (default, CPU-friendly). Override with `OLLAMA_MODEL`; disable with `USE_LLM=0`.
LLM-only routing eval: `cd backend; ../.venv/Scripts/python -m scripts.eval_llm`.

## Architecture
```
simulate.py -> OMOP tables (DuckDB) -> pathway.py (drug eras -> lines -> events) -> analytics.* tables
                                                                   |
                         metrics/core.py (definitions + denominators) -> FastAPI -> React "Pathway Atlas" UI
                                                                   |
                         llm/agent.py: rule router (guardrail) -> else Ollama tool-calling -> 9 vetted tools only
```
- The LLM never writes SQL. Known question types go through a deterministic router (25/25 on the eval set). Free-form
  questions go to the local LLM (qwen3:1.7b alone routes 15/25 = 60% correctly, which is why the rule-first design is used).
  The LLM writes the brief; any brief containing numbers not in the tool payload is replaced by a deterministic template.
- Rules (era gap 60d, switch window 90d, add-on overlap 28d, follow-up 730d) live in `config/rules.yaml`.

## UI ("Clinical Atlas" design)
Editorial, medical-journal look: warm paper, Fraunces serif headlines and numerals, IBM Plex Sans/Mono, one semantic color
per journey state (remain teal, discontinue ochre, switch indigo, hospitalized crimson). A vertical journey rail replaces tabs.
Views: 01 The cohort, 02 After initiation (custom SVG alluvial flow), 03 Sequencing (Sankey, sunburst), 04 Persistence (KM / competing-risk CIF),
05 Who struggles (segments, delays, escalation, utilization), 06 One journey (patient timeline), 07 Consult (local LLM).

## Honest limitations
- Synthetic simulator, not the Synthea RA dataset (see `docs/methodology.md`).
- Local concept ids (>2B) for drugs and comorbidities; map to RxNorm/SNOMED for real data. Specialty is in `visit_source_value`
  rather than a `provider` table.
- Hospitalization, ER, cost and covariate effects are uncalibrated assumptions.
- A 1.7B model can phrase things imprecisely; numbers are guarded, semantics are not.
