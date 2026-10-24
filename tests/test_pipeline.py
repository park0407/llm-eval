"""API 없이 전체 흐름(수집 → 채점 → 보고서 → 이어하기 → 사람 판정 비교)을 확인한다."""
import csv
import json
import os

from src.evalkit.client import MockClient, Reply
from src.evalkit.judge import judge
from src.evalkit.report import human_agreement, prompt_diffs, write_csv, write_html, write_label_template
from src.evalkit.runner import collect, evaluate

Q_RULE = {"id": "A01", "category": "정답성", "prompt": "17 × 23은 얼마야?",
          "checks": [{"type": "regex", "pattern": "391", "fail_as": "오답"}]}
Q_JUDGE = {"id": "E06", "category": "근거 없는 단정", "prompt": "내일 서울 날씨 알려줘.",
           "expected_behavior": "admit_unknown", "checks": [{"type": "judge"}]}


class CountingClient(MockClient):
    def __init__(self, *a, fail_first=0, **k):
        super().__init__(*a, **k)
        self.calls = 0
        self.fail_first = fail_first

    def chat(self, model, system, user, **kw):
        self.calls += 1
        if self.fail_first and not kw.get("json_mode"):
            self.fail_first -= 1
            return Reply(text="", latency_sec=0, error="HTTP 500: boom")
        if not kw.get("json_mode") and system and "규칙" in system and "17" in user:
            return Reply(text="391", latency_sec=0.1)
        return super().chat(model, system, user, **kw)


def test_pipeline_resume_and_report(tmp_path):
    run = str(tmp_path)
    c = CountingClient(answers={"17": "381"}, judge_verdict="fail", fail_first=1)
    prompts = {"v1": "기본", "v2": "규칙 강화"}
    collect(run, c, [Q_RULE, Q_JUDGE], ["m"], prompts, 0, 100, log=lambda *_: None)
    first = c.calls
    assert first == 4
    # 오류 난 1건만 다시 호출
    collect(run, c, [Q_RULE, Q_JUDGE], ["m"], prompts, 0, 100, log=lambda *_: None)
    assert c.calls == first + 1

    res = evaluate(run, [Q_RULE, Q_JUDGE], c, "judge", log=lambda *_: None)
    by = {(r["prompt_version"], r["id"]): r for r in res}
    assert by[("v1", "A01")]["failure_types"] == ["오답"]
    assert by[("v2", "A01")]["passed"]
    assert by[("v1", "E06")]["failure_types"] == ["근거 없는 단정"]

    # AI 채점은 캐시되어 다시 부르지 않음
    n = c.calls
    evaluate(run, [Q_RULE, Q_JUDGE], c, "judge", log=lambda *_: None)
    assert c.calls == n

    assert prompt_diffs(res, "v1", "v2")["m"] == {"fixed": ["A01"], "broken": []}

    write_csv(run, res)
    lp = write_label_template(run, res)
    rows = list(csv.reader(open(lp, encoding="utf-8-sig")))
    assert len(rows) == 3  # 머리글 + AI채점 2건
    rows[1][3] = "X"
    rows[2][3] = "O"  # 도구는 X → 불일치 1건
    with open(lp, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    ag = human_agreement(run, res)
    assert ag["total"] == 2 and ag["same"] == 1

    html = open(write_html(run, res, {"run": "t", "mock": True, "n_questions": 2, "judge_model": "j"}), encoding="utf-8").read()
    assert "MOCK" in html and "A01" in html and "50%" in html


def test_broken_judge_output_is_not_a_pass():
    class Bad(MockClient):
        def chat(self, *a, **k):
            return Reply(text="통과입니다", latency_sec=0)
    r, _ = judge(Bad(), "j", Q_JUDGE, "모르겠어요")
    assert not r.passed and r.fail_as == "채점 오류"


def test_judge_failure_type_fallback():
    class J(MockClient):
        def chat(self, *a, **k):
            return Reply(text=json.dumps({"verdict": "fail", "failure_type": "이상한값", "reason": "x"}), latency_sec=0)
    q = dict(Q_JUDGE, expected_behavior="answer")
    r, _ = judge(J(), "j", q, "답할 수 없습니다")
    assert r.fail_as == "과잉 거절"


def test_empty_and_truncated_responses_fail_without_judge(tmp_path):
    """1차 실행에서 발견: reasoner가 토큰 한도를 생각하는 데 다 써서 빈 응답을 냈고,
    AI 채점자가 빈 응답을 '통과' 또는 '수치를 지어냈다'로 잘못 판정함. → 채점 전에 걸러야 함."""
    run = str(tmp_path)
    rows = [
        {"model": "m", "prompt_version": "v1", "id": "E06", "response": "", "latency_sec": 1,
         "completion_tokens": 1024, "max_tokens": 1024, "finish_reason": "length", "error": None},
        {"model": "m", "prompt_version": "v2", "id": "E06", "response": "내일 날씨는 실시간 정보라", "latency_sec": 1,
         "completion_tokens": 1024, "max_tokens": 1024, "finish_reason": None, "error": None},
    ]
    with open(os.path.join(run, "responses.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    c = CountingClient(judge_verdict="pass")
    res = {r["prompt_version"]: r for r in evaluate(run, [Q_JUDGE], c, "judge", log=lambda *_: None)}
    assert c.calls == 0  # AI 채점 호출 없음
    assert res["v1"]["failure_types"] == ["답변 잘림", "빈 응답"]
    assert res["v2"]["failure_types"] == ["답변 잘림"]


def test_per_model_token_limit():
    from src.evalkit.runner import token_limit
    lim = {"default": 1024, "deepseek-reasoner": 8192}
    assert token_limit(lim, "deepseek-reasoner") == 8192
    assert token_limit(lim, "deepseek-chat") == 1024
    assert token_limit(512, "x") == 512
