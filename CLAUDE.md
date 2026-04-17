# ILUMINATY Project Context

This file is the shared start context for Claude Code and Codex.
Use it as the source of truth before touching code.

## Project Goal

ILUMINATY provides "eyes + hands" for external AI brains:
- Perceive what is happening on desktop surfaces.
- Expose reliable control primitives.
- Keep context continuity across actions.
- Evolve from MCP-first toward CLI-first operation.

## Main Repositories/Modules

- `iluminaty/`: core runtime (capture, perception, actions, API, MCP bridge).
- `desktop-app/`: desktop shell and packaging.
- `api/`: integration-facing API helpers.
- `website/`: public site/docs surface.
- `tests/`: integration, stability, and release-gate tests.
- `scripts/`: automation and operational tooling.

## Architecture Guardrails

- Do not rewrite core modules without migration shims.
- Keep behavior parity between runtime, desktop app, and docs/contracts.
- Changes must preserve multi-monitor correctness.
- Do not silently weaken authentication, kill switch, or safety pathways.

## Development Workflow

1. Read relevant files first.
2. Make minimal, bounded edits.
3. Run focused tests, then full regression when touching shared flows.
4. Report findings with concrete file/line references.
5. Keep docs and contracts synchronized with behavior.

## Rule Packs

Detailed standards live in:
- `.claude/rules/code-style.md`
- `.claude/rules/testing.md`
- `.claude/rules/api-conventions.md`
- `.claude/rules/auditoria-exhaustiva-2026-04-17.md`

## Command Packs

Reusable workflows live in:
- `.claude/commands/review.md`
- `.claude/commands/fix-issue.md`

## Specialized Agent Profiles

- `.claude/agents/code-reviewer.md`
- `.claude/agents/security-auditor.md`

## Hooks

- `.claude/hooks/validate-bash.sh`: defensive command validation.
