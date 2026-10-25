# Tandem

Cal Hacks 13.0 project: a group planning app where each person has a personal AI agent. Agents negotiate over real options, a solver checks feasibility, and only people can approve the final plan.

## Repo layout

- `contracts/backend.md` — how the worker and frontend talk to the backend (start here)
- `spacetimedb/` — SpacetimeDB module (TypeScript): rooms, constraints, jobs, agent dialogue, proposals, consent. Tests: `spacetimedb/scripts/test.sh`
- `dispatcher/` — wakes the worker when a job becomes claimable
- [`worker/`](worker/README.md) — runnable Python planning worker and offline tests (LangGraph, CP-SAT, optional Claude/Apify adapters)
- [`web/`](web/README.md) — Next.js approval demo with participant-scoped SpacetimeDB subscriptions

The worker's persistent local backend is a test double for independent development;
see the worker README for setup, scenarios and recovery tests.

## Workflow

For the local web app, worker, and dispatcher connected to Maincloud, see
[`LOCAL_DEV.md`](LOCAL_DEV.md) for environment files, registration, and startup commands.

Branch from `main` for each piece of work and open a PR back into `main`.
