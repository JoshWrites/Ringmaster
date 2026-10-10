# Ringmaster Documentation — Design Spec

## Purpose

Create a docs-as-code documentation site for Ringmaster (GPU workstation orchestrator) using MkDocs Material. The documentation serves two audiences simultaneously: sysadmins/homelabbers who want to run Ringmaster, and hiring managers evaluating Anny Levine's technical writing and docs-as-code skills.

## Phased Approach

### Phase 1 (this spec): 10 pages — tight, polished, complete

Ship a documentation site that covers "use it" (quickstart + guides), "understand it" (architecture overview), and "look things up" (reference). Every page is portfolio-quality.

### Phase 2 (future): expand to ~17 pages

Split architecture into separate pages (scheduler, worker, GPU, auth). Add dedicated guides for webhooks, notifications, power management. Add contributing.md.

---

## Site Structure (Phase 1)

```
docs/
├── index.md                 # Landing — what Ringmaster is, who it's for
├── quickstart.md            # Zero to running in 5 minutes, one happy path
├── guide/
│   ├── installation.md      # Full installation (based on existing 331-line guide)
│   ├── configuration.md     # ringmaster.yaml walkthrough, every section explained
│   ├── tasks.md             # Task submission, queue control, approval workflow
│   └── sessions.md          # Interactive sessions — when/why, lifecycle, idle timeout
├── architecture/
│   └── overview.md          # System diagram, scheduler, worker, GPU, auth — all in one
├── reference/
│   ├── api.md               # All 19 REST endpoints
│   ├── cli.md               # All 9 CLI commands
│   └── config.md            # Every config field with defaults and rationale
```

Reading path: **landing → quickstart → guides → architecture → reference**

---

## Content Approach

### Voice and tone

Clear, direct, second person. "You submit a task" not "tasks are submitted." The audience runs Ollama on their home network — they know what a GPU is. Don't dumb down, don't jargon up.

### Guide pages follow this pattern

1. One-sentence summary of what this page covers
2. The thing you most likely want to do (happy path with a working example)
3. Details and options
4. Gotchas / things that trip people up

### Architecture overview follows

1. What each component does and why it exists
2. System diagram showing how components connect
3. Design decisions — not just what but why this way
4. State charts for scheduler (6 states: queued, running, completed, failed, deferred, cancelled) and worker (13-step execution sequence)

### Reference pages

Lookup tables, not prose. Endpoint signature, parameters, response, example request/response. CLI command, flags, examples.

### Quickstart

The most important page. Clone → install → configure → submit a task → see the result. Under 5 minutes of reading. One happy path, no detours, no options.

---

## Existing Content to Reuse

| Source | Destination | Action |
|--------|-------------|--------|
| `docs/Anny/installation.md` (331 lines) | `docs/guide/installation.md` | Fold in mostly intact, clean up, add MkDocs formatting |
| `docs/Anny/README.md` | `docs/index.md` | Extract marketing copy for landing page |
| `ringmaster.example.yaml` | `docs/guide/configuration.md` | Annotate each section |
| `ringmaster/models.py` (Pydantic) | `docs/reference/api.md` | Extract endpoint contracts |
| `ringmaster/cli/main.py` (Click) | `docs/reference/cli.md` | Extract command signatures |
| `ringmaster/config.py` (Pydantic) | `docs/reference/config.md` | Extract field definitions with defaults |
| Code docstrings throughout | Architecture overview | Extract design rationale |

---

## MkDocs Setup

### mkdocs.yml

- **Theme:** Material with dark/light toggle
- **Navigation:** Explicitly ordered to match reading path
- **Features:** Search, code highlighting (YAML, Python, bash, JSON), admonitions (tip/warning/note), navigation tabs
- **Output:** `site/` directory (gitignored)

### Deployment

- **GitHub Pages** via `mkdocs gh-deploy` (pushes to `gh-pages` branch)
- **GitHub Actions** workflow to auto-deploy on push to main
- Live site at `https://joshwrites.github.io/Ringmaster/`

### Repo changes

- `mkdocs.yml` added to repo root
- `docs/` reorganized for MkDocs (existing `docs/plans/` and `docs/specs/` stay, not part of user-facing site)
- `.gitignore` updated to exclude `site/`
- Existing `docs/Anny/` content migrated into new structure

---

## Portfolio Integration

### LevineLabs tech writing card

Add to `src/anny/techwriting/index.html`:
- **Type:** Docs-as-code · Developer documentation · MkDocs
- **Title:** Ringmaster Documentation
- **Description:** Developer documentation for a GPU workstation orchestrator daemon. Full docs site with quickstart, user guides, architecture deep dive, and API/CLI/config reference. Built with MkDocs Material for an audience of sysadmins and homelabbers running local AI inference.
- **Links:** "View docs site" → GitHub Pages URL, "View source" → repo docs folder

### CV update

Add Ringmaster docs to longform CV projects section.

---

## Scope Boundaries

### In scope
- 10 documentation pages (Phase 1)
- MkDocs Material site with GitHub Pages deployment
- Portfolio card on LevineLabs site
- CV update

### Out of scope (Phase 2)
- Separate architecture pages (scheduler, worker, GPU, auth)
- Dedicated guides for webhooks, notifications, power management
- contributing.md
- Auto-generated API docs from OpenAPI schema
- Versioned documentation

---

## Success Criteria

1. A sysadmin can go from "what is this?" to "I have it running and submitted a task" by reading quickstart + installation
2. A hiring manager can see the docs site, click through 3-4 pages, and conclude "this person knows how to write documentation"
3. The source markdown in the repo demonstrates docs-as-code practice (MkDocs config, structured content, consistent formatting)
4. Every page is portfolio-quality — no thin pages, no placeholders, no "coming soon"
