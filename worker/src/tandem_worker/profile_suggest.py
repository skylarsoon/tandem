"""Room onboarding suggestions from a person's persistent profile.

A profile holds defaults ("I usually fly from JFK", "I need my own room"). In a
room they are offered for this trip and apply only once the person confirms
them; nothing is assumed, and a trip answer never changes the profile. Only the
profile answers a room's planning can use today are suggested.
"""
from __future__ import annotations

from typing import Any, Literal

from .contracts import Record


class Suggestion(Record):
    key: str
    value: Any
    hardness: Literal["HARD", "SOFT"]
    question_id: str  # the profile question it comes from (checked by the database)
    text: str

    def patch(self) -> dict:
        return {"key": self.key, "value": self.value, "hardness": self.hardness, "unit": "",
                "source_profile": self.question_id}


def suggestions(profile: dict) -> dict[str, Suggestion]:
    out = {}
    airports = (profile.get("A06") or {}).get("airports") or []
    if airports:
        out["origin_airport"] = Suggestion(key="origin_airport", value=airports[0], hardness="HARD", question_id="A06",
                                           text=f"Flying from {airports[0]}")
    sleeping = profile.get("T06") or {}
    if sleeping.get("selected") == "own_room":
        must = sleeping.get("strength") == "REQUIRED"
        out["own_bedroom"] = Suggestion(key="own_bedroom", value=True, hardness="HARD" if must else "SOFT", question_id="T06",
                                        text="Your own room" + (" (a must)" if must else " (if possible)"))
    if (profile.get("T04") or {}).get("selected") in ("checked", "specialty"):
        out["baggage_required"] = Suggestion(key="baggage_required", value=True, hardness="HARD", question_id="T04",
                                             text="A checked bag")
    return out


def asks_first(profile: dict) -> bool | None:
    """The profile's consultation style as a default for "check with me before
    agreeing to pay more" ("ask me for each plan" is asking first; None if unanswered)."""
    return {"ask_first": True, "ask_per_plan": True, "suggest_then_review": False}.get((profile.get("C09") or {}).get("selected"))
