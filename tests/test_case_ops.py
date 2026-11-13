import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from praxtrace.case_records import build_index, read_json, write_json, digest, now, search, validate_case
from praxtrace.case_ops import discover_outputs, import_report, reconcile, finish
from praxtrace.evidence_review import review


class CaseOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "analysis/workspace/outputs"
        self.folder.mkdir(parents=True)
        self.source = "analysis/workspace/outputs/coupon.md"
        (self.root / self.source).write_text("# PK coupon\n[\u6570\u636e](facts.json)", encoding="utf-8")
        (self.folder / "facts.json").write_text('{"used": 2, "issued": 10, "state": "CLOSED"}', encoding="utf-8")
        self.imported = import_report(self.root, self.source, "\u5df4\u57fa\u65af\u5766\u4f18\u60e0\u5238", "\u5238\u7684\u4f7f\u7528\u60c5\u51b5", "PK", ["PK", "\u4f18\u60e0\u5238"])
        self.case_path = self.imported["case"]
        self.case = read_json(self.root / self.case_path)
        self.scans = ["analysis/workspace/outputs"]

    def test_import_idempotent_and_links_rebased(self):
        again = import_report(self.root, self.source, "Different", "Question", "PK", ["PK"])
        self.assertFalse(again["created"])
        self.assertEqual(again["case"], self.case_path)
        artifact = self.case["artifacts"][0]
        self.assertTrue(artifact["links_rebased"])
        self.assertIn("../facts.json", (self.root / artifact["path"]).read_text(encoding="utf-8"))
        self.assertEqual(artifact["approval_status"], "unknown")
        self.assertTrue(validate_case(self.case, self.root)["ok"])

    def test_orphans_found_but_not_auto_approved_or_imported(self):
        (self.folder / "new-report.md").write_text("new")
        (self.folder / "evidence.md").write_text("raw evidence")
        result = build_index(self.root, self.scans)
        paths = [x["path"] for x in result["unregistered_outputs"]]
        self.assertIn("analysis/workspace/outputs/new-report.md", paths)
        self.assertNotIn(self.source, paths)
        self.assertNotIn("analysis/workspace/outputs/evidence.md", paths)
        self.assertEqual(len(result["entries"]), 1)

    def test_synonyms_chinese_question_and_scope(self):
        index = build_index(self.root, self.scans)
        self.assertEqual(len(search(index, "\u4ee5\u524d\u5206\u6790\u8fc7\u5df4\u57fa\u65af\u5766\u6298\u6263\u5238\u5417")), 1)
        self.assertEqual(len(search(index, "pakistan coupon", tag="\u5df4\u57fa\u65af\u5766")), 1)
        self.assertEqual(search(index, "coupon", tag="AO"), [])
        self.assertEqual(search(index, "\u80af\u5c3c\u4e9a\u4f18\u60e0\u5238"), [])
        self.assertEqual(search(index, "\u4e0d\u5b58\u5728\u7684\u9690\u542b\u6982\u5ff5"), [])

    def receipt_bundle(self, link, completed_at=None):
        receipt = {"thread_id": "thread", "turn_id": "turn", "message_id": "msg", "phase": "final_answer",
                   "completed_at": completed_at or now(), "links": [link]}
        bundle = "analysis/workspace/outputs/receipts.json"
        write_json(self.root / bundle, {"schema_version": 1, "source": "codex.read_thread", "receipts": [receipt]})
        return bundle

    def test_source_link_proves_reference_not_snapshot_or_approval(self):
        bundle = self.receipt_bundle(self.source)
        result = reconcile(self.root, self.case_path, bundle)
        self.assertEqual(result["changes"][0]["status"], "referenced")
        case = read_json(self.root / self.case_path)
        self.assertIsNone(case["artifacts"][0]["delivered_at"])
        self.assertEqual(case["artifacts"][0]["approval_status"], "unknown")
        self.assertTrue(validate_case(case, self.root)["ok"])

    def test_snapshot_link_after_capture_supports_delivery(self):
        bundle = self.receipt_bundle(self.case["artifacts"][0]["path"])
        reconcile(self.root, self.case_path, bundle)
        self.assertEqual(read_json(self.root / self.case_path)["artifacts"][0]["delivery_status"], "delivered")

    def test_old_receipt_cannot_prove_new_snapshot(self):
        old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        bundle = self.receipt_bundle(self.case["artifacts"][0]["path"], old)
        reconcile(self.root, self.case_path, bundle)
        self.assertEqual(read_json(self.root / self.case_path)["artifacts"][0]["delivery_status"], "referenced")

    def test_commentary_or_wrong_link_does_not_mark_delivery(self):
        bundle = self.receipt_bundle(self.source)
        data = read_json(self.root / bundle)
        data["receipts"][0]["phase"] = "commentary"
        write_json(self.root / bundle, data)
        self.assertEqual(reconcile(self.root, self.case_path, bundle)["changes"], [])
        data["receipts"][0].update(phase="final_answer", links=["method.md"])
        write_json(self.root / bundle, data)
        self.assertEqual(reconcile(self.root, self.case_path, bundle)["changes"], [])

    def test_receipt_tampering_detected(self):
        bundle = self.receipt_bundle(self.source)
        reconcile(self.root, self.case_path, bundle)
        data = read_json(self.root / bundle)
        data["receipts"][0]["message_id"] = "changed"
        write_json(self.root / bundle, data)
        self.assertFalse(validate_case(read_json(self.root / self.case_path), self.root)["ok"])

    def spec(self):
        source = "analysis/workspace/outputs/facts.json"
        def field(key):
            return {"path": source, "pointer": "/" + key}
        return {"schema_version": 1, "claims": [{"id": "C1", "statement": "\u4f7f\u7528\u738720%", "scope": "Synthetic PK", "as_of": "Historical sample",
                 "checked_at": now(), "status": "supported", "review_note": "Only saved arithmetic, not causal effect",
                 "sources": [{"path": source, "sha256": digest(self.root / source)}],
                 "checks": [{"kind": "ratio", "values": [field("used"), field("issued")], "expected": "0.2", "tolerance": "0.0001"},
                            {"kind": "equals", "value": field("state"), "expected": "CLOSED"}]}]}

    def test_numeric_and_literal_checks_detect_wrong_conclusion(self):
        spec = self.spec()
        self.assertTrue(review(self.root, spec)["ok"])
        spec["claims"][0]["checks"][0]["expected"] = "0.8"
        self.assertFalse(review(self.root, spec)["ok"])

    def test_changed_source_zero_denominator_missing_evidence(self):
        spec = self.spec()
        source = self.folder / "facts.json"
        source.write_text('{"used":2,"issued":0,"state":"CLOSED"}')
        self.assertFalse(review(self.root, spec)["ok"])
        spec["claims"][0]["sources"][0]["sha256"] = digest(source)
        self.assertFalse(review(self.root, spec)["ok"])
        spec["claims"][0].update(sources=[], checks=[])
        self.assertFalse(review(self.root, spec)["ok"])

    def test_finish_gates_review_and_refreshes_index(self):
        output = "analysis/.local/cases.json"
        self.assertFalse(finish(self.root, self.case_path, self.scans, output)["ok"])

        spec_path = "analysis/workspace/outputs/claims.json"
        write_json(self.root / spec_path, self.spec())
        self.case["claim_review"] = spec_path
        write_json(self.root / self.case_path, self.case)
        self.assertTrue(finish(self.root, self.case_path, self.scans, output)["ok"])
        self.assertTrue((self.root / output).exists())
        bad = self.spec()
        bad["claims"][0]["checks"][0]["expected"] = "0.8"
        write_json(self.root / spec_path, bad)
        self.assertFalse(finish(self.root, self.case_path, self.scans, output)["ok"])

    def test_malformed_review_fails_cleanly(self):
        review_path = "analysis/workspace/outputs/broken.json"
        (self.root / review_path).write_text("not json", encoding="utf-8")
        self.case["claim_review"] = review_path
        self.assertFalse(validate_case(self.case, self.root)["ok"])

    def test_claim_sources_are_protected(self):
        review_path = "analysis/workspace/outputs/claims.json"
        write_json(self.root / review_path, self.spec())
        self.case["claim_review"] = review_path
        result = validate_case(self.case, self.root)
        self.assertTrue(result["ok"])
        self.assertIn("analysis/workspace/outputs/facts.json", result["protected_paths"])


if __name__ == "__main__":
    unittest.main()
