from __future__ import annotations

from datetime import datetime
from typing import TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..contracts import Act, Candidate, ContractError, Decision, Payload, Snapshot, SolverResult, ValidationReport, digest
from ..explain import explanation, owner_conflict_note, relax_prompt, requirement_holders, requirement_note, room_conflict_event
from ..solver import RELAXABLE, relax_nudge
from .personal import build_personal, nudges, question_for, send_message

# Solver conflict → the research blocker a targeted search can act on, and what to search again.
RESEARCH_BLOCKER = {"HARD_LIMIT": "HARD_LIMIT", "BAGGAGE_UNAVAILABLE": "BAGGAGE_UNKNOWN",
                    "NO_CANDIDATES": "NO_MATCHING_CANDIDATE", "ARRIVAL_WINDOW": "ARRIVAL_OVERLAP",
                    "TRIP_LIMIT": "HARD_LIMIT", "STAY_UNAVAILABLE": "STAY_UNAVAILABLE",
                    "TRANSFER_UNAVAILABLE": "TRANSFER_UNAVAILABLE", "ARRIVAL_TIME": "ARRIVAL_OVERLAP",
                    "TOO_MANY_STOPS": "NO_MATCHING_CANDIDATE"}
RESEARCH_CATEGORIES = {"TRIP_LIMIT": ("FLIGHT", "STAY"), "STAY_UNAVAILABLE": ("STAY",), "TRANSFER_UNAVAILABLE": ("TRANSFER",)}


class CoordinatorState(TypedDict, total=False):
    job_id: str
    snapshot: dict
    candidates: list[dict]
    selected: list[str]
    round: int
    cycle: int
    draft_ref: str
    questions: list[str]
    outcome: str
    solver_status: str
    need_research: bool
    research_cause_ref: str
    proposal_ref: str
    research_target_owners: list[str]
    research_blocker_code: str
    validation_checks: list[str]
    # Room-safe only: refs and digests to replay the solver receipt, the group's
    # arrival window, and fares owners declined to pay extra for.
    solve_key: str
    solve_request: dict
    group_window: list
    excluded: list[str]
    resolve: bool
    research_unavailable: bool
    research_categories: list[str]
    pickup_time: str


def build_coordinator(backend, claim, model, research, solver, validator, clock, limits, checkpointer):
    def current():
        return backend.load_execution_context(claim)

    def briefs_for(snapshot):
        return [backend.read_brief(claim, o) for o in snapshot.members]

    def ask_to_relax(snapshot, conflicts, briefs):
        """When an owner's own room requirement blocks the plan, their agent asks
        them privately whether to set it aside. Each owner is asked once."""
        by_owner, asked = {b.owner: b for b in briefs}, []
        for conflict in conflicts:
            if conflict.requirement not in RELAXABLE:
                continue
            field = RELAXABLE[conflict.requirement][0]
            for holder in requirement_holders(conflict, briefs):
                if f"kept_{field}" in by_owner[holder].answered:
                    continue  # they already said they need it
                question = question_for(claim, holder, "MISSING_FACT", f"relax:{conflict.requirement.lower()}",
                                        relax_prompt(conflict, by_owner[holder]), relax_nudge(conflict.requirement))
                try:
                    backend.get_question(claim, question.id)
                except KeyError:
                    asked.append(backend.ask_owner(claim, question))
        return sorted(set(asked))

    def solution(state):
        # Impacts are private, so they live only in the solver receipt, not in state.
        def missing():
            raise ContractError("SOLVER_RECEIPT_MISSING")
        return SolverResult.model_validate(backend.operation(claim, state["solve_key"], state["solve_request"], "local", missing))

    def load(state):
        snapshot = current()
        questions = []
        for owner in snapshot.members:
            # Onboarded rooms confirm settings in each person's onboarding summary instead.
            if not snapshot.onboarding and not backend.read_brief(claim, owner).preferences.confirmed:
                questions.append(backend.ask_owner(claim, question_for(claim, owner, "PARTICIPATION", "participation-settings")))
        return {"snapshot": snapshot.model_dump(mode="json"), "round": 0, "cycle": 0, "questions": questions,
                "candidates": [], "outcome": "", "need_research": False}

    def discover(state):
        snapshot = current()
        if state["round"] == 0 and not state["candidates"]:
            reused = backend.reusable_candidates(claim)
            if reused:
                return {"candidates": candidates_data(reused), "need_research": False}
        purpose = "INITIAL" if state["round"] == 0 else "TARGETED"
        # Stable observation/request time across a replay, persisted in the op receipt.
        stamp = backend.operation(claim, f"research-time:{state['round']}", {}, "local", clock.now)
        trip = len(snapshot.components) > 1
        initial = [*snapshot.components, "ACTIVITY", "DINNER"] if trip else ["FLIGHT"]
        routes, facts = [], None
        if snapshot.onboarding:
            # Search from what people confirmed; wait until everyone has said where they fly from.
            briefs = {o: backend.read_brief(claim, o) for o in snapshot.members}
            if any(b.origin_airport is None for b in briefs.values()):
                return {"outcome": "WAITING_FOR_INTAKE"}
            routes = [{"owner": o, "origin": b.origin_airport, "destination": snapshot.destination_airport,
                       "date": snapshot.start_date, "max_stops": b.max_stops} for o, b in briefs.items()]
            facts = {"destination": snapshot.destination, "check_in": snapshot.start_date, "check_out": snapshot.end_date,
                     "party_size": len(snapshot.members), "pickup_time": state.get("pickup_time")}
            # The airport transfer is searched once flights are chosen, for the group's latest landing.
            initial = [c for c in initial if c != "TRANSFER"]
        request = {"purpose": purpose, "members": list(snapshot.members), "scope": "TRIP" if trip else "FLIGHTS_ONLY",
            "categories": initial if purpose == "INITIAL" else state.get("research_categories") or ["FLIGHT"],
            "routes": routes, "trip": facts,
            "room_id": snapshot.room_id,
            "input_revision": snapshot.input_revision, "permission_revision": snapshot.permission_revision, "observed_at": stamp,
            "blocker_message_ref": state.get("research_cause_ref", ""),
            "target_owners": state.get("research_target_owners") or list(snapshot.members),
            "blocker_code": state.get("research_blocker_code", "NONE")}
        # No private caps, reasons or transcripts leave through ResearchPort.
        try:
            raw = backend.operation(claim, f"research:{state['round']}", request, "research", lambda: research.search(request))
        except ContractError as error:
            if purpose != "TARGETED" or error.code != "TARGETED_SEARCH_NOT_AUTHORIZED":
                raise
            # No approved search could change the result: explain instead of failing.
            return {"need_research": False, "research_unavailable": True}
        candidates = {c["id"]: Candidate.model_validate(c) for c in state["candidates"]}
        for item in raw:
            candidate = Candidate.model_validate(item)
            if candidate.observed_at > clock.now() or candidate.valid_until <= clock.now():
                continue
            if candidate.id in candidates and candidates[candidate.id] != candidate:
                raise ContractError("CANDIDATE_ID_CONFLICT")
            candidates[candidate.id] = candidate
        fresh = dedupe(candidates.values())
        backend.store_candidates(claim, fresh)
        return {"candidates": candidates_data(fresh), "need_research": False}

    def solve(state):
        snapshot = current()
        if snapshot.onboarding and set(snapshot.onboarded) != set(snapshot.members):
            # Searched early; nothing is proposed until everyone has confirmed their onboarding.
            return {"outcome": "WAITING_FOR_INTAKE", "solver_status": "", "selected": []}
        excluded = set(state.get("excluded", ()))
        candidates = [Candidate.model_validate(c) for c in state["candidates"] if c["valid_until"] > clock.now() and c["id"] not in excluded]
        briefs = briefs_for(snapshot)
        relaxations = digest([b.model_dump(mode="json") for b in briefs])[:12]
        key = f"solve:{state['round']}:{len(excluded)}:{relaxations}"
        request = {"snapshot": claim.snapshot_ref, "candidates": digest(candidates_data(candidates)), "briefs": relaxations}
        # Onboarded rooms choose flights (and the stay) first; the transfer is then searched
        # for the group's actual latest landing and the plan solved again with it.
        plan_for = snapshot
        if snapshot.onboarding and "TRANSFER" in snapshot.components and not any(c.category == "TRANSFER" for c in candidates):
            plan_for = snapshot.model_copy(update={"components": tuple(c for c in snapshot.components if c != "TRANSFER")})
            key += ":before-transfer"
        raw = backend.operation(claim, key, request, "local", lambda: solver.solve(plan_for, briefs, candidates))
        result = SolverResult.model_validate(raw)
        draft = "draft-" + digest([list(result.selected), snapshot.input_revision, snapshot.membership_revision])[:20]
        outcome = "" if result.status in {"OPTIMAL", "FEASIBLE", "INFEASIBLE"} else result.status
        update = {"selected": list(result.selected), "draft_ref": draft, "solver_status": result.status, "outcome": outcome,
                  "solve_key": key, "solve_request": request, "resolve": False, "group_window": []}
        if result.group:
            zone = next(c.arrival_timezone for c in candidates if c.id == result.selected[0])
            update["group_window"] = [result.group.earliest_arrival, result.group.latest_arrival, zone]
            if plan_for is not snapshot:
                return {**update, "need_research": True, "research_categories": ["TRANSFER"],
                        "research_blocker_code": "TRANSFER_UNAVAILABLE", "research_target_owners": list(snapshot.members),
                        "pickup_time": pickup_after(result.group.latest_arrival, zone)}
        if result.status == "INFEASIBLE":
            owners = sorted({c.owner for c in result.conflicts if c.owner})
            categories = sorted({k for c in result.conflicts for k in RESEARCH_CATEGORIES.get(c.code, ("FLIGHT",))})
            update.update(research_target_owners=owners or list(snapshot.members), research_categories=categories or ["FLIGHT"],
                research_blocker_code=RESEARCH_BLOCKER[result.conflicts[0].code] if result.conflicts else "NO_MATCHING_CANDIDATE",
                questions=ask_to_relax(snapshot, result.conflicts, briefs))
        return update

    def dialogue(state):
        snapshot = current()
        candidates = {c["id"]: c for c in state["candidates"]}
        selected = {candidates[r]["owner"]: candidates[r] for r in state["selected"] if candidates[r]["owner"]}
        shared = [candidates[r] for r in state["selected"] if not candidates[r]["owner"]]
        if set(selected) != set(snapshot.members):
            raise ContractError("INVALID_COVERAGE")
        result = solution(state)
        impacts = {i.owner: i.model_dump(mode="json") for i in result.impacts}
        share = result.group.share_each_cents if result.group else 0
        # Before the agents talk, ask everyone at once about anything this plan depends
        # on that they never said (one question per topic, recorded as their answer).
        nudged = [backend.ask_owner(claim, question_for(claim, owner, "MISSING_FACT", f"nudge:{topic}", prompt,
                                                        {"yes": yes, "no": no}))
                  for owner in snapshot.members
                  for topic, prompt, yes, no in nudges(state["snapshot"], backend.read_brief(claim, owner), selected[owner], shared)]
        if nudged:
            return {"questions": nudged}
        window = state.get("group_window") or None
        # Private to each owner's agent: what this plan costs them and why. Passed by
        # closure, not task state, because subgraph state is checkpointed.
        personal = build_personal(backend, claim, model, impacts.get)
        questions, fingerprints = [], set()
        eligible = set()
        for owner in snapshot.members:
            thread_id = "conversation-" + digest([claim.generation, state["round"], state["cycle"], owner])[:24]
            initial = Payload(draft_ref=state["draft_ref"], evaluation="REQUESTED",
                              candidate_refs=(selected[owner]["id"], *(c["id"] for c in shared)))
            inbound_ref = send_message(backend, claim, state["snapshot"], "mediator", owner, Act.PROPOSE, initial,
                thread_id, tuple(sorted(("mediator", owner))), state["round"], operation_ref=f"dispatch:{owner}:{state['cycle']}",
                candidate=[selected[owner], *shared], group_window=window, share_cents=share)
            sender, recipient, inbound_type = "mediator", owner, "PROPOSE"
            inbound_payload = initial.model_dump(mode="json")
            for turn in range(limits.thread_turns):
                task = {"owner": recipient, "sender": sender, "candidate": selected[recipient],
                    "shared": shared, "share_cents": share,
                    "draft_ref": state["draft_ref"], "inbound_type": inbound_type, "inbound_ref": inbound_ref,
                    "inbound_payload": inbound_payload,
                    "inbound_evidence": [candidates[ref] for ref in inbound_payload["candidate_refs"]],
                    "thread_id": thread_id, "round": state["round"], "snapshot": state["snapshot"],
                    "permitted_recipients": ["mediator", *snapshot.members],
                    "window_seconds": snapshot.arrival_overlap_seconds,
                    "operation_ref": digest([claim.generation, state["round"], state["cycle"], owner, turn])}
                result = personal.invoke({"task": task})
                if result["questions"]:
                    questions.extend(result["questions"])
                    break
                decision = Decision.model_validate(result["decision"])
                fingerprint = digest([recipient, decision.model_dump(mode="json"), state["draft_ref"]])
                if fingerprint in fingerprints:
                    return {"outcome": "NO_PROGRESS", "questions": []}
                fingerprints.add(fingerprint)
                if decision.research:
                    evidence = task["inbound_evidence"] if decision.act == Act.COUNTERPROPOSE and task["inbound_payload"]["blocker"] == decision.blocker else [selected[recipient]]
                    return {"need_research": True, "research_cause_ref": result["message_ref"], "questions": [],
                        "research_target_owners": sorted({c["owner"] for c in evidence if c["owner"]}) or list(snapshot.members),
                        "research_blocker_code": decision.blocker if decision.blocker != "NONE" else "MISSING_FACT"}
                if decision.evaluation == "ELIGIBLE":
                    eligible.add(recipient)
                if decision.recipient == "mediator":
                    backend.record_message_processed(claim, result["message_ref"], "mediator", ())
                    break
                if recipient == decision.recipient or decision.recipient not in snapshot.members:
                    raise ContractError("NOT_AUTHORIZED")
                thread_id = result["outgoing_thread_id"]
                inbound_payload = result["outgoing_payload"]
                sender, recipient = recipient, decision.recipient
                inbound_type, inbound_ref = decision.act.value, result["message_ref"]
            else:
                return {"outcome": "TURN_BUDGET_EXHAUSTED", "questions": []}
            if questions:
                break
        if questions:
            return {"questions": questions}
        return {"outcome": "" if eligible == set(snapshot.members) else "UNRESOLVED_AGENT", "questions": []}

    def await_owner(state):
        # No side effects before interrupt. Resume data is only a wake-up signal;
        # all decisions are loaded from authoritative question records below.
        interrupt({"question_refs": state["questions"]})
        current()
        questions = [backend.get_question(claim, q) for q in state["questions"]]
        if any(q.answer is None for q in questions):
            raise ContractError("ANSWERS_NOT_READY")
        update = {"questions": [], "cycle": state["cycle"] + 1, "outcome": ""}
        if any(q.action_ref.startswith(("relax:", "nudge:")) for q in questions):
            # Re-solve with whatever was granted; a refusal leaves that requirement in place.
            return {**update, "resolve": True}
        declined = [q for q in questions if q.answer is False]
        if declined and all(q.kind == "CONSULTATION" for q in declined) and state.get("selected"):
            # "No, I won't pay more" is information, not a dead end: drop the fare
            # they declined and re-solve, so the trade-off can shift to others.
            owners = {c["id"]: c["owner"] for c in state["candidates"]}
            refused = [ref for ref in state["selected"] if owners[ref] in {q.owner for q in declined}]
            return {**update, "excluded": [*state.get("excluded", []), *refused], "resolve": True}
        return {**update, "outcome": "OWNER_DECLINED" if declined else ""}

    def next_round(state):
        backend.close_threads(claim, "RESEARCH_REQUIRED")
        return {"round": state["round"] + 1, "cycle": 0}

    def validate_publish(state):
        snapshot = current()
        candidates = [Candidate.model_validate(c) for c in state["candidates"]]
        briefs = briefs_for(snapshot)
        report = validator.validate(snapshot, briefs, candidates, tuple(state["selected"]), clock.now())
        report = ValidationReport.model_validate(report)
        if not report.valid:
            evidence_only = bool(report.checks) and set(report.checks) <= {"INCOMPLETE_COST_EVIDENCE", "BAGGAGE_UNKNOWN"}
            return {"outcome": "EXPLORATORY_ONLY" if evidence_only else "VALIDATION_FAILED", "validation_checks": list(report.checks)}
        result = solution(state)
        zone = state["group_window"][2] if state.get("group_window") else None
        ref = backend.publish_proposal(claim, tuple(state["selected"]), candidates, report, "publish",
            explanation(result.group, result.impacts, zone, candidates) if result.group else None)
        return {"proposal_ref": ref, "outcome": "AWAITING_REVIEW"}

    def exhausted(state):
        if state["solver_status"] != "INFEASIBLE":
            return {"outcome": "ROUND_BUDGET_EXHAUSTED"}
        snapshot = current()
        conflicts = solution(state).conflicts
        briefs = briefs_for(snapshot)
        by_owner = {b.owner: b for b in briefs}
        for conflict in conflicts:
            if conflict.owner:
                note = owner_conflict_note(conflict, by_owner[conflict.owner])
                backend.notify_owner(claim, conflict.owner, note, f"no-plan:{state['round']}:{conflict.owner}")
            # A shared requirement that blocks is told only to whoever set it.
            for holder in requirement_holders(conflict, briefs):
                backend.notify_owner(claim, holder, requirement_note(conflict, by_owner[holder]),
                                     f"no-plan:{state['round']}:{conflict.requirement}:{holder}")
        backend.room_event(claim, "NO_FEASIBLE_PLAN", {**room_conflict_event(conflicts), "travellers": len(snapshot.members)},
                           f"no-plan:{state['round']}")
        return {"outcome": "NO_FEASIBLE_CANDIDATE"}

    def route(state):
        if state.get("questions"):
            return "await_owner"
        if state.get("outcome"):
            return END
        if state.get("research_unavailable"):
            return "exhausted"
        if state.get("solver_status") == "INFEASIBLE" or state.get("need_research"):
            return "next_round" if state["round"] + 1 < limits.rounds else "exhausted"
        return "validate_publish"

    builder = StateGraph(CoordinatorState)
    for name, fn in [("load_snapshot", load), ("discover", discover), ("solve", solve), ("dialogue", dialogue),
                     ("await_owner", await_owner), ("next_round", next_round), ("validate_publish", validate_publish),
                     ("exhausted", exhausted)]:
        builder.add_node(name, fn)
    builder.add_edge(START, "load_snapshot")
    builder.add_conditional_edges("load_snapshot", lambda s: "await_owner" if s["questions"] else "discover")
    builder.add_edge("discover", "solve")
    builder.add_conditional_edges("solve", lambda s: route(s) if s["outcome"] or s["solver_status"] == "INFEASIBLE"
                                  or s.get("need_research") else "dialogue")
    builder.add_conditional_edges("dialogue", route)
    builder.add_conditional_edges("await_owner", lambda s: END if s["outcome"] else
                                  "solve" if s.get("resolve") else "dialogue" if s.get("selected") else "discover")
    builder.add_edge("next_round", "discover")
    builder.add_edge("validate_publish", END)
    builder.add_edge("exhausted", END)
    return builder.compile(checkpointer=checkpointer)


def candidates_data(candidates):
    return [c.model_dump(mode="json") for c in candidates]


def pickup_after(epoch: int, zone: str | None) -> str:
    """The group's latest landing, local, rounded up to the next half hour."""
    local = datetime.fromtimestamp(epoch, ZoneInfo(zone or "UTC"))
    minutes = (local.hour * 60 + local.minute + 29) // 30 * 30
    return f"{min(minutes, 23 * 60 + 30) // 60:02d}:{min(minutes, 23 * 60 + 30) % 60:02d}"


def dedupe(candidates):
    """Repeat searches return the same offer under new observation ids; keep the latest."""
    latest = {}
    for c in candidates:
        if c.category != "FLIGHT":
            key = (c.category, c.source_url, c.total_cents)  # the listing, place or vehicle offer
        else:
            key = (c.owner, c.flight_identity, c.total_cents, c.baggage_included) if c.flight_identity else c.id
        if key not in latest or c.observed_at > latest[key].observed_at:
            latest[key] = c
    return sorted(latest.values(), key=lambda c: c.id)
