# Tandem

Cal Hacks 13.0 project: a group planning app where each person has a personal AI agent. Agents negotiate over real options, a solver checks feasibility, and only people can approve the final plan.

## Repo layout (planned)

- `spacetimedb/` — SpacetimeDB module (TypeScript): rooms, constraints, jobs, agent messages, proposals, consent
- `worker/` — Python planning worker (LangGraph, Claude, OR-Tools, Apify)
- `web/` — Next.js frontend

## Workflow

Branch from `main` for each piece of work and open a PR back into `main`.
