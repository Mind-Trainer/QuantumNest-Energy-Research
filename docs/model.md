# Model and encoding

## Physical optimization

Slots last one hour. Power is quantized to 0.1 kW, so one slot at integer power 1 uses 0.1 kWh. Tariffs are integer cents/kWh in a configurable currency. Use nonnegative import tariffs and assume PV serves local demand first, with curtailed surplus and no exports. Equipment input ratings represent average power over active slots.

For each service group a, enumerate valid patterns k with active slots S(a,k). For a cycle of duration d, S is the consecutive interval starting at t and ending just before t+d. For an interruptible service, S is a subset of sufficient cardinality from the permitted hours. A fixed-power EV requires ceil(100 E/(P η)) slots, where E is in 0.1 kWh, P is in 0.1 kW and η is the percent AC-to-battery efficiency. At initial planning the delivery overshoot is strictly below one slot's battery energy. The rolling model credits executed slots and can retain already delivered energy after a target reduction. Essential appliances have one configured pattern. Unavailability and safety lockouts are explicit unavailable slots. Latest completion is exclusive.

Let x(a,k) be binary and each group have exactly one active pattern. For cycles x is the start-time variable. Operating state u(a,t) is the sum of x(a,k) over patterns covering t. With baseline b(t), demand L(t) = b(t) + sum over a of P(a)u(a,t). For PV scenario s, import G(s,t) = max(0,L(t) − PV(s,t)). Self-use is min(L(t),PV(s,t)). Let M(s) = max over t of G(s,t).

- Feeder constraint. L(t) ≤ F(t).
- Transformer constraint. G(s,t) + N(t) ≤ C(t) for every s,t. N(t) is exogenous nonnegative background import. If N(t) > C(t), the input is infeasible.
- Service and EV constraints. Exactly one generated pattern per appliance, with all operating, availability, duration and energy conditions embodied in the pattern.
- Common decisions. Every scenario uses the same appliance schedule. Realized PV is recourse in the import balance, and rolling reoptimization changes future service choices once forecasts or requirements are revised.

Let w(s)>0 be integer scenario weights, W their sum, c(t) the tariff, λ a peak price in cents/kW, and ρ(a,k) the disutility in cents. The latter is priority times the absolute distance in hours between first operation and the preferred hour. The common integer objective is

```text
J = Σ_s w(s) [ Σ_t c(t) G(s,t) + λ M(s) ] + 10 W Σ_a,k ρ(a,k)x(a,k)
```

Divide J by 10 W to express the objective in cents. Expected electricity cost excludes the peak and inconvenience terms. Scenario-weighted solar self-consumption is total weighted PV used divided by total weighted PV availability. Minimizing nonnegative-cost imports also favors self-use with fixed service energy, though the model gives PV self-use no separate reward. Full-house dispatch uses the same objective for all solvers. CP-SAT uses bounded integer imports and peaks. HiGHS uses continuous import and peak auxiliary variables with integer decisions, since their componentwise smallest values are the physical imports and peaks. Metrics are recomputed from the validated schedule, including at zero-price ties.

The transformer approximation concerns active power at hourly resolution. Power factor, phase imbalance, voltage, transient load, bidirectional flows, and transformer thermal dynamics require a richer distribution model. The AC scenario uses a labeled fixed-power thermal proxy with preferred hours. Its results contain no temperature, comfort, COP, or pre-cooling claims.

## Certifying an exact small QUBO

Project out imports in the capacity constraints. If background alone fits, the exact gross load limit is U(t) = min(F(t), min_s[PV(s,t)+C(t)−N(t)]). With nonnegative loads, patterns that exceed U on their own and pairs that exceed U together are forbidden. Capacity is pairwise representable when every invalid complete choice contains a forbidden unary or pair. Exhaustive verification of the small pattern domain checks this property. Three or more groups can require a higher-order conflict even when no pair fails. Use classical optimization or the exact-slack route when the objective is still quadratic.

Fix a reference pattern tuple r. Each candidate's linear coefficient is the difference J(r with choice a) − J(r). Each cross-group coefficient is the residual of the joint change after subtracting both linear terms. The compiler checks reconstructed energy on all tuples, including capacity-infeasible tuples, and rejects higher-order objective interactions. Any function of two pattern groups has this exact representation on one-hot assignments. With fixed terms substituted there may be fewer variable groups. For general residential models prefer classical dispatch.

Let B be the sum of absolute nonconstant coefficients of the unpenalized objective. The objective range over binary strings is at most B. Using P = 1 + B therefore makes any integer penalty violation worse than every zero-penalty physical solution. Standard X variants add P(Σ_k x(a,k) − 1)² and P per unary or pair conflict. For XY circuits, one-hot weight holds throughout ideal evolution and those terms can be omitted. Off-subspace ground energies of the XY Hamiltonian have no scheduling interpretation, and physical validation rejects invalid noisy measurements. Hybrid preprocessing deletes forbidden unary options and iteratively removes an option if any other group has no compatible choice. It substitutes singleton groups and preserves every physical feasible schedule. A zero-qubit output can represent a completely fixed schedule, as distinct from a zero bitstring with unmet one-hot requirements.

### Explicit capacity slack

The separate `standard_slack` path keeps the X mixer and encodes each nonredundant capacity row via P(Σ aᵢxᵢ + S − U′)², where U′ = U − baseline. With integer row coefficients, divide by their common gcd and floor the upper bound first. Represent slack with binary widths U′.bit_length(), in the scaled units, giving 0 through at least U′. A feasible row can be assigned exact slack. An overloaded row cannot have a zero residual. Extra representable values incur a squared penalty as needed. Choose one-hot and slack penalties together by the same B bound. Measurements are assessed using physical decisions, marginalizing auxiliary bits. The slack demonstration records the full measured bitstrings.

## Ising and circuit construction

Use upper triangular Q, with xᵀQx = Σ_i Qii xᵢ + Σ_i<j Qij xᵢxⱼ. In a symmetric representation off-diagonal entries would each hold half this value. Under xᵢ = (1−Zᵢ)/2, using c for the original constant:

```text
offset = c + Σ_i Qii/2 + Σ_i<j Qij/4
h_i    = −Qii/2 − Σ_j≠i Q_min(i,j),max(i,j)/4
J_ij   = Qij/4
H      = offset I + Σ_i h_i Z_i + Σ_i<j J_ij Z_i Z_j
```

Each Qiskit Pauli label has qubit 0 on the right. The same qubit is the least significant bit of the integer measurement. The cost circuit applies RZ(2γhᵢ/s) and RZZ(2γJᵢⱼ/s) where s is the largest nonzero coefficient magnitude or 1. The constant contributes a global phase and is retained in reported QUBO energies. Standard starts in a Hadamard product and applies RX(2β) per qubit.

For XY, use an initialized feasible rectangular subset of pattern tuples. Each group has a uniform W state over its local subset, prepared by excitation-transfer gates and phase corrections. Every product basis state initially satisfies capacity. The mixer applies an ordered ring of Qiskit XXPlusYY gates at angle 2β. Each gate acts only within the span of 01 and 10 and leaves 00 and 11 unchanged. Each group excitation count therefore remains exactly one. Diagonal cost gates also preserve this count. The ring can move into a capacity-conflicting tuple, and capacity is penalized and checked after measurement. This is a product of edge gates with its given ordering.

## Pseudocode

```text
generate contiguous, interruptible, EV and fixed service patterns
derive scenario-wise imports, peak and projected capacity
if classical:
    solve the hierarchical pattern problem with the selected baseline
else:
    build unary/pair conflicts and optionally prune unsupported options
    construct and certify the exact quadratic objective on the pattern domain
    add one-hot penalties for X and capacity penalties for every variant
    map the QUBO to its Ising Hamiltonian
    prepare the initial state and alternate cost and mixer layers
    optimize γ, β from actual Qiskit statevector expectations
    transpile and execute measurements with Aer
    decode the most significant bit first and validate physical feasibility
return schedule, metrics and any violations
```

For rolling optimization, the caller supplies recorded PV, the prior cutoff, and the previous plan. Completed slots and ongoing cycles are locked, essential profiles remain immutable, and future slots are optimized classically. The client can revise the forecast, add pending requests, or change energy and preferences where permitted. Completed-slot PV and physical inputs are fixed across scenarios. The previous plan's objective uses its original forecasts. Explanations for changed hours check the previous hours against all other updated dispatch choices, and report a service conflict, a capacity conflict, an objective change, or a tie.

## Complexity and boundary

Let T be hours, S be scenarios, n the total number of patterns, g the number of groups, and K the product of group sizes. A group may need binomial(T,k) interruptible patterns. Conflict generation has worst-case O(n²T) time. Pairwise certification evaluates O(K) tuples with physical evaluation O(gT+ST) and pair checks O(g²). This is a correctness-oriented compiler for small subproblems. Its validation already computes the feasible tuple set, so its timing includes real classical work that would let an exact small-instance solver return the best schedule directly.

For q encoded bits, exact statevectors require O(2^q) memory. The straightforward circuit has O(p(q²+q)) gates for p layers in the dense case, and statevector evolution is proportional to 2^q times gate count per expectation evaluation. Transpilation uses an unconstrained topology and the rz, sx, x, cx basis. Classical MILP and CP-SAT may have exponential worst cases. The community CP-SAT model couples household import variables with one shared transformer constraint per hour and solar scenario. Household background values model independent local restrictions and should exclude other jointly optimized homes unless that restriction is intentional.
