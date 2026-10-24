"""Minimal SpacetimeDB HTTP client used by the contract tests.

Mirrors how the Python worker talks to the module:
- reducers/procedures: POST /v1/database/<db>/call/<name> with named JSON args.
  SpacetimeDB expects top-level argument names exactly as declared in the module
  (camelCase) but snake_case for fields inside nested objects. Callers here use
  snake_case everywhere; `call`/`procedure` convert the top-level names.
- reads: POST /v1/database/<db>/sql against per-caller views; rows decode to snake_case dicts
- reducer errors: HTTP 530 with body "CODE: detail"
- procedures: HTTP 200 with a JSON-encoded string {"ok": bool, ...}
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class StdbError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(f"{status} {code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Identity:
    hex: str
    token: str

    @property
    def arg(self) -> dict:
        return {"__identity__": "0x" + self.hex}


def some(value: Any) -> dict:
    """Encode an option argument that has a value. Pass None for an empty option."""
    return {"some": value}


def _camel(name: str) -> str:
    return re.sub(r"_([a-z0-9])", lambda m: m.group(1).upper(), name)


def _wire_args(args: dict) -> bytes:
    return json.dumps({_camel(k): v for k, v in args.items()}).encode()


class Client:
    def __init__(self, base_url: str, database: str):
        self.base_url = base_url.rstrip("/")
        self.database = database

    # ---------- transport ----------

    def _post(self, path: str, body: bytes | None, token: str | None, content_type: str) -> tuple[int, str]:
        headers = {"Content-Type": content_type}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base_url + path, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def mint_identity(self) -> Identity:
        status, body = self._post("/v1/identity", None, None, "application/json")
        if status != 200:
            raise StdbError(status, "IDENTITY", body)
        data = json.loads(body)
        return Identity(hex=data["identity"], token=data["token"])

    # ---------- calls ----------

    def call(self, who: Identity, reducer: str, **args: Any) -> None:
        status, body = self._post(
            f"/v1/database/{self.database}/call/{reducer}", _wire_args(args), who.token, "application/json"
        )
        if status == 200:
            return
        code, _, detail = body.partition(": ")
        if status == 530 and code.isupper():
            raise StdbError(status, code, detail)
        raise StdbError(status, "TRANSPORT", body)

    def procedure(self, who: Identity, name: str, **args: Any) -> dict:
        status, body = self._post(
            f"/v1/database/{self.database}/call/{name}", _wire_args(args), who.token, "application/json"
        )
        if status != 200:
            raise StdbError(status, "TRANSPORT", body)
        result = json.loads(json.loads(body))
        if not result.get("ok"):
            raise StdbError(status, result.get("code", "UNKNOWN"), result.get("detail", ""))
        return result

    # ---------- reads ----------

    def sql(self, who: Identity | None, query: str) -> list[dict]:
        status, body = self._post(
            f"/v1/database/{self.database}/sql", query.encode(), who.token if who else None, "text/plain"
        )
        if status != 200:
            raise StdbError(status, "SQL", body)
        out: list[dict] = []
        for stmt in json.loads(body):
            elements = stmt["schema"]["elements"]
            for row in stmt["rows"]:
                out.append({el["name"]["some"]: _decode(v, el["algebraic_type"]) for el, v in zip(elements, row)})
        return out

    def view(self, who: Identity | None, name: str) -> list[dict]:
        return self.sql(who, f"SELECT * FROM {name}")


def _decode(value: Any, typ: dict) -> Any:
    if "Product" in typ:
        elements = typ["Product"]["elements"]
        if len(elements) == 1:
            name = elements[0]["name"].get("some")
            if name == "__identity__":
                return value[0][2:] if isinstance(value[0], str) and value[0].startswith("0x") else value[0]
            if name == "__timestamp_micros_since_unix_epoch__":
                return value[0]
        return {el["name"]["some"]: _decode(v, el["algebraic_type"]) for el, v in zip(elements, value)}
    if "Sum" in typ:
        variants = typ["Sum"]["variants"]
        tag, payload = value
        variant = variants[tag]
        if [v["name"].get("some") for v in variants] == ["some", "none"]:
            return None if variant["name"]["some"] == "none" else _decode(payload, variant["algebraic_type"])
        return variant["name"]["some"]
    if "Array" in typ:
        return [_decode(v, typ["Array"]) for v in value]
    return value
