"""Reproduce the executed circuit drawings and the separate engineering demonstrations."""
from dataclasses import asdict
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt

from classical.community import coordinate
from classical.solvers import solve
from datasets.cases import small_slack_case
from datasets.scenarios import household_case, research_case
from quantum.qaoa import measured_results, optimize, sample
from scheduling.model import evaluate, make_problem
from scheduling.rolling import reschedule

ROOT = Path(__file__).resolve().parents[1]


def write(path, document):
    (ROOT / path).write_text(json.dumps(document, indent=2, default=asdict) + "\n", encoding="utf-8")


def artifacts():
    case = make_problem(research_case(6))
    results = []
    for algorithm in ("standard", "constrained", "hybrid", "standard_slack"):
        p = small_slack_case() if algorithm == "standard_slack" else case
        run = optimize(p, algorithm, shots=256)
        counts = sample(run)
        optimum = evaluate(p, solve(p, "exhaustive").selection).objective
        report = measured_results(p, run, counts, optimum)
        results.append(dict(algorithm=algorithm, instance=p.house.model_dump(), angles=run.parameters,
                            labels=run.encoding.labels, decision=run.encoding.logical_bits,
                            auxiliary=len(run.encoding.labels) - run.encoding.logical_bits, counts=counts, **report))
        for label, circuit, fold in (("logical", run.circuit, 18), ("transpiled", run.transpiled, 26)):
            fig = circuit.draw("mpl", fold=fold, style="iqp", idle_wires=True)
            fig.savefig(ROOT / "circuits" / f"{algorithm}_{label}.svg", bbox_inches="tight")
            plt.close(fig)
    write("results/raw/circuit_runs.json", results)

    households = {}
    for name in ("normal", "summer", "winter", "cloudy", "uncertain", "high_tariff", "high_ev",
                 "limited_transformer", "preference_change", "measured_uci"):
        h = household_case(name)
        p = make_problem(h)
        result = solve(p, "cp_sat")
        households[name] = dict(house=h.model_dump(), result=evaluate(p, result.selection), solver=asdict(result))
    write("results/raw/household_runs.json", households)

    p = make_problem(household_case("normal"))
    previous = evaluate(p, solve(p, "cp_sat").selection)
    cutoff = previous.schedule["Washing machine"][0] + 1
    storm = p.house.model_copy(deep=True)
    for s in storm.solar:
        s.pv[cutoff:] = [v // 3 for v in s.pv[cutoff:]]
    storm.appliances[2] = storm.appliances[2].model_copy(update={"preferred": 21, "priority": 8})
    rolling = reschedule(p.house, storm, previous.schedule, cutoff, p.house.solar[0].pv[:cutoff])
    write("results/raw/rolling_run.json", rolling)
    micro = small_slack_case()
    write("results/raw/community_run.json", coordinate([micro, micro], [5, 5, 5], [1, 1, 1]))


if __name__ == "__main__":
    artifacts()
