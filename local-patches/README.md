# Local patches (this install only — NOT upstream)

Upstream: NousResearch/hermes-agent @ 8a33891bdd (clean clone). Since 2026-10-11 these
patches live as COMMITS on local branch `local/runaway-hardening` (tags `patch-0001`,
`patch-0002`, `patch-0003`, `patch-0004` pin the four patch commits by name), and the live checkout
`/home/you/.hermes/hermes-agent` sits ON that branch with a clean tree.

## Update procedure — branch-first (2026-10-11)

Config (in `~/.hermes/config.yaml`, keys set by the update-strategy batch):

- `updates.parked_branch_strategy: update_in_place` — `hermes update` merges
  `origin/main` INTO `local/runaway-hardening` (leaving a best-effort
  `pre-update-<stamp>` tag) instead of stash-parking a dirty tree; on merge conflict
  it aborts cleanly with a loud message.
- `updates.auto_switch_parked_branch: true` (default) — REQUIRED for the guard to
  fire at all (update_cmd_git.py:190 → update_cmd.py:1350-1357). Do not set false.
- `updates.pre_update_backup: quick` — snapshots HERMES_HOME state (config, cron,
  auth, profile DBs). General insurance, NOT patch protection.
- Channel pin: `update.installs.45626819cf168545.channel: main` (see CHANNEL HAZARD).

Normal update: just run `hermes update` (or the desktop update button). It merges
upstream into the branch; the patch commits ride along in history.

## POST-UPDATE procedure — re-derive the patch exports (after EVERY real update)

The `.patch` files below are context-based exports and go STALE as upstream drifts
(e.g. ruff reformat commit 2ff58849f4 changed context lines so the old 0002 no longer
applied to pristine main — caught 2026-10-10). After every real `hermes update`,
refresh them by cherry-picking the TAGGED patch commits onto the new tip (tags never
depend on rev-list topology, which breaks after the first merge):

```bash
cd /home/you/.hermes/hermes-agent
git fetch origin
git worktree add --detach ~/.hermes/cache/patch-rederive origin/main
cd ~/.hermes/cache/patch-rederive
# ONE STEP PER PATCH, tags from HEAD — never fixed HEAD~N offsets:
git cherry-pick patch-0001 && git tag -f patch-0001 HEAD
git cherry-pick patch-0002 && git tag -f patch-0002 HEAD
git cherry-pick patch-0003 && git tag -f patch-0003 HEAD
# 0004 is a 3-commit series (bc71d7e53f + c05877a834 + 33c00ded4e). Pick the range FIRST,
# from the STILL-ORIGINAL base tag — re-pointing patch-0004-base BEFORE the pick expands the
# range to the whole branch (10 commits, guaranteed conflicts):
git cherry-pick patch-0004-base..patch-0004   # resolve conflicts here if any — NEVER assumed automatic
git tag -f patch-0004 HEAD
git tag -f patch-0004-base patch-0003      # ONLY NOW re-point the series base at the new post-0003 tip
# EXPORTS: diff from each patch's TRUE base (table below), to ABSOLUTE paths under the LIVE
# repo (origin/main has no local-patches/ dir — redirecting into the worktree then deleting it
# destroys the exports):
git diff origin/main patch-0001 > /home/you/.hermes/hermes-agent/local-patches/0001-estop-gates-process-wake.patch
git diff patch-0001 patch-0002 > /home/you/.hermes/hermes-agent/local-patches/0002-runaway-watchdog.patch
git diff patch-0002 patch-0003 > /home/you/.hermes/hermes-agent/local-patches/0003-skill-rollback-quarantine-fix.patch
git diff patch-0004-base patch-0004 > /home/you/.hermes/hermes-agent/local-patches/0004-stale-wake-age.patch
cd /home/you/.hermes/hermes-agent && git worktree remove --force ~/.hermes/cache/patch-rederive
# then commit the refreshed exports on local/runaway-hardening (adjust paths if you
# copied them over), or repeat the cherry-pick directly on the branch if the in-place
# merge aborted on conflict:
#   git cherry-pick patch-0001 patch-0002 patch-0003   # on the branch, resolving as needed
```

### TRUE EXPORT BASES (state at 2026-10-16 — re-derive per recipe after any update)

| export | true base | proof sha256 (git diff <base> <tag> == file) |
|---|---|---|
| 0001 | merge-base 8a33891bdd (NEW origin/main tip after an update) | 48370faea87f86dd… (2026-10-22 audit: file == `git diff 8a33891bdd patch-0001` byte-verified; prior cell value was unreproducible from any ref — corrected) |
| 0002 | patch-0001 | fc0b085bc07106ef… (2026-10-22 amendment: +config_defaults key registration +test) |
| 0003 | patch-0002 (`-- . ':!local-patches'`; excluded-diff export so `0003` never embeds `local-patches/0001`, `0002`, or `README.md`) | 34466501a7a219a89f1567ffc402331ee689eabb1e177b7f6d0cfd0ef28cca3d |
| 0004 | **patch-0004-base** (= 9299009f39, the pre-0004 series tip — patch-0003 tag is NOT the boundary; 0004 = bc71d7e53f + c05877a834 + 33c00ded4e, only the last tagged) | a01ab1988abd149d… (2026-10-22 audit: file REGENERATED from true base after the 0002 amendment had staled its context lines; byte-verified == diff) |
| 0005 | **patch-0005-base** (= 57205e5e34, the README-fix tip before the 0005 code commit) | b74639277157e052… (2026-10-22 audit: file REGENERATED from true base after the 0002 amendment had staled its context lines; byte-verified == diff) |
| 0007 | **patch-0007-base** (= the docs tip before the 0007 code commit) | af2b77a4c31ca91b… |
| 0008 | **patch-0008-base** (= 7d08a6e155) | 58380440c2cc5b2b… (2026-10-22: true range export — replaces the old two-commit listing, dd4ca978…) |
| 0009 | **patch-0009-base** (= 32f9c1186d) | 3324ab9c80 |
| 0010 | **patch-0010-base** (= patch-0009, 57cbad4af1) | 0010-foreign-gateway-live-stream-bridge.patch |
| 0011 | **patch-0011-base** (= patch-0010, af13e5c8e2) | ef62c18723357a62b9105cbe8f47efe2226c7b4c9dc6cc43501f47dbf7c61c1b |
| 0012 | **patch-0012-base** (= patch-0011, 14e574bb50) | d603b61e824e32e45a94bc063d3b1a933ec255432fb62bc9720f89b0b35d563e |
| 0013 | **patch-0013-base** (= patch-0012, 8a141e287d) | e1243ff0b216e4f29e69d280ab94e4fc9e6035db2db08adf6a5f4f89b56dd0cd |
| 0014 | **patch-0014-base** (= patch-0013, 702d9b2af4) | bfce2a02267a5617411d6fb5f3561d9dba6425c6edb9e3b9cea2094009a2a84c (2026-10-22: `git diff patch-0013 patch-0014 -- . ':!local-patches'`) |
| 0015 | **patch-0015-base** (= patch-0014, f67a30e686) | 393e38f9fff55b21322361f4af9f95d788700a2d93e9b6c8b9c883d40e779281 (`git diff patch-0015-base patch-0015 -- . ':!local-patches'`) |
| 0016 | **patch-0016-base** (= patch-0015, 1523dce129) | 67d249ae2c15b02c4adab52d98cdf2267fd7dc4cb938013b38433f646daf2353 (`git diff patch-0016-base patch-0016 -- . ':!local-patches'`) |
| 0017 | **patch-0017-base** (= patch-0016, 04f89979d8) | ef9cbb89534a472dad919fe1f7211dab4c5a213500f674a06ffc0cee05bf7863 (`git diff patch-0017-base patch-0017 -- . ':!local-patches'`) |

## Emergency fallback — sequential apply (patches only, no branch)

`0002` stacks on `0001`, `0003` on `0002`, `0004` on `0003` — never `--check` 0003 against pristine main.
Apply IN ORDER:

```bash
git apply --check local-patches/0001-estop-gates-process-wake.patch && git apply local-patches/0001-estop-gates-process-wake.patch
git apply --check local-patches/0002-runaway-watchdog.patch && git apply local-patches/0002-runaway-watchdog.patch
git apply --check local-patches/0003-skill-rollback-quarantine-fix.patch && git apply local-patches/0003-skill-rollback-quarantine-fix.patch
git apply --check local-patches/0004-stale-wake-age.patch && git apply local-patches/0004-stale-wake-age.patch
```

(resolve conflicts by hand if the upstream files moved; then regenerate the .patch via
git diff.)

## CHANNEL HAZARD — stable channel detaches the checkout (bypasses the branch guard)

Source installs default to the STABLE release channel since upstream 6a92a2aa49. A
stable-channel update lands the checkout DETACHED at its release tag — bypassing the
parked-branch guard entirely and dropping both patches from the running code. This
install is pinned: `update.installs.45626819cf168545.channel: main`. The weekly
staging script (`~/.hermes/scripts/stage-hermes-update.sh`, Sundays 09:17, verdict to
Discord DM) asserts this record on every run. If it is ever not `main`, re-pin:

```bash
HERMES_HOME=/home/you/.hermes hermes update --set-channel main
```

(metadata-only: writes the channel record and exits BEFORE the update lock / any
update side effect — no update is performed).

## When to DROP a patch (upstream superseded) — retained verbatim

If a future upstream release includes equivalent estop gating for process-wake
notifications (check for PRs referencing run_inbound._hm_estop_gate is_internal bypass,
or issues #96247/#96318), drop patch 0001 instead of re-applying; if it ships a bounded
auto-wake chain (OmO native fixed this class in v5.1.27 with 150 auto-turns/message),
drop patch 0002. If upstream ships an equivalent rollback-quarantine relocation (search
skill_manager_batch _restore_snapshot), drop patch 0003. If upstream stamps exit ages on
process-completion notifications (exited_at) and stale-wake labeling, drop patch 0004.
DROP DIRECTION (completes the above): delete the patch's export .patch file AND its curated
marker/wire-in rows in stage-hermes-update.sh (and bump MARKER_TABLE_PATCHES), or the gate goes
permanently red with a misleading reason (leftover-export ERROR or marker-probe NO-GO).
DROPPING A PATCH: delete its tag AND its base tag if it has one, re-state the next export's
true base in the table, and refresh that export before the next fallback use.

## 0001-estop-gates-process-wake.patch (2026-10-10)
Why: background-process completions are injected as user-role prompts that start new
agent turns whenever a session is idle; they are internal events and BYPASS the global
estop (gateway/run_inbound.py `_hm_estop_gate` returns None for is_internal). Result:
after the user typed "pause/stop everything" (session c71d14, 2026-10-09), four stale
worker exit-notices each started a fresh model turn until the user manually interrupted.
What: `hermes pause` (ESTOP sentinel) now holds ALL wake paths —
tui_gateway/session_notifications.py `_notif_claim_turn` (desktop/TUI),
gateway/run_notifications.py `_inject_watch_notification` (gateway, at function entry so
the api-server self-post branches are covered too), and
hermes_cli/cli_process_notifications.py `_drain_process_notifications` (interactive CLI
drains — events stay queued during a pause and re-drain as one batch after `hermes resume`).
Held events requeue and deliver as one consolidated batch after `hermes resume`. No
behavior change while not paused (check_paused returns False).

## 0002-runaway-watchdog.patch (2026-10-10)
Why: the per-turn iteration cap (agent.max_turns) resets on every synthetic wake, so
notification wake chains are unbounded — the c71d14 shape (2026-10-09), where four stale
worker exit-notices each started a fresh model turn long after the user walked away.
Patch 0001 holds wakes only during an explicit `hermes pause`; 0002 bounds the chain
mechanically, per session.
What: new module agent/runaway_watchdog.py — a process-local registry keyed by session id
counting synthetic wakes CLAIMED since the last genuine user message. A wake site checks
`should_allow(key, component)` BEFORE claiming and calls `note_claim_success(key)` only
when the claim actually succeeded (A3: presents during a busy turn do not count). Trip
condition: synthetic_wakes >= max_synthetic_wakes (default 25) OR
monotonic-since-last-genuine-user-message > max_silent_hours (default 4h). On trip the
claim is refused — held events requeue under patch-0001 semantics and batch-deliver after
the next user message — and a ONE-SHOT notice logs via `agent.runaway_watchdog`
("session held by runaway watchdog: N auto-turns without user input — send a message or
/stop"); the TUI/desktop additionally emits one status.update row per episode
("runaway watchdog holding auto-wakes (N without user input); send a message or /stop",
via _notif_loop_status, A4). A genuine user submit resets the counter and releases the
hold. PER-SESSION scope by design — never touches the global estop, cron, kanban, or
discord.
Wire-ins (claim side): tui_gateway/session_notifications.py `_notif_claim_turn` (sid
threaded from the 6 poller callers; increment inside the admission branch where running
flips True), gateway/run_notifications.py `_inject_watch_notification` (key =
evt.session_key, fallback `_raw_process_event_session_id` for raw api-server routes —
the `_session_key_for_source` keyspace, A2; increment on adapter acceptance incl. both
_self_post_api_server branches), hermes_cli/cli_process_notifications.py
`_drain_process_notifications` (key = self.session_id; one increment per completion
batch actually queued to _pending_input — heartbeat/delegation groups do not count).
Reset sites (genuine user input only, A1): tui_gateway/methods_prompt.py
`_lock_in_submit_turn` (sid), gateway/run_inbound.py `_hm_admit_event` non-internal admit
(`_session_key_for_source(source)`), hermes_cli/cli_tui_mixin.py `_tui_handle_enter`
(idle + busy routes), cli_voice_mixin.py (both transcript submits), cli_modal_mixin.py
(editor submit). Synthetic producers (heartbeat/parked-goal//loop-tick/goal continuation
in cli_loops_mixin.py) deliberately get NO reset.
Limits: `agent:` config keys `runaway_max_synthetic_wakes` / `runaway_max_silent_hours`;
env overrides for testing: HERMES_RUNAWAY_MAX_WAKES / HERMES_RUNAWAY_MAX_SILENT_HOURS.
Any config/env error falls back to the defaults.
Caveats: the registry is process-local — a gateway/desktop/CLI restart resets counters,
and a session served by two processes keeps one counter per process (the bound splits;
degraded, never stuck). A held session is released by any genuine user message (or /stop
+ a message); held notifications are not lost, they redeliver as one batch.
Rollback: revert the 0002 files (git apply -R after reverting any later edits), restart
gateway + desktop.
Amendment (2026-10-22): the two `agent:` limit keys are now REGISTERED in DEFAULT_CONFIG
(hermes_cli/config_defaults.py, agent section — 25 / 4.0, the deliberate incident sizing),
killing the false "not a recognized config key" warning on `hermes config get/set`; pinned
by tests/hermes_cli/test_runaway_config_keys.py (registration + validator acceptance +
default parity with agent.runaway_watchdog._DEFAULT_*). Folded INTO the patch-0002 commit
by history replay (patch-0010-fixup precedent): descendants re-picked, in-range tags
re-pointed, ALL exports regenerated from true bases — 0001/0003/0004/0005/0008 shas moved
(rewrite side-effect, content identical); 0002's new export sha is in the table below.
stage-hermes-update.sh marker table: +`runaway_max_synthetic_wakes` in config_defaults.py,
+0012 row (`AlibabaTokenPlanProfile`), MARKER_TABLE_PATCHES 10→11 (0012 row was missing —
would have aborted the weekly gate).
NOTE (2026-10-22 rewrite): pre-rewrite bare commit hashes quoted in older sections
(e.g. 0004's bc71d7e53f…, 0008's 35274ace14…) are HISTORICAL — the re-pointed tags are
authoritative; rollback recipes use tags, which track the rewritten commits.

## 0003-skill-rollback-quarantine-fix.patch (2026-10-13)
Why: the skill_manage batch rollback quarantined a broken state as
`<skill>.rollback-broken` INSIDE the skills root and deleted it best-effort. On a
lane-locked (555) dir the delete failed silently; the leftover carried a duplicate
frontmatter `name:` and collided with the live skill on EVERY resolution path —
`/planner /verify /dual-review` became un-dispatchable and skill_view refused to guess
(skill-tool-drift incident, 2026-10-13, errors.log:1876-1882). The curator retry then
resolved the quarantine itself as the patch target (PermissionError cascade).
What: `_restore_snapshot` (tools/skill_manager_batch.py) moves the aside OUTSIDE the
skills tree to `<home>/backups/skill-rollbacks/<skill>-<mkdtemp>/` (home-derived via
get_hermes_home, so temp-HOME tests never write the real ~), deletes it with a
chmod-parent-retry handler that RAISES on final failure (never silent), and moves are
EXDEV-safe (rename -> copy+rm). `*.rollback-broken` dirs are never edit targets
(`_find_skill`) nor load candidates (`iter_skill_index_files`,
`is_excluded_skill_path`, `skills_tool._collect_skill_candidates`) — loader-level
immunity even if litter reappears. `_validate_batch_ops` refuses a lane-locked target
EARLY (pre-snapshot) with the sanctioned route (`~/.hermes/scripts/lane-edit.sh`;
`references/` stays writable by design), and `_guarded_write` has single-op parity —
a locked SKILL.md edit is a loud skip instead of a mid-batch failure cascade.
Tests: tests/tools/test_skill_manage_batch.py +4 (locked-refusal hygiene, 555-rollback
aside placement + byte-for-byte restore, quarantine exclusion, failure control);
12 skill-related modules green (409 passed, 1 skipped, 2026-10-13).
Rollback: `git revert patch-0003` on the branch + gateway restart.

## 0007-model-display-labels.patch (2026-10-19)
Why: one model id can front several OpenRouter endpoints at different prices —
`google/gemini-4-argon` serves a flex endpoint ($1/M in, $5/M out) and a standard one
($2/M in, $10/M out), selected by `provider_routing`. The picker prints the bare id, so an
operator cannot tell which rate a pick buys; the desktop review round needed the flex row to
announce itself.
What: display-only label map. `model_labels.<provider>.<exact model id>` in config.yaml;
`hermes_cli/inventory.py::_apply_model_labels` attaches `row["model_labels"]` to the picker
payload (providers absent from the map keep raw ids); the field is declared on the
`ModelOptionProvider` pydantic contract and the TS/OpenRPC contracts are regenerated from it
(`scripts/gen_gateway_contracts.py`); `apps/desktop/src/components/model-picker.tsx` renders the
label with the real id as the hover title; a labelled id is also injected into the provider's
model list when its curated index omits it (OpenRouter's list rotates), and the injected ids still
pass `drop_unofferable_model_ids`. Routing, pricing and switching still key off the id — nothing on
the wire changes.
Deployment note: the desktop renderer ships unpacked in `release/linux-unpacked`, so the label
appears after a `npm run build` + `npm run builder -- --dir` and an app restart.
Rollback: `git revert patch-0007` + rebuild the desktop bundle.

## 0005-spawn-admission.patch (2026-10-16)
Why: no layer bounded concurrent process fan-out — the 2026-10-16 06:31 spawn-flood OOM
(310 processes / 35 concurrent × ~0.7 GB from one resumed turn's re-executed battery) lived
exactly in that gap; patches 0001/0002/0004 bind wake chains, dispatch authorization and
stale wakes but never capped how many children run at once.
What: agent/spawn_admission.py — admission guard before EVERY local spawn path (PTY and
pipe): refuses when live `hermes-worker-*` terminal scopes ≥ `tools.max_spawn_running`
(default 12; systemd user scopes are the cross-process, crash-recovery-free counter — dead
holders self-clean via `--collect`; cron-*/kanban-* scopes excluded) or when effective
MemAvailable < `tools.spawn_mem_floor_mb` (default 4096; resources.memory_info already folds
cgroup headroom). Refusal is a normal failure-tolerant tool error naming the guard and
remedy; probe failures fail open (the 45 s swarm guard remains the backstop). Env overrides
HERMES_MAX_SPAWN_RUNNING / HERMES_SPAWN_MEM_FLOOR_MB; 0 disables. Config lane via the house
accessor (hermes_cli.config) with DEFAULT_CONFIG keys registered.
Tests: tests/agent/test_spawn_admission.py — 19 (polarity pairs, threaded storm, slot
release, retry replay, fail-open, parsing, config lane end-to-end, bus-env regression,
cron/kanban exclusion, PTY-hoist registry-level regression). Dual-review: r1 both-REJECT
(dead config lane / PTY bypass / bus-env fail-open — all folded), r2 both-PASS.
NOT SHIPPED (dropped 2026-10-16, spawn-oom-remediation task 5): a would-be 0006 (estop-honoring, admission-aware auto-continue kickoff) was dropped after the gap analysis — desktop.auto_continue.enabled=false already closes the re-execution path (control pair: enabled=True path ARMED / enabled=False path CLOSED, discriminating), and patch 0005 bounds any execution's blast radius. Standing control SUPERSEDED (A3 ruling, 2026-10-21): `desktop.auto_continue.enabled: true` is now the INTENDED steady state — flipped back on deliberately by session 20261016_070326_5ee6ca on 2026-10-17 (per Task 0.2's attribution report /home/you/Documents/.hermes/evidence/stallout-and-startup-fixes/b0-auto-continue-attribution.md) and kept, bounded by patch 0005 (spawn admission caps any execution's blast radius), patch 0002 (runaway watchdog bounds synthetic wake chains) and patch 0001 (estop gates process wake). Do not flip it off as a "fix" without re-ruling A3. See 0005's drop condition below for the fan-out patch itself.
Drop condition: upstream shipping an equivalent admission cap on terminal/process spawns
(max_concurrent_child_processes or similar on the registry).
Rollback: `git revert patch-0005` + gateway restart.

## 0008-stallout-subscribe-and-breaker.patch (2026-10-20)
Why: kanban dispatch had two stallout-shaped gaps — `work kanban create` gave workers no
way to opt into their own completion notifications (the orchestrator's `--subscribe` was
dispatch-only), and a breaker-tripped dispatch recorded a `status->blocked` audit row but
no explicit gave-up event or non-retry signal, leaving the stallout trail ambiguous.
What: `hermes kanban create --subscribe` (explicit opt-in completion subscription, commit
35274ace14); session_id stamping on dispatch rows; a breaker `status->blocked` audit row
plus `will_retry:false` on the gave-up path (breaker observability, commit 5a7aac447d);
and `_module_hermes_argv` passes `-P` (safe-path module argv) so workers resolve against
their own profile scope. Tag range: patch-0008-base..patch-0008 (commits 35274ace14 +
5a7aac447d). Export base: patch-0008-base.
Rollback: `git revert 5a7aac447d 35274ace14` on the branch + gateway restart.

## 0004-stale-wake-age.patch (2026-10-15)
Why: the runaway watchdog (0002) cannot tell a LONG-RUNNING still-alive process from a dead
one — a resumed session would hold auto-wakes for a worker that exited hours ago, and
completion wakes for ancient commands could re-fire on restart. Root need surfaced in the
2026-10-15 stale-wake investigation: gateway notifications carried no exit timestamp, so
age-gating was impossible at the injection site.
What: `exited_at` is stamped on process-completion notifications (tools/
process_registry_results.py, gateway/run_notifications.py) and the wake admission path
age-gates completion wakes against `agent.stale_wake_after_seconds` (default 300s;
hermes_cli/config_defaults.py, tools/process_registry_notifications.py) — wakes older than
the window are labeled STALE WAKE and held (0001 semantics) instead of claiming a turn.
Series: 3 commits (bc71d7e53f stamp + c05877a834 review-r1 fold + 33c00ded4e r2 fold),
exported from tag patch-0004-base; tests: tests/agent/test_stale_wake_age.py (2/23 blocked
by the pre-existing pm/manifest home-I/O location artifact — documented, not patch breakage).
Rollback: `git revert 33c00ded4e c05877a834 bc71d7e53f` on the branch + gateway restart.

## 0009-desktop-sidebar-stack-tabs.patch (2026-10-21)
Why: `openStoredSession` in `apps/desktop/src/app/contrib/wiring.tsx` called
`openSession(sessionId, navigate)` with the default `'in-place'` intent, so plain-clicking
a session row in the left sidebar replaced the active `workspace` tab instead of stacking
a new tab in `grp-main` (or filling an open blank `"New session"` tab).
What: changes `openStoredSession` (`apps/desktop/src/app/contrib/wiring.tsx`) to pass
`'stack'` (`openSession(sessionId, navigate, 'stack')`), plus applies the equivalent
1-line edit to the unpacked bundle (`release/linux-unpacked/resources/app.asar.unpacked/dist/assets/index-BWEfC7vL.js`).
Rollback: `git revert patch-0009` on the branch (and restore `~/.hermes/backups/index-BWEfC7vL.js.bak-sidebar-stack-20261021` for the unpacked bundle).

## 0010-foreign-gateway-live-stream-bridge.patch (2026-10-21)
Why: when a messaging gateway (`hermes gateway run`, e.g. `cat` profile's Discord DM `research-bot`)
runs a turn in its own OS process, Hermes Desktop (`hermes serve`) had no live stream consumer
for that turn AND its `change_watcher.py` only watched `_watcher_home()` + `_served_profile_homes`
(which missed secondary profiles accessed via REST until a WS RPC named them). As a result,
viewing a secondary profile's Discord session in Hermes Desktop showed no live reasoning stream,
no live tool call progress, and no `working` status until the turn finished and flushed to SQLite.
What:
1. `tui_gateway/change_watcher.py` (`_all_live_profile_homes`, `_sessions_sig`): automatically
   discovers and watches `state.db` across all live sibling profiles (`named_profile_is_live`,
   cached on `profiles/` dir mtime + 30s TTL) so `sessions.changed` fires immediately on any
   profile's DB commit even before a WS RPC scopes to that profile.
2. `gateway/run_turn_runner.py` (`_DesktopSessionEventBridge`): tees `AIAgent` turn callbacks
   (`message.start`, `reasoning.delta`, `message.delta`, `message.interim`, `tool.generating`,
   `tool.start`, `tool.complete`, `message.complete`) onto a bounded non-blocking daemon queue
   (4096 items, 25ms coalescing window) that POSTs batched events to the local Desktop `hermes serve`
   backend discovered via `gateway.host_rendezvous` (`ROLE_SERVE` / `ROLE_DESKTOP_SERVE`).
   Leaves platform delivery (e.g. `display.platforms.discord.streaming: false`) untouched and
   fails open silently if Desktop is not running.
3. `hermes_cli/web_routers/dashboard_ui.py` (`POST /api/dashboard/foreign-session-event`) +
   `tui_gateway/server.py` (`broadcast_foreign_session_event`, `_session_live_status`):
   token-authenticated loopback endpoint that fans foreign session events out to connected
   Desktop WebSocket transports (mapping stored `session_key` to any open Desktop runtime `sid`)
   and marks `_foreign_running = True` on the session so `session.active_list` reports
   `status="working"` while the external turn is in flight.
Fix (2026-10-21, dual-review r3/r4-approved fixup, amended into patch-0010): the bridge emitted
contract-violating payloads. Fixed: labels now a LIST of canonical payloads via
`agent.display.tool_labels_for_call` + `ToolLabel.as_payload()` (was a hand-built verb/target/
`' Past'` dict); `emit()` no longer suppresses `tui_gateway.contracts.registry.check_payload`
(STRICT raises, prod logs); `message.complete.status` maps canonically
interrupted → error (`error` or `failed`) → complete (was emitting out-of-enum `'failed'`);
`_worker()` serializes with `json.dumps(..., default=str)` and builds the `Request` inside the
try so tuple-keys/circular refs recover instead of killing the thread; `ctx.session_id`
accesses at run_turn_runner.py:1517/:2205 guarded with `getattr(..., None)` (restores
tests/gateway/test_display_null_turn_wiring.py 3/3). New STRICT regression gate:
tests/tui_gateway/contracts/test_bridge_emit_contracts.py (16-call emit matrix + real-emit
raises-on-violation). Export regenerated from index; sha256:
5760c7892e869948f97c04d9756e328df7269a82d0d445db7000071538a94b90
Rollback: `git revert patch-0010` on the branch + restart `hermes-gateway.service` and Desktop backend.

## 0011-mirrored-tool-complete-preview.patch (2026-10-21)
Why: `tui_gateway/agent_callbacks.py::_mirror_subagent_to_child` stores the `tool.start` dict
(which legally carries `preview` per `ToolStartPayload`) as `st["open_tool"]` and re-emits the
SAME dict as `tool.complete`. `ToolCompletePayload` had no `preview` field and `Payload` sets
`extra="forbid"`, so under `HERMES_TEST_ISOLATION` every watched-subagent tool with a preview
raised `ContractViolation` ("violates its wire contract" in agent.log; 9 entries on 2026-10-21,
first 20:18:15). The frame IS the stored tool.start dict, so stripping would delete data
Hermes Desktop already uses (its `tool-parts.ts` reads `payload.preview` on tool.complete at 3
sites, dual-review leg-A-verified) for reconnect hydration; and `ToolStartPayload.preview`
already legalises the same value on the same logical row, so the pair must agree.
What (4 files in code commit 6a0de1f930, tag patch-0011; base tag patch-0011-base = patch-0010):
1. `tui_gateway/contracts/events.py` — `ToolCompletePayload.preview: str | None = None`
   (mirrored child rows only, `submirror:*` tool ids) + mirror-row docstring note.
2. `apps/shared/src/gateway-contract.generated.ts` +
   `apps/shared/src/gateway-contract.openrpc.json` — regenerated via
   `.venv/bin/python scripts/gen_gateway_contracts.py` (patch-0007 precedent).
3. `tests/tui_gateway/contracts/test_mirror_tool_complete.py` — 2 STRICT tests: mirror row
   with preview accepted + args-only mirror row still accepted; failure control asserts
   `ContractViolation` match `bogus_field` (unknown field still rejected).
Gate: 4-file run (this test + test_bridge_emit_contracts.py + test_generated.py +
tests/gateway/test_display_null_turn_wiring.py) → 9 passed.
Export regenerated: `git diff patch-0011-base patch-0011 -- . ':!local-patches'`; sha256:
ef62c18723357a62b9105cbe8f47efe2226c7b4c9dc6cc43501f47dbf7c61c1b
Rollback: `git revert patch-0011` on the branch + restart `hermes-gateway.service`.

## 0012-token-plan-qwen-reasoning-adapter.patch (2026-10-21)
Why: the vanilla `alibaba-token-plan` profile emitted NO reasoning field, so every Hermes
reasoning pin (`--reasoning`, `agent.reasoning_effort`, `reasoning_overrides`) silently
no-op'ed on this endpoint — dual-review qwen legs rode server-default thinking (~12k
reasoning tokens, ~7 min per review brief; some big-job legs 2-3+ h). Live-probed
2026-10-21 (token-plan.ap-southeast-1): top-level `reasoning_effort` HONORED
(minimal..max all 200; low = 3.7x faster, 12,066 -> 2,935 r-tokens on a 10k-token gap-check
brief); nested `reasoning.effort` accepted-but-IGNORED; `enable_thinking: false` = the off
switch (0 r-tokens). Full probe evidence: ~/.hermes/cache/scratch/qwen38max-probe/.
What (2 files in code commit 5e249ff03b, tag patch-0012; base tag patch-0012-base = patch-0011):
1. `plugins/model-providers/alibaba/__init__.py` — `AlibabaTokenPlanProfile` subclass:
   explicit-config-only, qwen3-text-only adapter. effort in {minimal..max} -> top-level
   `reasoning_effort`; disabled/none -> `enable_thinking: false`; unseen -> medium;
   unset OR non-qwen3 -> exact vanilla payload (byte-identical).
2. `tests/plugins/model_providers/test_alibaba_token_plan_profile.py` — 15 tests pinning
   the wire-shape contract incl. leg B regression guard (deepseek-v4.1-flash-1017 emits
   nothing) and transport integration both directions.
Gate: `uv run --no-sync --with pytest pytest tests/plugins/model_providers/ -q` ->
15 new passed, 291 passed, 1 pre-existing unrelated fail (test_commandcode_profile
test_resolve_provider_full, fails on clean tree too — stash-verified).
Wire-level verification: transport kwargs now carry reasoning_effort=low for qwen3.8-max
with a low pin (probe A2A shape), bare for deepseek/glm/qwen-audio/wan ids.
Rollback: `git revert patch-0012` on the branch (no service restart needed — profiles are
imported per agent process; running gateway/desktop sessions pick it up on next spawn).

## 0013-discord-role-mention-addressing.patch (2026-10-22)
Why: a ping of a ROLE was not a mention. `_self_is_explicitly_mentioned()` parses only
`<@USER_ID>`, so `<@&ROLE_ID>` in any `require_mention` channel (the default) was dropped
with NO log line at all — the operator cannot tell it apart from a gateway that never got the
event. Live case: #research-channel 2026-10-22 16:50 UTC, a user pinged the managed role
"research-bot" (`1234567890123456701`) instead of the bot user (`1234567890123456702`); message
`1234567890123456704` never reached research-bot's ingress.
What (code commit 702d9b2af4, tag patch-0013; base tag patch-0013-base = patch-0012):
1. `plugins/platforms/discord/adapter.py` — `_self_is_role_mentioned()` (`role_mentions`
   plus raw `<@&ID>` tokens, intersected with the roles the bot actually holds),
   `_strip_self_mention_tokens()` for prompt hygiene, role check wired into
   `_handle_message()` and `_dispatch_recovered_message()`; new knob
   `discord.role_mentions` / `DISCORD_ROLE_MENTIONS` = true (any held role) or a CSV of role
   IDs (pin one role where several bots share a guild-wide role). Bot authors unchanged:
   literal `<@thisbot>` only, so role pings cannot start bot-to-bot volleys.
2. `tests/gateway/test_discord_role_mentions.py` — 11 tests: detection, foreign role, DM,
   flag off, CSV pin, prompt stripping, recovered-ingress admit/drop.
Gate: full Discord suite (`tests/gateway/test_discord_*.py` + `tests/e2e/test_discord_adapter.py`)
-> 387 passed, 15 failed, 1 skipped; the SAME 15 fail on the unpatched tree (voice-rekey,
attachment and send mock failures), so no regressions.
Export: `git diff patch-0012 patch-0013 -- . ':!local-patches'`; sha256:
e1243ff0b216e4f29e69d280ab94e4fc9e6035db2db08adf6a5f4f895a6dd0cd
Rollback: `git revert patch-0013` + restart `hermes-gateway.service` (the adapter is imported
once per gateway process, so a running gateway keeps the old gate until it restarts).

## 0014-discord-drop-reason-logging.patch (2026-10-22)
Why: every mention-gate drop returned silently at the gateway's running level (0 DEBUG lines
ever in the secondary profile's gateway.log) — indistinguishable from an event that never arrived;
the 2026-10-22 research-bot role-ping forensics existed because of this. Plan
discord-drop-observability, dual-review approved round 4 (both legs).
What (code commit f67a30e686, tag patch-0014; base tag patch-0014-base = patch-0013):
1. `plugins/platforms/discord/adapter.py` — three drop sites emit
   `[<platform>] admission: drop msg= chan= author= reason=` at INFO, metadata only:
   require_mention_not_addressed (live), backfill_require_mention_not_addressed (recovered),
   mentions_others_not_self (admission upgrade of the old DEBUG line). One-log-per-drop per
   dispatch attempt holds structurally ((b) short-circuits before admission). Plus
   `_self_role_ids()` now excludes the guild @everyone role (leg-B round-1 fold).
2. `tests/gateway/test_discord_drop_logging.py` (8 tests) + admission_thread_exemption log
   string updated to the new format.
Gate: full Discord suite 395 passed / 15 failed / 1 skipped — the identical 15 pre-existing
environmental failures as the unpatched tree; 8 new tests green.
Export: `git diff patch-0013 patch-0014 -- . ':!local-patches'`; sha256:
bfce2a02267a5617411d6fb5f3561d9dba6425c6edb9e3b9cea2094009a2a84c
Rollback: `git revert patch-0014` + restart `hermes-gateway.service` (operator-gated).
Stage-script: marker row added + MARKER_TABLE_PATCHES bumped 12 -> 13 in the same pass.

## 0015-serve-process-recovery-notify-drop.patch (2026-10-23)
Why: Desktop/serve backend sessions spawned `notify_on_complete=true` children, then the
backend exited cleanly ~26-40s later (desktop supervisor respawn following skill
self-improvement reviews); the respawned backend never ran `process_registry.recover_from_checkpoint()`
(only `gateway run` did), so the children's reader threads were lost. On child exit nobody
ran `_move_to_finished`: no `logs/process-results/proc_*.json` receipt and no completion
wake — the silent notify-drop class seen three times in one evening (Kanban card `t_695b4b52`).
What (code commit 1523dce129, tag patch-0015; base tag patch-0015-base = patch-0014):
1. `hermes_cli/web_server.py` — `_lifespan` recovers the launch profile's and every served
   profile's `processes.json` at backend startup (`_recover_served_profile_process_checkpoints`).
2. `tools/process_registry.py` + `tools/process_registry_checkpoint.py` — recovered-alive
   children are watched by a detached-sweeper thread (`_ensure_detached_sweeper`, 5s cadence);
   recovered-dead `notify_on_complete` children without a receipt publish an unobserved-exit
   receipt + completion wake (`_emit_uncollected_exit`) with WARNING audit logging; and
   `_move_to_finished` catches `Exception` around `save_completed_result` so receipt-write
   errors never wedge the registry lock or drop the wake.
3. `tests/tools/test_process_recovery_notify_drop.py` — 8 tests covering dead-child recovery,
   alive-child detached sweeper, receipt-failure resilience, and served-profile recovery.
Export: `git diff patch-0015-base patch-0015 -- . ':!local-patches'`; sha256:
393e38f9fff55b21322361f4af9f95d788700a2d93e9b6c8b9c883d40e779281
Rollback: `git revert patch-0015` + restart desktop/gateway.

## 0016-desktop-idempotent-render-message-uid.patch (2026-10-23)
Why: A foreign-bridge (`patch-0010`) turn streaming live into an open desktop session
rendered its reply twice: the streamed bubble sealed from the terminal frame's address-only
receipt, then the canonical DB row arrived ~8s later as a SECOND bubble because hydration
folds narration + reply into one bubble, defeating text-equality guards (Kanban card `t_345ed8bf`).
What (code commit 04f89979d8, tag patch-0016; base tag patch-0016-base = patch-0015):
1. `tui_gateway/contracts/events.py`, `tui_gateway/prompt_turn.py`, `gateway/run_turn_runner.py`,
   `apps/shared/src/gateway-contract.*` — `PersistedTurn` gains `message_uid: str | None`,
   populated on both native and foreign-bridge `message.complete` receipts; `tui_gateway/server.py`
   logs one INFO line on foreign `message.complete`.
2. `apps/desktop/src/...` — `ChatMessage.messageUid` stamped from the receipt (live) or DB
   row (hydration); `pending-turn-identity.ts`, `utils.ts`, and `transcript-backfill.ts`
   reconcile and absorb streamed bubbles by shared `messageUid` instead of appending duplicates.
3. `apps/desktop/src/app/session/hooks/use-session-actions/uid-idempotent-render.test.ts` +
   contract tests — 499/499 UI vitest + 10/10 pytest contract tests green.
Export: `git diff patch-0016-base patch-0016 -- . ':!local-patches'`; sha256:
67d249ae2c15b02c4adab52d98cdf2267fd7dc4cb938013b38433f646daf2353
Rollback: `git revert patch-0016`.

## 0017-bughunt-hardening.patch (2026-10-23)
Why: Argon-4 patch-stack bughunt (`remediation-argon4.md`) identified three code-level edge
cases across patches `0002`, `0010`, and `0013`/`0014`:
1. `plugins/platforms/discord/adapter.py` — `_strip_self_mention_tokens()` intersected
   `_raw_mentioned_role_ids` with raw `_self_role_ids` instead of the `role_mentions`-gated
   set, stripping held-role tokens from the prompt even when `DISCORD_ROLE_MENTIONS` was
   pinned to another role or set to `false`. Extracted `_addressing_role_ids()` + added
   regression test in `tests/gateway/test_discord_role_mentions.py`.
2. `gateway/run_turn_runner.py` — moved `try ... finally: bridge.close()` above
   `_load_turn_history` and `_prepare_turn_message` so pre-run exceptions cannot leak the
   `_DesktopSessionEventBridge` daemon thread.
3. `agent/runaway_watchdog.py` — added a 5s monotonic TTL cache (`_CFG_CACHE_TTL_S = 5.0`)
   around `load_config_readonly()` in `_limits()` so idle wake polls do not re-parse
   `config.yaml` on every check.
What (code commit adfcb7217d, tag patch-0017; base tag patch-0017-base = patch-0016):
Export: `git diff patch-0017-base patch-0017 -- . ':!local-patches'`; sha256:
ef9cbb89534a472dad919fe1f7211dab4c5a213500f674a06ffc0cee05bf7863
Rollback: `git revert patch-0017`.
