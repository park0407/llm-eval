"""규칙 기반 채점. 같은 응답이면 항상 같은 결과가 나온다 (AI 채점 아님)."""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass

FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$", re.S)
LIST_PREFIX_RE = re.compile(r"^\s*([-*•·▪●]|\d+[.)]|[①-⑩])\s*")


@dataclass
class CheckResult:
    passed: bool
    fail_as: str | None
    detail: str


def _lines(text: str) -> list[str]:
    return [ln for ln in text.strip().splitlines() if ln.strip()]


def strip_fence(text: str) -> str:
    m = FENCE_RE.match(text)
    return m.group(1) if m else text


def _norm(s: str) -> str:
    return re.sub(r"[\s.!?。]+$", "", s.strip())


def run_check(check: dict, text: str) -> CheckResult:
    t = check["type"]
    fail_as = check.get("fail_as", "기타")
    ok, detail = False, ""

    if t == "contains_any":
        hit = [v for v in check["values"] if v in text]
        ok, detail = bool(hit), f"포함 필요: {check['values']}"
    elif t == "not_contains":
        hit = [v for v in check["values"] if v in text]
        ok, detail = not hit, f"들어가면 안 되는 말이 들어감: {hit}" if hit else "금지어 없음"
    elif t == "regex":
        ok = re.search(check["pattern"], text, re.M) is not None
        detail = f"패턴: {check['pattern']}"
    elif t == "exact":
        ok = _norm(text) == check["value"]
        detail = f"정확히 '{check['value']}' 여야 함"
    elif t == "starts_with":
        ok = text.strip().startswith(check["value"])
        detail = f"'{check['value']}'로 시작해야 함"
    elif t == "max_chars":
        n = len(text.strip())
        ok, detail = n <= check["value"], f"{n}자 (한도 {check['value']}자)"
    elif t == "line_count":
        n = len(_lines(text))
        ok, detail = n == check["equals"], f"{n}줄 (필요 {check['equals']}줄)"
    elif t == "no_line_prefix":
        bad = [ln for ln in _lines(text) if LIST_PREFIX_RE.match(ln)]
        ok, detail = not bad, f"번호·기호로 시작하는 줄 {len(bad)}개"
    elif t == "no_latin":
        found = re.findall(r"[A-Za-z]+", text)
        ok, detail = not found, f"영문 발견: {found[:5]}" if found else "영문 없음"
    elif t == "dash_list":
        lines = _lines(text)
        dash = [ln for ln in lines if ln.lstrip().startswith("- ")]
        ok = len(dash) == check["equals"] and len(dash) == len(lines)
        detail = f"'- ' 항목 {len(dash)}개, 전체 {len(lines)}줄 (필요 항목 {check['equals']}개, 다른 줄 없음)"
    elif t == "numbered_list":
        lines = _lines(text)
        nums = [ln for ln in lines if re.match(r"^\s*\d+\.\s", ln)]
        expected = [f"{i}." for i in range(1, check["equals"] + 1)]
        got = [ln.strip().split()[0] for ln in nums]
        ok = got == expected and len(nums) == len(lines)
        detail = f"번호 {got}, 전체 {len(lines)}줄 (필요 {expected}, 다른 줄 없음)"
    elif t in ("json", "json_array"):
        raw = text.strip() if check.get("strict") else strip_fence(text).strip()
        try:
            obj = json.loads(raw)
        except Exception:
            return CheckResult(False, fail_as, "JSON으로 읽을 수 없음" + (" (코드블록 금지)" if check.get("strict") else ""))
        if t == "json":
            exp = check["expect"]
            ok = isinstance(obj, dict) and set(obj) == set(exp) and all(obj[k] == v for k, v in exp.items())
            detail = f"기대값 {exp}, 실제 {obj}"
        else:
            ok = (isinstance(obj, list) and len(obj) == check["length"]
                  and all(x in obj for x in check.get("contains", [])))
            detail = f"기대: {check['length']}개 {check.get('contains')}, 실제 {obj}"
    elif t == "md_table":
        rows = [ln.strip() for ln in _lines(text) if ln.strip().startswith("|")]
        others = [ln for ln in _lines(text) if not ln.strip().startswith("|")]
        if len(rows) < 2:
            return CheckResult(False, fail_as, "표가 없음")
        header = [c.strip() for c in rows[0].strip("|").split("|")]
        body = [r for r in rows[2:] if not re.fullmatch(r"\|?[\s:\-|]+\|?", r)]
        ok = header == check["header"] and len(body) == check["rows"] and not others
        detail = f"머리글 {header}, 데이터 {len(body)}행, 표 밖 줄 {len(others)}개"
    elif t == "csv":
        raw = strip_fence(text).strip()
        got = [[c.strip() for c in r] for r in csv.reader(io.StringIO(raw)) if r]
        ok, detail = got == check["rows"], f"기대 {check['rows']}, 실제 {got}"
    else:
        raise ValueError(f"알 수 없는 검사 종류: {t}")

    return CheckResult(ok, None if ok else fail_as, detail)
