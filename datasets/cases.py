"""Designed validation fixtures with analytically understandable capacity conflicts."""
from scheduling.model import Appliance, House, Solar, make_problem


def small_slack_case():
    return make_problem(House(name="Three-slot slack validation", baseline=[1] * 3, tariff=[10, 20, 40],
                              feeder=[4] * 3, transformer=[5] * 3, neighbor=[1] * 3,
                              solar=[Solar(name="PV", weight=1, pv=[0, 1, 2])],
                              appliances=[Appliance(id=a, kind="cycle", power=2, earliest=0, latest=3)
                                          for a in ("washer", "pump")]))
