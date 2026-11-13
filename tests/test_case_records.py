import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from praxtrace.case_records import build_index, capture, digest, now, read_json, search, seal, validate_case, write_json


class CaseRecordsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "cases/one").mkdir(parents=True)
        (self.root / "method.md").write_text("Method v1", encoding="utf-8")
        (self.root / "cases/one/report.md").write_text("Synthetic report", encoding="utf-8")
        self.path = self.root / "cases/one/case.json"
        self.case = {"schema_version": 1, "case_id": "one", "title": "\u4f18\u60e0\u5238\u6548\u679c", "question": "\u4f18\u60e0\u5238\u662f\u5426\u6539\u5584\u7ed3\u679c\uff1f",
                     "scope": "Synthetic PK, fixed historical period", "tags": ["PK", "\u4f18\u60e0\u5238"],
                     "registered_at": now(), "retention": "retain", "relations": [],
                     "runs": [{"id": "R1", "status": "partial", "origin": "native", "recorded_at": now(),
                               "summary": "Evidence gap remains", "execution": None}],
                     "artifacts": [], "reuse": [],
                     "learning": [{"id": "L1", "run_id": "R1", "decision": "none", "layer": "case", "status": "applied",
                                   "reason": "Existing method sufficient", "validation": "Synthetic boundary checked", "evidence": []}]}
        self.save()
        capture(self.root, "cases/one/case.json", "R1", "cases/one/report.md", "A1", "report", "1")
        self.case = read_json(self.path)

    def save(self):
        write_json(self.path, self.case)

    def check(self):
        return validate_case(self.case, self.root)

    def test_ready_partial_is_not_delivered_or_approved(self):
        self.assertTrue(self.check()["ok"])
        art = self.case["artifacts"][0]
        self.assertEqual(art["delivery_status"], "ready")
        self.assertEqual(art["approval_status"], "not_requested")
        art["delivery_status"] = "delivered"
        self.assertFalse(self.check()["ok"])
        art.update(delivered_at=now(), delivery_basis="Explicit synthetic send record")
        art["approval_status"] = "approved"
        self.assertFalse(self.check()["ok"])
        art["approval_basis"] = "Explicit synthetic reviewer approval"
        self.assertTrue(self.check()["ok"])

    def test_snapshot_preserves_old_report_and_detects_tamper(self):
        original = self.root / "cases/one/report.md"
        original.write_text("Changed report", encoding="utf-8")
        self.assertTrue(self.check()["ok"])
        snap = self.root / self.case["artifacts"][0]["path"]
        self.assertEqual(snap.read_text(), "Synthetic report")
        snap.write_text("Tampered", encoding="utf-8")
        self.assertFalse(self.check()["ok"])

    def test_versions_link_without_overwriting(self):
        with self.assertRaises(ValueError):
            capture(self.root, "cases/one/case.json", "R1", "cases/one/report.md", "A1", "report", "1")
        capture(self.root, "cases/one/case.json", "R1", "cases/one/report.md", "A2", "report", "2", "A1")
        self.case = read_json(self.path)
        self.assertTrue(self.check()["ok"])
        self.case["artifacts"][0]["supersedes"] = "A2"
        self.assertFalse(self.check()["ok"])

    def test_learning_required_per_run_and_host_approval_separate(self):
        self.case["learning"] = []
        self.assertFalse(self.check()["ok"])
        self.case["learning"] = [{"id": "L1", "run_id": "R1", "decision": "add", "layer": "host", "status": "applied",
                                 "target": "method.md", "reason": "Rule extracted", "validation": "Checked", "evidence": ["cases/one/report.md"]}]
        self.assertFalse(self.check()["ok"])
        self.case["learning"][0]["approval_basis"] = "Explicit synthetic authorization"
        self.assertTrue(self.check()["ok"])

    def test_paths_and_missing_targets_fail(self):
        self.case["artifacts"][0]["path"] = "../outside.md"
        self.assertFalse(self.check()["ok"])
        with self.assertRaises(ValueError):
            capture(self.root, "cases/one/case.json", "R1", "../outside.md", "A2", "report", "2")

    def test_case_relations_resolve_and_cycles_fail(self):
        self.case["relations"] = [{"case_id": "two", "kind": "follow_up", "basis": "Continues question"}]
        self.save()
        self.assertTrue(build_index(self.root, ["cases"])["issues"])
        other = copy.deepcopy(self.case)
        other.update(case_id="two", relations=[])
        write_json(self.root / "cases/two/case.json", other)
        self.assertFalse(build_index(self.root, ["cases"])["issues"])
        other["relations"] = [{"case_id": "one", "kind": "correction", "basis": "Cycle"}]
        write_json(self.root / "cases/two/case.json", other)
        self.assertTrue(build_index(self.root, ["cases"])["issues"])

    def test_duplicate_ids_not_silently_merged(self):
        write_json(self.root / "cases/duplicate/case.json", self.case)
        index = build_index(self.root, ["cases"])
        self.assertTrue(index["issues"])
        self.assertEqual(search(index), [])

    def test_search_reverse_lookup_and_no_raw_evidence_search(self):
        self.case["reuse"] = [{"run_id": "R1", "path": "method.md", "version": "v1", "reason": "Applicable"}]
        self.save()
        index = build_index(self.root, ["cases"])
        self.assertEqual(len(search(index, "\u4f18\u60e0\u5238", tag="pk")), 1)
        self.assertEqual(len(search(index, target="method.md")), 1)
        self.assertEqual(search(index, "Synthetic report"), [])
        self.assertEqual(search(index, tag="AO"), [])
        self.assertIn(self.case["artifacts"][0]["path"], index["protected_paths"])

    def test_legacy_visible_but_unknown_and_not_double_counted(self):
        ex = {"case_id": "legacy", "request": "Old question", "scope": "Old scope", "report": "cases/one/report.md"}
        write_json(self.root / "cases/old/execution.json", ex)
        write_json(self.root / "cases/one/execution.json", {**ex, "case_id": "one"})
        index = build_index(self.root, ["cases", "cases/one"])
        self.assertEqual(len(index["entries"]), 2)
        old = search(index, case_id="legacy")[0]
        self.assertEqual(old["record_type"], "legacy_execution")
        self.assertTrue(old["gaps"])
        self.assertEqual(old["artifacts"], [])

    def test_seal_execution_detects_later_changes(self):
        ex = {"case_id": "one", "tasks": [{"id": "Q1", "status": "partial", "evidence": ["method.md"]}]}
        write_json(self.root / "cases/one/execution.json", ex)
        self.case["runs"][0]["execution"] = "cases/one/execution.json"
        self.assertFalse(self.check()["ok"])
        self.save()
        seal(self.root, "cases/one/case.json", "R1")
        self.case = read_json(self.path)
        self.assertTrue(self.check()["ok"])
        ex["tasks"][0]["status"] = "answered"
        write_json(self.root / "cases/one/execution.json", ex)
        self.assertTrue(self.check()["ok"])
        snapshot = self.root / self.case["runs"][0]["execution"]
        snapshot.write_text("{}", encoding="utf-8")
        self.assertFalse(self.check()["ok"])

    def test_legacy_unknown_not_allowed_for_new_work(self):
        self.case["learning"][0]["decision"] = "legacy_unknown"
        self.assertFalse(self.check()["ok"])
        self.case["runs"][0]["origin"] = "legacy"
        self.assertTrue(self.check()["ok"])

    def test_cli_roundtrip_and_nonzero_on_broken_delivery(self):
        command = [sys.executable, "-B", "-X", "utf8", "-m", "praxtrace.case_records", "--root", str(self.root)]
        result = subprocess.run(command + ["index", "--scan", "cases", "--output", ".local/cases.json"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(read_json(self.root / ".local/cases.json")["entries"]), 1)
        result = subprocess.run(command + ["search", "--scan", "cases", "--case-id", "one"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["matches"][0]["title"], "\u4f18\u60e0\u5238\u6548\u679c")
        self.case["learning"] = []
        self.save()
        result = subprocess.run(command + ["check", "cases/one/case.json"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1)

    def test_cli_init_and_no_overwrite(self):
        command = [sys.executable, "-B", "-X", "utf8", "-m", "praxtrace.case_records", "--root", str(self.root),
                   "init", "--directory", "cases/new", "--id", "new", "--title", "\u6807\u9898", "--question", "\u95ee\u9898", "--scope", "Scope"]
        first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(first.returncode, 0, first.stderr)
        record = self.root / "cases/new/case.json"
        content = record.read_bytes()
        second = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(record.read_bytes(), content)
        self.assertTrue(validate_case(read_json(record), self.root, "plan")["ok"])
        self.assertFalse(validate_case(read_json(record), self.root, "delivery")["ok"])

    def test_host_scan_excludes_apply_to_cases_and_outputs(self):
        hidden = self.root / "cases/host-history"
        hidden.mkdir()
        (hidden / "case.json").write_text("{}", encoding="utf-8")
        (hidden / "report.md").write_text("Historical source, not a current case", encoding="utf-8")
        command = [sys.executable, "-B", "-X", "utf8", "-m", "praxtrace.case_records", "--root", str(self.root),
                   "index", "--scan", "cases", "--output", ".local/filtered.json"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                env={**os.environ, "PRAXTRACE_SCAN_EXCLUDES": "host-history"})
        self.assertEqual(result.returncode, 0, result.stderr)
        index = read_json(self.root / ".local/filtered.json")
        self.assertEqual(len(index["entries"]), 1)
        self.assertFalse(index["issues"])
        self.assertFalse(any("host-history" in x["path"] for x in index["unregistered_outputs"]))

    def test_malformed_records_are_errors_not_crashes(self):
        for key, value in (("runs", None), ("relations", [None]), ("case_id", []), ("artifacts", [{"run_id": []}])):
            with self.subTest(key=key):
                bad = {**self.case, key: value}
                self.assertFalse(validate_case(bad, self.root)["ok"])
                write_json(self.path, bad)
                self.assertTrue(build_index(self.root, ["cases"])["issues"])


if __name__ == "__main__":
    unittest.main()
