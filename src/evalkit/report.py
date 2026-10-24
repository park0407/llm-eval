"""결과 집계 → CSV + HTML 보고서. 숫자는 results.jsonl 에서만 계산한다."""
from __future__ import annotations

import csv
import html
import json
import os
from collections import Counter, defaultdict


def load_results(run_dir: str) -> list[dict]:
    with open(os.path.join(run_dir, "results.jsonl"), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def combos(results):
    return sorted({(r["model"], r["prompt_version"]) for r in results})


def summarize(results: list[dict]) -> dict:
    by_combo = defaultdict(list)
    for r in results:
        by_combo[(r["model"], r["prompt_version"])].append(r)
    out = {}
    for k, rows in by_combo.items():
        cat = defaultdict(lambda: [0, 0])
        ft = Counter()
        for r in rows:
            cat[r["category"]][1] += 1
            cat[r["category"]][0] += r["passed"]
            ft.update(r["failure_types"])
        lat = [r["latency_sec"] for r in rows if r.get("latency_sec")]
        out[k] = {"passed": sum(r["passed"] for r in rows), "total": len(rows),
                  "by_category": dict(cat), "failure_types": dict(ft),
                  "avg_latency": round(sum(lat) / len(lat), 2) if lat else None}
    return out


def prompt_diffs(results: list[dict], a: str, b: str) -> dict[str, dict]:
    """모델별로 프롬프트 a → b 에서 통과/실패가 바뀐 질문."""
    idx = {(r["model"], r["prompt_version"], r["id"]): r for r in results}
    out = {}
    for model in sorted({r["model"] for r in results}):
        fixed, broken = [], []
        for (m, pv, qid), r in idx.items():
            if m != model or pv != a or (m, b, qid) not in idx:
                continue
            rb = idx[(m, b, qid)]
            if not r["passed"] and rb["passed"]:
                fixed.append(qid)
            elif r["passed"] and not rb["passed"]:
                broken.append(qid)
        out[model] = {"fixed": sorted(fixed), "broken": sorted(broken)}
    return out


def write_csv(run_dir: str, results: list[dict]) -> str:
    path = os.path.join(run_dir, "results.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["모델", "프롬프트", "질문ID", "분류", "통과", "실패 유형", "판정 근거", "AI채점 사용", "응답시간(초)", "질문", "응답"])
        for r in results:
            reasons = " / ".join(c["detail"] for c in r["checks"] if not c["passed"])
            w.writerow([r["model"], r["prompt_version"], r["id"], r["category"], "O" if r["passed"] else "X",
                        ", ".join(r["failure_types"]), reasons, "O" if r["used_judge"] else "",
                        r["latency_sec"], r["prompt"], r["response"]])
    return path


def human_agreement(run_dir: str, results: list[dict]) -> dict | None:
    """human_labels.csv 에 사람이 적은 판정과 도구 판정이 얼마나 같은지."""
    path = os.path.join(run_dir, "human_labels.csv")
    if not os.path.exists(path):
        return None
    idx = {(r["model"], r["prompt_version"], r["id"]): r for r in results}
    same = total = 0
    diffs = []
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            h = (row.get("사람판정") or "").strip().upper()
            if h not in ("O", "X"):
                continue
            r = idx.get((row["모델"], row["프롬프트"], row["질문ID"]))
            if not r:
                continue
            total += 1
            tool = "O" if r["passed"] else "X"
            if tool == h:
                same += 1
            else:
                diffs.append(f"{row['모델']} {row['프롬프트']} {row['질문ID']} (도구 {tool} / 사람 {h})")
    if not total:
        return None
    return {"same": same, "total": total, "diffs": diffs}


def write_label_template(run_dir: str, results: list[dict]) -> str:
    path = os.path.join(run_dir, "human_labels.csv")
    if os.path.exists(path):
        return path
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["모델", "프롬프트", "질문ID", "사람판정", "메모", "질문", "응답"])
        for r in results:
            if r["used_judge"]:
                w.writerow([r["model"], r["prompt_version"], r["id"], "", "", r["prompt"], r["response"]])
    return path


CSS = """
body{font-family:'Noto Sans KR','Malgun Gothic',sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#1d1d1f;line-height:1.5}
h1{font-size:24px}h2{font-size:19px;margin-top:36px;border-bottom:2px solid #ddd;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px}th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f4f4f6}.ok{color:#137333;font-weight:600}.ng{color:#b3261e;font-weight:600}
.banner{background:#fff4e5;border:1px solid #f0b35b;padding:10px 14px;border-radius:6px;margin:12px 0}
details{margin:4px 0}pre{white-space:pre-wrap;background:#f7f7f9;padding:8px;border-radius:4px;font-size:13px}
.muted{color:#666;font-size:13px}
"""


def _pct(p, t):
    return f"{p}/{t} ({p / t * 100:.0f}%)" if t else "-"


def write_html(run_dir: str, results: list[dict], meta: dict) -> str:
    e = html.escape
    summ = summarize(results)
    cs = combos(results)
    cats = sorted({r["category"] for r in results}, key=lambda c: min(r["id"] for r in results if r["category"] == c))
    out = [f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>LLM 응답 품질 평가 — {e(meta.get('run', ''))}</title><style>{CSS}</style></head><body>"]
    out.append(f"<h1>LLM 응답 품질 평가 보고서</h1><p class='muted'>실행: {e(meta.get('run',''))} · 시각: {e(meta.get('started',''))} · "
               f"질문 {meta.get('n_questions')}개 · 채점 모델: {e(meta.get('judge_model',''))}</p>")
    if meta.get("mock"):
        out.append("<div class='banner'><b>MOCK 실행</b> — 실제 AI를 부르지 않고 가짜 응답으로 흐름만 확인한 결과입니다. 아래 숫자는 모델 성능이 아닙니다.</div>")

    out.append("<h2>1. 전체 통과율</h2><table><tr><th>모델</th><th>프롬프트</th><th>통과</th><th>평균 응답시간(초)</th></tr>")
    for k in cs:
        s = summ[k]
        out.append(f"<tr><td>{e(k[0])}</td><td>{e(k[1])}</td><td>{_pct(s['passed'], s['total'])}</td><td>{s['avg_latency'] or '-'}</td></tr>")
    out.append("</table>")

    out.append("<h2>2. 분류별 통과율</h2><table><tr><th>분류</th>" + "".join(f"<th>{e(m)}<br>{e(p)}</th>" for m, p in cs) + "</tr>")
    for c in cats:
        cells = "".join(f"<td>{_pct(*summ[k]['by_category'].get(c, [0, 0]))}</td>" for k in cs)
        out.append(f"<tr><td>{e(c)}</td>{cells}</tr>")
    out.append("</table>")

    fts = sorted({ft for s in summ.values() for ft in s["failure_types"]})
    out.append("<h2>3. 실패 유형</h2>")
    if fts:
        out.append("<table><tr><th>실패 유형</th>" + "".join(f"<th>{e(m)}<br>{e(p)}</th>" for m, p in cs) + "</tr>")
        for ft in fts:
            out.append(f"<tr><td>{e(ft)}</td>" + "".join(f"<td>{summ[k]['failure_types'].get(ft, 0)}</td>" for k in cs) + "</tr>")
        out.append("</table><p class='muted'>한 응답이 여러 유형으로 동시에 실패할 수 있어 합계가 실패 건수보다 클 수 있습니다.</p>")
    else:
        out.append("<p>실패 없음.</p>")

    pvs = sorted({p for _, p in cs})
    if len(pvs) >= 2:
        a, b = pvs[0], pvs[-1]
        out.append(f"<h2>4. 프롬프트 수정 전후 ({e(a)} → {e(b)})</h2><table><tr><th>모델</th><th>고쳐진 질문 (실패→통과)</th><th>나빠진 질문 (통과→실패)</th></tr>")
        for m, d in prompt_diffs(results, a, b).items():
            out.append(f"<tr><td>{e(m)}</td><td>{e(', '.join(d['fixed']) or '없음')}</td><td>{e(', '.join(d['broken']) or '없음')}</td></tr>")
        out.append("</table>")

    ag = human_agreement(run_dir, results)
    out.append("<h2>5. AI 채점 검증 (사람 판정과 비교)</h2>")
    if ag:
        out.append(f"<p>사람이 판정한 {ag['total']}건 중 도구 판정과 같은 것: <b>{ag['same']}건</b> ({ag['same']/ag['total']*100:.0f}%)</p>")
        if ag["diffs"]:
            out.append("<p>다른 것:</p><ul>" + "".join(f"<li>{e(x)}</li>" for x in ag["diffs"]) + "</ul>")
    else:
        out.append("<p class='muted'>아직 사람 판정이 없습니다. <code>human_labels.csv</code>의 '사람판정' 칸에 O/X를 적고 report를 다시 만들면 여기에 일치율이 나옵니다.</p>")

    out.append("<h2>6. 실패한 응답 전체</h2>")
    for r in results:
        if r["passed"]:
            continue
        reasons = "".join(f"<li>{e(c['fail_as'] or '')}: {e(c['detail'])}</li>" for c in r["checks"] if not c["passed"])
        out.append(f"<details><summary><span class='ng'>✗</span> {e(r['id'])} · {e(r['model'])} · {e(r['prompt_version'])} · "
                   f"{e(', '.join(r['failure_types']))}</summary><p><b>질문</b> {e(r['prompt'])}</p><ul>{reasons}</ul>"
                   f"<pre>{e(r['response'])}</pre></details>")

    out.append("<h2>7. 전체 결과표</h2><table><tr><th>질문</th><th>분류</th>" + "".join(f"<th>{e(m)}<br>{e(p)}</th>" for m, p in cs) + "</tr>")
    idx = {(r["model"], r["prompt_version"], r["id"]): r for r in results}
    for qid in sorted({r["id"] for r in results}):
        any_r = next(r for r in results if r["id"] == qid)
        cells = ""
        for m, p in cs:
            r = idx.get((m, p, qid))
            cells += "<td>-</td>" if not r else ("<td class='ok'>O</td>" if r["passed"] else f"<td class='ng'>X {e(', '.join(r['failure_types']))}</td>")
        out.append(f"<tr><td>{e(qid)}</td><td>{e(any_r['category'])}</td>{cells}</tr>")
    out.append("</table><p class='muted'>평가 기준과 한계는 CRITERIA.md 참고.</p></body></html>")
    path = os.path.join(run_dir, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(out))
    return path
