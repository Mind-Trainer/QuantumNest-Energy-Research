# Legacy community demonstration

The [original repository](https://github.com/Mind-Trainer/Quantum/tree/748e1749090d8494ee57ac6cbb6f88313ee8afb9) describes community facilities, maintenance operations and utilities. The local dashboard preserves these concepts in a separate demonstration and reuses the historical tariff and normalized solar arrays extracted from `src/lib/qubo-engine.ts`. The arrays are labeled synthetic and remain outside the research model and results. The original README identified the project as MIT licensed.

| Upstream finding | Independently observed | Treatment |
|---|---|---|
| TypeScript solver substitutes handcrafted interference | `solveQaoaSimulator` constructs trigonometric weights directly and selects the modal state | Replaced in the research module by genuine Qiskit circuit evolution and seeded Aer shot measurements |
| Benchmark evidence is hard-coded | `BenchmarksView.tsx` embeds timings, energy and feasibility series | Replaced with generated raw runs, statistics, and plots |
| Synthetic runtime and derived metrics | `syntheticSimulatorOverheadMs` is summed into measured duration, solar utilization depends on qubit count | Uses wall-clock measured phases and recomputed power balances |
| Python “QAOA” scripts execute classical search | Amenity and routing scripts loop through all bitstrings. The energy script falls back to full classical enumeration | Archived upstream by reference. Research modes label classical and quantum paths explicitly |
| All-zero solution | The standalone energy matrix has positive diagonal and overlap penalties with no service reward or one-hot requirement | Enforced one service pattern per appliance with exact validation |
| Ising coefficient and ordering inconsistencies | Symmetric Q has both entries for a pair, while the converter reads only one; constant is omitted and label ordering differs from input indexing | Upper-triangular convention, explicit offset and exact spectrum test |
| Continuity and capacity omissions | Independent per-hour load bits can satisfy duration by disjoint hours. The old pair overlap penalties approximate rather than enforce transformer limits | Cycles are full consecutive patterns. Feeder and transformer import are evaluated for every hour and scenario |

The upstream repository was read through its authenticated GitHub connection. Source commit `748e1749090d8494ee57ac6cbb6f88313ee8afb9` is the sole source anchor for the reused arrays.
