"""Interactive QuantumNest dashboard using the same solvers as the HTTP API."""
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from backend.service import RunRequest, solve_schedule
from datasets.cases import small_slack_case
from datasets.scenarios import household_case, research_case
from scheduling.model import House
from scheduling.rolling import reschedule

RESULTS = Path(__file__).resolve().parent / "results"
HOUSEHOLDS = {
    "Normal operation": "normal",
    "Summer": "summer",
    "Winter": "winter",
    "Cloud cover": "cloudy",
    "Solar uncertainty": "uncertain",
    "High tariffs": "high_tariff",
    "High EV demand": "high_ev",
    "Limited transformer": "limited_transformer",
    "Preference change": "preference_change",
    "UCI measured baseline": "measured_uci",
}
CLASSICAL = {
    "CP-SAT": "cp_sat",
    "HiGHS MILP": "milp",
    "Exhaustive search": "exhaustive",
    "Greedy tariff": "greedy",
    "Random feasible": "random",
    "Simulated annealing": "annealing",
}
QUANTUM = {
    "Hybrid pruned XY": "hybrid",
    "Standard penalty X": "standard",
    "Constraint-preserving XY": "constrained",
    "Pruned X ablation": "pruned_x",
    "Explicit capacity slack": "standard_slack",
}
NOISE = {
    "Ideal": "ideal",
    "Single-qubit depolarization": "gate_1q",
    "Two-qubit depolarization": "gate_2q",
    "Readout errors": "readout",
    "Combined synthetic noise": "combined",
}
PALETTE = ["#206044", "#d68c32", "#314c63", "#8a5e73", "#737d4c"]


def hours_text(slots):
    return ", ".join(f"{hour:02d}:00" for hour in slots) if slots else "—"


def csv_name(value):
    needs_escape = value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
    return "'" + value if needs_escape else value


def show_schedule(house, result):
    rows = []
    schedule = result["schedule"]
    for appliance in house.appliances:
        active = set(schedule[appliance.id])
        rows.extend({"Appliance": appliance.id, "Hour": hour, "Service": "On" if hour in active else "Off"}
                    for hour in range(house.hours))
    chart = alt.Chart(pd.DataFrame(rows)).mark_rect(stroke="white", strokeWidth=1).encode(
        x=alt.X("Hour:O", title="Hour starting at", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("Appliance:N", sort=list(schedule), title=None),
        color=alt.Color("Service:N", scale=alt.Scale(domain=["On", "Off"], range=["#398358", "#edf3eb"])),
        tooltip=["Appliance", "Hour", "Service"],
    ).properties(height=max(85, 25 * len(schedule)))
    st.altair_chart(chart, width="stretch")
    table = pd.DataFrame([
        {"Appliance": a.id, "Type": a.kind, "Active hours": hours_text(schedule[a.id]),
         "EV energy (kWh)": result["ev_kwh"].get(a.id)}
        for a in house.appliances
    ])
    st.dataframe(table, hide_index=True)
    export = table.assign(Appliance=table["Appliance"].map(csv_name))
    st.download_button("Download schedule CSV", export.to_csv(index=False), "quantumnest-schedule.csv",
                       mime="text/csv", on_click="ignore")


def show_dispatch(house, result):
    solar_share = result["solar_share"]
    cards = st.columns(4)
    cards[0].metric("Electricity cost", f'{result["electricity_cents"] / 100:.2f}')
    cards[1].metric("Grid energy", f'{result["grid_kwh"]:.2f} kWh')
    cards[2].metric("PV self-consumption", "—" if solar_share is None else f"{solar_share:.1%}")
    cards[3].metric("Expected peak import", f'{result["expected_peak_kw"]:.2f} kW')
    weights = [s.weight for s in house.solar]

    def expected(profiles):
        return [sum(weight * power for weight, power in zip(weights, row, strict=True)) / (10 * house.weight)
                for row in zip(*profiles, strict=True)]

    st.subheader("Hourly power")
    power = pd.DataFrame({
        "Hour": range(house.hours),
        "Household demand": [v / 10 for v in result["demand"]],
        "Expected PV": expected([s.pv for s in house.solar]),
        "Expected grid import": expected(result["imports"]),
    })
    st.line_chart(power, x="Hour", y=["Household demand", "Expected PV", "Expected grid import"],
                  x_label="Hour starting at", y_label="Power (kW)", color=PALETTE[:3])
    with st.expander("Transformer headroom"):
        headroom = [
            (rating - neighbor - max(imports)) / 10
            for rating, neighbor, imports in zip(house.transformer, house.neighbor,
                                                 zip(*result["imports"], strict=True), strict=True)
        ]
        st.metric("Minimum headroom across PV scenarios", f"{min(headroom):.2f} kW")
        st.area_chart(pd.DataFrame({"Hour": range(house.hours), "Headroom": headroom}), x="Hour",
                      y="Headroom", y_label="Headroom (kW)", color=PALETTE[0])

    st.subheader("Appliance schedule")
    show_schedule(house, result)


def configuration(label):
    with st.expander("Edit household configuration"):
        st.caption("Power and energy fields use tenths of kW or kWh. Tariffs use cents per kWh. "
                   "Hour ranges end before the listed latest hour.")
        with st.form("house_configuration"):
            draft = st.text_area("Household JSON", value=st.session_state.house.model_dump_json(indent=2),
                                 height=320, key=f"config_{label}")
            if st.form_submit_button("Apply configuration"):
                try:
                    st.session_state.house = House.model_validate_json(draft)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("Configuration applied. Solve a fresh plan or use it in the next rolling update.")


def rolling_plan():
    house, result, cutoff, changes, status = st.session_state.plan
    st.subheader("Rolling reschedule")
    if cutoff == house.hours:
        st.info("All hours in this plan are complete.")
    else:
        st.caption("Apply the current configuration and update future solar from a chosen hour. "
                   "Completed hours and in-progress non-interruptible cycles are preserved.")
        with st.form("rolling_update"):
            col1, col2 = st.columns(2)
            next_hour = col1.number_input("Current hour", min_value=cutoff, max_value=house.hours,
                                          value=max(cutoff, house.hours // 2))
            sunlight = col2.slider("Remaining forecast (%)", 0, 100, 35)
            measured = st.text_input("Realized PV history (optional)", placeholder="One integer per completed hour",
                                     help="Comma-separated tenths of kW. Leave blank to use the plan's first "
                                          "PV scenario as a simulated observation.")
            reroute = st.form_submit_button("Reoptimize remaining hours")
        if reroute:
            future = st.session_state.house.model_copy(deep=True)
            for forecast in future.solar:
                forecast.pv[next_hour:] = [round(v * sunlight / 100) for v in forecast.pv[next_hour:]]
            try:
                actual = [int(v.strip()) for v in measured.split(",")] if measured.strip() else house.solar[0].pv[:next_hour]
                new = reschedule(house, future, result["schedule"], next_hour, actual, cutoff)
            except (ValueError, RuntimeError) as exc:
                st.error(str(exc))
            else:
                st.session_state.plan = (House.model_validate(new["house"]), new["updated"], new["cutoff"],
                                         new["changes"], new["status"])
                st.rerun()
    if changes is not None:
        st.success(f"Plan updated from hour {cutoff:02d}:00 · {status}")
        st.dataframe(pd.DataFrame([
            {"Appliance": item["id"], "Previous hours": hours_text(item["previous"]),
             "Updated hours": hours_text(item["updated"]), "State": item["state"], "Reason": item["reason"]}
            for item in changes
        ]), hide_index=True)


def dispatch_page():
    st.header("Residential energy dispatch")
    st.caption("Optimize appliance and EV demand around tariffs, solar generation and capacity.")
    selected = st.selectbox("Household scenario", HOUSEHOLDS)
    if st.session_state.get("scenario") != selected:
        st.session_state.scenario = selected
        st.session_state.house = household_case(HOUSEHOLDS[selected])
        st.session_state.pop("plan", None)
    house = st.session_state.house
    st.caption("Measured UCI baseline with synthetic dispatch assumptions." if selected == "UCI measured baseline"
               else "Synthetic household inputs.")
    configuration(selected)

    with st.form("classical_dispatch"):
        solver = st.selectbox("Classical solver", CLASSICAL)
        execute = st.form_submit_button("Optimize schedule", type="primary")
    if execute:
        try:
            with st.spinner("Solving the household model…"):
                house = st.session_state.house
                response = solve_schedule(RunRequest(house=house, solver=CLASSICAL[solver]))
        except ValueError as exc:
            st.error(str(exc))
        else:
            result = response["result"]
            if result is None:
                st.warning(f"No feasible schedule returned · {response['status']}")
            else:
                st.session_state.plan = (house, result, 0, None, response["status"])
    if "plan" in st.session_state:
        solved_house, result, _, _, status = st.session_state.plan
        st.caption(f"Current plan · {solved_house.name} · {status}")
        show_dispatch(solved_house, result)
        rolling_plan()


def show_quantum(house, run):
    columns = st.columns(4)
    columns[0].metric("Feasible shots", f'{run["feasible_probability"]:.1%}')
    optimum = run["optimal_probability"]
    columns[1].metric("Optimal shots", "—" if optimum is None else f"{optimum:.1%}")
    columns[2].metric("Decision qubits", run["decision_qubits"])
    columns[3].metric("Transpiled CX gates", run["two_qubit_gates"])
    st.caption(f'{house.name} · {run["solver"]} · {run["noise"]} · {run["shots"]} shots · '
               f'{run["layers"]} layer(s) · {run["seconds"]:.2f} s')
    result = run["result"]
    optimum_cost = run["comparison_optimum"]
    if optimum_cost is not None:
        factor = 10 * house.weight
        st.write(f"Classical optimum: {optimum_cost / factor:.2f} cents.")
        if result is not None:
            st.write(f"Best measured objective: {result['objective'] / factor:.2f} cents.")
    if result is None:
        st.warning("The measured shots contained no feasible schedule.")
    else:
        with st.expander("Decoded feasible schedule"):
            show_schedule(house, result)

    counts = pd.DataFrame(
        [{"Bitstring": bits or "∅", "Shots": count} for bits, count in run["counts"].items()]
    ).sort_values("Shots", ascending=False, ignore_index=True)
    st.subheader("Measurement distribution")
    st.caption("Most frequent outcomes, with Qiskit’s most significant bit shown first.")
    st.bar_chart(counts.head(20), x="Bitstring", y="Shots", sort=False, color=PALETTE[0])
    st.download_button("Download measured counts", counts.to_csv(index=False), "quantumnest-counts.csv",
                       mime="text/csv", on_click="ignore")

    with st.expander("QUBO matrix and executed circuits"):
        st.caption(f'Upper triangular QUBO. Constant offset {run["cost_offset"]}. '
                   f'{run["auxiliary_qubits"]} auxiliary qubits · transpiled depth {run["transpiled_depth"]}.')
        if run["labels"]:
            st.dataframe(pd.DataFrame(run["matrix"], index=run["labels"], columns=run["labels"]))
        else:
            st.info("Preprocessing fixed every choice.")
        st.markdown("**Parameterized circuit after optimization**")
        st.code(run["circuit"], language=None)
        st.markdown("**Transpiled measurement circuit**")
        st.code(run["transpiled"], language=None)


def quantum_page():
    st.header("Quantum lab")
    st.caption("Run Qiskit Aer locally and compare sampled schedules with the classical optimum. "
               "Noise models are synthetic.")
    cases = {f"{n} original decision bits": n for n in (6, 8, 10, 12, 14)}
    slack_fixture = "Three-slot slack fixture"
    cases[slack_fixture] = None
    if "house" in st.session_state:
        cases["Current household configuration"] = None
    with st.form("quantum_simulation"):
        left, right = st.columns(2)
        case = left.selectbox("Quantum problem", cases, index=1)
        solver = right.selectbox("Quantum encoding", QUANTUM,
                                 help="Use the three-slot fixture to test explicit capacity slack.")
        noise = left.selectbox("Noise model", NOISE)
        layers = right.number_input("QAOA layers", min_value=1, max_value=3, value=1)
        shots = left.selectbox("Shots", [64, 128, 256, 512, 1024, 2048], index=2)
        seed = right.number_input("Seed", min_value=0, max_value=2**31 - 1, value=0)
        execute = st.form_submit_button("Run Qiskit simulation", type="primary")
    if execute:
        st.session_state.pop("quantum_run", None)
        try:
            size = cases[case]
            if size is not None:
                house = research_case(size, seed)
            elif case == slack_fixture:
                house = small_slack_case().house
            else:
                house = st.session_state.house
            with st.spinner("Optimizing angles and sampling the circuit…"):
                response = solve_schedule(RunRequest(house=house, solver=QUANTUM[solver], noise=NOISE[noise],
                                                     layers=layers, shots=shots, seed=seed))
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.quantum_run = (house, response)
    if "quantum_run" in st.session_state:
        show_quantum(*st.session_state.quantum_run)


def research_page():
    st.header("Experiment results")
    st.caption("Recorded benchmark statistics across seeds. The intervals are descriptive.")
    path = RESULTS / "processed" / "summary.csv"
    if not path.is_file():
        st.info("Generate results with python -m experiments.run.")
        return
    data = pd.read_csv(path)
    left, right = st.columns(2)
    studies = sorted(data["study"].unique().tolist())
    study = left.selectbox("Study", studies, index=studies.index("scaling") if "scaling" in studies else 0)
    data = data[data["study"] == study]
    metrics = sorted(data["metric"].unique().tolist())
    metric = right.selectbox("Metric", metrics, index=metrics.index("feasible_probability") if "feasible_probability" in metrics else 0,
                             format_func=lambda name: name.replace("_", " "))
    view = data[data["metric"] == metric].copy()
    view["Configuration"] = view.apply(
        lambda row: f'{row["algorithm"]} · p={row["layers"]} · {row["noise"]}', axis=1,
    )
    axis = "layers" if study == "noise" else "size"
    color = "algorithm" if study != "noise" else "Condition"
    if study == "noise":
        view["Condition"] = view["algorithm"] + " · " + view["noise"]
    figure = alt.Chart(view).mark_line(point=True).encode(
        x=alt.X(f"{axis}:Q", title="QAOA layers" if study == "noise" else "Original decision bits",
                axis=alt.Axis(tickMinStep=1)),
        y=alt.Y("mean:Q", title=metric.replace("_", " ")),
        color=alt.Color(f"{color}:N", title=None),
        tooltip=["Configuration", "size", "n", "mean", "low95", "high95"],
    )
    st.altair_chart(figure, width="stretch")
    st.dataframe(view[["size", "Configuration", "n", "attempted", "mean", "sd", "low95", "high95"]],
                 hide_index=True)
    st.download_button("Download selected statistics", view.to_csv(index=False), "quantumnest-statistics.csv",
                       mime="text/csv", on_click="ignore")
    raw = RESULTS / "raw" / "runs.jsonl"
    if raw.is_file():
        st.download_button("Download raw runs", raw.read_bytes(), "runs.jsonl",
                           mime="application/jsonl", on_click="ignore")


def main():
    st.set_page_config(page_title="QuantumNest Energy Research", page_icon="◈", layout="wide")
    st.sidebar.title("QuantumNest")
    st.sidebar.caption("Energy Research")
    pages = {
        "Dispatch": dispatch_page,
        "Quantum lab": quantum_page,
        "Experiment results": research_page,
    }
    page = st.sidebar.radio("Workspace", pages)
    pages[page]()


if __name__ == "__main__":
    main()
