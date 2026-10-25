"""What Claude is asked to fill is schema-constrained, and a strict schema closes every
object to the fields it names: an untyped dict field could only ever come back empty
(this hid every onboarding rule, arrival window and stretch budget in live runs)."""
import json

import pytest
from anthropic.lib._parse._transform import transform_schema

from tandem_worker import contracts
from tandem_worker.contracts import IntakeItem

OUTPUTS = ("Decision", "VoiceDraft", "IntakeExtraction", "IntakeQuestion", "BriefDraft", "ProfileSummary")


@pytest.mark.parametrize("name", OUTPUTS)
def test_no_model_output_has_an_object_it_can_only_leave_empty(name):
    sent = json.dumps(transform_schema(getattr(contracts, name)))
    assert '"properties": {}, "additionalProperties": false' not in sent


def test_a_rule_names_only_the_fields_it_uses():
    item = IntakeItem(key="rule", source_message_id=1, source_quote="I can't leave before November 21",
                      rule={"category": "FLIGHT", "attribute": "departure_date", "aggregate": None, "operator": "gte",
                            "value": "2026-11-21"})
    assert item.rule == {"category": "FLIGHT", "attribute": "departure_date", "operator": "gte", "value": "2026-11-21"}
