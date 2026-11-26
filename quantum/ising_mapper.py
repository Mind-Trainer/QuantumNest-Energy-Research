"""Map upper-triangular QUBO coefficients to Qiskit's least-significant-qubit ordering."""
from dataclasses import dataclass

import numpy as np
from qiskit.quantum_info import SparsePauliOp


@dataclass
class Ising:
    offset: float
    fields: np.ndarray
    couplings: dict[tuple[int, int], float]

    def operator(self):
        n = len(self.fields)
        terms = [("I" * n, self.offset)]
        for indices, coefficient in [
            *(([i], v) for i, v in enumerate(self.fields) if v), *self.couplings.items()
        ]:
            label = ["I"] * n
            for i in indices:
                label[n - 1 - i] = "Z"
            terms.append(("".join(label), coefficient))
        return SparsePauliOp.from_list(terms)


def to_ising(qubo):
    fields = -0.5 * np.array(qubo.linear, dtype=float)
    couplings = {}
    offset = qubo.constant + sum(qubo.linear) / 2
    for (i, j), value in qubo.pairs.items():
        term = value / 4
        fields[i] -= term
        fields[j] -= term
        couplings[i, j] = term
        offset += term
    return Ising(offset, fields, couplings)
