from app.models.models import Job
from app.schemas.schemas import ResumeStructured
from app.services.matcher import _rule_score


def test_rule_score_partial():
    s = ResumeStructured(skills=["python", "go"])
    job = Job(required_skills=["python", "go", "rust"])
    score, matched, missing, _ = _rule_score(s, job)
    assert matched == ["python", "go"]
    assert missing == ["rust"]
    assert 0 < score < 100


def test_rule_score_full():
    s = ResumeStructured(skills=["python", "go", "rust"])
    job = Job(required_skills=["python", "go", "rust"])
    score, matched, missing, _ = _rule_score(s, job)
    assert score == 100.0
    assert missing == []
