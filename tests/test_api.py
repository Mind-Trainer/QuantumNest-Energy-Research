from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def test_dashboard_and_live_quantum():
    assert "Residential energy dispatch" in client.get("/").text
    assert client.get("/assets/app.js").status_code == 200
    h = client.get("/api/scenario/research-8").json()
    classical = client.post("/api/solve", json={"house": h}).json()
    assert classical["status"] == "optimal"
    request = {"house": h, "solver": "hybrid", "noise": "combined", "shots": 64}
    response = client.post("/api/solve", json=request)
    assert response.status_code == 200
    q = response.json()
    assert q["mode"] == "qiskit_aer"
    assert sum(q["counts"].values()) == q["shots"]
    assert q["circuit"] and q["transpiled"] and q["labels"]
    assert len(q["matrix"]) == q["decision_qubits"]
    assert q["comparison_optimum"] == classical["result"]["objective"]
    assert client.post("/api/solve", json=request | {"layers": 40}).status_code == 422


def test_reschedule_endpoint():
    house = client.get("/api/scenario/normal").json()
    before = client.post("/api/solve", json={"house": house}).json()["result"]["schedule"]
    cutoff = before["Washing machine"][0] + 1
    payload = {"original": house, "changed": house, "schedule": before,
               "cutoff": cutoff, "actual_pv": house["solar"][0]["pv"][:cutoff]}
    response = client.post("/api/reschedule", json=payload)
    assert response.status_code == 200
    assert response.json()["updated"]["schedule"]["Washing machine"] == before["Washing machine"]
    assert client.post("/api/reschedule", json=payload | {"actual_pv": []}).status_code == 422
