"""Account for all currently collected test nodes across regression rounds."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).parent / "evidence"
collected = {line.strip() for line in (BASE / "final-collected.txt").read_text(encoding="utf-8").splitlines()
             if line.startswith("tests/") and "::" in line}
files = ["full-regression.xml", "followup-tests.xml", "smtp-planning-final-tests.xml",
         "migrations-final.xml", "citation-final-tests.xml"]
latest, rounds, retired = {}, [], set()
for name in files:
    root = ET.parse(BASE / name).getroot()
    cases = root.findall(".//testcase")
    failed = [c.get("name") for c in cases if c.find("failure") is not None or c.find("error") is not None]
    skipped = [c.get("name") for c in cases if c.find("skipped") is not None]
    assert not failed and not skipped, (name, failed, skipped)
    rounds.append({"junit": name, "passed": len(cases), "failed": 0, "skipped": 0})
    for case in cases:
        node = case.get("classname").replace(".", "/") + ".py::" + case.get("name")
        if node in collected:
            latest[node] = name
        else:
            retired.add(node)
missing = sorted(collected - latest.keys())
assert not missing, missing
browser = [node for node in collected if node.startswith(("tests/test_browser_flow.py::",
    "tests/test_browser_onboarding.py::", "tests/test_agent_browser.py::", "tests/test_chat_browser.py::"))]
report = {"collected_current_tests": len(collected), "current_tests_with_success": len(latest),
          "full_run_passed": rounds[0]["passed"], "full_run_failed": 0, "full_run_skipped": 0,
          "browser_tests_executed": browser, "rounds": rounds,
          "intermediate_tests_removed_from_final_code": sorted(retired),
          "latest_success_by_node": latest}
(BASE / "test-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if k != "latest_success_by_node"}, ensure_ascii=False))
