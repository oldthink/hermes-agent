"""skill_manage operations[] batch (#95681 arc, maintainer-approved).

Memory-tool pattern: several ops on ONE skill, atomically — create + N
supporting files, or SKILL.md + the script it references, in one call.
Any failure rolls the skill directory back to its pre-batch state.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

SK = (
    "---\nname: {n}\ndescription: Use when probing batch ops. Behavior.\n---\n"
    "# Probe\nStep 1.\n"
)


class TestSkillManageBatch(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="skmbatch_t_")
        os.environ["HERMES_HOME"] = self.home
        os.environ["HERMES_YOLO_MODE"] = "1"
        os.makedirs(os.path.join(self.home, "skills"), exist_ok=True)
        # Re-import against the temp home (module caches SKILLS_DIR).
        import importlib

        import tools.skill_manager_tool as smt
        importlib.reload(smt)
        self.smt = smt

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _call(self, name, ops):
        # Inject the per-op name (tests were written per-skill; the
        # interface is name-per-op, maintainer-directed).
        for op in ops:
            op.setdefault("name", name)
        return json.loads(self.smt.skill_manage(action="", name="", operations=ops))

    def test_create_plus_files_atomic(self):
        r = self._call("probe", [
            {"action": "create", "content": SK.format(n="probe")},
            {"action": "write_file", "file_path": "references/a.md", "file_content": "a"},
            {"action": "write_file", "file_path": "scripts/r.py", "file_content": "pass"},
        ])
        self.assertTrue(r["success"], r)
        self.assertEqual(r["operations_applied"], 3)
        base = os.path.join(self.home, "skills", "probe")
        for rel in ("SKILL.md", "references/a.md", "scripts/r.py"):
            self.assertTrue(os.path.exists(os.path.join(base, rel)), rel)

    def test_advisory_findings_ride_on_the_op_result(self):
        # operations[] is the only call shape, so a finding the per-op handler attaches must
        # survive into the batch row or the linter is silent for every model call.
        from tools.skill_linter import _BODY_SOFT_BUDGET_CHARS
        self._call("probe", [{"action": "create", "content": SK.format(n="probe")}])
        r = self._call("probe", [{"action": "patch", "old_string": "Step 1.",
                                  "new_string": "- rule; why.\n" * (_BODY_SOFT_BUDGET_CHARS // 12 + 1)}])
        self.assertTrue(r["success"], r)
        rules = {w["rule"] for w in r["results"][0]["lint_warnings"]}
        self.assertIn("oversized-body", rules)

    def test_midbatch_failure_rolls_back_existing_skill(self):
        self._call("probe", [{"action": "create", "content": SK.format(n="probe")}])
        r = self._call("probe", [
            {"action": "patch", "old_string": "Step 1.", "new_string": "Step ONE."},
            {"action": "write_file", "file_path": "bad/nope.md", "file_content": "x"},
        ])
        self.assertFalse(r["success"])
        self.assertEqual(r["failed_index"], 1)
        content = open(os.path.join(self.home, "skills", "probe", "SKILL.md")).read()
        self.assertIn("Step 1.", content)       # patch undone
        self.assertNotIn("Step ONE.", content)

    def test_failed_create_batch_removes_partial_skill(self):
        r = self._call("fresh", [
            {"action": "create", "content": SK.format(n="fresh")},
            {"action": "write_file", "file_path": "../escape.md", "file_content": "x"},
        ])
        self.assertFalse(r["success"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "skills", "fresh")))

    def test_validation_rules(self):
        # delete as SOLE op routes to the real delete (works)
        self._call("probe", [{"action": "create", "content": SK.format(n="probe")}])
        r = self._call("probe", [{"action": "delete"}])
        self.assertTrue(r["success"], r)
        self.assertFalse(os.path.exists(os.path.join(self.home, "skills", "probe")))
        # delete mixed with other ops rejected
        self._call("probe", [{"action": "create", "content": SK.format(n="probe")}])
        r = self._call("probe", [
            {"action": "patch", "old_string": "Step 1.", "new_string": "X."},
            {"action": "delete"},
        ])
        self.assertFalse(r["success"])
        self.assertIn("SOLE", r["error"])
        # create must be first
        r = self._call("x", [
            {"action": "write_file", "file_path": "references/a.md", "file_content": "a"},
            {"action": "create", "content": SK.format(n="x")},
        ])
        self.assertFalse(r["success"])
        # a non-string create category is a JSON error (never a TypeError) and, rejected
        # pre-effect, leaves no skill_batch_ snapshot tempdir behind.
        with tempfile.TemporaryDirectory(prefix="skmbatch_tmp_") as tmp, \
                patch.dict(os.environ, {"TMPDIR": tmp}), patch.object(tempfile, "tempdir", None):
            r = self._call("x", [{"action": "create", "content": SK.format(n="x"), "category": 5}])
            self.assertFalse(r["success"])
            self.assertIn("Category must be a string", r["error"])
            self.assertEqual(os.listdir(tmp), [])
        # empty / capped
        r = self._call("x", [])
        self.assertFalse(r["success"])
        r = self._call("x", [{"action": "patch"}] * 21)
        self.assertFalse(r["success"])
        self.assertIn("capped", r["error"])

    def test_intra_batch_conflict_guard(self):
        """Same-file double writes and post-edit full rewrites are always
        a confused plan under last-wins sequencing — rejected BEFORE any
        side effect. Patch chains and rewrite-first stay legal."""
        self._call("probe", [{"action": "create", "content": SK.format(n="probe")}])
        # destructive op on an already-touched file: rejected — double
        # write, write+remove, patch-then-write, patch-then-remove, and a
        # path-spelling variant of the same file.
        self._call("probe", [{"action": "write_file",
                              "file_path": "references/c.md", "file_content": "seed"}])
        for ops in (
            [{"action": "write_file", "file_path": "references/a.md", "file_content": "1"},
             {"action": "write_file", "file_path": "references/a.md", "file_content": "2"}],
            [{"action": "write_file", "file_path": "references/b.md", "file_content": "x"},
             {"action": "remove_file", "file_path": "references/b.md"}],
            [{"action": "patch", "file_path": "references/c.md",
              "old_string": "seed", "new_string": "edited"},
             {"action": "write_file", "file_path": "references/c.md", "file_content": "CLOB"}],
            [{"action": "patch", "file_path": "references/c.md",
              "old_string": "seed", "new_string": "edited"},
             {"action": "remove_file", "file_path": "references/c.md"}],
            [{"action": "write_file", "file_path": "references/d.md", "file_content": "1"},
             {"action": "write_file", "file_path": "./references//d.md", "file_content": "2"}],
        ):
            r = self._call("probe", ops)
            self.assertFalse(r["success"], ops)
            self.assertIn("discard", r["error"])
        # ...and rejected pre-effect: c.md still holds its seed text.
        c_md = os.path.join(self.home, "skills", "probe", "references", "c.md")
        self.assertEqual(open(c_md).read(), "seed")
        # write-then-patch on one supporting file stays legal (additive).
        r = self._call("probe", [
            {"action": "write_file", "file_path": "references/e.md", "file_content": "base"},
            {"action": "patch", "file_path": "references/e.md",
             "old_string": "base", "new_string": "base+"},
        ])
        self.assertTrue(r["success"], r)
        # patch then full rewrite: rejected; rewrite-first: allowed
        r = self._call("probe", [
            {"action": "patch", "old_string": "Step 1.", "new_string": "P."},
            {"action": "patch", "content": SK.format(n="probe")},
        ])
        self.assertFalse(r["success"])
        self.assertIn("rewrite", r["error"])
        r = self._call("probe", [
            {"action": "patch", "content": SK.format(n="probe").replace("Step 1.", "F.")},
            {"action": "patch", "old_string": "F.", "new_string": "G."},
        ])
        self.assertTrue(r["success"], r)
        # patch chains stay legal
        r = self._call("probe", [
            {"action": "patch", "old_string": "G.", "new_string": "H."},
            {"action": "patch", "old_string": "H.", "new_string": "I."},
        ])
        self.assertTrue(r["success"], r)

    def test_cross_skill_batch_and_rollback(self):
        """Ops may target DIFFERENT skills; a late failure rolls back
        every touched skill, including removing a batch-created one — but a dir
        that pre-dated the batch (empty leftover create adopted) is never rmtree'd:
        every file the BATCH wrote there is undone, anything else survives."""
        self._call("alpha", [{"action": "create", "content": SK.format(n="alpha")}])
        gamma = os.path.join(self.home, "skills", "gamma")
        os.mkdir(gamma)  # empty pre-existing dir with no SKILL.md: create adopts it
        real_from = self.smt._skill_manage_from

        def drop_file_before_failing_op(payload, **kw):
            if payload.get("action") == "write_file":  # something lands mid-batch
                with open(os.path.join(gamma, "dropped.txt"), "w", encoding="utf-8") as fh:
                    fh.write("keep me")
            return real_from(payload, **kw)

        with patch.object(self.smt, "_skill_manage_from", side_effect=drop_file_before_failing_op):
            r = json.loads(self.smt.skill_manage(action="", name="", operations=[
                {"name": "alpha", "action": "patch",
                 "old_string": "Step 1.", "new_string": "Step A."},
                {"name": "beta", "action": "create", "content": SK.format(n="beta")},
                {"name": "gamma", "action": "create", "content": SK.format(n="gamma")},
                {"name": "gamma", "action": "write_file",
                 "file_path": "references/a.md", "file_content": "a"},
                {"name": "beta", "action": "write_file",
                 "file_path": "bad/nope.md", "file_content": "x"},
            ]))
        self.assertFalse(r["success"])
        self.assertEqual(r["failed_index"], 4)
        # alpha's patch undone; beta (batch-created) removed entirely.
        with open(os.path.join(self.home, "skills", "alpha", "SKILL.md"), encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("Step 1.", content)
        self.assertNotIn("Step A.", content)
        self.assertFalse(os.path.exists(os.path.join(self.home, "skills", "beta")))
        # gamma pre-existed: SKILL.md and the batch's references/a.md are undone (with the
        # dir that held it); the dir and the foreign file survive.
        self.assertTrue(os.path.isdir(gamma))
        with open(os.path.join(gamma, "dropped.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "keep me")
        self.assertEqual(os.listdir(gamma), ["dropped.txt"])
        # Without a foreign file the adopted dir is left as create found it (empty -> gone),
        # so a retry create is not refused as "occupied".
        delta = os.path.join(self.home, "skills", "delta")
        os.mkdir(delta)
        r = self._call("delta", [
            {"action": "create", "content": SK.format(n="delta")},
            {"action": "write_file", "file_path": "references/a.md", "file_content": "a"},
            {"action": "write_file", "file_path": "bad/nope.md", "file_content": "x"},
        ])
        self.assertFalse(r["success"])
        self.assertFalse(os.path.exists(delta))
        r = self._call("delta", [{"action": "create", "content": SK.format(n="delta")}])
        self.assertTrue(r["success"], r)

    def test_failed_restore_never_destroys_the_skill(self):
        """Rollback used to rmtree the live skill directory BEFORE
        copytree restored the snapshot. When copytree failed (disk full,
        locked file) the except only appended a note and the finally then
        deleted the snapshot too: nothing survived. The broken state must
        be moved aside and only deleted once the restore succeeded."""
        from unittest.mock import patch as _patch

        import shutil as _shutil

        self._call("probe", [{"action": "create", "content": SK.format(n="probe")}])
        state = {"n": 0}
        real_copytree = _shutil.copytree

        def flaky_copytree(src, dst, *a, **k):
            state["n"] += 1
            if state["n"] == 2:  # call 1 snapshots, call 2 is the restore
                raise OSError("disk full")
            return real_copytree(src, dst, *a, **k)

        with _patch("shutil.copytree", side_effect=flaky_copytree):
            r = self._call("probe", [
                {"action": "patch",
                 "old_string": "Step 1.", "new_string": "Step ONE."},
                {"action": "write_file",
                 "file_path": "bad/nope.md", "file_content": "x"},
            ])
        self.assertFalse(r["success"], r)
        self.assertIn("ROLLBACK FAILED", r["error"])
        # The skill directory was NOT destroyed by the failed rollback:
        # the half applied state survives instead of nothing at all.
        skill_md = os.path.join(self.home, "skills", "probe", "SKILL.md")
        self.assertTrue(os.path.exists(skill_md))
        content = open(skill_md).read()
        self.assertIn("Step ONE.", content)

    def test_single_op_path_unchanged(self):
        self._call("probe", [{"action": "create", "content": SK.format(n="probe")}])
        raw = self.smt.skill_manage(
            action="patch", name="probe",
            old_string="Step 1.", new_string="Step 1 (single).",
        )
        self.assertTrue(json.loads(raw)["success"])

    def test_batch_stages_as_one_pending_write_when_gated(self):
        """Approval gate: the whole batch stages as ONE pending record, and
        apply_skill_pending replays it (operations key round-trips)."""
        from unittest.mock import patch as _patch

        class _Decision:
            allow = False
            blocked = False
            message = "staged for review"

        staged = {}

        def fake_stage_write(area, payload, summary=None, origin=None):
            staged.update(payload=payload, summary=summary)
            return {"id": "pend_1"}

        import tools.write_approval as wa

        with _patch.object(wa, "evaluate_gate", return_value=_Decision()), \
             _patch.object(wa, "stage_write", side_effect=fake_stage_write):
            r = self._call("probe", [
                {"action": "create", "content": SK.format(n="probe")},
                {"action": "write_file", "file_path": "references/a.md",
                 "file_content": "a"},
            ])
        self.assertTrue(r.get("staged"), r)
        self.assertEqual(staged["payload"]["action"], "batch")
        self.assertEqual(len(staged["payload"]["operations"]), 2)
        # Replay applies the batch (gate bypassed inside).
        out = json.loads(self.smt.apply_skill_pending(staged["payload"]))
        self.assertTrue(out["success"], out)
        self.assertEqual(out["operations_applied"], 2)

    def _probe_home_paths(self):
        skills_root = os.path.join(self.home, "skills")
        backups_root = os.path.join(self.home, "backups", "skill-rollbacks")
        return skills_root, backups_root

    def test_rollback_leaves_no_litter_and_aside_lands_in_backups(self):
        """RC1 dead-proof: a failing batch on a skill whose dir the lane lock later
        renders read-only must leave NO *.rollback-broken in the skills root, must
        restore the pre-batch content byte-for-byte, and the aside (only if cleanup
        itself fails) lives under <home>/backups/skill-rollbacks — outside the tree."""
        skills_root, backups_root = self._probe_home_paths()
        self._call("probe", [{"action": "create", "content": SK.format(n="probe")}])
        self._call("probe", [{"action": "write_file",
                              "file_path": "references/r.md", "file_content": "keep"}])
        before = open(os.path.join(skills_root, "probe", "SKILL.md"), encoding="utf-8").read()
        # 555 the skill dir between snapshot and rollback? Cannot time-travel; instead
        # make the SKILL.md write fail via a locked dir at op time: lock AFTER create
        # so the batch's patch op hits a read-only dir mid-batch.
        os.chmod(os.path.join(skills_root, "probe"), 0o555)
        try:
            r = self._call("probe", [
                {"action": "patch", "old_string": "Step 1.", "new_string": "Step ONE."},
            ])
            self.assertFalse(r["success"], r)
            self.assertIn("locked", r["error"])
        finally:
            os.chmod(os.path.join(skills_root, "probe"), 0o755)
        # No quarantine litter anywhere in the skills root
        self.assertEqual([d for d in os.listdir(skills_root) if d.endswith(".rollback-broken")], [])
        # Content untouched (refused before any effect)
        after = open(os.path.join(skills_root, "probe", "SKILL.md"), encoding="utf-8").read()
        self.assertEqual(after, before)

    def test_rollback_with_readonly_state_lands_aside_outside_skills(self):
        """RC1 dead-proof, legacy path: even when a rollback DOES have to move a
        broken state aside (snapshot restore over a mid-batch failure), the aside
        lands under backups/skill-rollbacks — never *.rollback-broken in the skills
        root — and the restore is byte-for-byte."""
        skills_root, backups_root = self._probe_home_paths()
        self._call("probe", [{"action": "create", "content": SK.format(n="probe")}])
        self._call("probe", [{"action": "write_file",
                              "file_path": "references/r.md", "file_content": "keep"}])
        before_files = {}
        for rel in ("SKILL.md", "references/r.md"):
            with open(os.path.join(skills_root, "probe", rel), "rb") as fh:
                before_files[rel] = fh.read()
        # Force the MID-BATCH failure + rollback path with a read-only dir state:
        # make the second op's write fail by pre-555-ing the skill dir AFTER the
        # batch snapshots it — patch the op-time only via _skill_manage_from hook.
        real_from = self.smt._skill_manage_from
        state = {"locked": False}

        def lock_before_failing_op(payload, **kw):
            if payload.get("action") == "write_file" and not state["locked"]:
                state["locked"] = True
                os.chmod(os.path.join(skills_root, "probe"), 0o555)
            return real_from(payload, **kw)

        try:
            with patch.object(self.smt, "_skill_manage_from", side_effect=lock_before_failing_op):
                r = self._call("probe", [
                    {"action": "patch", "old_string": "Step 1.", "new_string": "Step ONE."},
                    {"action": "write_file", "file_path": "bad/nope.md", "file_content": "x"},
                ])
            self.assertFalse(r["success"], r)
        finally:
            os.chmod(os.path.join(skills_root, "probe"), 0o755)
        # _restore_snapshot moved the 555 state aside under backups and deleted it hard
        self.assertEqual([d for d in os.listdir(skills_root) if d.endswith(".rollback-broken")], [])
        # byte-for-byte restore
        for rel, raw in before_files.items():
            with open(os.path.join(skills_root, "probe", rel), "rb") as fh:
                self.assertEqual(fh.read(), raw, rel)
        # aside root existed (or still exists only if cleanup failed, which would surface)
        # and the note surfaced in the error
        self.assertIn("rolled back", r["error"])

    def test_find_skill_and_candidates_ignore_quarantines(self):
        """RC3: *.rollback-broken dirs are never edit targets nor load candidates."""
        from pathlib import Path
        skills_root = os.path.join(self.home, "skills")
        os.makedirs(os.path.join(skills_root, "probe"), exist_ok=True)
        with open(os.path.join(skills_root, "probe", "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(SK.format(n="probe"))
        os.makedirs(os.path.join(skills_root, "probe.rollback-broken"), exist_ok=True)
        with open(os.path.join(skills_root, "probe.rollback-broken", "SKILL.md"), "w",
                  encoding="utf-8") as fh:
            fh.write(SK.format(n="probe"))
        found = self.smt._find_skill("probe")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertNotIn(".rollback-broken", str(found["path"]))
        from agent.skill_utils import iter_skill_index_files
        scanned = [p.parent.name for p in iter_skill_index_files(Path(skills_root), "SKILL.md")]
        self.assertEqual(scanned, ["probe"])
        from tools.skills_tool import _collect_skill_candidates
        cands = _collect_skill_candidates("probe", None, [Path(skills_root)])
        self.assertEqual([str(c[0].name) for c in cands], ["probe"])

    def test_failing_batch_failure_control(self):
        """Failure control: a batch that MUST fail (unmatchable old_string) restores
        the skill, leaves no litter, no snapshot tempdir, and says so."""
        skills_root, _ = self._probe_home_paths()
        self._call("probe", [{"action": "create", "content": SK.format(n="probe")}])
        with open(os.path.join(skills_root, "probe", "SKILL.md"), encoding="utf-8") as fh:
            before = fh.read()
        with tempfile.TemporaryDirectory(prefix="skmbatch_ctl_") as tmp, \
                patch.dict(os.environ, {"TMPDIR": tmp}), patch.object(tempfile, "tempdir", None):
            r = self._call("probe", [
                {"action": "patch", "old_string": "NO SUCH STRING EXISTS",
                 "new_string": "irrelevant"},
            ])
            self.assertFalse(r["success"], r)
            self.assertIn("rolled back", r["error"])
            self.assertEqual(os.listdir(tmp), [])  # snapshot tempdir cleaned up
        self.assertEqual([d for d in os.listdir(skills_root) if d.endswith(".rollback-broken")], [])
        with open(os.path.join(skills_root, "probe", "SKILL.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)


if __name__ == "__main__":
    unittest.main()
