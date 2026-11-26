"""Synthetic, independent gate depolarization and symmetric readout flips."""
from qiskit_aer.noise import NoiseModel, ReadoutError, depolarizing_error

NOISE = {
    "ideal": (0., 0., 0.),
    "gate_1q": (.003, 0., 0.),
    "gate_2q": (0., .02, 0.),
    "readout": (0., 0., .02),
    "combined": (.003, .02, .02),
}
BASIS = ["rz", "sx", "x", "cx"]


def make_noise(name):
    p1, p2, pr = NOISE[name]
    if name == "ideal":
        return None
    model = NoiseModel(basis_gates=BASIS)
    if p1:
        model.add_all_qubit_quantum_error(depolarizing_error(p1, 1), ["sx", "x"])
    if p2:
        model.add_all_qubit_quantum_error(depolarizing_error(p2, 2), ["cx"])
    if pr:
        model.add_all_qubit_readout_error(ReadoutError([[1-pr, pr], [pr, 1-pr]]))
    return model
