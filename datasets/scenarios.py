"""Synthetic scenarios. Values model hourly averages and can be edited in the UI."""
import json
from random import Random

from datasets.uci_baseline import PROFILE

from scheduling.model import Appliance, House, Solar


def research_case(qubits=8, seed=0):
    if qubits not in (6, 8, 10, 12, 14):
        raise ValueError("Use 6, 8, 10, 12 or 14 original decision bits")
    rng = Random(seed)
    options = qubits // 2
    baseline = [rng.randint(4, 8) for _ in range(24)]
    baseline[11] = 24
    feeder = [50] * 24
    feeder[11] = 45  # The EV cannot charge during this pre-existing peak.
    tariff = [rng.choice((17, 19, 22)) if 9 <= t < 16 else 27 for t in range(24)]
    tariff[10] = 9
    pv = [round(max(0, 1 - abs(t - 13) / 5) * rng.randint(16, 30)) for t in range(24)]
    return House(name=f"{qubits}-bit synthetic research case · seed {seed}", baseline=baseline,
                 tariff=tariff, feeder=feeder, transformer=[62] * 24, neighbor=[20] * 24,
                 solar=[Solar(name="Clear", weight=3, pv=pv),
                        Solar(name="Cloudier", weight=2, pv=[p // 3 for p in pv])],
                 appliances=[
                     Appliance(id="Washing machine", kind="cycle", power=18, earliest=9,
                               latest=10 + options, duration=2, preferred=10, priority=2),
                     Appliance(id="Electric vehicle", kind="ev", power=33, earliest=10,
                               latest=10 + options, energy=29, efficiency=90, preferred=12, priority=1)
                 ])


def household_case(style="normal", seed=0):
    h = research_case(14, seed)
    h.name = f"Synthetic {style.replace('_', ' ')} household"
    h.appliances += [
        Appliance(id="Dishwasher", kind="cycle", power=12, earliest=11, latest=23, duration=2,
                  forbidden=[17, 18], preferred=15, priority=1),
        Appliance(id="Water heater", kind="interruptible", power=15, earliest=6, latest=18, duration=2),
        Appliance(id="Water pump", kind="cycle", power=8, earliest=7, latest=21, preferred=14, priority=2),
        Appliance(id="Air-conditioning (thermal proxy)", kind="thermal_proxy", power=12,
                  earliest=14, latest=18, duration=2, preferred=15, priority=5),
        Appliance(id="Essential refrigeration", kind="essential", power=2, earliest=0, latest=24,
                  fixed=list(range(24))),
    ]
    if style == "summer":
        h.solar = [s.model_copy(update={"pv": [round(p * 1.4) for p in s.pv]}) for s in h.solar]
        h.baseline = [p + (3 if 12 <= t <= 19 else 0) for t, p in enumerate(h.baseline)]
    elif style in {"winter", "cloudy", "uncertain"}:
        h.solar = [s.model_copy(update={"pv": [round(p * (0.5 if style != "uncertain" else 0.15))
                                                for p in s.pv]})
                   if i > 0 or style != "uncertain" else s for i, s in enumerate(h.solar)]
    elif style == "high_tariff":
        h.tariff = [2 * p if 12 <= t < 19 else p for t, p in enumerate(h.tariff)]
    elif style == "high_ev":
        h.appliances[1] = h.appliances[1].model_copy(update={"energy": 85, "latest": 21})
    elif style == "limited_transformer":
        h.transformer = [56] * 24
    elif style == "preference_change":
        h.appliances[2] = h.appliances[2].model_copy(update={"preferred": 21, "priority": 8})
    elif style == "measured_uci":
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        h.name = "UCI measured residual · synthetic dispatch replay"
        h.baseline = profile["baseline"]
        h.appliances = [a for a in h.appliances if a.kind != "essential"]
    elif style != "normal":
        raise ValueError("Unknown synthetic household scenario")
    return House.model_validate(h.model_dump())
