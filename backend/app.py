"""Local same-origin application and typed scheduling API."""
import csv
from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from classical.solvers import NAMES, solve
from datasets.scenarios import household_case, research_case
from quantum.qaoa import measured_results, optimize, sample
from quantum.noise_models import NOISE
from scheduling.model import House, Input, Infeasible, evaluate, make_problem
from scheduling.rolling import reschedule

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="QuantumNest Research", version="0.1.0")
app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")
app.mount("/circuits", StaticFiles(directory=ROOT / "circuits"), name="circuits")
app.mount("/figures", StaticFiles(directory=ROOT / "results" / "figures"), name="figures")

Solver = Literal["milp", "cp_sat", "exhaustive", "greedy", "random", "annealing",
                 "standard", "constrained", "hybrid", "pruned_x", "standard_slack"]
Classical = Literal["milp", "cp_sat", "exhaustive", "greedy", "random", "annealing"]
Noise = Literal["ideal", "gate_1q", "gate_2q", "readout", "combined"]


class RunRequest(Input):
    house: House
    solver: Solver = "cp_sat"
    noise: Noise = "ideal"
    layers: int = Field(default=1, ge=1, le=3)
    shots: int = Field(default=512, ge=32, le=2048)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


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
    start = perf_counter()
    try:
        p = make_problem(request.house)
        if request.solver in NAMES:
            run = solve(p, request.solver, request.seed)
            best = evaluate(p, run.selection) if run.selection is not None else None
            return {"solver": request.solver, "mode": "classical", "status": run.status,
                    "seconds": run.seconds, "result": asdict(best) if best else None}
        # The oracle is evaluated only for comparison against the sampled solution.
        baseline = solve(p, "cp_sat")
        optimum = (evaluate(p, baseline.selection).objective if baseline.status == "optimal" else None)
        run = optimize(p, request.solver, request.layers, request.seed, shots=request.shots)
        counts = sample(run, request.noise)
        stats = measured_results(p, run, counts, optimum)
        best = stats.pop("best")
        encoding = run.encoding
        return {
            "solver": request.solver, "mode": "qiskit_aer", "status": "feasible" if best else "none_found",
            "seconds": perf_counter() - start, "result": asdict(best) if best else None,
            "comparison_optimum": optimum, "noise": request.noise, "noise_parameters": NOISE[request.noise],
            "layers": request.layers, "evaluation_count": run.evaluations, "shots": run.shots,
            "decision_qubits": encoding.logical_bits, "auxiliary_qubits": len(encoding.labels) - encoding.logical_bits,
            "transpiled_depth": run.transpiled.depth(
                lambda op: op.operation.name not in {"measure", "barrier"}),
            "two_qubit_gates": run.transpiled.count_ops().get("cx", 0),
            "cost_offset": encoding.qubo.constant,
            "matrix": encoding.qubo.matrix().tolist(), "labels": encoding.labels,
            "circuit": str(run.circuit.draw(output="text", fold=76)),
            "transpiled": str(run.transpiled.draw(output="text", fold=76)),
            "counts": counts, "ideal_probabilities": {
                (format(i, f"0{len(encoding.labels)}b") if encoding.labels else ""): float(x)
                for i, x in enumerate(run.probabilities) if x > 1e-12
            }, **stats,
        }
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
