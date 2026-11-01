"""Publish evidence-backed handoffs while provider jobs continue independently.

This is research coordination, not early approval or simulated negotiation.
Personal agents hand actual observed options to the mediator; the existing
optimizer and personal decision graph still determine eligibility and tradeoffs.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from queue import Queue, Empty

from .contracts import Act, ContractError, Payload, digest
from .conversation import grounded_turn
from .policy import candidate_facts
from .progress import activity, research_results
from .graphs.personal import send_message


def search_with_dialogue(backend, claim, model, research, request, snapshot, round_id, clock):
    # Older fixture adapters have no completion observer and remain supported.
    updates = Queue()
    def complete(key, category, owner, candidates):
        backend.check(claim)
        updates.put((key, category, owner, candidates))

    def discover():
        with research_results(complete):
            return research.search(request)

    recent = []
    # Only confirmed participants may enter the shared discussion. Early research
    # still runs while a traveller finishes intake; it cannot speak for them yet.
    can_speak = bool(snapshot.members) and all(
        backend.read_brief(claim, o).preferences.confirmed for o in snapshot.members)

    def handoff(key, category, owner, candidates):
        valid = [c for c in candidates if c.observed_at <= clock.now() < c.valid_until]
        if not can_speak or not valid:
            return
        # One real example per completed search, not a claim that it is best,
        # affordable, or complete. The solver evaluates the full batch afterwards.
        example = min(valid, key=lambda c: (c.total_cents is None, c.total_cents or 0, c.id))
        # Research observations are not complete itinerary candidates. Distinct
        # refs prevent one-way examples from entering round-trip reuse/selection.
        example = example.model_copy(update={'id': 'research-only:' + example.id})
        backend.store_candidates(claim, [example])
        sender = owner if owner in snapshot.members else 'mediator'
        recipient = 'mediator' if sender != 'mediator' else snapshot.members[0]
        ref = f"research:{round_id}:{key}"
        payload = Payload(draft_ref=ref, evaluation='RESEARCH_ONLY', research=True,
                          candidate_refs=(example.id,))
        kind = {'FLIGHT':'flight', 'STAY':'accommodation', 'TRANSFER':'transfer',
                'ACTIVITY':'activity', 'DINNER':'restaurant'}[category]
        facts = {"act:RESEARCH_UPDATE":
                 f"This {kind} search has returned observed options. I am handing these findings over "
                 "for comparison with the rest of the trip. This example has not been evaluated "
                 "against anyone's requirements, selected, approved, or booked."}
        facts.update(candidate_facts(example.model_dump(mode='json')))
        activity(backend, claim, sender, 'WRITING')
        text, refs, status = grounded_turn(backend, claim, model, act=Act.RESEARCH_UPDATE,
            facts=facts, operation_ref=ref, role='personal' if owner else 'mediator',
            evaluation='RESEARCH_ONLY', blocker='NONE', recent_turns=())
        # Per-search context is stable on replay; prior arrival order is not.
        message = send_message(backend, claim, snapshot.model_dump(mode='json'), sender, recipient,
            Act.RESEARCH_UPDATE, payload, ref, (sender, recipient), 1000000 + round_id,
            text=text, refs=refs, status=status, operation_ref=ref)
        recent.append({'speaker': sender, 'text': text})
        # A mediator receipt acknowledges real delivery, without claiming that the
        # solver has already accepted an individual option or the whole plan.
        if sender != 'mediator' and example.leg != 'return':
            answer = {'act:RESEARCH_UPDATE':
                'I have received these research findings. I will compare the complete set of returned '
                'options for the group before proposing a plan. Traveller approval is still required.'}
            reply_text, reply_refs, reply_status = grounded_turn(backend, claim, model,
                act=Act.RESEARCH_UPDATE, facts=answer, operation_ref=ref+':receipt', role='mediator',
                evaluation='RESEARCH_ONLY', blocker='NONE', recent_turns=(recent[-1],))
            send_message(backend, claim, snapshot.model_dump(mode='json'), recipient, sender,
                Act.RESEARCH_UPDATE, payload.model_copy(update={'candidate_refs': ()}), ref,
                (sender, recipient), 1000000 + round_id, text=reply_text,
                refs=reply_refs, status=reply_status, reply_to=message, operation_ref=ref+':receipt')
        activity(backend, claim, sender, 'SEARCHING')

    # Carry job budget/observer context into provider work. Model generation stays
    # off its polling loop, so a slow reply cannot consume a scraper's deadline.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='research') as pool:
        future = pool.submit(copy_context().run, discover)
        while not future.done() or not updates.empty():
            try:
                update = updates.get(timeout=0.2)
            except Empty:
                continue
            handoff(*update)
        return future.result()
