"""Private credentials and explicit, non-secret live-run configuration."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, ValidationError, model_validator

from .adapters.memo23 import ACTOR_BUILD, MemoSearch
from .contracts import Brief, ContractError, Record


class LiveLimits(Record):
    model_calls: int = Field(default=4, ge=1, le=100)
    actor_runs: int = Field(default=1, ge=1, le=20)
    max_output_tokens: int = Field(default=512, ge=128, le=2048)
    max_items: int = Field(default=10, ge=1, le=100)
    max_charge_usd: float = Field(default=1.0, gt=0, le=10)
    http_timeout: float = Field(default=30, gt=0, le=30)
    poll_deadline_seconds: float = Field(default=120, gt=0, le=120)
    freshness_seconds: int = Field(default=900, ge=60, le=900)


class LiveConfig(Record):
    room_id: str = "live-provider-demo"
    briefs: tuple[Brief, ...] = Field(min_length=1, max_length=20)
    searches: tuple[MemoSearch, ...]
    limits: LiveLimits = Field(default_factory=LiveLimits)

    @model_validator(mode="after")
    def validate_owners(self):
        owners = [b.owner for b in self.briefs]
        if len(set(owners)) != len(owners) or "mediator" in owners or len(self.searches) != len(owners) or {s.owner for s in self.searches} != set(owners):
            raise ValueError("SEARCH_OWNER_MISMATCH")
        if any(not b.preferences.confirmed for b in self.briefs):
            raise ValueError("PARTICIPATION_CONFIRMATION_REQUIRED")
        return self

    def validate_dates(self, now):
        for search in self.searches:
            if search.departure_date <= datetime.fromtimestamp(now, ZoneInfo(search.origin_timezone)).date():
                raise ContractError("FUTURE_DEPARTURE_DATE_REQUIRED")


class Settings(Record):
    anthropic_api_key: SecretStr | None = None
    claude_model: str | None = None
    apify_token: SecretStr | None = None
    apify_actor_build: str = ACTOR_BUILD
    # The SpacetimeDB database live runs plan against, and the worker identity
    # the database owner registered with add_worker.
    stdb_url: str | None = None
    stdb_database: str | None = None
    stdb_worker_token: SecretStr | None = None

    def missing(self):
        return [name for name, value in (("ANTHROPIC_API_KEY", self.anthropic_api_key), ("CLAUDE_MODEL", self.claude_model), ("APIFY_TOKEN", self.apify_token)) if not value]

    def require(self):
        if self.missing():
            raise ContractError("LIVE_SETTINGS_MISSING")
        if self.apify_actor_build != ACTOR_BUILD:
            raise ContractError("UNSUPPORTED_ACTOR_BUILD")

    def missing_backend(self):
        return [name for name, value in (("STDB_URL", self.stdb_url), ("STDB_DB", self.stdb_database),
                ("STDB_WORKER_TOKEN", self.stdb_worker_token)) if not value]

    def require_backend(self):
        if self.missing_backend():
            raise ContractError("BACKEND_SETTINGS_MISSING")


def load_settings(env_file: str | Path = ".env"):
    try:
        from dotenv import dotenv_values
    except ImportError:
        raise ContractError("LIVE_DEPENDENCIES_MISSING") from None
    path = Path(env_file)
    if path == Path(".env") and not path.exists() and Path(".env.local").exists():
        path = Path(".env.local")
    values = dotenv_values(path, interpolate=False)

    def setting(canonical, alias=None):
        # Any process-environment spelling outranks file values. Keep private
        # file contents out of global os.environ and pass keys explicitly.
        for source in (os.environ, values):
            for key in (canonical, alias):
                if key and source.get(key):
                    return source[key]
        return None

    return Settings(anthropic_api_key=setting("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
        claude_model=setting("CLAUDE_MODEL"), apify_token=setting("APIFY_TOKEN", "APIFY_API_KEY"),
        apify_actor_build=setting("APIFY_ACTOR_BUILD") or ACTOR_BUILD,
        stdb_url=setting("STDB_URL"), stdb_database=setting("STDB_DB", "STDB_DATABASE"),
        stdb_worker_token=setting("STDB_WORKER_TOKEN"))


def load_config(path):
    try:
        return LiveConfig.model_validate(json.loads(Path(path).read_text()))
    except (OSError, ValueError, ValidationError):
        # Pydantic errors include rejected input, which can contain private data.
        raise ContractError("INVALID_LIVE_CONFIG") from None
