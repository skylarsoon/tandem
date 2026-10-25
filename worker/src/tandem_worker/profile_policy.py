"""Turning profile answers into plain facts the agent's summary may use, and the
checks on that summary. Facts come only from answered questions (skipped and
unanswered ones say nothing), worded from the question catalog."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((Path(__file__).with_name("profile_questions.json")).read_text())


def _questions() -> dict:
    return {q["id"]: q for q in catalog()["questions"]}


def _label(options, option_id) -> str:
    return next((o["label"] for o in options or () if o["id"] == option_id), option_id)


def _strength(value) -> str:
    return {"REQUIRED": "a must", "PREFERRED": "a preference"}.get(value, "")


def fact(question: dict, value) -> str | None:
    """One answered question as a plain statement, or None when there's nothing to say."""
    prompt, options, kind = question["prompt"], question.get("options"), question["input"]
    if kind in ("text", "long_text"):
        return f"{prompt} {value}"
    if kind == "single":
        extra = [x for x in (_strength(value.get("strength")), value.get("details")) if x]
        return f"{prompt} {_label(options, value['selected'])}" + (f" ({'; '.join(extra)})" if extra else "")
    if kind == "multi":
        picked = ", ".join(_label(options, i) for i in value["selected"])
        parts = [f"{prompt} {picked}" + (f" ({value['other']})" if value.get("other") else "")]
        if value.get("avoid"):
            parts.append("Would rather avoid: " + ", ".join(_label(options, i) for i in value["avoid"]))
        if value.get("text"):
            parts.append(f"{question['text_field']['label']} {value['text']}")
        return ". ".join(parts)
    if kind == "rank":
        return "Priorities, most important first: " + ", ".join(_label(options, i) for i in value["ranked"])
    if kind == "groups":
        return "; ".join(f"{g['label']}: {_label(g['options'], value[g['id']])}" for g in question["groups"] if g["id"] in value)
    if kind == "home_base":
        place = ", ".join(x for x in (value.get("city"), value.get("region"), value.get("country")) if x)
        airports = f" (usually flies from {', '.join(value['airports'])})" if value.get("airports") else ""
        return f"Usually starts trips from {place}{airports}"
    if kind == "needs":
        return "; ".join(f"{_label(options, i['id'])} ({_strength(i['strength'])})" + (f": {i['details']}" if i.get("details") else "")
                         for i in value["items"])
    if kind == "allergies":
        return "Must avoid: " + "; ".join(
            f"{i['food']} ({', '.join(_label(question['checks'], c) for c in i['checks'])})" for i in value["items"])
    if kind == "boundaries":
        return "Always respect: " + "; ".join(f"{i['text']} ({_strength(i['strength'])})" for i in value["items"])
    return None  # avatars say nothing about how to plan


def facts(answers: list[dict]) -> list[str]:
    questions = _questions()
    out = []
    for a in answers:
        if a["status"] != "ANSWERED" or a["question_id"] not in questions:
            continue
        stated = fact(questions[a["question_id"]], a["value"])
        if stated:
            out.append(stated)
    return out


def fallback_summary(stated: list[str]) -> str:
    """Our own plain wording when the model's summary can't be used."""
    text = "Here's what I know about you so far: " + " ".join(s.rstrip(".") + "." for s in stated)
    return text[:1197] + "..." if len(text) > 1200 else text


def summary_ok(text: str) -> bool:
    return 0 < len(text.strip()) <= 1200 and "http" not in text and not text.rstrip().endswith("?")
