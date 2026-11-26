"""Exact, certified quadratic restriction for small service-pattern subproblems."""
from dataclasses import dataclass, field
from itertools import combinations, product
from math import gcd, prod

import numpy as np

from scheduling.model import Infeasible, Problem, evaluate, gross_bounds

VARIANTS = ("standard", "constrained", "hybrid", "pruned_x", "standard_slack")


@dataclass
class Qubo:
    constant: int = 0
    linear: list[int] = field(default_factory=list)
    pairs: dict[tuple[int, int], int] = field(default_factory=dict)

    def bit(self):
        self.linear.append(0)
        return len(self.linear) - 1

    def couple(self, i, j, value):
        if i == j:
            self.linear[i] += value
            return
        key = tuple(sorted((i, j)))
        self.pairs[key] = self.pairs.get(key, 0) + value
        if self.pairs[key] == 0:
            self.pairs.pop(key)

    def square(self, terms, target, penalty):
        self.constant += penalty * target**2
        for i, v in terms.items():
            self.linear[i] += penalty * (v * v - 2 * target * v)
        for (i, v), (j, w) in combinations(terms.items(), 2):
            self.couple(i, j, 2 * penalty * v * w)

    def energies(self):
        states = np.arange(1 << len(self.linear), dtype=np.uint32)
        x = [(states >> i & 1).astype(np.int64) for i in range(len(self.linear))]
        energies = np.full(len(states), self.constant, dtype=np.int64)
        for i, v in enumerate(self.linear):
            energies += v * x[i]
        for (i, j), v in self.pairs.items():
            energies += v * x[i] * x[j]
        return energies

    def matrix(self):
        out = np.diag(self.linear)
        for (i, j), v in self.pairs.items():
            out[i, j] = v
        return out


@dataclass
class Encoding:
    qubo: Qubo
    indices: list[dict[int, int]]
    fixed: dict[int, int]
    feasible: dict[int, tuple[int, ...]]
    labels: list[str]
    penalty: int
    init: list[list[int]]
    logical_bits: int
    original_bits: int
    prep_seconds: float = 0

    def decode(self, number):
        chosen = dict(self.fixed)
        for g, options in enumerate(self.indices):
            if g in self.fixed:
                continue
            picks = [option for option, bit in options.items() if number >> bit & 1]
            if len(picks) != 1:
                return None
            chosen[g] = picks[0]
        return tuple(chosen[g] for g in range(len(self.indices)))


def conflict_tables(problem, domains):
    h = problem.house
    limits = gross_bounds(h)
    headroom = [a - b for a, b in zip(limits, h.baseline, strict=True)]
    if min(headroom) < 0:
        raise Infeasible("Baseline alone exceeds available capacity")
    singles, edges = set(), set()
    for g, group in enumerate(domains):
        for i in group:
            if any(h.appliances[g].power > headroom[t] for t in problem.groups[g][i].slots):
                singles.add((g, i))
    for g, k in combinations(range(len(domains)), 2):
        power = h.appliances[g].power + h.appliances[k].power
        for i, j in product(domains[g], domains[k]):
            common = set(problem.groups[g][i].slots).intersection(problem.groups[k][j].slots)
            if any(power > headroom[t] for t in common):
                edges.add(((g, i), (k, j)))
    return singles, edges


def prune_domains(domains, singles, edges):
    domains = [[i for i in options if (g, i) not in singles] for g, options in enumerate(domains)]
    while True:
        reduced = []
        for g, options in enumerate(domains):
            reduced.append([i for i in options if all(
                any(tuple(sorted(((g, i), (k, j)))) not in edges for j in other)
                for k, other in enumerate(domains) if k != g
            )])
        if any(not options for options in reduced):
            raise Infeasible("Capacity propagation eliminated every option for an appliance")
        if reduced == domains:
            return reduced
        domains = reduced


def capacity_slack(qubo, problem, domains, indices, labels, penalty, max_bits):
    h = problem.house
    for t, limit in enumerate(gross_bounds(h)):
        residual = limit - h.baseline[t]
        terms = {}
        max_load = 0
        for g, a in enumerate(h.appliances):
            active = [i for i in domains[g] if t in problem.groups[g][i].slots]
            if active:
                terms.update({indices[g][i]: a.power for i in active})
                max_load += a.power
        if max_load <= residual:
            continue
        divisor = gcd(*terms.values())
        if divisor > 1:
            terms = {i: v // divisor for i, v in terms.items()}
            residual //= divisor
        width = residual.bit_length()
        if len(labels) + width > max_bits:
            raise ValueError(f"Slack encoding exceeds the {max_bits}-qubit simulation limit")
        for b in range(width):
            labels.append(f"capacity@{t}:slack{b}")
            terms[qubo.bit()] = 1 << b
        qubo.square(terms, residual, penalty)


def compile_qubo(problem: Problem, variant="standard", max_bits=14):
    if variant not in VARIANTS:
        raise ValueError("Unknown quantum encoding")
    domains = [list(range(len(group))) for group in problem.groups]
    if prod(map(len, domains)) > 30000 or sum(map(len, domains)) > 150:
        raise ValueError("Quantum certification is limited to small subproblems")
    original = sum(map(len, domains))
    singles, edges = conflict_tables(problem, domains)
    pruned = variant in {"hybrid", "pruned_x"}
    constrained = variant in {"constrained", "hybrid"}
    slack = variant == "standard_slack"
    if pruned:
        domains = prune_domains(domains, singles, edges)
    fixed = {g: choices[0] for g, choices in enumerate(domains) if pruned and len(choices) == 1}
    indices = [{} for _ in domains]
    labels = []
    qubo = Qubo()
    for g, options in enumerate(domains):
        if g in fixed:
            continue
        for i in options:
            labels.append(f"{problem.house.appliances[g].id} @ " +
                          ", ".join(map(str, problem.groups[g][i].slots)))
            indices[g][i] = qubo.bit()
    logical = len(labels)
    if logical > max_bits:
        raise ValueError(f"Encoding needs {logical} decision qubits. Limit is {max_bits}.")

    anchor = tuple(group[0] for group in domains)
    observations = {s: evaluate(problem, s) for s in product(*domains)}
    qubo.constant = observations[anchor].objective
    for g, options in enumerate(indices):
        for i, bit in options.items():
            point = anchor[:g] + (i,) + anchor[g + 1:]
            qubo.linear[bit] = observations[point].objective - qubo.constant
    for g, k in combinations(range(len(domains)), 2):
        for (i, b), (j, c) in product(indices[g].items(), indices[k].items()):
            point = list(anchor)
            point[g], point[k] = i, j
            residual = observations[tuple(point)].objective - qubo.constant - qubo.linear[b] - qubo.linear[c]
            qubo.couple(b, c, residual)

    penalty = 1 + sum(map(abs, qubo.linear)) + sum(map(abs, qubo.pairs.values()))
    for options in indices:
        if options and not constrained:
            qubo.square(dict.fromkeys(options.values(), 1), 1, penalty)
    if slack:
        capacity_slack(qubo, problem, domains, indices, labels, penalty, max_bits)
    else:
        for g, i in singles:
            if i in indices[g]:
                qubo.linear[indices[g][i]] += penalty
        for (g, i), (k, j) in edges:
            if i in indices[g] and j in indices[k]:
                qubo.couple(indices[g][i], indices[k][j], penalty)
            if fixed.get(g) == i and j in indices[k]:
                qubo.linear[indices[k][j]] += penalty
            if fixed.get(k) == j and i in indices[g]:
                qubo.linear[indices[g][i]] += penalty

    viable = {s for s, r in observations.items() if not r.violations}
    if not viable:
        raise Infeasible("No physically feasible joint schedule exists")
    # Verify representability separately from the QAOA run, including higher-order capacity violations.
    for s, result in observations.items():
        active = [options[i] for i, options in zip(s, indices, strict=True) if i in options]
        if not slack:
            actual = qubo.constant + sum(qubo.linear[b] for b in active)
            actual += sum(qubo.pairs.get((b, c), 0) for b, c in combinations(active, 2))
            encoded_penalties = sum((g, i) in singles for g, i in enumerate(s))
            encoded_penalties += sum(p in edges for p in combinations(enumerate(s), 2))
            if bool(encoded_penalties) != bool(result.violations):
                raise ValueError("Capacity has a higher-order conflict. Use the classical solver or slack.")
            expected = result.objective + penalty * encoded_penalties
            if actual != expected:
                raise ValueError("Physical objective has higher-order interactions. Use a classical solver.")
        else:
            # Recover unpenalized coefficients by checking this objective using the anchor decomposition.
            deltas = []
            for g in range(len(s)):
                p = anchor[:g] + (s[g],) + anchor[g + 1:]
                deltas.append(observations[p].objective - observations[anchor].objective)
            value = observations[anchor].objective + sum(deltas)
            for g, k in combinations(range(len(s)), 2):
                p = list(anchor)
                p[g], p[k] = s[g], s[k]
                value += observations[tuple(p)].objective - observations[anchor].objective - deltas[g] - deltas[k]
            if value != result.objective:
                raise ValueError("Physical objective has higher-order interactions. Use a classical solver.")

    # Grow a Cartesian product of jointly safe pattern choices around a feasible reference.
    # This supplies a coherent W-product start with zero physical violations.
    init = [[i] for i in min(viable)]
    if constrained:
        for g, options in enumerate(domains):
            for i in options:
                if i in init[g]:
                    continue
                candidate = init[:g] + [init[g] + [i]] + init[g + 1:]
                if all(s in viable for s in product(*candidate)):
                    init = candidate
    feasible = {sum(1 << opts[i] for i, opts in zip(s, indices, strict=True) if i in opts): s for s in viable}
    return Encoding(qubo, indices, fixed, feasible, labels, penalty,
                    [[options[i] for i in init[g]] for g, options in enumerate(indices) if options],
                    logical, original)
