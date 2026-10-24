from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..contracts import Act, Candidate, ContractError, Decision, Payload, Snapshot, SolverResult, ValidationReport, digest
from .personal import build_personal, question_for, send_message


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


def build_coordinator(backend, claim, model, research, solver, validator, clock, limits, checkpointer):
    def current():
        return backend.load_execution_context(claim)

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
        purpose = "INITIAL" if not state["candidates"] else "TARGETED"
        # Stable observation/request time across a replay, persisted in the op receipt.
        stamp = backend.operation(claim, f"research-time:{state['round']}", {}, "local", clock.now)
        request = {"purpose": purpose, "members": list(snapshot.members), "scope": "FLIGHTS_ONLY", "room_id": snapshot.room_id,
            "input_revision": snapshot.input_revision, "permission_revision": snapshot.permission_revision, "observed_at": stamp,
            "blocker_message_ref": state.get("research_cause_ref", "")}
        # No private caps, reasons or transcripts leave through ResearchPort.
        raw = backend.operation(claim, f"research:{state['round']}", request, "research", lambda: research.search(request))
        candidates = {c["id"]: Candidate.model_validate(c) for c in state["candidates"]}
        for item in raw:
            candidate = Candidate.model_validate(item)
            if candidate.observed_at > clock.now() or candidate.valid_until <= clock.now():
                continue
            if candidate.id in candidates and candidates[candidate.id] != candidate:
                raise ContractError("CANDIDATE_ID_CONFLICT")
            candidates[candidate.id] = candidate
        backend.store_candidates(claim, list(candidates.values()))
        return {"candidates": [c.model_dump(mode="json") for c in candidates.values()], "need_research": False}

    def solve(state):
        snapshot = current()
        candidates = [Candidate.model_validate(c) for c in state["candidates"] if c["valid_until"] > clock.now()]
        briefs = [backend.read_brief(claim, o) for o in snapshot.members]
        request = {"snapshot": claim.snapshot_ref, "candidates": digest(candidates_data(candidates))}
        raw = backend.operation(claim, f"solve:{state['round']}", request, "local", lambda: solver.solve(snapshot, briefs, candidates))
        result = SolverResult.model_validate(raw)
        draft = "draft-" + digest([list(result.selected), snapshot.input_revision, snapshot.membership_revision])[:20]
        outcome = "" if result.status in {"OPTIMAL", "FEASIBLE", "INFEASIBLE"} else result.status
        return {"selected": list(result.selected), "draft_ref": draft, "solver_status": result.status, "outcome": outcome}

    def dialogue(state):
        snapshot = current()
        candidates = {c["id"]: c for c in state["candidates"]}
        selected = {candidates[r]["owner"]: candidates[r] for r in state["selected"]}
        if set(selected) != set(snapshot.members):
            raise ContractError("INVALID_COVERAGE")
        personal = build_personal(backend, claim, model)
        questions, fingerprints = [], set()
        eligible = set()
        for owner in snapshot.members:
            thread_id = "conversation-" + digest([claim.generation, state["round"], state["cycle"], owner])[:24]
            initial = Payload(draft_ref=state["draft_ref"], evaluation="REQUESTED", candidate_refs=(selected[owner]["id"],))
            inbound_ref = send_message(backend, claim, state["snapshot"], "mediator", owner, Act.PROPOSE, initial,
                thread_id, ("mediator", owner), state["round"], operation_ref=f"dispatch:{owner}:{state['cycle']}")
            sender, recipient, inbound_type = "mediator", owner, "PROPOSE"
            inbound_payload = initial.model_dump(mode="json")
            for turn in range(limits.thread_turns):
                task = {"owner": recipient, "sender": sender, "candidate": selected[recipient],
                    "draft_ref": state["draft_ref"], "inbound_type": inbound_type, "inbound_ref": inbound_ref,
                    "inbound_payload": inbound_payload,
                    "inbound_evidence": [candidates[ref] for ref in inbound_payload["candidate_refs"]],
                    "thread_id": thread_id, "round": state["round"], "snapshot": state["snapshot"],
                    "permitted_recipients": ["mediator", *snapshot.members],
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
                    return {"need_research": True, "research_cause_ref": result["message_ref"], "questions": []}
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
        answers = [backend.get_question(claim, q).answer for q in state["questions"]]
        if any(a is None for a in answers):
            raise ContractError("ANSWERS_NOT_READY")
        return {"questions": [], "cycle": state["cycle"] + 1, "outcome": "" if all(answers) else "OWNER_DECLINED"}

    def next_round(state):
        backend.close_threads(claim, "RESEARCH_REQUIRED")
        return {"round": state["round"] + 1, "cycle": 0}

    def validate_publish(state):
        snapshot = current()
        candidates = [Candidate.model_validate(c) for c in state["candidates"]]
        briefs = [backend.read_brief(claim, o) for o in snapshot.members]
        report = validator.validate(snapshot, briefs, candidates, tuple(state["selected"]), clock.now())
        report = ValidationReport.model_validate(report)
        if not report.valid:
            return {"outcome": "VALIDATION_FAILED"}
        ref = backend.publish_proposal(claim, tuple(state["selected"]), candidates, report, "publish")
        return {"proposal_ref": ref, "outcome": "AWAITING_REVIEW"}

    def route(state):
        if state.get("questions"):
            return "await_owner"
        if state.get("outcome"):
            return END
        if state.get("solver_status") == "INFEASIBLE" or state.get("need_research"):
            return "next_round" if state["round"] + 1 < limits.rounds else "exhausted"
        return "validate_publish"

    builder = StateGraph(CoordinatorState)
    for name, fn in [("load_snapshot", load), ("discover", discover), ("solve", solve), ("dialogue", dialogue),
                     ("await_owner", await_owner), ("next_round", next_round), ("validate_publish", validate_publish),
                     ("exhausted", lambda state: {"outcome": "NO_FEASIBLE_CANDIDATE" if state["solver_status"] == "INFEASIBLE" else "ROUND_BUDGET_EXHAUSTED"})]:
        builder.add_node(name, fn)
    builder.add_edge(START, "load_snapshot")
    builder.add_conditional_edges("load_snapshot", lambda s: "await_owner" if s["questions"] else "discover")
    builder.add_edge("discover", "solve")
    builder.add_conditional_edges("solve", lambda s: route(s) if s["outcome"] or s["solver_status"] == "INFEASIBLE" else "dialogue")
    builder.add_conditional_edges("dialogue", route)
    builder.add_conditional_edges("await_owner", lambda s: END if s["outcome"] else ("dialogue" if s.get("selected") else "discover"))
    builder.add_edge("next_round", "discover")
    builder.add_edge("validate_publish", END)
    builder.add_edge("exhausted", END)
    return builder.compile(checkpointer=checkpointer)


def candidates_data(candidates):
    return [c.model_dump(mode="json") for c in candidates]
