#!/usr/bin/env bash
# PostToolUse hook, matched on Write|Edit|MultiEdit in .claude/settings.json.
# Canonical home: devcontainer-sandbox/.claude/hooks/ — downstream repos vendor
# this file via template-sync; edit it here, not in a downstream copy.
# (Filename is historical, from the retired three-agent pipeline; it is kept so
# existing settings.json registrations across repos stay valid.)
#
# Enforces the verification half of the AI engineering workflow
# (standards/AI-WORKFLOW.md): after any file modification it reminds the Lead
# Engineer that the work is not done until it is verified — and, at the higher
# rigor levels, independently criticised.

# Stay silent in a repo that has the hook but not (yet) the workflow agents —
# e.g. freshly bootstrapped, before the first template-sync run.
[ -f "${CLAUDE_PROJECT_DIR:-.}/.claude/agents/architect.md" ] || exit 0

cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"[workflow hook] A file was just written or edited. Per the AI engineering workflow: before this task is considered done, the Lead Engineer must run the automated verification relevant to the change (tests, linters, type checks, builds) and investigate any failure rather than assuming the implementation is correct — never remove or weaken a failing test to get green. For a NORMAL task, add an independent critic review if it materially improves confidence; for a COMPLEX task, the relevant critics (correctness-critic, test-critic, architecture-critic, security-critic, performance-critic) must review before completion, with each finding classified ACCEPT / REJECT / DEFER. Do not invoke critics for trivial changes."}}
EOF
