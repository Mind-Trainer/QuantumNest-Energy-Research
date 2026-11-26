from itertools import product

import numpy as np
import pytest
from qiskit.quantum_info import Statevector

from classical.solvers import NAMES, solve
from datasets.cases import small_slack_case
from datasets.scenarios import research_case, household_case
from quantum.ising_mapper import to_ising
from quantum.qaoa import build_circuit, measured_results, mixer_edges, optimize, sample
from quantum.qubo_encoder import compile_qubo, conflict_tables
from scheduling.model import Appliance, House, Infeasible, evaluate, make_problem
from scheduling.rolling import reschedule



@pytest.mark.parametrize("size", [6, 8, 10, 12, 14])
def test_physical_optimum_and_all_zero(size):
    p = make_problem(research_case(size, seed=12))
    solutions = [solve(p, a) for a in NAMES]
    optimum = evaluate(p, solutions[0].selection).objective
    for s in solutions[:3]:
        assert s.status == "optimal"
        assert evaluate(p, s.selection).objective == optimum
    for s in solutions:
        assert s.selection is not None and not evaluate(p, s.selection).violations
    for variant in ("standard", "constrained", "hybrid", "pruned_x"):
        e = compile_qubo(p, variant)
        energy = e.qubo.energies()
        assert set(e.feasible.values()) == {
            s for s in product(*(range(len(g)) for g in p.groups)) if not evaluate(p, s).violations
        }
        assert min(energy[i] for i in e.feasible) == optimum
        assert all(energy[i] == evaluate(p, s).objective for i, s in e.feasible.items())
        if variant in {"standard", "pruned_x"}:
            assert min(energy) == optimum
            assert e.decode(0) is None
            assert energy[0] > optimum


def test_qubo_and_ising_full_spectrum():
    p = make_problem(research_case(6))
    e = compile_qubo(p)
    q = e.qubo
    h = to_ising(q)
    diagonal = np.diag(h.operator().to_matrix()).real
    np.testing.assert_allclose(diagonal, q.energies(), atol=1e-6, rtol=0)
    singles, pairs = conflict_tables(p, [range(len(g)) for g in p.groups])
    for state, energy in enumerate(q.energies()):
        vec = np.array([state >> i & 1 for i in range(len(q.linear))])
        assert q.constant + vec @ q.matrix() @ vec == energy
        choice = e.decode(state)
        if choice is not None:
            failures = sum((g, i) in singles for g, i in enumerate(choice))
            failures += tuple(enumerate(choice)) in pairs
            assert energy == evaluate(p, choice).objective + e.penalty * failures


def test_slack_exact_optimum_and_infeasible_overlap():
    p = small_slack_case()
    e = compile_qubo(p, "standard_slack")
    assert len(e.labels) == 9 and e.logical_bits == 6
    energies = e.qubo.energies()
    for s in product(range(3), repeat=2):
        signature = sum(1 << e.indices[g][i] for g, i in enumerate(s))
        low = min(energies[signature::1 << e.logical_bits])
        result = evaluate(p, s)
        if result.violations:
            assert low > result.objective
        else:
            assert low == result.objective
    eq = solve(p, "milp")
    assert min(energies) == evaluate(p, eq.selection).objective
    run = optimize(p, "standard_slack", budget=12, shots=128)
    assert sum(sample(run).values()) == 128


def reference_state(e, variant, g, b):
    """Direct computational-basis evolution with explicit rotations on pairs of amplitudes."""
    size = 1 << len(e.labels)
    if variant == "standard":
        psi = np.ones(size, dtype=complex) / np.sqrt(size)
    else:
        psi = np.zeros(size, dtype=complex)
        support = [sum(1 << i for i in choice) for choice in product(*e.init)]
        psi[support] = 1 / np.sqrt(len(support))
    h = to_ising(e.qubo)
    divisor = max(1, max(map(abs, h.fields), default=0),
                  max(map(abs, h.couplings.values()), default=0))
    psi *= np.exp(-1j * g * (e.qubo.energies() - h.offset) / divisor)
    if variant == "standard":
        branches = [[(n, n | (1 << i)) for n in range(size) if not n >> i & 1]
                    for i in range(len(e.labels))]
    else:
        branches = [[(n, n ^ (1 << i) ^ (1 << j)) for n in range(size)
                     if n >> i & 1 and not n >> j & 1] for i, j in mixer_edges(e)]
    for branch in branches:
        for i, j in branch:
            u, v = psi[i], psi[j]
            psi[i] = np.cos(b) * u - 1j * np.sin(b) * v
            psi[j] = np.cos(b) * v - 1j * np.sin(b) * u
    return psi


@pytest.mark.parametrize("variant", ["standard", "constrained", "hybrid"])
def test_independent_probabilities_and_sampling(variant):
    p = make_problem(research_case(6))
    e = compile_qubo(p, variant)
    template, gamma, beta = build_circuit(e, variant, 1)
    numerical = template.assign_parameters({k: v for k, v in ((gamma[0], .4), (beta[0], .7))
                                            if k in template.parameters})
    reference = reference_state(e, variant, .4, .7)
    prob = Statevector.from_instruction(numerical).probabilities()
    np.testing.assert_allclose(prob, abs(reference)**2, atol=1e-11)
    if variant != "standard":
        assert sum(v for n, v in enumerate(prob) if e.decode(n) is None) < 1e-12
        init, _, _ = build_circuit(e, variant, 0)
        p0 = Statevector.from_instruction(init).probabilities()
        assert sum(v for n, v in enumerate(p0) if n not in e.feasible) < 1e-12
    run = optimize(p, variant, seed=39, budget=12, shots=8192)
    count = sample(run)
    for i, expected in enumerate(run.probabilities):
        empirical = count.get(format(i, f"0{len(e.labels)}b"), 0) / run.shots
        assert abs(empirical - expected) <= 5 * np.sqrt(expected * (1 - expected) / run.shots) + .002
    optimum = evaluate(p, solve(p, "exhaustive").selection).objective
    results = measured_results(p, run, count, optimum)
    assert results["feasible_probability"] == sum(
        c for b, c in count.items() if int(b, 2) in e.feasible
    ) / run.shots


def test_pairwise_capacity_guard_and_transformer():
    h = small_slack_case().house
    h.appliances.append(Appliance(id="heater", kind="cycle", power=2, earliest=0, latest=3))
    h.feeder = [6] * 3
    h.transformer = [100] * 3
    h.peak_price = 0
    with pytest.raises(ValueError, match="higher-order conflict"):
        compile_qubo(make_problem(h))
    h.transformer = [0] * 3
    with pytest.raises(Infeasible, match="Neighbor"):
        compile_qubo(make_problem(h))


@pytest.mark.parametrize("style", ["normal", "summer", "winter", "cloudy", "uncertain", "high_tariff",
                                  "high_ev", "limited_transformer", "preference_change", "measured_uci"])
def test_full_household(style):
    p = make_problem(household_case(style))
    result = evaluate(p, solve(p, "cp_sat").selection)
    assert not result.violations
    for a in p.house.appliances:
        slots = result.schedule[a.id]
        assert all(a.earliest <= t < a.latest and t not in a.forbidden for t in slots)
        if a.kind == "cycle":
            assert slots == tuple(range(slots[0], slots[0] + a.duration))
        if a.kind == "ev":
            assert result.ev_kwh[a.id] >= a.energy / 10


def test_rolling_preserves_history_and_ongoing_cycle():
    h = research_case(10)
    h.appliances[1] = h.appliances[1].model_copy(update={"energy": 58, "latest": 17})
    p = make_problem(h)
    old = evaluate(p, solve(p, "cp_sat").selection)
    w = old.schedule["Washing machine"]
    at = w[0] + 1
    changed = h.model_copy(deep=True)
    changed.appliances[1] = changed.appliances[1].model_copy(update={"energy": 85})
    for s in changed.solar:
        s.pv[at:] = [i // 5 for i in s.pv[at:]]
    revised = reschedule(h, changed, old.schedule, at, h.solar[0].pv[:at])
    assert revised["updated"]["schedule"]["Washing machine"] == w
    for id, slots in old.schedule.items():
        assert [t for t in slots if t < at] == [t for t in revised["updated"]["schedule"][id] if t < at]
    assert revised["updated"]["ev_kwh"]["Electric vehicle"] >= 8.5
    assert all(s["pv"][:at] == h.solar[0].pv[:at] for s in revised["house"]["solar"])
    assert any(c["state"] == "ongoing" for c in revised["changes"])


def test_repeated_dispatch_preserves_realized_ev_and_solar():
    h = research_case(14)
    h.appliances[1] = h.appliances[1].model_copy(update={"energy": 58})
    p = make_problem(h)
    before = evaluate(p, solve(p, "cp_sat").selection)
    cutoff = max(before.schedule["Electric vehicle"]) + 1
    changed = h.model_copy(deep=True)
    changed.appliances[1] = changed.appliances[1].model_copy(update={"energy": 29})
    rolled = reschedule(h, changed, before.schedule, cutoff, h.solar[0].pv[:cutoff])
    assert len(rolled["updated"]["schedule"]["Electric vehicle"]) == 2
    plan = House.model_validate(rolled["house"])
    realized = plan.solar[0].pv[:cutoff + 1]
    repeated = reschedule(plan, plan, rolled["updated"]["schedule"], cutoff + 1, realized, cutoff)
    assert repeated["updated"]["schedule"] == rolled["updated"]["schedule"]
    with pytest.raises(ValueError, match="Previously recorded solar"):
        reschedule(plan, plan, rolled["updated"]["schedule"], cutoff + 1, [realized[0] + 1, *realized[1:]], cutoff)


def test_classical_community_shares_transformer():
    from classical.community import coordinate

    p = small_slack_case()
    plan = coordinate([p, p], [5, 5, 5], [1, 1, 1])
    assert plan["status"] == "optimal"
    assert all(v <= 5 for row in plan["transformer_load"] for v in row)
    assert sum(x.objective for x in plan["homes"]) >= 2 * evaluate(p, solve(p, "cp_sat").selection).objective
