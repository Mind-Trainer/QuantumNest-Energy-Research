"""Local same-origin application and typed scheduling API."""
import csv
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from backend.service import RunRequest, solve_schedule
from datasets.scenarios import household_case, research_case
from scheduling.model import House, Input, Infeasible
from scheduling.rolling import reschedule

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="QuantumNest Research", version="0.1.0")
app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")
app.mount("/circuits", StaticFiles(directory=ROOT / "circuits"), name="circuits")
app.mount("/figures", StaticFiles(directory=ROOT / "results" / "figures"), name="figures")


class RollingRequest(Input):
    original: House
    changed: House
    schedule: dict[str, list[int]]
    cutoff: int = Field(ge=0, le=48)
    previous_cutoff: int = Field(default=0, ge=0, le=48)
    actual_pv: list[int] = Field(max_length=48)


def error(exc):
    return HTTPException(409 if isinstance(exc, Infeasible) else 422, str(exc))


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/api/scenario/{name}")
def scenario(name: str):
    try:
        if name.startswith("research-"):
            return research_case(int(name.partition("-")[2]))
        return household_case(name)
    except ValueError as exc:
        raise error(exc) from exc


@app.post("/api/solve")
def schedule(request: RunRequest):
    try:
        return solve_schedule(request)
    except ValueError as exc:
        raise error(exc) from exc


@app.post("/api/reschedule")
def rolling(request: RollingRequest):
    try:
        return reschedule(request.original, request.changed, request.schedule,
                          request.cutoff, request.actual_pv, request.previous_cutoff)
    except ValueError as exc:
        raise error(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


FILES = {"raw": "raw/runs.jsonl", "runs": "processed/runs.csv",
         "summary": "processed/summary.csv", "paired": "processed/paired_differences.csv",
         "instances": "raw/instances.jsonl", "manifest": "manifest.json"}


@app.get("/api/results/{name}")
def results(name: str):
    relative = FILES.get(name)
    path = ROOT / "results" / relative if relative else None
    if path is None or not path.is_file():
        raise HTTPException(404, "Experiment artifact unavailable. Run python -m experiments.run.")
    return FileResponse(path, filename=path.name)


@app.get("/api/study")
def study():
    path = ROOT / "results" / FILES["summary"]
    if not path.is_file():
        return {"summary": [], "manifest": {}}
    with path.open(encoding="utf-8") as source:
        summary = list(csv.DictReader(source))
    manifest = json.loads((ROOT / "results" / "manifest.json").read_text(encoding="utf-8"))
    return {"summary": summary, "manifest": manifest}
