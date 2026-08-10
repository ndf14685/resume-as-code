import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
PROFILES_DIR = REPO_ROOT / "profiles"
JOBS_DIR = REPO_ROOT / "jobs"

sys.path.insert(0, str(REPO_ROOT / "src"))
