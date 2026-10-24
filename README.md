# Tandem

Cal Hacks 13.0 project: a group planning app where each person has a personal AI agent. Agents negotiate over real options, a solver checks feasibility, and only people can approve the final plan.

## Repo layout

- `spacetimedb/` — SpacetimeDB module (TypeScript): rooms, constraints, jobs, agent messages, proposals, consent
- [`worker/`](worker/README.md) — runnable Python planning worker and offline tests (LangGraph, CP-SAT, optional Claude/Apify adapters)
- `web/` — Next.js frontend

The backend module and frontend directories are planned. The worker's persistent
local backend is a test double for independent development, not a deployed
SpacetimeDB integration. See the worker README for setup, scenarios and recovery tests.

## Workflow

Branch from `main` for each piece of work and open a PR back into `main`.
