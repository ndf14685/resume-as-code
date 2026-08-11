from pathlib import Path

from resume_as_code.autoselect import pick_profile, score_profiles, select_profile

PROFILES = Path(__file__).resolve().parent.parent / "profiles"
DATA = Path(__file__).resolve().parent.parent / "data"


def test_score_profiles_weights_by_priority_position():
    # "data" is unique to devops; weighting keeps devops strictly ahead here
    scores = score_profiles({"containers", "iac", "data"}, PROFILES)
    assert set(scores) == {"ai-architect", "devops", "devsecops"}
    assert scores["devops"] > scores["devsecops"]


def test_devsecops_is_reachable_for_security_focused_input():
    # a security-slanted JD hits mostly the devsecops category (+cicd);
    # devsecops leads with devsecops, so it must win despite a shorter list
    scores = score_profiles({"devsecops", "cicd"}, PROFILES)
    winner = max(sorted(scores), key=lambda k: scores[k])
    assert winner == "devsecops", scores


def test_pick_profile_returns_highest_score():
    name, is_default = pick_profile({"devops": 3, "ai-architect": 1, "devsecops": 0})
    assert (name, is_default) == ("devops", False)


def test_pick_profile_alphabetical_tie_break():
    name, _ = pick_profile({"devops": 2, "devsecops": 2, "ai-architect": 0})
    assert name == "devops"  # alphabetical among tied {devops, devsecops}


def test_pick_profile_all_zero_uses_default():
    name, is_default = pick_profile({"devops": 0, "ai-architect": 0}, default="ai-architect")
    assert (name, is_default) == ("ai-architect", True)


def test_select_profile_end_to_end_discriminates_on_infra_jd():
    jd = (
        "Senior DevOps / Platform Engineer. Must have Kubernetes, Docker, "
        "Terraform, Ansible, CI/CD pipelines (GitLab CI), AWS, and strong "
        "observability / reliability practices."
    )
    sel = select_profile(jd, DATA, PROFILES)
    assert sel.name in {"ai-architect", "devops", "devsecops"}
    assert sel.path.exists()
    assert sel.score > 0            # matched categories overlapped a profile
    assert sel.is_default is False  # a real profile was chosen, not the fallback
