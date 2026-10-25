from tandem_worker.persona import persona


def test_a_persona_is_their_confirmed_words_and_what_shapes_decisions():
    profile = {"summary": "Comfort first; ask me before compromising.",
               "answers": [{"question_id": "C01", "status": "ANSWERED", "value": "Bella"},
                           {"question_id": "A02", "status": "ANSWERED", "value": "Sunny"},
                           {"question_id": "C05", "status": "ANSWERED", "value": {"selected": "slow"}},
                           {"question_id": "C09", "status": "ANSWERED", "value": {"selected": "ask_first"}}]}
    text = persona(profile)
    assert text.startswith("In their own confirmed words: Comfort first; ask me before compromising.")
    assert "Slow and spacious" in text and "Ask me before suggesting a compromise" in text
    assert "Sunny" not in text and "Bella" not in text, "an agent's name or theirs doesn't change a decision"


def test_nothing_said_means_no_persona():
    assert persona(None) is None and persona({"summary": None, "answers": []}) is None
