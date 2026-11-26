"""Shared solve requests and results for the FastAPI and Streamlit dashboards."""
from dataclasses import asdict
from time import perf_counter
from typing import Literal

from pydantic import Field

from classical.solvers import NAMES, solve
from quantum.noise_models import NOISE
from quantum.qaoa import measured_results, optimize, sample
from scheduling.model import House, Input, evaluate, make_problem

Solver = Literal["milp", "cp_sat", "exhaustive", "greedy", "random", "annealing",
                 "standard", "constrained", "hybrid", "pruned_x", "standard_slack"]
Noise = Literal["ideal", "gate_1q", "gate_2q", "readout", "combined"]


class RunRequest(Input):
    house: House
    solver: Solver = "cp_sat"
    noise: Noise = "ideal"
    layers: int = Field(default=1, ge=1, le=3)
    shots: int = Field(default=512, ge=32, le=2048)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


def solve_schedule(request: RunRequest):
    start = perf_counter()
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
