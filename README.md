# Terraluna

**Gradient Descent · NASA Space Apps 2026**

Earth analogs for lunar and Martian sites, using terrain fingerprints, a cross-planet terrain encoder and qualified radar evidence.

## Start building

[PRD.md](PRD.md) defines the product and implementation backlog. Start with **section 12, T0** (repository and contracts) and **T1** (real-data pilot). Section 8 defines the architecture; section 23 defines the review workflow.

Build toward one real evidence card flowing through the offline pipeline, a versioned release bundle and the web app. The application, pipeline and trained model are still to be built; T0 establishes runnable setup, test commands and CI.

The PRD specifies **Python 3.12 + uv** for offline science and training, and **Node 22 + pnpm** for the web app and shared TypeScript packages.

[config/run.yaml](config/run.yaml) holds the starting configuration with kickoff decisions unset. [.env.example](.env.example) lists deployment variables. [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md) provide working instructions; `.claude/agents/` contains the PRD's specialist roles.

## Optional references

[Five reference projects](reference-library/README.md) provide ideas for independent implementation (D17). Their local checkouts are ignored by Git; this repository keeps a small source index. Reference research is optional and does not block the build.

Original project code uses [Apache-2.0](LICENSE); third-party material retains its own terms. Codex assisted with repository preparation and reference research. Record further AI assistance as required by the PRD.
