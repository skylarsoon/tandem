"""Who a person is, for their own agent: the profile summary they confirmed and what
they answered, in plain words. Private to that one agent's decisions: never in the
room, other agents' contexts, graph state or anything shared."""
from __future__ import annotations

from .profile_policy import facts

# What says how they travel and how they want to be represented; the rest (avatars,
# a name for the agent) doesn't change a decision.
SHAPING = ("C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09", "A04", "A05", "N01", "N02", "N03", "N04", "N05",
           "T01", "T02", "T03", "T04", "T05", "T06", "T07", "G01", "G02", "G03", "G04", "G05", "F01", "F02", "F03")


def persona(profile: dict | None) -> str | None:
    """A short private brief on the person, or None if their profile says nothing yet."""
    if not profile:
        return None
    stated = facts([a for a in profile.get("answers") or () if a.get("question_id") in SHAPING])
    parts = []
    if profile.get("summary"):
        parts.append(f"In their own confirmed words: {profile['summary']}")
    if stated:
        parts.append("From their profile: " + " ".join(s.rstrip(".") + "." for s in stated))
    return " ".join(parts)[:2400] or None
