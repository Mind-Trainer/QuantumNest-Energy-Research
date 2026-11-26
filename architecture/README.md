# System architecture

![Rendered system architecture](system.svg)

The browser and the Python API share one origin. The app uses the same service-pattern generator and evaluator as the experiment runner. The classroom community display reads only its isolated source-derived JSON fixture.

```mermaid
sequenceDiagram
    participant Browser
    participant API as FastAPI
    participant Model as Physical model
    participant Engine as CP-SAT or Qiskit
    Browser->>API: GET scenario
    API-->>Browser: Typed house and forecast profiles
    Browser->>API: POST solve with solver and options
    API->>Model: Construct service patterns
    Model->>Engine: Choices and physical constraints
    Engine->>Model: Selected patterns or shot bitstrings
    Model-->>API: Validated schedule and metrics
    API-->>Browser: Schedule, validation and circuit evidence
```

## Modeling, optimization and measurement

```mermaid
flowchart LR
    A["Appliances, windows, tariffs and solar scenarios"] --> B["Start-time and service-pattern domains"]
    B --> C["Physical load, import, peak and capacity model"]
    C --> D["Shared classical model"]
    D --> D1["Exact enumeration / HiGHS MILP / OR-Tools CP-SAT"]
    D --> D2["Greedy / random feasible rejection / annealing"]
    C --> E["Certify pairwise capacity and quadratic objective"]
    E --> F["One-hot QUBO with unary and pair terms"]
    F --> G["x → (1 - Z) / 2"]
    G --> H["Constant + Z + ZZ Ising expansion"]
    H --> I["H + X, or feasible W product + XY"]
    I --> J["Qiskit statevector and COBYLA"]
    J --> K["Transpile and measure in Aer"]
    K --> L["Decode and validate physical schedule"]
    D1 --> L
    D2 --> L
```

## Rolling update

```mermaid
flowchart LR
    A["Previous plan and execution cutoff"] --> C["Verify previous plan and lock history"]
    B["Realized PV and revised forecast or service"] --> C
    C --> D["Lock ongoing non-interruptible cycles"]
    D --> E["Compute remaining charging and service"]
    E --> F["CP-SAT on admissible remaining patterns"]
    F --> G["Feasible revision or explicit infeasibility"]
    G --> H["Compare original hours and explain local counterfactuals"]
```

## Experimental validation

```mermaid
flowchart LR
    A["Exact physical schedules"] --> E["Compare objectives"]
    B["QUBO and Ising spectrum"] --> E
    C["Independent state evolution"] --> F["Check Qiskit probabilities"]
    D["Aer shots"] --> F
    E --> G["Run seeded common-instance experiment"]
    F --> G
    G --> H["Store inputs, counts, angles and metrics"]
    H --> I["Compute sample SD, 95% intervals and paired effects"]
```

Regenerate the overview SVG with `dot -Tsvg architecture/system.dot -o architecture/system.svg`. Circuit images are exported from executed circuits with `python -m experiments.demonstrations`.
