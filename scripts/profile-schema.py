#!/usr/bin/env python3
"""The JSON Schema for a whole profile onboarding (one person's answers), generated
from contracts/profile_questions.json so it always matches what the database
accepts. Keys are the stable profile keys (display_name, activity_interests, ...);
a question's value is its answer, or "SKIPPED"; leaving a key out means
"not shared yet". Rules JSON Schema can't express (exclusive choices like "None",
"other" text needing the Other choice) are annotated with x- keys and enforced by
the database. Writes contracts/profile_answers.schema.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRENGTH = {"enum": ["REQUIRED", "PREFERRED"], "description": "A must, or a preference"}


def ids(options):
    return [o["id"] for o in options]


def text(max_length, min_length=0):
    return {"type": "string", "minLength": min_length, "maxLength": max_length}


def shape(q: dict) -> dict:
    kind, options = q["input"], q.get("options", [])
    if kind in ("text", "long_text"):
        return text(q.get("max_length", 1000), 1 if q.get("required") else 0)
    if kind == "single":
        props = {"selected": {"enum": ids(options)}}
        if q.get("text_field"):
            props["details"] = {**text(300), "description": q["text_field"]["label"]}
        if any(o.get("needs_strength") for o in options):
            props["strength"] = {**STRENGTH, "description": "Required with: " + ", ".join(o["id"] for o in options if o.get("needs_strength"))}
        return {"type": "object", "properties": props, "required": ["selected"], "additionalProperties": False}
    if kind == "multi":
        selected = {"type": "array", "items": {"enum": ids(options)}, "minItems": 1, "uniqueItems": True}
        if q.get("max"):
            selected["maxItems"] = q["max"]
        props = {"selected": selected}
        if any(o.get("other_text") for o in options):
            props["other"] = {**text(100), "description": "Only with the 'other' choice"}
        if q.get("text_field"):
            props["text"] = {**text(300), "description": q["text_field"]["label"]}
        if q.get("avoid_list"):
            props["avoid"] = {"type": "array", "items": {"enum": [o["id"] for o in options if not o.get("exclusive")]},
                              "uniqueItems": True, "description": q["avoid_list"]["label"] + " (never also in selected)"}
        out = {"type": "object", "properties": props, "required": ["selected"], "additionalProperties": False}
        exclusive = [o["id"] for o in options if o.get("exclusive")]
        if exclusive:
            out["x-exclusive"] = exclusive
        return out
    if kind == "rank":
        return {"type": "object", "required": ["ranked", "confirmed"], "additionalProperties": False, "properties": {
            "ranked": {"type": "array", "items": {"enum": ids(options)}, "minItems": 1, "maxItems": q.get("max", len(options)),
                       "uniqueItems": True, "description": "Most important first"},
            "confirmed": {"const": True, "description": "The person confirmed this order"}}}
    if kind == "groups":
        return {"type": "object", "minProperties": 1, "additionalProperties": False,
                "properties": {g["id"]: {"enum": ids(g["options"]), "description": g["label"]} for g in q["groups"]}}
    if kind == "avatar":
        return {"oneOf": [{"type": "object", "properties": {"avatar": text(40, 1)}, "required": ["avatar"], "additionalProperties": False},
                          {"type": "object", "properties": {"initials": {"const": True}}, "required": ["initials"], "additionalProperties": False}]}
    if kind == "home_base":
        return {"type": "object", "required": ["city", "country"], "additionalProperties": False, "properties": {
            "city": text(80, 1), "region": text(80), "country": text(60, 1),
            "airports": {"type": "array", "items": {"type": "string", "pattern": "^[A-Z]{3}$"}, "maxItems": 5}}}
    if kind == "needs":
        item = {"type": "object", "required": ["id", "strength"], "additionalProperties": False,
                "properties": {"id": {"enum": ids(options)}, "strength": STRENGTH, "details": text(300), "other": text(100)}}
        props = {"items": {"type": "array", "items": item, "minItems": 1, "maxItems": 20}}
        if q.get("ask_privately"):
            props["ask_privately"] = {"type": "boolean"}
        return {"type": "object", "required": ["items"], "additionalProperties": False, "properties": props}
    if kind == "allergies":
        item = {"type": "object", "required": ["food", "checks"], "additionalProperties": False, "properties": {
            "food": text(80, 1), "checks": {"type": "array", "items": {"enum": ids(q["checks"])}, "minItems": 1, "uniqueItems": True},
            "instructions": text(300)}}
        return {"type": "object", "required": ["items"], "additionalProperties": False,
                "properties": {"items": {"type": "array", "items": item, "minItems": 1, "maxItems": 20}}}
    if kind == "boundaries":
        item = {"type": "object", "required": ["text", "strength"], "additionalProperties": False,
                "properties": {"text": text(200, 1), "strength": STRENGTH}}
        return {"type": "object", "required": ["items"], "additionalProperties": False,
                "properties": {"items": {"type": "array", "items": item, "minItems": 1, "maxItems": 20}}}
    raise ValueError(kind)


def main():
    catalog = json.loads((ROOT / "contracts" / "profile_questions.json").read_text())
    properties = {}
    for q in catalog["questions"]:
        answer = shape(q)
        properties[q["key"]] = {"title": f"{q['id']} · {q['prompt']}", "x-question-id": q["id"], "x-section": q["section"],
                                **({"x-private": True} if q.get("private") else {}),
                                "oneOf": [answer, {"const": "SKIPPED", "description": "Skip for now (known unanswered)"}]}
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://tandem/contracts/profile_answers.schema.json",
        "title": "Tandem profile onboarding answers",
        "description": ("One person's answers to profile onboarding (catalog version %d). Keys are profile keys; a value "
                        "is the answer or \"SKIPPED\"; a missing key means not shared yet. Only display_name is required. "
                        "x-exclusive choices (None, No preference, ...) stand alone; 'other' text needs the 'other' "
                        "choice; the database enforces these." % catalog["version"]),
        "type": "object", "required": ["display_name"], "additionalProperties": False, "properties": properties}
    (ROOT / "contracts" / "profile_answers.schema.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    print(f"{len(properties)} questions -> contracts/profile_answers.schema.json")


if __name__ == "__main__":
    main()
