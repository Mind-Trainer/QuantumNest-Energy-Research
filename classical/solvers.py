"""Exact and heuristic baselines over the same service-pattern problem."""
from dataclasses import dataclass
from itertools import product
from math import exp, prod
from random import Random
from time import perf_counter

import numpy as np
from ortools.sat.python import cp_model
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from scheduling.model import Infeasible, Problem, evaluate, gross_bounds

NAMES = ("exhaustive", "milp", "cp_sat", "greedy", "random", "annealing")


@dataclass
class Solution:
    selection: tuple[int, ...] | None
    status: str
    seconds: float
    evaluations: int | None = None


def selections(problem):
    return product(*(range(len(g)) for g in problem.groups))


def linear_model(problem):
    h = problem.house
    costs, upper, rows, rhs = [], [], [], []
    groups = []
    load = [{} for _ in h.baseline]

    def variable(cost, bound):
        i = len(costs)
        costs.append(cost)
        upper.append(bound)
        return i

    for a, patterns in zip(h.appliances, problem.groups, strict=True):
        ids = []
        for p in patterns:
            i = variable(10 * h.weight * p.cost, 1)
            ids.append(i)
            for t in p.slots:
                load[t][i] = a.power
        groups.append(ids)
    for t, row in enumerate(load):
        rows.append(row)
        rhs.append(h.feeder[t] - h.baseline[t])
    imports = []
    for scenario in h.solar:
        grids = []
        peak = variable(scenario.weight * h.peak_price, max(h.transformer))
        for t, row in enumerate(load):
            grid = variable(scenario.weight * h.tariff[t], h.transformer[t] - h.neighbor[t])
            grids.append(grid)
            rows += [row | {grid: -1}, {grid: 1, peak: -1}]
            rhs += [scenario.pv[t] - h.baseline[t], 0]
        imports.append(grids)
    return costs, upper, rows, rhs, groups, imports


def cp_block(model, problem):
    gross_bounds(problem.house)
    costs, upper, rows, rhs, groups, imports = linear_model(problem)
    variables = [model.new_int_var(0, bound, f"v{len(model.proto.variables)}") for bound in upper]
    for group in groups:
        model.add_exactly_one(variables[i] for i in group)
    for row, bound in zip(rows, rhs, strict=True):
        model.add(sum(v * variables[i] for i, v in row.items()) <= bound)
    objective = sum(v * x for v, x in zip(costs, variables, strict=True))
    return objective, [[variables[i] for i in group] for group in groups], [
        [variables[i] for i in hours] for hours in imports
    ]


def cp_optimize(model, seconds):
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = 1
    status = solver.solve(model)
    return solver, solver.status_name(status).lower()


def cp_selection(solver, groups):
    return tuple(next(i for i, x in enumerate(group) if solver.value(x)) for group in groups)


def solve_linear(problem, method, seconds):
    if method == "cp_sat":
        model = cp_model.CpModel()
        objective, groups, _ = cp_block(model, problem)
        model.minimize(objective)
        solver, status = cp_optimize(model, seconds)
        return (cp_selection(solver, groups) if status in {"optimal", "feasible"} else None), status
    else:
        gross_bounds(problem.house)
        c, upper, rows, rhs, groups, _ = linear_model(problem)
        group_rows = [{i: 1 for i in group} for group in groups]
        entries = [(j, i, v) for j, row in enumerate(rows + group_rows) for i, v in row.items()]
        r, col, vals = zip(*entries, strict=True)
        matrix = coo_matrix((vals, (r, col)), shape=(len(rows) + len(groups), len(c))).tocsc()
        integrality = np.zeros(len(c))
        integrality[:sum(map(len, groups))] = 1
        solution = milp(c, integrality=integrality, bounds=Bounds(0, upper),
                        constraints=LinearConstraint(matrix, [-np.inf] * len(rows) + [1] * len(groups),
                                                     rhs + [1] * len(groups)),
                        options={"time_limit": seconds, "mip_rel_gap": 0})
        if solution.x is None:
            return None, {1: "limit", 2: "infeasible"}.get(solution.status, "solver_error")
        value, status_label = solution.x, "optimal" if solution.status == 0 else "feasible"
    return tuple(max(range(len(g)), key=lambda j: value[g[j]]) for g in groups), status_label


def feasible_draw(problem: Problem, orders, node_limit=10000):
    """Depth-first search with nonnegative-load pruning and a bounded node count."""
    h = problem.house
    bounds = gross_bounds(h)
    slack = [limit - load for limit, load in zip(bounds, h.baseline, strict=True)]
    if min(slack) < 0:
        return None
    chosen = []
    nodes = 0

    def visit(g):
        nonlocal nodes
        if g == len(orders):
            return tuple(chosen)
        a = h.appliances[g]
        for i in orders[g]:
            if nodes >= node_limit:
                break
            nodes += 1
            slots = problem.groups[g][i].slots
            if any(slack[t] < a.power for t in slots):
                continue
            chosen.append(i)
            for t in slots:
                slack[t] -= a.power
            result = visit(g + 1)
            if result is not None:
                return result
            for t in slots:
                slack[t] += a.power
            chosen.pop()
        return None

    return visit(0)


def solve(problem, method, seed=0, budget=128, timeout=20):
    start = perf_counter()
    h = problem.house
    rng = Random(seed)
    ids = [list(range(len(g))) for g in problem.groups]
    best, score, calls = None, float("inf"), 0

    def assess(pick):
        nonlocal best, score, calls
        calls += 1
        result = evaluate(problem, pick)
        if result.violations:
            return float("inf")
        if result.objective < score:
            best, score = pick, result.objective
        return result.objective

    if method in ("milp", "cp_sat"):
        try:
            pick, status = solve_linear(problem, method, timeout)
        except Infeasible:
            return Solution(None, "infeasible", perf_counter() - start)
        if pick is not None and evaluate(problem, pick).violations:
            raise RuntimeError(f"{method} produced an invalid schedule")
        return Solution(pick, status, perf_counter() - start)
    if method == "exhaustive":
        if prod(map(len, ids)) > 200000:
            raise ValueError("Instance exceeds exhaustive-search limit")
        for pick in selections(problem):
            assess(pick)
        return Solution(best, "optimal" if best is not None else "infeasible", perf_counter() - start, calls)
    if method == "greedy":
        for i, (a, group) in enumerate(zip(h.appliances, problem.groups, strict=True)):
            ids[i].sort(key=lambda j: a.power * sum(h.tariff[t] for t in group[j].slots) + 10 * group[j].cost)
        try:
            pick = feasible_draw(problem, ids)
        except Infeasible:
            pick = None
        if pick is not None:
            assess(pick)
    elif method in ("random", "annealing"):
        if method == "random":
            # Independent uniform complete pattern draws, retaining feasible draws.
            for _ in range(budget):
                assess(tuple(rng.choice(group) for group in ids))
        else:
            for group in ids:
                rng.shuffle(group)
            try:
                current = feasible_draw(problem, ids)
            except Infeasible:
                current = None
            if current is not None:
                current_score = assess(current)
                for step in range(1, budget):
                    neighbor = list(current)
                    g = rng.randrange(len(ids))
                    neighbor[g] = rng.choice(ids[g])
                    candidate = tuple(neighbor)
                    new_score = assess(candidate)
                    temp = max(1, current_score * .10 * (1 - step / budget))
                    if new_score <= current_score or rng.random() < exp((current_score - new_score) / temp):
                        current, current_score = candidate, new_score
    else:
        raise ValueError(f"Unknown solver {method}")
    return Solution(best, "feasible" if best is not None else "none_found", perf_counter() - start, calls)
