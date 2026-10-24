"""실행 진입점.

python -m src.evalkit.cli plan                 호출 수 미리 계산
python -m src.evalkit.cli run                  전체 실행 (응답 수집 → 채점 → 보고서)
python -m src.evalkit.cli run --limit 3        질문 3개만 빠르게 시험
python -m src.evalkit.cli run --mock           API 없이 흐름만 확인
python -m src.evalkit.cli report --run 이름     저장된 응답으로 다시 채점·보고서
python -m src.evalkit.cli compare 이름A 이름B   두 실행 비교
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

from .client import ChatClient, MockClient, load_dotenv
from .report import load_results, summarize, write_csv, write_html, write_label_template
from .runner import collect, evaluate, load_questions

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _cfg(path: str) -> dict:
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return json.load(f)


def _prompts(cfg: dict, only: list[str] | None) -> dict[str, str]:
    out = {}
    for name, p in cfg["prompts"].items():
        if only and name not in only:
            continue
        with open(os.path.join(ROOT, p), encoding="utf-8") as f:
            out[name] = f.read().strip()
    return out


def _client(cfg: dict, mock: bool):
    if mock:
        return MockClient(answers={"17 × 23": "391입니다.", "대한민국의 수도": "서울입니다."})
    load_dotenv(os.path.join(ROOT, ".env"))
    a = cfg["api"]
    return ChatClient(a["base_url"], os.environ.get(a["key_env"], ""), a["timeout_sec"], a["max_retries"])


def _finish(run_dir: str, questions, judge_client, meta: dict) -> None:
    results = evaluate(run_dir, questions, judge_client, meta["judge_model"])
    write_csv(run_dir, results)
    write_label_template(run_dir, results)
    path = write_html(run_dir, results, meta)
    print("\n=== 결과 ===")
    for (m, p), s in sorted(summarize(results).items()):
        print(f"{m:20s} {p:4s} 통과 {s['passed']}/{s['total']}  실패유형 {s['failure_types']}")
    print(f"\n보고서: {path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="evalkit")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("plan")
    r = sub.add_parser("run")
    r.add_argument("--name", help="실행 이름 (기본: 날짜시각)")
    r.add_argument("--models", nargs="*")
    r.add_argument("--prompts", nargs="*")
    r.add_argument("--limit", type=int, help="앞에서 N개 질문만")
    r.add_argument("--mock", action="store_true")
    rp = sub.add_parser("report")
    rp.add_argument("--run", required=True)
    rp.add_argument("--mock", action="store_true", help="AI 채점이 남은 경우 가짜 채점 사용")
    cp = sub.add_parser("compare")
    cp.add_argument("a")
    cp.add_argument("b")
    args = ap.parse_args(argv)

    cfg = _cfg(args.config)
    questions = load_questions(os.path.join(ROOT, cfg["questions"]))

    if args.cmd == "plan":
        n_m, n_p, n_q = len(cfg["models"]), len(cfg["prompts"]), len(questions)
        n_j = sum(any(c["type"] == "judge" for c in q["checks"]) for q in questions)
        print(json.dumps({"질문": n_q, "모델": n_m, "프롬프트": n_p, "응답 호출": n_m * n_p * n_q,
                          "AI 채점 호출(최대)": n_m * n_p * n_j}, ensure_ascii=False))
        return 0

    if args.cmd == "run":
        models = args.models or cfg["models"]
        prompts = _prompts(cfg, args.prompts)
        qs = questions[: args.limit] if args.limit else questions
        name = args.name or dt.datetime.now().strftime("%Y%m%d-%H%M") + ("-mock" if args.mock else "")
        run_dir = os.path.join(ROOT, "runs", name)
        os.makedirs(run_dir, exist_ok=True)
        meta_path = os.path.join(run_dir, "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("mock") != args.mock:
                print("이 실행 폴더는 mock 여부가 다릅니다. 다른 --name 을 쓰세요.")
                return 1
        else:
            meta = {"run": name, "started": dt.datetime.now().isoformat(timespec="seconds"), "mock": args.mock,
                    "models": models, "prompts": prompts, "judge_model": cfg["judge_model"],
                    "temperature": cfg["temperature"], "n_questions": len(qs), "question_ids": [q["id"] for q in qs]}
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        client = _client(cfg, args.mock)
        collect(run_dir, client, qs, models, prompts, cfg["temperature"], cfg["max_tokens"])
        _finish(run_dir, qs, client, meta)
        return 0

    if args.cmd == "report":
        run_dir = os.path.join(ROOT, "runs", args.run)
        with open(os.path.join(run_dir, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        qs = [q for q in questions if q["id"] in set(meta["question_ids"])]
        _finish(run_dir, qs, _client(cfg, args.mock or meta.get("mock")), meta)
        return 0

    if args.cmd == "compare":
        sa = summarize(load_results(os.path.join(ROOT, "runs", args.a)))
        sb = summarize(load_results(os.path.join(ROOT, "runs", args.b)))
        print(f"{'모델':20s} {'프롬프트':6s} {args.a:>16s} {args.b:>16s}")
        for k in sorted(set(sa) | set(sb)):
            fa = f"{sa[k]['passed']}/{sa[k]['total']}" if k in sa else "-"
            fb = f"{sb[k]['passed']}/{sb[k]['total']}" if k in sb else "-"
            print(f"{k[0]:20s} {k[1]:6s} {fa:>16s} {fb:>16s}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
