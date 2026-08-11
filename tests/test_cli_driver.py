# tests/test_cli_driver.py
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def _run(args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "resume_as_code.cli", *args],
                          cwd=cwd, capture_output=True, text=True)

def test_driver_emits_json_with_auto_profile_and_validation(tmp_path):
    jd = tmp_path / "job.txt"
    jd.write_text("DevOps engineer: Kubernetes, Terraform, CI/CD, IaC, observability.")
    out = tmp_path / "out"
    res = _run(["generate", "--jd", str(jd), "--auto-profile", "--validate",
                "--json", "--out", str(out), "--job-name", "Devops Role"])
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["profileSelected"] in {"ai-architect", "devops", "devsecops"}
    assert payload["profileAuto"] is True
    assert isinstance(payload["atsPassed"], bool)
    assert Path(payload["artifacts"]["pdf"]).exists()
    assert Path(payload["artifacts"]["docx"]).exists()

def test_driver_explicit_profile_disables_auto(tmp_path):
    jd = tmp_path / "job.txt"
    jd.write_text("Some role.")
    out = tmp_path / "out"
    res = _run(["generate", "--jd", str(jd), "--profile", "devops",
                "--json", "--out", str(out), "--job-name", "Fixed"])
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["profileSelected"] == "devops"
    assert payload["profileAuto"] is False
