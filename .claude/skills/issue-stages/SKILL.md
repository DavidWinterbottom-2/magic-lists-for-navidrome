---
name: issue-stages
description: Drive a GitHub issue through the claude-tracker board stages (stage:define → investigate → spec → implement → test → review → deploy), one gated stage at a time. Use when asked to work on, pick up or take an issue through the process. Not for a bare request to move an issue to a given stage, which is just a label swap.
---

Work an issue as a **gated pipeline**. Each stage has a gate: a checkable condition, usually David's explicit approval in the conversation. Do the stage's work, report it on the issue, and stop at the gate. Never do a later stage's work early: a fix that's already built makes define and spec theatre. This happened on german-story-generator#48, whose first PR was closed unmerged.

## Rules

- **Labels:** an issue carries exactly one `stage:*` label. Move it only when the current gate is passed. Moving it is your job; passing the gate is David's.
- **Gates need explicit approval.** Silence, a question, or "looks good so far" doesn't pass a gate. Ask.
- **Issues close at merge.** The PR says `Closes #N`. Deployment is tracked on the commit, by the deploy check (stage 7), not by keeping the issue open.
- **Never close an issue by hand**, and never merge. David merges.
- **Size the issue at define:**
  - **Small** (one module, no new behaviour surface): define and spec collapse into one issue comment plus a short `proposal.md` + `tasks.md`, with 3–4 grill questions and no `design.md`. Review is code, security and documentation; no architecture review.
  - **Big** (cross-repo, new component or workflow, auth-adjacent, or new tooling): every stage in full.
  - If unsure, call it big.
- Mention `#N` in every commit and PR so the tracker links the work.
- **Report the session's cost on the issue.** Every gate comment, and the last comment a session posts before it ends or hands off, ends with `Session cost: $X (session <id>)`. Read it with the `get_session` tool, no session id: `external_metadata.usage.cost_usd`, which is the session's running total at list price. A session that spans stages also gives the increase since its last report. A session it starts (a host or cloud session) reports its own cost the same way. If the tool isn't available, say so on the issue rather than guessing.

## Stages

### 1. define: what is needed

Read the issue and the repo's `CLAUDE.md`. Find what the issue missed. In #48 that was: the Docker image didn't ship the version file, and `Closes` would have closed the issue before deploy. Ask David every open requirement question, each with your recommended answer.

**Gate:** David agrees the requirements list. Post it on the issue as **"Define: agreed requirements"**, with numbered requirements and a definition of done. Then move to `stage:investigate`, or straight to `stage:spec` if nothing is unknown.

### 2. investigate (optional): find out what's going on

Use this stage for bugs, unknown behaviour or unconfirmed assumptions (which repos are affected, how a system really behaves). Use the `diagnosing-bugs` skill for bugs. Write the findings on the issue.

**Gate:** every unknown from define is answered, or recorded as an explicit assumption that David accepts.

### 3. spec: write down what will be built

1. **OpenSpec:**
   - Branch `openspec/<change-id>`. Implementation continues on the same branch, so the one PR carries spec and code.
   - If the repo has no `openspec/`, run `npm i -g @fission-ai/openspec@1.14.3` (if `openspec` is missing), then `openspec init --tools claude --no-animation --no-copilot-cloud`.
   - Then follow the `openspec-propose` skill to write `proposal.md`, the spec deltas, `design.md` (big issues only) and `tasks.md`.
   - Each task carries its own verification. Tests and docs go in the task group that needs them, not in a final group.
2. **Screenshots for any UI change.** Prototype it locally (never committed), screenshot light, dark and 390px wide with Playwright, and commit the images under `openspec/changes/<id>/screenshots/`. Send them to David as files. Never send a path that only resolves inside the change folder.
3. **Estimate the remaining cost** in `tasks.md`: model calls, tokens and dollars to deploy, from current model pricing (the `claude-api` skill). Compare finishing in the current session with a fresh one.
4. **Grill** David on every call you made yourself (use the `grilling` skill if present). Ask one round at a time, number the questions, and give a recommended answer for each. Fold each answer into the artifacts as a numbered decision ("Agreed at spec").
5. `openspec validate <id> --strict` passes. Push the branch.

**Gate:** the grill has no open questions and David approves the spec. Post **"Spec: approved"** on the issue, listing the decisions, then move to `stage:implement`.

### 4. implement: build it

- Follow the `openspec-apply-change` skill. Write tests first, and tick each task only when its stated verification passes.
- If the spec turns out wrong while implementing (in #48, the daily Watchtower schedule broke the planned post-build check), pause. Say so, propose the change, and update the artifacts before carrying on.
- Run the repo's own checks. Bump the version with the repo's tool and add a CHANGELOG entry.
- Open **one PR** carrying the spec, code and tests. It follows the repo's PR template, says `Closes #N`, and states the merge order of any cross-repo PRs.
- Subscribe to the PR and drive CI to green. If every job fails in about 2s with no runner, Actions is blocked at the account level: re-run once, then stop and tell David. It isn't a code failure, so don't push fixes for it.

**Gate:** all tasks except the deploy group are ticked, and CI is green. Move to `stage:test`.

### 5. test: prove the tests prove it

Run an independent test review with the `test-critic` agent. Give it the spec paths and code, not your conclusions. Classify each finding as ACCEPT, REJECT or DEFER, with a reason. For each accepted gap, write the test, then break the behaviour on purpose and confirm the test fails (then restore it). Post the classification on the issue.

**Gate:** every accepted finding is fixed, CI is green, and David says to move on. Move to `stage:review`.

### 6. review: iterate until it passes

Run the reviews in [REVIEWS.md](REVIEWS.md) in parallel. Fix what you accept, and send each review that found issues back for another round. Repeat until every review says PASS. Log them with the `record-review` skill.

**Gate:** every review passes and is logged. Then:
- Create the **follow-up issues** the requirements call for, in this repo or others, each at `stage:define` and linking this issue.
- Post **"Review: passed"** on the issue with the follow-up links, and move to `stage:deploy`.

### 7. deploy: merge, then confirm live

1. **David merges the PR**, which closes the issue. If someone else merged it, carry on from step 2 and tell David.
2. **Deploy and verify from a session on the host**: the Pi, or a desktop that can reach it. Every deploy runs here; don't wait for Watchtower's next pull. A cloud session can reach neither the host's Docker nor, usually, its public URL, so it can't deploy or check the host.
   - **Start it** with `claude remote-control` in the service's `docker-infra` folder, in default permission mode. David approves each `docker` command, so he must be reachable while it runs. Touch only this service's stack, and never approve broader shell access.
   - **`gh` must be signed in on the host** with access to the service repo. If it isn't, tell David. The pull and the `/health` check are the only steps that need the host. The `gh` steps can run from the cloud session or from David.
   - **Precondition:** the image-publish run on the merge commit has succeeded (`gh run list -R <owner>/<repo> --commit <merge-sha>`, the workflow that publishes the image, completed and successful). Until it has, `latest` is still the old build and a pull changes nothing.
   - **Pull and restart this service only:** `docker compose pull <service> && docker compose up -d <service>`.
   - **Check `/health` on the host:** `curl` the service's local port from its compose file. A `version` equal to the version declared on `main` is a pass. Read that without a checkout: `gh api repos/<owner>/<repo>/contents/<version file> -H 'Accept: application/vnd.github.raw'`. If Watchtower recreates the container at the same moment, that's harmless: run the check again. No `version` field means the old build is still running. A refused connection means the container isn't up.
   - **Record it:** `gh workflow run verify-deploy.yml -R <owner>/<repo> --ref main`, then `gh run watch -R <owner>/<repo>` until it passes. It checks the public `/health` and records the result as a GitHub `production` deployment. The `-R` matters: the session sits in `docker-infra`, so a bare `gh` targets that repo.
   - **Live** means the commit of the latest successful `production` deployment contains the change. Later commits on `main` (follow-up PRs, the archive PR) include it, so a pass on one of them counts.
   - **Apps without `verify-deploy` yet** get it from their follow-up issue. Until then, the host `/health` check, pasted on the issue, is the evidence.
3. **If it fails:**
   - **The deploy breaks the service:** stop and tell David. Don't roll back, or touch other stacks, without his say.
   - **`verify-deploy` logs `<no answer>`:** this isn't a verdict, because the old version and an unreachable host print the same thing. Use the host `/health` check to tell which.
   - **The image never publishes** (the publish run failed or never started): stop and tell David. Pulling won't help.
   - **No host session is possible** (the host is down, or David can't start a session): stop and tell David. Watchtower still deploys at 08:00 and the scheduled `verify-deploy` records it. The stage isn't done until a host `/health` pass or a successful `production` deployment exists.
   - **Actions can't run** (every job fails in about 2s with no runner): re-run once, then stop and tell David. He may accept the host `/health` check, pasted on the issue, in place of the deployment record. Say so on the issue.
4. **Deploys that need manual infra work** (compose changes, a new service, DNS or SSO, secrets) get their own issue in `docker-infra` or `azure-infra`, with an open / running / closed workflow.
5. **Archive** the OpenSpec change with `openspec-archive-change`, only after step 2 passes, because its version bump moves `main` past production. It's a new PR. Keep it docs-only (`openspec/`, `docs/`, Markdown; nothing that ships in the image or changes CI). Where the repo exempts docs-only PRs from the version bump, don't bump: `main`'s version stays the one production already serves, so no deploy follows. Only in a repo without that exemption does it bump. Then the scheduled `verify-deploy` fails until that commit is deployed too, so run step 2 again after it merges. It needs a checkout of the service repo, so it runs in a cloud session, not the host session.
6. **Compare the actual cost** with the spec's estimate. Sum the `Session cost` lines on the issue, taking each session's latest figure. Record the comparison in `docs/reviews/LOG.md` as part of the archive PR. Host sessions still to come aren't included; David may add them.

**Gate / done when:** a successful `production` deployment on the merge commit or a later `main` commit, the change archived, and the cost comparison recorded. If the archive PR bumped the version, done also needs a successful `production` deployment on that commit. David confirms done on the issue. The `stage:deploy` label stays on the closed issue.

## Cost

A long session costs more per call, and the cost jumps whenever the 1-hour prompt cache expires. Hand waits of an hour or more (waiting for a merge, an overnight gap) to a fresh session, primed with the spec's `tasks.md` and the issue thread. The deploy itself runs in a host session (stage 7), started after the merge. Spawn review agents only at their stage, never "to be safe".
