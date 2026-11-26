"""Replan future slots with prior dispatch and metered solar treated as fixed history."""
from dataclasses import asdict

from classical.solvers import solve
from scheduling.model import House, Infeasible, evaluate, make_problem


def existing_selection(problem, schedule):
    if set(schedule) != {a.id for a in problem.house.appliances}:
        raise ValueError("Historical schedule must include each original appliance")
    result = []
    for a, group in zip(problem.house.appliances, problem.groups, strict=True):
        wanted = tuple(schedule[a.id])
        index = next((i for i, p in enumerate(group) if p.slots == wanted), None)
        if index is None:
            raise ValueError(f"{a.id}: previous schedule does not deliver the configured service")
        result.append(index)
    return tuple(result)


def revised_reason(problem, selection, index, slots, result):
    choices = problem.groups[index]
    old_index = next((i for i, pattern in enumerate(choices) if pattern.slots == slots), None)
    if old_index is None:
        return "Previous hours no longer meet the current service window, EV target or execution state."
    alternative = selection[:index] + (old_index,) + selection[index + 1:]
    prior = evaluate(problem, alternative)
    if prior.violations:
        return "With other updated services in place, the previous hours exceed " + ", ".join(prior.violations) + "."
    delta = (prior.objective - result.objective) / (10 * problem.house.weight)
    if delta > 0:
        return f"Keeping the previous hours with other updated services would add {delta:.2f} cents to the objective."
    if delta < 0:
        return "The time-limited solve changed these hours. A feasible alternative retains them at a lower objective."
    return "Both sets of hours have the same objective with other updated services in place."


def reschedule(original: House, changed: House, schedule, cutoff, actual_pv, previous_cutoff=0):
    if not 0 <= previous_cutoff <= cutoff <= original.hours or len(actual_pv) != cutoff:
        raise ValueError("Advance the current hour and supply one realized solar value per completed slot")
    if any(s.pv[:previous_cutoff] != actual_pv[:previous_cutoff] for s in original.solar):
        raise ValueError("Previously recorded solar history cannot be revised")
    if changed.hours != original.hours:
        raise ValueError("Keep the original dispatch horizon")
    previous_problem = make_problem(original, schedule, previous_cutoff)
    previous = evaluate(previous_problem, existing_selection(previous_problem, schedule))
    if previous.violations:
        raise ValueError("Previous plan is infeasible under its original assumptions")
    new_items = {a.id: a for a in changed.appliances}
    positions = {a.id: i for i, a in enumerate(changed.appliances)}
    for old in original.appliances:
        started = any(t < cutoff for t in previous.schedule[old.id])
        essential = old.kind == "essential"
        new = new_items.get(old.id)
        if (started or essential) and new is None:
            raise ValueError(f"{old.id}: cannot remove an executed or essential service")
        if new is None:
            continue
        if essential and new != old:
            raise ValueError(f"{old.id}: essential service is fixed")
        if started:
            fields = {"priority", "preferred", "energy"} if old.kind == "ev" else {"priority", "preferred"}
            if any(value != getattr(new, field) for field, value in old.model_dump().items() if field not in fields):
                raise ValueError(f"{old.id}: a started service may only change its remaining EV target or preferences")
    payload = changed.model_dump()
    for field in ("baseline", "tariff", "feeder", "transformer", "neighbor"):
        payload[field][:cutoff] = getattr(original, field)[:cutoff]
    for item in payload["solar"]:
        item["pv"][:cutoff] = actual_pv
    house = House.model_validate(payload)
    replanning = make_problem(house, previous.schedule, cutoff)
    solution = solve(replanning, "cp_sat")
    if solution.selection is None:
        if solution.status == "infeasible":
            raise Infeasible("Replanning is infeasible with realized history, capacity and service deadlines")
        raise RuntimeError(f"Replanning did not find a solution ({solution.status})")
    updated = evaluate(replanning, solution.selection)
    changes = []
    for a in original.appliances:
        old = previous.schedule[a.id]
        new = updated.schedule.get(a.id)
        if new is None:
            state, reason = "removed", "Pending request was withdrawn."
        elif a.kind == "essential":
            state, reason = "fixed", "Essential load stays on its configured profile."
        elif max(old) < cutoff and new == old:
            state, reason = "completed", "Delivered service is part of the fixed history."
        elif a.kind in {"cycle", "thermal_proxy"} and min(old) < cutoff:
            state, reason = "ongoing", "Started cycle must finish without interruption."
        elif old == new:
            state, reason = "retained", "The optimized revision retained these hours."
        else:
            state = "replanned"
            reason = revised_reason(replanning, solution.selection, positions[a.id], old, updated)
            if new_items[a.id].energy != a.energy:
                reason += " The EV energy target changed."
        changes.append({"id": a.id, "previous": old, "updated": new, "state": state, "reason": reason})
    old_ids = set(previous.schedule)
    for a in changed.appliances:
        if a.id not in old_ids:
            changes.append({"id": a.id, "previous": None, "updated": updated.schedule[a.id],
                            "state": "new", "reason": "New service request scheduled within the remaining horizon."})
    return {"previous": asdict(previous), "updated": asdict(updated), "house": house.model_dump(),
            "changes": changes, "cutoff": cutoff, "status": solution.status}
