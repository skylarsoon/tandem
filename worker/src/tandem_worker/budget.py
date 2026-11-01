"""Durable provider-attempt budgets and allowlisted request telemetry."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from .contracts import ContractError


def utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class ProviderBudget:
    """Attempts per scope. A one-off run (live.py) gets one lifetime count; a
    long-running service passes daily=True so its ceiling resets each UTC day."""
    def __init__(self, path, scope, *, model_calls=4, actor_runs=1, daily=False, day=utc_day):
        self.path, self.base_scope, self.daily, self.day = str(path), scope, daily, day
        self.limits = {"claude": model_calls, "apify": actor_runs}
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS provider_budget (scope TEXT, kind TEXT, used INTEGER, PRIMARY KEY(scope, kind))")
            conn.execute("CREATE TABLE IF NOT EXISTS provider_metrics (scope TEXT, body TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS provider_daily_limits (scope TEXT, kind TEXT, ceiling INTEGER NOT NULL, PRIMARY KEY(scope, kind))")

    @property
    def scope(self):
        return f"{self.base_scope}:{self.day()}" if self.daily else self.base_scope

    def set_daily_limit(self, kind, ceiling, *, utc_date):
        """Operator-authorized ceiling for exactly one UTC date; never resets usage."""
        if not self.daily or kind not in self.limits or type(ceiling) is not int or ceiling < 1:
            raise ValueError("INVALID_DAILY_LIMIT")
        if date.fromisoformat(utc_date).isoformat() != utc_date:
            raise ValueError("INVALID_UTC_DATE")
        with sqlite3.connect(self.path, timeout=15) as conn:
            conn.execute("INSERT OR REPLACE INTO provider_daily_limits VALUES (?, ?, ?)",
                         (f"{self.base_scope}:{utc_date}", kind, ceiling))

    def _limits(self, conn, scope):
        limits = dict(self.limits)
        if self.daily:
            limits.update(conn.execute("SELECT kind, ceiling FROM provider_daily_limits WHERE scope=?", (scope,)))
        return limits

    def reserve(self, kind):
        scope = self.scope
        with sqlite3.connect(self.path, timeout=15) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT used FROM provider_budget WHERE scope=? AND kind=?", (scope, kind)).fetchone()
            used = row[0] if row else 0
            if used >= self._limits(conn, scope)[kind]:
                raise ContractError("MODEL_CALL_BUDGET_EXHAUSTED" if kind == "claude" else "ACTOR_RUN_BUDGET_EXHAUSTED")
            conn.execute("INSERT OR REPLACE INTO provider_budget VALUES (?, ?, ?)", (scope, kind, used + 1))
            return used + 1

    def release(self, kind):
        """Return a reservation the provider definitely refused (nothing ran)."""
        with sqlite3.connect(self.path, timeout=15) as conn:
            conn.execute("UPDATE provider_budget SET used = used - 1 WHERE scope=? AND kind=? AND used > 0", (self.scope, kind))

    def record(self, **values):
        allowed = {"provider", "kind", "model", "request_id", "input_tokens", "output_tokens", "latency_ms", "outcome"}
        if set(values) - allowed:
            raise ValueError("UNSAFE_METRIC_FIELD")
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO provider_metrics VALUES (?, ?)", (self.scope, json.dumps(values)))

    def report(self):
        scope = self.scope
        with sqlite3.connect(self.path) as conn:
            limits = self._limits(conn, scope)
            used = dict(conn.execute("SELECT kind, used FROM provider_budget WHERE scope=?", (scope,)))
            metrics = [json.loads(r[0]) for r in conn.execute("SELECT body FROM provider_metrics WHERE scope=? ORDER BY rowid", (scope,))]
        return {"limits": limits, "used": {k: used.get(k, 0) for k in self.limits}, "calls": metrics}
