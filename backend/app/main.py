import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .llm import agent
from .metrics import core as m

@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=m.wh, daemon=True).start()
    if agent.USE_LLM:
        threading.Thread(target=agent.warm_up, daemon=True).start()
    yield


app = FastAPI(title="Treatment Pathway Intelligence (synthetic RA POC)", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

Group = Literal["age", "sex", "comorbidity"]


@app.get("/api/health")
def health():
    return {"status": "ok", "warehouse": config.DB_PATH.exists(), "llm_model": agent.MODEL, "llm_enabled": agent.USE_LLM}


@app.get("/api/kpis")
def kpis():
    return m.kpis()


@app.get("/api/funnel")
def funnel(window: Optional[int] = None):
    return m.funnel(window)


@app.get("/api/pathways")
def pathways(depth: int = 3, min_n: int = 1):
    return m.pathways(depth, min_n)


@app.get("/api/survival")
def survival(event: Literal["persistence", "switch", "discontinuation"] = "persistence", group: Optional[Group] = None):
    return m.survival(event, group)


@app.get("/api/utilization")
def utilization():
    return m.utilization()


@app.get("/api/pre-index")
def pre_index():
    return m.pre_index()


@app.get("/api/escalation")
def escalation(window: Optional[int] = None):
    return m.escalation(window)


@app.get("/api/subgroups")
def subgroups(by: Group = "age", window: Optional[int] = None):
    return m.subgroups(by, window)


@app.get("/api/unmet-need")
def unmet_need():
    return m.unmet_need()


@app.get("/api/attrition")
def attrition():
    return m.attrition()


@app.get("/api/patients/sample")
def sample(kind: Literal["switch", "discontinue", "persistent", "any"] = "switch", n: int = 6):
    return m.sample_patients(kind, n)


@app.get("/api/patients/{person_id}/journey")
def journey(person_id: int):
    r = m.patient_journey(person_id)
    if r is None:
        raise HTTPException(404, "patient not found")
    return r


class Chat(BaseModel):
    question: str


@app.post("/api/chat")
def chat(body: Chat):
    return agent.ask(body.question)


@app.get("/api/chat/suggestions")
def suggestions():
    return agent.SUGGESTIONS


DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if f.is_file() else DIST / "index.html")
