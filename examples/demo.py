"""Synthetic installed-package lifecycle demonstration; no external services."""
from pathlib import Path
from tempfile import TemporaryDirectory
from praxtrace import check_record
from praxtrace.case_records import now, write_json, capture, seal
from praxtrace.case_ops import finish

with TemporaryDirectory() as directory:
    root = Path(directory)
    (root / "evidence.txt").write_text("Synthetic configuration: disabled", encoding="utf-8")
    (root / "report.md").write_text("# Result\nSynthetic configuration is disabled; deployment is unknown.\n", encoding="utf-8")
    record = {"schema_version":1,"case_id":"demo","request":"Inspect synthetic configuration","scope":"Synthetic project only","scope_basis":"Demo fixture","reuse":"No prior evidence",
      "tasks":[{"id":"Q1","intent":"describe","question":"Is the setting enabled?","acceptance":"Cite the fixture","status":"answered","evidence":["evidence.txt"],"result":"Disabled in fixture; deployment unknown"}],
      "routes":[{"task_ids":["Q1"],"provider":"local","capability":"fixture inspection","decision":"selected","reason":"Synthetic source","stage":"run","status":"completed","evidence":["evidence.txt"]}],
      "checks":[{"id":x,"status":"pass","detail":"Synthetic scope checked"} for x in ["scope","evidence","decision"]],"actions":[],"report":"report.md","elapsed_seconds":0.1}
    record["analysis_brief"]={"version":1,"revision":1,"decision":"Check fixture state","risk":"Fixture is not production","evidence_plan":"Read the fixture and preserve deployment uncertainty","stop_condition":"Report fixture result and unknown deployment","revision_reason":"Initial synthetic brief","sources":[{"source_id":"fixture","kind":"file","availability":"available","applicability":"matched","freshness":"current","note":"Frozen synthetic fixture"}]}
    assert check_record(record,root,require_brief=True)["ok"]
    write_json(root/"execution.json",record)
    case={"schema_version":1,"case_id":"demo","title":"Synthetic evidence demo","question":"Inspect fixture","scope":"Synthetic only","tags":[],"registered_at":now(),"retention":"retain","relations":[],"runs":[{"id":"R1","origin":"native","recorded_at":now(),"status":"complete","summary":"Fixture checked","execution":"execution.json"}],"artifacts":[],"reuse":[],"learning":[{"id":"L1","run_id":"R1","decision":"none","layer":"case","status":"applied","reason":"Synthetic demo only","validation":"Fixture assertions","evidence":["evidence.txt"]}],"review_not_applicable":"Synthetic bookkeeping demo, not a real business claim"}
    write_json(root/"case.json",case)
    capture(root,"case.json","R1","report.md","A1","report","v1")
    seal(root,"case.json","R1")
    result=finish(root,"case.json",["."],".local/index.json")
    assert result["ok"],result
    broken={**record,"tasks":[{**record["tasks"][0],"evidence":["missing.txt"]}]}
    assert not check_record(broken,root)["ok"]
    print("PASS: installed package, report capture, execution seal, case finish, missing evidence rejection")
