"""질문 세트 실행 → 응답 저장 → 채점. 응답과 AI 채점은 파일에 쌓아 두고, 중간에 끊겨도 이어서 한다."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import asdict

from .checks import run_check
from .judge import judge


def load_questions(path: str) -> list[dict]:
    qs = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            for key in ("id", "category", "prompt", "checks"):
                if key not in q:
                    raise ValueError(f"{path} {n}번째 줄: '{key}' 없음")
            if any(c["type"] == "judge" for c in q["checks"]) and "expected_behavior" not in q:
                raise ValueError(f"{q['id']}: AI 채점 문항은 expected_behavior 가 필요함")
            qs.append(q)
    ids = [q["id"] for q in qs]
    if len(ids) != len(set(ids)):
        raise ValueError("질문 id 가 중복됨")
    return qs


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _append(path: str, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _key(r: dict) -> tuple:
    return (r["model"], r["prompt_version"], r["id"])


def collect(run_dir: str, client, questions: list[dict], models: list[str], prompts: dict[str, str],
            temperature: float, max_tokens, log=print) -> None:
    """max_tokens: 숫자 하나, 또는 {모델이름: 숫자, "default": 숫자}."""
    """응답 수집. 이미 받은 (모델, 프롬프트, 질문)은 건너뛴다. 오류 난 것은 다시 시도한다."""
    path = os.path.join(run_dir, "responses.jsonl")
    done = {_key(r) for r in _read_jsonl(path) if not r.get("error")}
    total = len(models) * len(prompts) * len(questions)
    i = 0
    for model in models:
        for pv, system in prompts.items():
            for q in questions:
                i += 1
                k = (model, pv, q["id"])
                if k in done:
                    continue
                mt = token_limit(max_tokens, model)
                rep = client.chat(model, system, q["prompt"], temperature=temperature, max_tokens=mt)
                _append(path, {"model": model, "prompt_version": pv, "id": q["id"], "response": rep.text,
                               "latency_sec": rep.latency_sec, "prompt_tokens": rep.prompt_tokens,
                               "completion_tokens": rep.completion_tokens, "max_tokens": mt,
                               "finish_reason": rep.finish_reason, "error": rep.error,
                               "at": dt.datetime.now().isoformat(timespec="seconds")})
                log(f"[{i}/{total}] {model} {pv} {q['id']} " + ("오류: " + rep.error if rep.error else "ok"))


def token_limit(max_tokens, model: str) -> int:
    if isinstance(max_tokens, dict):
        return int(max_tokens.get(model, max_tokens.get("default", 1024)))
    return int(max_tokens)


def response_problems(resp: dict) -> list[dict]:
    """내용과 상관없이 응답 자체가 쓸 수 없는 경우. 이런 응답은 AI 채점에 넘기지 않는다."""
    out = []
    if not resp["response"].strip():
        out.append({"passed": False, "fail_as": "빈 응답",
                    "detail": f"응답 내용이 비어 있음 (생성 토큰 {resp.get('completion_tokens')}개)"})
    fr = resp.get("finish_reason")
    ct, mt = resp.get("completion_tokens"), resp.get("max_tokens", 1024)
    if fr == "length" or (fr is None and ct is not None and ct >= mt):
        out.append({"passed": False, "fail_as": "답변 잘림",
                    "detail": f"토큰 한도({mt})에 걸려 답이 중간에 끊김"})
    return out


def latest_responses(run_dir: str) -> dict[tuple, dict]:
    out = {}
    for r in _read_jsonl(os.path.join(run_dir, "responses.jsonl")):
        out[_key(r)] = r  # 나중 기록이 이전 기록(오류 포함)을 덮어씀
    return out


def evaluate(run_dir: str, questions: list[dict], judge_client, judge_model: str, log=print) -> list[dict]:
    """채점. 규칙 검사는 매번 새로 계산, AI 채점은 저장된 것이 있으면 재사용."""
    jpath = os.path.join(run_dir, "judgments.jsonl")
    cached = {}
    for r in _read_jsonl(jpath):
        if r.get("failure_type") != "채점 오류":
            cached[(r["model"], r["prompt_version"], r["id"], r["response_hash"])] = r
    qmap = {q["id"]: q for q in questions}
    results = []
    for k, resp in sorted(latest_responses(run_dir).items()):
        model, pv, qid = k
        q = qmap.get(qid)
        if q is None:
            continue
        row = {"model": model, "prompt_version": pv, "id": qid, "category": q["category"],
               "prompt": q["prompt"], "response": resp["response"], "latency_sec": resp["latency_sec"],
               "checks": [], "used_judge": False}
        if resp.get("error"):
            row.update(passed=False, failure_types=["호출 오류"],
                       checks=[{"passed": False, "fail_as": "호출 오류", "detail": resp["error"]}])
            results.append(row)
            continue
        problems = response_problems(resp)
        if problems:
            row["checks"] = problems
            row.update(passed=False, failure_types=sorted({p["fail_as"] for p in problems}))
            results.append(row)
            continue
        for c in q["checks"]:
            if c["type"] == "judge":
                row["used_judge"] = True
                h = _stable_hash(resp["response"])
                ck = (model, pv, qid, h)
                if ck in cached:
                    j = cached[ck]
                    res = {"passed": j["passed"], "fail_as": j["failure_type"], "detail": j["detail"]}
                else:
                    cr, raw = judge(judge_client, judge_model, q, resp["response"])
                    res = asdict(cr)
                    _append(jpath, {"model": model, "prompt_version": pv, "id": qid, "response_hash": h,
                                    "judge_model": judge_model, "passed": cr.passed,
                                    "failure_type": cr.fail_as, "detail": cr.detail, **raw})
                    log(f"채점 {model} {pv} {qid}: {'통과' if cr.passed else cr.fail_as}")
                row["checks"].append(res)
            else:
                row["checks"].append(asdict(run_check(c, resp["response"])))
        fails = [c["fail_as"] for c in row["checks"] if not c["passed"]]
        row["passed"] = not fails
        row["failure_types"] = sorted(set(fails))
        results.append(row)
    with open(os.path.join(run_dir, "results.jsonl"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return results


def _stable_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
