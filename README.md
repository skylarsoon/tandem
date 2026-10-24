# Tandem

Cal Hacks 13.0 project: a group planning app where each person has a personal AI agent. Agents negotiate over real options, a solver checks feasibility, and only people can approve the final plan.

## Repo layout

- `contracts/backend.md` — how the worker and frontend talk to the backend (start here)
- `spacetimedb/` — SpacetimeDB module (TypeScript): rooms, constraints, jobs, agent dialogue, proposals, consent. Tests: `spacetimedb/scripts/test.sh`
- `dispatcher/` — wakes the worker when a job becomes claimable
- `worker/` — Python planning worker (LangGraph, Claude, OR-Tools, Apify) — planned
- `web/` — Next.js frontend — planned

## Workflow

Branch from `main` for each piece of work and open a PR back into `main`.
