"""Reproducible Qiskit CPU experiment. Re-run from the repository root."""
import argparse
import csv
from collections import defaultdict
from dataclasses import asdict
from importlib.metadata import version
from itertools import product
import json
from pathlib import Path
import platform
from statistics import mean, stdev
from time import perf_counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import t

from classical.solvers import NAMES, solve
from datasets.scenarios import research_case
from quantum.noise_models import NOISE
from quantum.qaoa import measured_results, optimize, sample
from scheduling.model import evaluate, make_problem

VARIANTS = ("standard", "constrained", "hybrid", "pruned_x")
SIZES = (6, 8, 10, 12, 14)
SELECT = ("feasible_probability", "optimal_probability", "relative_gap", "seconds",
          "two_qubit_gates", "transpiled_depth", "decision_qubits", "objective", "expected_feasible_objective",
          "grid_kwh", "solar_kwh", "solar_share", "electricity_cents", "expected_peak_kw",
          "ev_kwh", "service_rate", "deadline_rate", "ev_completion_rate")


def write_csv(path, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def physical(row, result, optimum, house):
    if result is None:
        return
    for k in ("objective", "grid_kwh", "solar_kwh", "solar_share", "electricity_cents", "expected_peak_kw"):
        row[k] = getattr(result, k)
    row["ev_kwh"] = sum(result.ev_kwh.values())
    services, deadlines, ev_services = [], [], []
    for a in house.appliances:
        slots = result.schedule.get(a.id, ())
        deadlines.append(bool(slots) and min(slots) >= a.earliest and max(slots) < a.latest)
        if a.kind == "ev":
            delivered = len(slots) * a.power * a.efficiency >= 100 * a.energy
            ev_services.append(delivered)
        elif a.kind == "essential":
            delivered = slots == tuple(sorted(a.fixed))
        else:
            delivered = len(slots) == a.duration
        services.append(delivered)
    row["service_rate"] = mean(services)
    row["deadline_rate"] = mean(deadlines)
    row["ev_completion_rate"] = mean(ev_services) if ev_services else None
    row["relative_gap"] = (result.objective - optimum) / optimum if optimum else None
    row["absolute_gap"] = result.objective - optimum


def interval(values):
    n = len(values)
    average = mean(values)
    if n < 2:
        return {"mean": average, "sd": None, "low95": None, "high95": None}
    sd = stdev(values)
    radius = float(t.ppf(.975, n - 1)) * sd / n**.5
    return {"mean": average, "sd": sd, "low95": average - radius, "high95": average + radius}


def summarize(rows, target):
    groups = defaultdict(list)
    for row in rows:
        groups[row["study"], row["size"], row["algorithm"], row["layers"], row["noise"]].append(row)
    summary = []
    for (study, size, alg, depth, noise), group in sorted(groups.items()):
        for name in SELECT:
            values = [r[name] for r in group if r.get(name) is not None]
            if values:
                summary.append(dict(study=study, size=size, algorithm=alg, layers=depth, noise=noise,
                                    metric=name, n=len(values), attempted=len(group), **interval(values)))
    write_csv(target / "processed" / "summary.csv", summary)
    paired = []
    for size, (a, b), metric in product(
        SIZES, (("hybrid", "standard"), ("hybrid", "constrained"), ("pruned_x", "standard"),
                ("hybrid", "pruned_x"), ("constrained", "standard")),
        ("feasible_probability", "optimal_probability", "decision_qubits", "two_qubit_gates", "relative_gap")
    ):
        left = {r["seed"]: r.get(metric) for r in groups["scaling", size, a, 1, "ideal"]}
        right = {r["seed"]: r.get(metric) for r in groups["scaling", size, b, 1, "ideal"]}
        values = [left[s] - right[s] for s in left if left[s] is not None and right[s] is not None]
        if values:
            paired.append(dict(size=size, left=a, right=b, metric=metric, n=len(values), **interval(values)))
    write_csv(target / "processed" / "paired_differences.csv", paired)
    return summary


def plot(summary, target):
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.5), layout="constrained")
    for ax, name, title in zip(axes, ("feasible_probability", "optimal_probability", "two_qubit_gates"),
                               ("Feasible shot fraction", "Optimal shot fraction", "Transpiled CX gates"), strict=True):
        for alg in VARIANTS:
            series = [r for r in summary if r["study"] == "scaling" and r["algorithm"] == alg and r["metric"] == name]
            x, y = [r["size"] for r in series], [r["mean"] for r in series]
            ax.plot(x, y, marker="o", label=alg, linewidth=1.6)
            ax.fill_between(x, [r["low95"] if r["low95"] is not None else r["mean"] for r in series],
                            [r["high95"] if r["high95"] is not None else r["mean"] for r in series], alpha=.12)
        ax.set(title=title, xlabel="Original decision bits", xticks=SIZES)
        ax.grid(axis="y", alpha=.18)
    axes[0].legend(fontsize=8)
    fig.savefig(target / "figures" / "scaling.svg")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(9.8, 3.7), layout="constrained")
    for ax, name, title in zip(axes, ("feasible_probability", "optimal_probability"),
                               ("Feasible shot fraction", "Optimal shot fraction"), strict=True):
        for alg in VARIANTS[:3]:
            for condition, style in (("ideal", "--"), ("combined", "-")):
                rows = [r for r in summary if r["study"] == "noise" and r["algorithm"] == alg
                        and r["metric"] == name and r["noise"] == condition]
                rows.sort(key=lambda r: r["layers"])
                ax.plot([r["layers"] for r in rows], [r["mean"] for r in rows],
                        linestyle=style, marker="o", label=f"{alg} · {condition}")
        ax.set(title=title, xlabel="QAOA layers", xticks=[1, 2, 3])
        ax.grid(axis="y", alpha=.18)
    axes[0].legend(fontsize=7)
    fig.savefig(target / "figures" / "noise_depth.svg")
    plt.close(fig)


def execute(seeds, shots, budget, target):
    for area in ("raw", "processed", "figures"):
        (target / area).mkdir(parents=True, exist_ok=True)
    manifest = dict(sizes=SIZES, seeds=list(range(seeds)), shots=shots, optimizer_budget=budget,
                    python=platform.python_version(), platform=platform.platform(),
                    versions={p: version(p) for p in ("qiskit", "qiskit-aer", "scipy", "ortools", "numpy")},
                    synthetic_noise=NOISE, noise_study_size=8, layers=[1, 2, 3],
                    noise_training="ideal statevector parameters reused for all noise channels")
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    rows = []
    with (target / "raw" / "runs.jsonl").open("w", encoding="utf-8") as raw, (
        target / "raw" / "instances.jsonl"
    ).open("w", encoding="utf-8") as instances:
        def record(row, **details):
            rows.append(row)
            print(json.dumps(row | details), file=raw)

        for size in SIZES:
            for seed in range(seeds):
                house = research_case(size, seed)
                print(json.dumps({"size": size, "seed": seed, "house": house.model_dump()}), file=instances)
                inst = make_problem(house)
                exact = solve(inst, "exhaustive")
                optimum = evaluate(inst, exact.selection).objective
                for alg in NAMES:
                    sol = exact if alg == "exhaustive" else solve(inst, alg, seed, budget=128)
                    result = evaluate(inst, sol.selection) if sol.selection is not None else None
                    row = dict(study="classical", size=size, seed=seed, algorithm=alg, noise="ideal", layers=0,
                               seconds=sol.seconds, status=sol.status, evaluations=sol.evaluations, optimum=optimum)
                    physical(row, result, optimum, house)
                    record(row, result=asdict(result) if result else None)
                designs = [(alg, 1, ("scaling", "ideal")) for alg in VARIANTS]
                if size == 8:
                    designs += [(alg, 0, ("initialization", "ideal")) for alg in VARIANTS[:3]]
                    designs += [(alg, 1, *[("noise", n) for n in NOISE if n != "ideal"])
                                for alg in VARIANTS[:3]]
                    designs += [(alg, p, ("noise", "ideal"), ("noise", "combined"))
                                for p in (2, 3) for alg in VARIANTS[:3]]
                cache = {}
                for alg, layers, *conditions in designs:
                    if (alg, layers) not in cache:
                        cache[alg, layers] = optimize(inst, alg, layers, seed, budget, shots)
                    run = cache[alg, layers]
                    routes = len(run.encoding.labels)
                    device = run.transpiled
                    for study, noise in conditions:
                        start = perf_counter()
                        counts = sample(run, noise)
                        sample_seconds = perf_counter() - start
                        stats = measured_results(inst, run, counts, optimum)
                        result = stats.pop("best")
                        row = dict(study=study, size=size, seed=seed, algorithm=alg, layers=layers, noise=noise,
                                   seconds=run.seconds + sample_seconds, preprocessing_seconds=run.encoding.prep_seconds,
                                   optimization_and_compile_seconds=run.seconds - run.encoding.prep_seconds,
                                   sample_seconds=sample_seconds, total_qubits=routes, decision_qubits=run.encoding.logical_bits,
                                   auxiliary_qubits=routes - run.encoding.logical_bits, circuit_depth=run.circuit.depth(),
                                   transpiled_depth=device.depth(lambda op: op.operation.name not in {"measure", "barrier"}),
                                   two_qubit_gates=device.count_ops().get("cx", 0), evaluations=run.evaluations,
                                   optimum=optimum, status="feasible" if result else "none_found", **stats)
                        physical(row, result, optimum, house)
                        record(row, counts=counts, parameters=run.parameters, labels=run.encoding.labels,
                               best_schedule=asdict(result) if result else None)
                        if study == "scaling" and size == 8 and alg in VARIANTS[:3]:
                            record(row | {"study": "noise"}, counts=counts, parameters=run.parameters,
                                   labels=run.encoding.labels, best_schedule=asdict(result) if result else None,
                                   reused_scaling_run=True)
            print(f"Finished {size} original bits × {seeds} seeds", flush=True)
    write_csv(target / "processed" / "runs.csv",
              [{k: json.dumps(v) if isinstance(v, dict) else v for k, v in r.items()} for r in rows])
    summary = summarize(rows, target)
    plot(summary, target)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seeds", default=20, type=int)
    p.add_argument("--shots", default=256, type=int)
    p.add_argument("--budget", default=24, type=int)
    p.add_argument("--output", default=Path("results"), type=Path)
    args = p.parse_args()
    execute(args.seeds, args.shots, args.budget, args.output)
