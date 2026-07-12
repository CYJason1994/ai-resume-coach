import uuid

from app.models.models import Job, JobMatch
from app.schemas.schemas import ResumeStructured
from app.services import matcher
from app.services.matcher import _rule_match_all, _rule_score, match_resume


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """极简异步 session 替身，仅供无 DB 的规则匹配单测。"""

    def __init__(self, jobs):
        self._jobs = jobs
        self.added = []

    async def scalars(self, stmt):
        # 忽略 where 条件，返回带 required_skills 的岗位
        return _Result([j for j in self._jobs if j.required_skills])

    def add(self, obj):
        # AsyncSession.add 是同步方法（非协程）
        self.added.append(obj)

    async def flush(self):
        pass


def _make_jobs():
    j1 = Job(title="后端工程师", required_skills=["python", "go", "rust"])
    j2 = Job(title="前端工程师", required_skills=["react", "typescript"])
    j3 = Job(title="数据工程师", required_skills=["python", "sql", "etl"])
    return [j1, j2, j3]


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


async def test_rule_match_all_ranks_and_limits():
    s = ResumeStructured(skills=["python", "go"])
    jobs = _make_jobs()
    sess = _FakeSession(jobs)
    out = await _rule_match_all(uuid.uuid4(), s, sess)
    assert len(out) == 3  # 仅 3 个岗位，未超 TOP_K
    scores = [m.score for m in out]
    assert scores == sorted(scores, reverse=True)  # 降序
    assert abs(out[0].score - 66.7) < 0.01  # 后端(2/3) 排第一
    assert out[-1].score == 0.0  # 前端(0 命中) 垫底
    assert len(sess.added) == 3


async def test_match_resume_falls_back_to_rule_when_embed_fails(monkeypatch):
    """P1-3：无嵌入 Key（embed 抛错）时，match_resume 必须产出规则匹配，
    而非返回空列表（否则 MVP 无 Key 无法演示匹配）。"""

    class _BoomLLM:
        async def embed(self, texts):
            raise RuntimeError("no api key / network down")

        async def chat(self, *a, **k):
            raise RuntimeError("unreachable")

    monkeypatch.setattr(matcher, "get_llm", lambda: _BoomLLM())
    s = ResumeStructured(skills=["python", "go"])
    sess = _FakeSession(_make_jobs())
    out = await match_resume(uuid.uuid4(), s, sess)
    assert out, "无 Key 时匹配不应为空（规则兜底必须可达）"
    assert all(isinstance(m, JobMatch) for m in out)
    assert out[0].score >= out[-1].score
