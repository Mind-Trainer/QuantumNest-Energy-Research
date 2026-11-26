"""Classical joint dispatch with shared scenario-wise transformer capacity."""
from ortools.sat.python import cp_model

from classical.solvers import cp_block, cp_optimize, cp_selection
from scheduling.model import evaluate


def coordinate(homes, capacity, other_load=None, timeout=20):
    if not homes:
        raise ValueError("Supply at least one household")
    first = homes[0].house
    if other_load is None:
        other_load = [0] * first.hours
    if len(capacity) != first.hours or len(other_load) != first.hours or min(*capacity, *other_load) < 0:
        raise ValueError("Supply nonnegative hourly capacity and background load")
    scenarios = [(s.name, s.weight) for s in first.solar]
    for p in homes:
        if p.house.hours != first.hours or [(s.name, s.weight) for s in p.house.solar] != scenarios:
            raise ValueError("Homes must share the horizon and scenario names and weights")
    model = cp_model.CpModel()
    blocks = [cp_block(model, p) for p in homes]
    for s in range(len(scenarios)):
        for t in range(first.hours):
            model.add(sum(imports[s][t] for _, _, imports in blocks) <= capacity[t] - other_load[t])
    model.minimize(sum(objective for objective, _, _ in blocks))
    solver, status = cp_optimize(model, timeout)
    if status not in {"optimal", "feasible"}:
        return {"status": status, "homes": [], "transformer_load": []}
    outcomes = [evaluate(p, cp_selection(solver, groups))
                for p, (_, groups, _) in zip(homes, blocks, strict=True)]
    load = [[other_load[t] + sum(r.imports[s][t] for r in outcomes) for t in range(first.hours)]
            for s in range(len(scenarios))]
    if any(r.violations for r in outcomes) or any(a > b for row in load for a, b in zip(row, capacity, strict=True)):
        raise RuntimeError("Community solver returned an invalid dispatch")
    return {"status": status, "homes": outcomes, "transformer_load": load}
