"""Authoritative integer model and physical validation for hourly, import-only dispatch."""
from dataclasses import dataclass
from itertools import combinations
from math import comb

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

Unit = int  # 0.1 kW, or 0.1 kWh across one hourly slot


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Appliance(Input):
    id: str = Field(min_length=1, max_length=45)
    kind: Literal["cycle", "interruptible", "ev", "thermal_proxy", "essential"]
    power: int = Field(gt=0, le=500)
    earliest: int = Field(ge=0, le=47)
    latest: int = Field(ge=1, le=48)
    duration: int = Field(default=1, ge=1, le=48)
    forbidden: list[int] = Field(default_factory=list, max_length=48)
    preferred: int | None = Field(default=None, ge=0, le=47)
    priority: int = Field(default=0, ge=0, le=100)
    energy: int = Field(default=0, ge=0, le=24000)
    efficiency: int = Field(default=90, ge=1, le=100)
    fixed: list[int] = Field(default_factory=list, max_length=48)

    @model_validator(mode="after")
    def valid_service(self):
        if self.earliest >= self.latest or (self.kind == "ev" and self.energy == 0):
            raise ValueError(f"{self.id}: invalid service window or EV target")
        if self.kind == "essential":
            if not self.fixed or len(self.fixed) != len(set(self.fixed)):
                raise ValueError(f"{self.id}: essential load requires distinct fixed slots")
        elif self.fixed:
            raise ValueError(f"{self.id}: only essential loads accept fixed slots")
        if self.kind != "ev" and self.energy:
            raise ValueError(f"{self.id}: energy target is reserved for EVs")
        return self


class Solar(Input):
    name: str = Field(min_length=1, max_length=40)
    weight: int = Field(gt=0, le=1000)
    pv: list[int] = Field(max_length=48)


class House(Input):
    name: str = Field(min_length=1, max_length=75)
    baseline: list[int] = Field(min_length=1, max_length=48)
    tariff: list[int] = Field(max_length=48)
    feeder: list[int] = Field(max_length=48)
    transformer: list[int] = Field(max_length=48)
    neighbor: list[int] = Field(max_length=48)
    solar: list[Solar] = Field(min_length=1, max_length=8)
    appliances: list[Appliance] = Field(min_length=1, max_length=20)
    peak_price: int = Field(default=5, ge=0, le=1000)

    @property
    def hours(self):
        return len(self.baseline)

    @property
    def weight(self):
        return sum(s.weight for s in self.solar)

    @model_validator(mode="after")
    def validate_profiles(self):
        h = self.hours
        profiles = [self.baseline, self.tariff, self.feeder, self.transformer,
                    self.neighbor, *(s.pv for s in self.solar)]
        if any(len(v) != h or any(x < 0 or x > 10000 for x in v) for v in profiles):
            raise ValueError("Every profile must span the horizon with values in [0, 10000]")
        if len({a.id for a in self.appliances}) != len(self.appliances):
            raise ValueError("Appliance identifiers must be unique")
        if len({s.name for s in self.solar}) != len(self.solar):
            raise ValueError("Solar scenario names must be unique")
        for a in self.appliances:
            if a.latest > h or any(not 0 <= t < h for t in a.forbidden + a.fixed):
                raise ValueError(f"{a.id}: slot outside horizon")
        return self


@dataclass(frozen=True)
class Pattern:
    slots: tuple[int, ...]
    cost: int


@dataclass(frozen=True)
class Problem:
    house: House
    groups: tuple[tuple[Pattern, ...], ...]


@dataclass(frozen=True)
class Result:
    schedule: dict[str, tuple[int, ...]]
    objective: int | None
    violations: tuple[str, ...]
    demand: list[int]
    imports: list[list[int]]
    solar_used: list[list[int]]
    electricity_cents: float
    grid_kwh: float
    solar_kwh: float
    solar_share: float | None
    expected_peak_kw: float
    ev_kwh: dict[str, float]


class Infeasible(ValueError):
    pass


def pattern_cost(a, slots):
    return a.priority * abs(slots[0] - a.preferred) if a.preferred is not None else 0


def make_problem(house: House, frozen=None, cutoff=0) -> Problem:
    """Historical slots are fixed. Started cycles and essential profiles are frozen in full."""
    frozen = frozen or {}
    groups = []
    for a in house.appliances:
        allowed = set(range(a.earliest, a.latest)) - set(a.forbidden)
        old = tuple(frozen.get(a.id, ()))
        past = tuple(t for t in old if t < cutoff)
        transactional = a.kind in {"cycle", "thermal_proxy"}
        energy_per_slot = a.power * a.efficiency
        count = (100 * a.energy + energy_per_slot - 1) // energy_per_slot if a.kind == "ev" else a.duration
        if a.kind == "ev":
            count = max(count, len(past))
        if a.kind == "essential":
            # A configured essential profile is immutable through rolling updates.
            choices = [tuple(sorted(a.fixed))]
        elif transactional and past:
            choices = [old] if len(old) == count and set(old) <= allowed else []
        elif transactional:
            choices = [tuple(range(t, t + count)) for t in range(max(a.earliest, cutoff), a.latest - count + 1)
                       if set(range(t, t + count)) <= allowed]
        else:
            remaining = count - len(past)
            future = sorted(t for t in allowed if t >= cutoff)
            if not set(past) <= allowed or not 0 <= remaining <= len(future):
                choices = []
            else:
                if comb(len(future), remaining) > 50000:
                    raise ValueError(f"{a.id}: too many service patterns for the hourly pattern model")
                choices = (past + tail for tail in combinations(future, remaining))
        patterns = tuple(Pattern(slots, pattern_cost(a, slots)) for slots in choices)
        if not patterns or any(not set(p.slots) <= allowed for p in patterns):
            raise Infeasible(f"{a.id}: service cannot fit available slots")
        groups.append(patterns)
    return Problem(house, tuple(groups))


def evaluate(problem: Problem, selected: tuple[int, ...]) -> Result:
    h = problem.house
    if len(selected) != len(h.appliances) or any(
        not 0 <= i < len(group) for i, group in zip(selected, problem.groups, strict=False)
    ):
        raise ValueError("Invalid schedule pattern indices")
    patterns = [group[i] for group, i in zip(problem.groups, selected, strict=True)]
    schedule = {a.id: p.slots for a, p in zip(h.appliances, patterns, strict=True)}
    demand = list(h.baseline)
    delivered = {}
    violations = []
    for a, p in zip(h.appliances, patterns, strict=True):
        slots = p.slots
        if len(set(slots)) != len(slots) or any(t < a.earliest or t >= a.latest or t in a.forbidden for t in slots):
            violations.append(f"{a.id}:window")
        if a.kind in {"cycle", "thermal_proxy"} and slots != tuple(range(slots[0], slots[0] + a.duration)):
            violations.append(f"{a.id}:continuity")
        if a.kind == "interruptible" and len(slots) != a.duration:
            violations.append(f"{a.id}:duration")
        if a.kind == "essential" and slots != tuple(sorted(a.fixed)):
            violations.append(f"{a.id}:essential")
        if a.kind == "ev":
            delivered[a.id] = len(slots) * a.power * a.efficiency / 1000
            if len(slots) * a.power * a.efficiency < 100 * a.energy:
                violations.append(f"{a.id}:energy")
        for t in slots:
            demand[t] += a.power
    for t, load in enumerate(demand):
        if load > h.feeder[t]:
            violations.append(f"feeder@{t}")
    imports, solar_used = [], []
    bill = peak = pv = used = grid = 0
    for s in h.solar:
        grid_s = [max(0, d - p) for d, p in zip(demand, s.pv, strict=True)]
        used_s = [min(d, p) for d, p in zip(demand, s.pv, strict=True)]
        imports.append(grid_s)
        solar_used.append(used_s)
        for t, e in enumerate(grid_s):
            if e + h.neighbor[t] > h.transformer[t]:
                violations.append(f"transformer:{s.name}@{t}")
        bill += s.weight * sum(tariff * e for tariff, e in zip(h.tariff, grid_s, strict=True))
        peak += s.weight * max(grid_s)
        pv += s.weight * sum(s.pv)
        used += s.weight * sum(used_s)
        grid += s.weight * sum(grid_s)
    objective = bill + h.peak_price * peak + 10 * h.weight * sum(p.cost for p in patterns)
    scale = 10 * h.weight
    return Result(schedule, objective, tuple(violations), demand, imports, solar_used,
                  bill / scale, grid / scale, used / scale, used / pv if pv else None,
                  peak / scale, delivered)


def gross_bounds(house):
    """Exact per-slot equivalent limit after projecting out scenario imports."""
    if any(n > c for n, c in zip(house.neighbor, house.transformer, strict=True)):
        raise Infeasible("Neighbor load alone exceeds transformer capacity")
    return [min(house.feeder[t], *(s.pv[t] + house.transformer[t] - house.neighbor[t]
                                 for s in house.solar)) for t in range(house.hours)]
