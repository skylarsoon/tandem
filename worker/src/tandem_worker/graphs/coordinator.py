from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..contracts import Act, Candidate, ContractError, Decision, Payload, Snapshot, SolverResult, ValidationReport, digest
from ..explain import explanation, owner_conflict_note, relax_prompt, requirement_holders, requirement_note, room_conflict_event
from ..solver import RELAXABLE, relax_ref, relaxed
from .personal import build_personal, question_for, send_message

# Solver conflict → the research blocker a targeted search can act on, and what to search again.
RESEARCH_BLOCKER = {"HARD_LIMIT": "HARD_LIMIT", "BAGGAGE_UNAVAILABLE": "BAGGAGE_UNKNOWN",
                    "NO_CANDIDATES": "NO_MATCHING_CANDIDATE", "ARRIVAL_WINDOW": "ARRIVAL_OVERLAP",
                    "TRIP_LIMIT": "HARD_LIMIT", "STAY_UNAVAILABLE": "STAY_UNAVAILABLE",
                    "TRANSFER_UNAVAILABLE": "TRANSFER_UNAVAILABLE"}
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


def build_coordinator(backend, claim, model, research, solver, validator, clock, limits, checkpointer):
    def current():
        return backend.load_execution_context(claim)

    def briefs_for(snapshot):
        # Room requirements an owner agreed to set aside for this trip no longer bind.
        briefs = [backend.read_brief(claim, o) for o in snapshot.members]
        return relaxed(briefs, snapshot.input_revision, lambda owner, ref: backend.has_permission(claim, owner, "CONSULTATION", ref))

    def ask_to_relax(snapshot, conflicts, briefs):
        """When an owner's own room requirement blocks the plan, their agent asks
        them privately whether to set it aside. Each owner is asked once."""
        by_owner, asked = {b.owner: b for b in briefs}, []
        for conflict in conflicts:
            if conflict.requirement not in RELAXABLE:
                continue
            for holder in requirement_holders(conflict, briefs):
                question = question_for(claim, holder, "CONSULTATION", relax_ref(snapshot.input_revision, conflict.requirement),
                                        relax_prompt(conflict, by_owner[holder]))
                try:
                    backend.get_question(claim, question.id)
                    continue  # already answered: a yes is applied in briefs_for, a no stands
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
            if not backend.read_brief(claim, owner).preferences.confirmed:
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
        request = {"purpose": purpose, "members": list(snapshot.members), "scope": "TRIP" if trip else "FLIGHTS_ONLY",
            "categories": initial if purpose == "INITIAL" else state.get("research_categories") or ["FLIGHT"],
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
        excluded = set(state.get("excluded", ()))
        candidates = [Candidate.model_validate(c) for c in state["candidates"] if c["valid_until"] > clock.now() and c["id"] not in excluded]
        briefs = briefs_for(snapshot)
        relaxations = digest([b.model_dump(mode="json") for b in briefs])[:12]
        key = f"solve:{state['round']}:{len(excluded)}:{relaxations}"
        request = {"snapshot": claim.snapshot_ref, "candidates": digest(candidates_data(candidates)), "briefs": relaxations}
        raw = backend.operation(claim, key, request, "local", lambda: solver.solve(snapshot, briefs, candidates))
        result = SolverResult.model_validate(raw)
        draft = "draft-" + digest([list(result.selected), snapshot.input_revision, snapshot.membership_revision])[:20]
        outcome = "" if result.status in {"OPTIMAL", "FEASIBLE", "INFEASIBLE"} else result.status
        update = {"selected": list(result.selected), "draft_ref": draft, "solver_status": result.status, "outcome": outcome,
                  "solve_key": key, "solve_request": request, "resolve": False, "group_window": []}
        if result.group:
            zone = next(c.arrival_timezone for c in candidates if c.id == result.selected[0])
            update["group_window"] = [result.group.earliest_arrival, result.group.latest_arrival, zone]
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
                thread_id, ("mediator", owner), state["round"], operation_ref=f"dispatch:{owner}:{state['cycle']}",
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
        if any(q.action_ref.startswith("relax-") for q in questions):
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
    builder.add_conditional_edges("solve", lambda s: route(s) if s["outcome"] or s["solver_status"] == "INFEASIBLE" else "dialogue")
    builder.add_conditional_edges("dialogue", route)
    builder.add_conditional_edges("await_owner", lambda s: END if s["outcome"] else
                                  "solve" if s.get("resolve") else "dialogue" if s.get("selected") else "discover")
    builder.add_edge("next_round", "discover")
    builder.add_edge("validate_publish", END)
    builder.add_edge("exhausted", END)
    return builder.compile(checkpointer=checkpointer)


def candidates_data(candidates):
    return [c.model_dump(mode="json") for c in candidates]


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
