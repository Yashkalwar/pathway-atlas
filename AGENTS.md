# AGENTS.md
- Python venv at `.venv` (Windows). Run from `backend/`: `../.venv/Scripts/python -m app.pipeline.build` (rebuild warehouse), `-m pytest -q tests`, `-m uvicorn app.main:app --port 8000`.
- Frontend: `cd frontend && npm run build` (tsc + vite). API serves `frontend/dist`.
- Config in `config/*.yaml`; after changing calibration/rules, rebuild the warehouse.
- Tests in `backend/tests` need `data/warehouse.duckdb` (API tests skip otherwise); set `USE_LLM=0` for deterministic runs.
- Default LLM `qwen3:1.7b` via Ollama (qwen3:4b is too slow on CPU here). LLM routing eval: `-m scripts.eval_llm` from `backend/`.
- Calibration profile via `CALIBRATION_PROFILE` (`literature` default, `manager_example`); rebuild after switching.
- Study designer: `GET/POST /api/study` + `POST /api/study/reset` re-derive `analytics.*` from raw tables with client rule overrides (`app/study.py`, `backend/app/study.py` FIELD spec drives the Atlas 00 form).
- Visual check without a browser: `chrome --headless=new --window-size=1440,1700 --virtual-time-budget=15000 --screenshot=out.png "http://localhost:8000/#02"`.
