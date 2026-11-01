import pytest
from tandem_worker.conversation import grounded_turn, check_draft
from tandem_worker.contracts import Act, ConversationDraft, ConversationReview, ContractError

FACTS = {"act:EVALUATE": "This works for my traveller; they will still review and approve it themselves.",
         "flight#price": "The listed fare is $128.00; some fees are not confirmed.",
         "flight#bag": "Checked-bag coverage is not confirmed."}

class Backend:
    def __init__(self): self.calls = []; self.revoked = False
    def operation(self, claim, key, request, kind, fn):
        self.calls.append((key, request)); return fn()
    def check(self, claim):
        if self.revoked: raise ContractError("NOT_AUTHORIZED")

class Model:
    def __init__(self, text, **review):
        self.draft = ConversationDraft(text=text, claim_refs=tuple(FACTS))
        self.review = ConversationReview(**({k: True for k in ConversationReview.model_fields} | review))
        self.context = None
    def converse(self, context): self.context = context; return self.draft
    def review_conversation(self, context): return self.review

def run(model, backend=None):
    return grounded_turn(backend or Backend(), None, model, act=Act.EVALUATE, facts=FACTS,
                         operation_ref="turn", role="traveller agent", evaluation="ELIGIBLE", blocker="NONE")

def test_free_wording_kept_verbatim_after_independent_review():
    text = "I can bring this option to my traveller for their final review. The $128 fare still has unconfirmed fees."
    backend = Backend()
    result = run(Model(text), backend)
    assert result == (text, tuple(FACTS), "VALIDATED")
    assert len(backend.calls) == 2
    assert backend.calls[0][0] != backend.calls[1][0]
    assert set(backend.calls[0][1]) == {"act", "evaluation", "blocker", "role", "facts", "recent_turns"}

@pytest.mark.parametrize("draft", [
    ConversationDraft(text="The fare is $99.", claim_refs=("act:EVALUATE", "flight#price")),
    ConversationDraft(text="A flight exists.", claim_refs=("act:EVALUATE", "made-up#price")),
    ConversationDraft(text="I agree.", claim_refs=("flight#price",)),
    ConversationDraft(text="I agree.", claim_refs=("act:EVALUATE", "act:EVALUATE")),
])
def test_mechanical_gate_rejects_unknown_values_and_refs(draft):
    with pytest.raises(ContractError, match="UTTERANCE_PAYLOAD_MISMATCH"):
        check_draft(draft, FACTS, "EVALUATE")

@pytest.mark.parametrize("field,text", [
    ("facts_supported", "The flight is nonstop."),
    ("intent_preserved", "I reject this option."),
    ("uncertainty_preserved", "The $128 fare includes every fee and checked bags."),
    ("privacy_preserved", "My traveller has a medical condition."),
    ("human_authority_preserved", "I booked this for my traveller."),
])
def test_failed_semantic_review_is_never_published_as_generated(field, text):
    backend = Backend()
    result = run(Model(text, **{field: False}), backend)
    assert result[2] == "TEMPLATE_FALLBACK"
    assert result[0] == " ".join(FACTS.values())
    assert len(backend.calls) == 4

def test_lease_or_permission_revoked_during_generation_prevents_publishing():
    backend = Backend(); backend.revoked = True
    with pytest.raises(ContractError, match="NOT_AUTHORIZED"):
        run(Model("Ready for my traveller to review."), backend)

def test_provider_budget_failure_is_not_disguised_as_conversation():
    class Exhausted(Model):
        def converse(self, context): raise ContractError("MODEL_CALL_BUDGET_EXHAUSTED")
    with pytest.raises(ContractError, match="MODEL_CALL_BUDGET_EXHAUSTED"):
        run(Exhausted("irrelevant"))


def test_private_fallback_is_bounded_to_status_not_all_personal_rules():
    facts={'act:RESEARCH_UPDATE':'Here is the recorded planning status.',
           'planning_job':'The latest attempt is queued for a worker.',
           'own:budget':'Your personal limit is $650.00.',
           'search:1':'A long list of returned evidence.',
           'status':'No execution update for this attempt yet.'}
    result=grounded_turn(Backend(),None,Model('unsupported'),act=Act.RESEARCH_UPDATE,
        facts=facts,operation_ref='private',role='personal',evaluation='STATUS_ONLY',
        blocker='NONE',audience='OWNER_PRIVATE')
    assert result[2]=='TEMPLATE_FALLBACK'
    assert 'queued' in result[0]
    assert '$650.00' not in result[0] and 'long list' not in result[0]
    assert len(result[1])==3


def test_reviewer_receives_published_context_and_rejected_draft_gets_repair_feedback():
    class Reviewing(Model):
        def __init__(self):
            super().__init__('This option is ready for my traveller to review.',facts_supported=False)
            self.contexts=[];self.reviews=[]
        def converse(self,context):
            self.contexts.append(context.copy());return self.draft
        def review_conversation(self,context):
            self.reviews.append(context);return self.review
    model=Reviewing()
    recent=[{'speaker':'mediator','text':'Please evaluate this option.'}]
    grounded_turn(Backend(),None,model,act=Act.EVALUATE,facts=FACTS,operation_ref='context',
                  role='personal',evaluation='ELIGIBLE',blocker='NONE',recent_turns=recent)
    assert model.reviews[0]['recent_turns']==recent
    assert model.contexts[1]['revision_needed']==['facts_supported']
