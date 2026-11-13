import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from praxtrace.execution_record import check_record
from praxtrace.upstream_usage import read_records, summarize, SCHEMA


class ExecutionRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "evidence.md").write_text("Synthetic evidence", encoding="utf-8")
        (self.root / "report.md").write_text("Synthetic report", encoding="utf-8")
        self.record = {
            "schema_version": 1, "case_id": "synthetic-case",
            "request": "Evaluate the intervention and recommend next steps",
            "scope": "Synthetic project, historical period, UTC", "scope_basis": "Explicit test scope",
            "reuse": "Source version v1 checked",
            "tasks": [{"id": "Q1", "intent": "effect", "question": "Did the intervention help?",
                       "acceptance": "Verify comparable outcomes", "status": "answered",
                       "evidence": ["evidence.md"], "result": "Synthetic result"}],
            "routes": [{"task_ids": ["Q1"], "capability": "statistics", "provider": "local",
                        "decision": "selected", "reason": "Need outcome comparison", "stage": "run",
                        "status": "completed", "evidence": ["evidence.md"]}],
            "checks": [{"id": key, "status": "pass", "detail": "Reviewed synthetic case"}
                       for key in ("scope", "evidence", "decision")],
            "actions": [{"task_ids": ["Q1"], "action": "Validate on a held-out cohort",
                         "metric": "Mature outcome rate", "condition": "Comparable assignment"}],
            "report": "report.md", "elapsed_seconds": 1.5,
        }

    def check(self, phase="delivery"):
        usage = [
            {"id": 1, "case_id": "synthetic-case", "tool": "dds", "stage": "method",
             "status": "completed", "tool_version": "pinned-version"},
            {"id": 2, "case_id": "synthetic-case", "tool": "dds", "stage": "none", "status": "skipped"},
        ]
        return check_record(self.record, self.root, phase, usage_records=usage)

    def test_complete_delivery_and_missing_evidence(self):
        self.assertTrue(self.check()["ok"])
        (self.root / "evidence.md").unlink()
        self.assertFalse(self.check()["ok"])

    def test_brief_legacy_compatibility_and_required_mode(self):
        self.assertTrue(self.check()["ok"])
        self.assertFalse(check_record(self.record, self.root, require_brief=True)["ok"])

    def test_brief_unknown_sources_allowed_but_bad_structure_rejected(self):
        self.record["analysis_brief"] = {
            "version": 1, "revision": 1, "decision": "Assess outcome",
            "risk": "Records may not represent actions", "evidence_plan": "Check event semantics first",
            "stop_condition": "Report verified results and gaps", "revision_reason": "Initial scope",
            "sources": [{"source_id": "events", "kind": "database", "availability": "unknown",
                         "applicability": "unknown", "freshness": "unknown", "note": "Access not checked"}],
        }
        self.assertTrue(check_record(self.record, self.root, require_brief=True)["ok"])
        valid = copy.deepcopy(self.record["analysis_brief"])
        for key, bad in [("revision", True), ("risk", "\u5f85\u586b"), ("sources", []), ("sources", [None])]:
            self.record["analysis_brief"] = {**valid, key: bad}
            self.assertFalse(self.check()["ok"], key)
        self.record["analysis_brief"] = copy.deepcopy(valid)
        self.record["analysis_brief"]["sources"][0]["availability"] = "success"
        self.assertFalse(self.check()["ok"])
        self.record["analysis_brief"] = copy.deepcopy(valid)
        self.record["analysis_brief"]["sources"].append(copy.deepcopy(valid["sources"][0]))
        self.assertFalse(self.check()["ok"])

    def test_partial_requires_impact_and_next_check(self):
        task = self.record["tasks"][0]
        task["status"] = "partial"
        self.assertFalse(self.check()["ok"])
        task.update(limitation="Assignment is unknown", next_check="Read assignment configuration")
        result = self.check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage"], "limited")

    def test_blocked_can_deliver_documented_gap(self):
        self.record["tasks"][0].update(status="blocked", evidence=[], result="No connection",
                                      limitation="Outcome cannot be counted", next_check="Restore read access")
        self.record["routes"][0].update(status="blocked", stage="none", evidence=[])
        self.record["actions"] = []
        self.assertTrue(self.check()["ok"])
        self.assertEqual(self.check()["coverage"], "limited")

    def test_plan_accepts_pending_but_delivery_rejects(self):
        self.record["tasks"][0].update(status="pending", evidence=[], result="")
        self.record["routes"][0].update(status="planned", stage="none", evidence=[])
        self.record["report"] = ""
        self.record["elapsed_seconds"] = None
        self.assertTrue(self.check("plan")["ok"])
        self.assertFalse(self.check()["ok"])

    def test_each_task_has_a_route(self):
        second = copy.deepcopy(self.record["tasks"][0])
        second.update(id="Q2", intent="describe")
        self.record["tasks"].append(second)
        self.assertFalse(self.check()["ok"])
        self.record["routes"][0]["task_ids"].append("Q2")
        self.assertTrue(self.check()["ok"])

    def test_effect_needs_action_but_simple_description_does_not(self):
        self.record["actions"] = []
        self.assertFalse(self.check()["ok"])
        self.record["tasks"][0]["intent"] = "describe"
        self.assertTrue(self.check()["ok"])

    def test_upstream_requires_actual_stage_version_and_ledger_link(self):
        route = self.record["routes"][0]
        route.update(provider="dds", stage="none")
        self.assertFalse(self.check()["ok"])
        route.update(stage="method", version="pinned-version", usage_id=1)
        self.assertTrue(self.check()["ok"])
        route["usage_id"] = 999
        self.assertFalse(self.check()["ok"])
        route["usage_id"] = True
        self.assertFalse(self.check()["ok"])

    def test_skipped_candidate_is_recorded_without_counting_as_execution(self):
        self.record["routes"].append({
            "task_ids": ["Q1"], "capability": "field-review", "provider": "dds",
            "decision": "skipped", "reason": "Valid source review already available",
            "status": "skipped", "stage": "none", "evidence": [], "usage_id": 2,
        })
        self.assertTrue(self.check()["ok"])
        self.record["routes"][1]["stage"] = "run"
        self.assertFalse(self.check()["ok"])

    def test_duplicate_ids_and_unknown_task_links_fail(self):
        self.record["tasks"].append(copy.deepcopy(self.record["tasks"][0]))
        self.record["actions"][0]["task_ids"] = ["Q9"]
        self.assertFalse(self.check()["ok"])

    def test_outside_root_reference_fails(self):
        self.record["tasks"][0]["evidence"] = [str(self.root / "evidence.md")]
        self.assertFalse(self.check()["ok"])

    def test_malformed_values_produce_errors(self):
        for key, value in (("tasks", None), ("routes", [None]), ("checks", []),
                           ("schema_version", True), ("elapsed_seconds", float("nan"))):
            with self.subTest(key=key):
                bad = {**self.record, key: value}
                self.assertFalse(check_record(bad, self.root)["ok"])

    def test_cli_exit_status(self):
        path = self.root / "execution.json"
        path.write_text(json.dumps(self.record), encoding="utf-8")
        command = [sys.executable, "-B", "-m", "praxtrace.execution_record",
                   str(path), "--root", str(self.root)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.record["tasks"][0]["status"] = "pending"
        path.write_text(json.dumps(self.record), encoding="utf-8")
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)

    def test_cli_checks_case_links_when_present(self):
        from praxtrace.case_records import now, capture, read_json, seal, write_json
        write_json(self.root / "execution.json", self.record)
        case = {"schema_version": 1, "case_id": self.record["case_id"], "title": "Synthetic", "question": "Question",
                "scope": "Scope", "tags": [], "registered_at": now(), "retention": "retain", "relations": [],
                "runs": [{"id": "R1", "origin": "native", "status": "complete", "summary": "Complete",
                          "recorded_at": now(), "execution": "execution.json"}], "artifacts": [], "reuse": [],
                "learning": [{"id": "L1", "run_id": "R1", "decision": "none", "layer": "case", "status": "applied",
                              "reason": "No new method", "validation": "Reviewed", "evidence": []}]}
        write_json(self.root / "case.json", case)
        capture(self.root, "case.json", "R1", "report.md", "A1", "report", "v1")
        seal(self.root, "case.json", "R1")
        command = [sys.executable, "-B", "-X", "utf8", "-m", "praxtrace.execution_record",
                   str(self.root / "execution.json"), "--root", str(self.root)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["case_links"]["ok"])
        case = read_json(self.root / "case.json")
        case["learning"] = []
        write_json(self.root / "case.json", case)
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1)


    def test_new_execution_requires_registration(self):
        path = self.root / "execution.json"
        path.write_text(json.dumps(self.record), encoding="utf-8")
        command = [sys.executable, "-B", "-X", "utf8", "-m", "praxtrace.execution_record",
                   str(path), "--root", str(self.root)]
        legacy = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        strict = subprocess.run(command + ["--require-case"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(strict.returncode, 1, strict.stderr)
        self.assertIn("case.json required", json.loads(strict.stdout)["errors"][-1])
        brief_required = subprocess.run(command + ["--require-brief"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(brief_required.returncode, 1, brief_required.stderr)
        self.assertIn("analysis_brief required", " ".join(json.loads(brief_required.stdout)["errors"]))


class UpstreamSummaryTests(unittest.TestCase):
    def test_repeat_use_prompts_comparison_without_claiming_review_ready(self):
        rows = [{"tool": "dds", "stage": "method", "sample": "work", "status": "completed",
                 "paired": 0, "case_id": f"case-{n}", "evidence": "added", "baseline_ms": None,
                 "assisted_ms": None} for n in range(6)]
        rows.append(copy.deepcopy(rows[0]))
        summary = summarize(rows, ["dds"], 5)["tools"]["dds"]["comparisons"]["method"]
        self.assertEqual(summary["used_work_cases"], 6)
        self.assertTrue(summary["comparison_needed"])
        self.assertFalse(summary["review_due"])
        self.assertEqual(summary["work_cases"], 0)
        for row in rows[:5]:
            row["paired"] = 1
        summary = summarize(rows, ["dds"], 5)["tools"]["dds"]["comparisons"]["method"]
        self.assertTrue(summary["review_due"])
        self.assertFalse(summary["comparison_needed"])

    def test_legacy_database_remains_readable(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(SCHEMA)
                connection.execute("""INSERT INTO usage
                    (recorded_at,tool,case_id,sample,trigger,stage,status,evidence,paired)
                    VALUES ('2026-11-10','dds','old-case','work','match','method','completed','added',0)""")
            rows = read_records(path, "dds")
            summary = summarize(rows, ["dds"], 5)["tools"]["dds"]["comparisons"]["method"]
            self.assertEqual(summary["used_work_cases"], 1)
            self.assertFalse(summary["comparison_needed"])


if __name__ == "__main__":
    unittest.main()
