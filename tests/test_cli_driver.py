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

def test_validate_forces_pdf_render_even_when_formats_excludes_it(tmp_path):
    jd = tmp_path / "job.txt"
    jd.write_text("DevOps engineer: Kubernetes, Terraform, CI/CD.")
    out = tmp_path / "out"
    res = _run(["generate", "--jd", str(jd), "--profile", "devops",
                "--validate", "--json", "--out", str(out),
                "--formats", "docx,txt", "--job-name", "Edge"])
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert isinstance(payload["atsPassed"], bool)          # validation ran
    assert Path(payload["artifacts"]["pdf"]).exists()      # pdf was rendered

def test_driver_reports_the_gap_analysis_in_json(tmp_path):
    """El consumidor del CV (CareerOps / Jarvis) tiene que saber que le falta al
    candidato sin parsear markdown ni importar internals de este paquete."""
    jd = tmp_path / "job.txt"
    jd.write_text("SRE: Kubernetes, Terraform, Prometheus, Go, Rust, COBOL.")
    out = tmp_path / "out"
    res = _run(["generate", "--jd", str(jd), "--auto-profile", "--json",
                "--out", str(out), "--job-name", "Gaps"])
    assert res.returncode == 0, res.stderr
    match = json.loads(res.stdout)["jobMatch"]
    assert isinstance(match["covered"], list)
    assert isinstance(match["transferable"], list)
    assert isinstance(match["gaps"], list)
    assert isinstance(match["matchedSkills"], list)


def test_driver_omits_job_match_when_there_is_no_job_description(tmp_path):
    """Sin JD no hay analisis: no se inventa un jobMatch vacio que parezca real."""
    out = tmp_path / "out"
    res = _run(["generate", "--profile", "devops", "--json", "--out", str(out),
                "--job-name", "SinJD"])
    assert res.returncode == 0, res.stderr
    assert "jobMatch" not in json.loads(res.stdout)
