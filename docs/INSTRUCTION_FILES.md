# AI Instruction Files — Layout and Sync

This repo gives AI agents (Claude Code, GitHub Copilot) layered instructions so the right rules load at the right time without bloating every prompt.

## Why this exists

- A single 500-line instruction file bloats every Claude/Copilot turn by ~15K+ tokens. Anthropic's official guidance is **under 200 lines per CLAUDE.md**.
- Claude Code auto-loads ancestor `CLAUDE.md` files **at session start** and descendant `CLAUDE.md` files **on demand** (when Claude reads a file in that subtree).
- GitHub Copilot reads a universal `.github/copilot-instructions.md` plus path-scoped `.github/instructions/*.instructions.md` files (matched by `applyTo:` globs).
- We exploit both lazy-loading mechanisms to keep the always-on context small while still giving full guidance when work touches a specific area.

## File layout

```
CLAUDE.md                          # Root — always-on rules (~135 lines)
docs/
  ARCHITECTURE.md                  # Descriptive prose; not auto-loaded
  INSTRUCTION_FILES.md             # This file
.github/
  copilot-instructions.md          # Mirror of root CLAUDE.md (Copilot universal)
  instructions/                    # Path-scoped Copilot files — one per directory CLAUDE.md
    cli.instructions.md
    services.instructions.md
    bridge.instructions.md
    shared.instructions.md
    infra.instructions.md
    infra-gateway-controller.instructions.md
    types.instructions.md
cli/CLAUDE.md                      # Lazy-loaded by Claude
services/CLAUDE.md
services/bridge/CLAUDE.md
services/shared/CLAUDE.md
infra/CLAUDE.md
infra/gateway-controller/CLAUDE.md
types/CLAUDE.md
.claude/skills/                    # Long-form playbooks (loaded only when invoked)
  export-new-model-to-types/SKILL.md
  bump-ib-async-version/SKILL.md
```

The `applyTo:` glob for each Copilot instruction file lives in its YAML frontmatter — that's the source of truth. Most match the obvious directory (e.g. `services/bridge/**`), but two intentionally cover paths beyond it:

- `cli.instructions.md` → `cli/**,Makefile,docker-compose*.yml,env_examples/**,terraform/**` (the CLI owns deploy, env templates, and IaC, so the same rules apply to all).
- `infra.instructions.md` → `infra/**,docker-compose*.yml` (Caddy snippet + compose changes are paired).

When in doubt, read the frontmatter.

## Maintenance contract

Each `<dir>/CLAUDE.md` has a paired `.github/instructions/<slug>.instructions.md` covering the **same rules** plus a YAML frontmatter:

```markdown
---
applyTo: "<glob matching the same files>"
---

<rules, same content as the CLAUDE.md — see "Allowed divergence" below>
```

**When editing any `CLAUDE.md`, update its mirror in the same commit.** Same for the root `CLAUDE.md` ↔ `.github/copilot-instructions.md` pair. The rule **content** must stay in sync; presentation may differ slightly.

### Allowed divergence

These differences are intentional and don't violate the sync contract:

1. **Cross-references.** `CLAUDE.md` files use Markdown links to other CLAUDE.md files (`[../CLAUDE.md](../CLAUDE.md)`); Copilot mirrors reference siblings by name (`services.instructions.md`) since the directory structure differs.
2. **Glob breadth.** `applyTo:` globs may legitimately cover files outside the CLAUDE.md's directory (e.g. `cli.instructions.md` applies to `Makefile`, `docker-compose*.yml`, `env_examples/**`, `terraform/**` because those files share the same deploy rules). Document such inclusions inline.
3. **Skill pointers.** CLAUDE.md may link to a Skill via Markdown link (`[export-new-model-to-types](.claude/skills/export-new-model-to-types/SKILL.md)`); the Copilot mirror references it by name only.
4. **Summarization for tool fit.** Copilot mirrors may abbreviate, reorder, or lightly rephrase prose when needed for brevity or tool-specific formatting, as long as they preserve the same normative rules, constraints, required steps, and factual content as the paired `CLAUDE.md`. Summarization must not drop or weaken any requirement.

### What must stay identical

- Every **rule** (every bullet starting with **"…"**, every numbered procedure step, every code block enforcing a pattern) must appear in both files with the same meaning and enforcement.
- Tables of facts (env vars, error codes, source → output mappings) must match row for row unless a documented tool-specific formatting difference applies.

### Sanity check during PR review

A simple grep is more useful than a strict diff: confirm both files have the same set of rule-bullets:

```bash
diff <(grep -E '^- \*\*' services/X/CLAUDE.md) \
     <(grep -E '^- \*\*' .github/instructions/X.instructions.md)
```

Empty diff = rules match. Anything else = a rule drifted between the two.

## What goes where

- **Root `CLAUDE.md`** — rules that apply to every file in the repo (security, deprecated APIs, type safety, error handling, concurrency, dependencies, Dependabot bump policy).
- **Directory `CLAUDE.md`** — rules that only matter when working in that subtree (cli deploy specifics, bridge event wiring, Gateway controller behaviour).
- **`.claude/skills/<name>/SKILL.md`** — multi-step procedures only relevant for rare tasks (exporting a new model, bumping ib_async). Zero context cost until invoked.
- **`docs/ARCHITECTURE.md`** — descriptive prose (file trees, system diagrams, model inventory). Not auto-loaded.

## When to move a rule

| Lives where? | When | Why |
| --- | --- | --- |
| Root | Applies repo-wide or could be violated in any file | Always-on for safety |
| Directory CLAUDE.md | Only applies in that subtree | Lazy-load saves context |
| Skill | Multi-step procedure for a rare task | Zero context cost until invoked |
| ARCHITECTURE.md | Descriptive prose, not enforceable | Reference, not a rule |

If a rule appears in three places, collapse it. Cross-reference instead of duplicating.

## Verifying Claude loads what you expect

When working in `services/bridge/`, Claude should have:
1. Root `CLAUDE.md` (always).
2. `services/CLAUDE.md` (descendant, loaded on first read).
3. `services/bridge/CLAUDE.md` (descendant, loaded on first read).

Subdirectory `CLAUDE.md` files **only load when Claude reads a file in that subtree** — purely listing the directory doesn't trigger the load.
