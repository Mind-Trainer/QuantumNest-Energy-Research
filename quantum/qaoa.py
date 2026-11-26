"""Genuine Qiskit variational state evolution and Aer measurements."""
from dataclasses import dataclass
from math import asin, pi, sqrt
from time import perf_counter

import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit.circuit import ParameterVector
from qiskit.circuit.library import XXPlusYYGate
from qiskit.quantum_info import Statevector
from qiskit_aer import AerSimulator
from scipy.optimize import minimize

from scheduling.model import evaluate
from .ising_mapper import to_ising
from .noise_models import BASIS, make_noise
from .qubo_encoder import compile_qubo

XY_VARIANTS = {"constrained", "hybrid"}


def prepare_support(circuit, support):
    for bits in support:
        circuit.x(bits[0])
        for i, b in enumerate(bits[1:]):
            angle = 2 * asin(1 / sqrt(len(bits) - i))
            circuit.append(XXPlusYYGate(angle), [bits[0], b])
            circuit.p(pi / 2, b)


def mixer_edges(encoding):
    for group in encoding.indices:
        bits = list(group.values())
        yield from zip(bits, bits[1:], strict=False)
        if len(bits) > 2:
            yield bits[-1], bits[0]


def build_circuit(encoding, variant, layers):
    n = len(encoding.labels)
    circuit = QuantumCircuit(n)
    gamma = ParameterVector("γ", layers)
    beta = ParameterVector("β", layers)
    if not n:
        return circuit, gamma, beta
    if variant in XY_VARIANTS:
        prepare_support(circuit, encoding.init)
    else:
        circuit.h(range(n))
    ising = to_ising(encoding.qubo)
    scale = max(1, max(map(abs, ising.fields), default=0),
                max(map(abs, ising.couplings.values()), default=0))
    for g, b in zip(gamma, beta, strict=True):
        for i, field in enumerate(ising.fields):
            if field:
                circuit.rz(2 * field / scale * g, i)
        for (i, j), coupling in ising.couplings.items():
            circuit.rzz(2 * coupling / scale * g, i, j)
        if variant in XY_VARIANTS:
            for i, j in mixer_edges(encoding):
                circuit.append(XXPlusYYGate(2 * b), [i, j])
        else:
            circuit.rx(2 * b, range(n))
    return circuit, gamma, beta


@dataclass
class Run:
    encoding: object
    circuit: QuantumCircuit
    transpiled: QuantumCircuit
    probabilities: np.ndarray
    parameters: list[float]
    evaluations: int
    seconds: float
    ideal_expected_qubo: float
    seed: int
    shots: int


def optimize(problem, variant, layers=1, seed=0, budget=24, shots=256, max_bits=14):
    start = perf_counter()
    encoding = compile_qubo(problem, variant, max_bits)
    encoding.prep_seconds = perf_counter() - start
    template, gammas, betas = build_circuit(encoding, variant, layers)
    energies = encoding.qubo.energies()
    if not template.num_qubits:
        return Run(encoding, template, template.copy(), np.ones(1), [], 0,
                   perf_counter() - start, float(energies[0]), seed, shots)

    parameters = list(gammas) + list(betas)
    active = set(template.parameters)
    scale = max(1, int(np.ptp(energies)))
    rng = np.random.default_rng(seed)
    best = None

    def bind(values):
        return template.assign_parameters({p: v for p, v in zip(parameters, values, strict=True) if p in active})

    def expectation(values):
        nonlocal best
        probabilities = Statevector.from_instruction(bind(values)).probabilities()
        energy = float(np.sum(probabilities * energies))
        if best is None or energy < best[0]:
            best = energy, values.copy(), probabilities
        return (energy - int(energies.min())) / scale

    initial = np.concatenate((rng.uniform(.15, 1.1, layers), rng.uniform(.1, .8, layers)))
    if layers:
        result = minimize(expectation, initial, method="COBYLA",
                          options={"maxiter": budget, "rhobeg": .35, "tol": 1e-4})
        evaluations = result.nfev
    else:
        expectation(initial)
        evaluations = 0
    energy, values, probabilities = best
    circuit = bind(values)
    measured = circuit.copy()
    measured.measure_all()
    compiled = transpile(measured, basis_gates=BASIS, optimization_level=1, seed_transpiler=seed)
    return Run(encoding, circuit, compiled, probabilities, values.tolist(), evaluations,
               perf_counter() - start, energy, seed, shots)


def sample(run, noise="ideal"):
    if not run.circuit.num_qubits:
        return {"": run.shots}
    simulator = AerSimulator(method="statevector", noise_model=make_noise(noise),
                             max_parallel_threads=1)
    return simulator.run(run.transpiled, shots=run.shots, seed_simulator=run.seed).result().get_counts()


def measured_results(problem, run, counts, optimum):
    enc = run.encoding
    decision_mask = (1 << enc.logical_bits) - 1
    feasible_shots = optimum_shots = 0
    histogram = {}
    best = best_bits = None
    total_objective = 0
    for bits, count in counts.items():
        number = int(bits, 2) if bits else 0
        decision = number & decision_mask
        if decision not in enc.feasible:
            chosen = enc.decode(number)
            errors = ["one_hot"] if chosen is None else evaluate(problem, chosen).violations
            for error in errors:
                histogram[error] = histogram.get(error, 0) + count
            continue
        result = evaluate(problem, enc.feasible[decision])
        feasible_shots += count
        total_objective += count * result.objective
        if result.objective == optimum:
            optimum_shots += count
        if best is None or result.objective < best.objective:
            best, best_bits = result, bits
    return {
        "feasible_probability": feasible_shots / run.shots,
        "optimal_probability": optimum_shots / run.shots if optimum is not None else None,
        "expected_feasible_objective": total_objective / feasible_shots if feasible_shots else None,
        "best": best, "best_bits": best_bits, "violations": histogram,
    }
