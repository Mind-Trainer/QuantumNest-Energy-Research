import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def widget(widgets, label):
    return next(item for item in widgets if item.label == label)


def test_household_dispatch_and_rolling_update():
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    widget(app.button, "Optimize schedule").click().run()
    assert not app.exception
    house, previous, _, _, status = app.session_state["plan"]
    assert status == "optimal"
    assert widget(app.metric, "Expected peak import").value.endswith("kW")
    assert not previous["violations"]

    draft = json.loads(widget(app.text_area, "Household JSON").value)
    draft["appliances"][2]["preferred"] = 21
    widget(app.text_area, "Household JSON").set_value(json.dumps(draft))
    widget(app.button, "Apply configuration").click().run()
    assert not app.exception
    assert app.session_state["house"].appliances[2].preferred == 21

    hour = previous["schedule"]["Washing machine"][0] + 1
    widget(app.number_input, "Current hour").set_value(hour)
    widget(app.button, "Reoptimize remaining hours").click().run()
    assert not app.exception
    _, result, cutoff, changes, _ = app.session_state["plan"]
    assert cutoff == hour
    assert result["schedule"]["Washing machine"] == previous["schedule"]["Washing machine"]
    assert result["imports"]
    assert not result["violations"]
    assert any(change["state"] == "ongoing" for change in changes)
    assert app.success[-1].value.startswith("Plan updated")


def test_quantum_simulation_and_research_results():
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    widget(app.radio, "Workspace").set_value("Quantum lab").run()
    widget(app.selectbox, "Noise model").set_value("Combined synthetic noise")
    widget(app.selectbox, "Shots").set_value(64)
    widget(app.button, "Run Qiskit simulation").click().run()
    assert not app.exception
    _, q = app.session_state["quantum_run"]
    assert q["mode"] == "qiskit_aer"
    assert q["noise"] == "combined"
    assert sum(q["counts"].values()) == 64
    assert q["matrix"] and q["transpiled"]
    assert widget(app.metric, "Decision qubits").value == str(q["decision_qubits"])

    widget(app.radio, "Workspace").set_value("Experiment results").run()
    widget(app.selectbox, "Study").set_value("noise").run()
    assert not app.exception
    assert len(app.dataframe[0].value) > 0
    assert "low95" in app.dataframe[0].value.columns
