# Odysseus — operator fork

> **This is RichardRacette's public fork of [odysseus-dev/odysseus](https://github.com/odysseus-dev/odysseus), not an independently authored AI platform.** Upstream contributors created the application and inherited features below. This fork documents operating experience, evaluation and coordination. See [Fork attribution and contribution evidence](FORK-ATTRIBUTION.md) for the exact baseline and claim boundaries.

<p align="center">
  <img src="assets/branding/odysseus-wordmark.png" alt="Upstream Odysseus wordmark" width="238">
</p>

<p align="center">
  Upstream Odysseus is a self-hosted AI workspace for chat, agents, research, documents, email, notes, calendar, and local model workflows.
</p>

<p align="center">
  <a href="FORK-ATTRIBUTION.md">Attribution & Evidence</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="website/setup.md">Setup Guide</a> ·
  <a href="HANDOFF.md">Historical Fork Handoff</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="ROADMAP.md">Upstream Roadmap Snapshot</a>
</p>

<p align="center">
  <img src="assets/branding/odysseus-browser.jpg" alt="Inherited upstream Odysseus interface illustration; not a screenshot of this fork owner's deployment">
</p>

## What this fork adds

At the baseline reviewed on **October 28, 2026**, the default branch's fork-local changes were documentation and coordination metadata only. No application-code additions appeared in that comparison. Operating or evaluating upstream software is valuable work, but is not authorship of the platform. Unmerged experiments and machine-only configuration are not shipped default-branch features.

Read the [dated contribution ledger](FORK-ATTRIBUTION.md) and [September 5 operator handoff](HANDOFF.md). Historical observations and narrow test results are not fresh runtime verification or a full-suite pass.

This fork is **not automatically synchronized with upstream**. Review upstream changes and compatibility before deploying or updating; this documentation pass does not perform an upstream merge or certify the old snapshot.

## Quick Start

`dev` is this fork's default branch. The commands below clone **this fork**. For the upstream distribution instead, use [odysseus-dev/odysseus](https://github.com/odysseus-dev/odysseus) and its current installation instructions; do not assume this fork contains its newest changes.

```bash
git clone https://github.com/RichardRacette/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:7000` when the containers are healthy. The first admin password is printed in `docker compose logs odysseus`; do not publish it in screenshots or logs.

Native installs, GPU notes, Windows/macOS instructions, HTTPS, and configuration live in the [setup guide](website/setup.md). These are inherited setup instructions, not a claim that this documentation change was tested on a fresh installation.

## Upstream features in this snapshot

Credit for these inherited capabilities belongs to upstream Odysseus and its contributors:

- **Chat + Agents** — local/API models, tools, MCP, files, shell, skills, and memory.
- **Cookbook** — hardware-aware model recommendations, downloads, and serving.
- **Deep Research** — multi-step web research with source reading and report generation.
- **Compare** — blind side-by-side model testing and synthesis.
- **Documents** — writing-first editor with AI edits, suggestions, Markdown, HTML, CSV, and syntax highlighting.
- **Email** — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
- **Notes, Tasks + Calendar** — reminders, todos, scheduled agent tasks, and CalDAV sync.
- **Extras** — gallery/image editor, themes, uploads, web search, presets, sessions, and 2FA.

## Upstream demo and contribution guidance

The [Odysseus landing page](https://odysseus-dev.github.io/odysseus/) and its tour belong to the upstream project. They are not a custom deployment, original product demonstration, or traction attributable to this fork.

Inherited contribution guidance lives in [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md). Proposing a fork patch is not the same as having a contribution accepted upstream; cite the upstream pull request and its actual status when claiming an upstream contribution.

## Security

Odysseus is a self-hosted workspace with powerful local tools. Keep auth enabled, keep private data out of Git, and do not expose raw model/service ports publicly.

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Keep `LOCALHOST_BYPASS=false` outside local development.

Deployment details are in the [setup guide](website/setup.md#security-notes). A historical configuration or passing test is not a security certification. Do not publish private operational records, addresses, credentials or unapproved screenshots.

## License and upstream credit

AGPL-3.0-or-later, as identified by this repository. See the unchanged [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md). Upstream branding, artwork, code and third-party components retain their attribution; this pass does not relicense them.
