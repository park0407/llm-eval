# LLM 응답 품질 평가 도구

같은 질문 44개를 여러 AI 모델·프롬프트에 똑같이 던지고, **맞았는지 / 지시를 지켰는지 / 형식이 깨지지 않았는지 / 위험한 요청을 거절했는지 / 모르는 걸 지어내지 않았는지 / 개인정보를 가렸는지**를 채점해서 표로 정리하는 도구입니다.

```
질문 세트(44개) → 모델 × 프롬프트별 응답 수집 → 채점(규칙 28개 + AI 채점 16개)
→ 실패 유형 분류 → 프롬프트 수정 전후 비교 → 보고서(HTML·CSV)
```

- 기본 비교: `deepseek-chat`, `deepseek-reasoner` × 프롬프트 `v1`(기본) · `v2`(규칙 강화)
- 평가 기준·실패 유형·한계: [CRITERIA.md](CRITERIA.md)

## 1. 준비

필요한 것: Python 3.10 이상. **외부 라이브러리 설치 없이** 돌아갑니다 (테스트에만 pytest 사용).

```bash
cd llm-eval
copy .env.example .env     # Windows   (Mac: cp .env.example .env)
```

`.env` 를 메모장으로 열어 DeepSeek API 키를 넣습니다. `.env` 는 깃허브에 올라가지 않습니다.
키 발급: [DeepSeek Platform](https://platform.deepseek.com/) → API keys

## 2. 실행

```bash
python -m src.evalkit.cli plan            # 호출 수 미리 보기 (응답 176번 + AI 채점 최대 64번)
python -m src.evalkit.cli run --limit 3   # 질문 3개만 먼저 시험
python -m src.evalkit.cli run --name 1차  # 전체 실행
```

끝나면 `runs/1차/` 에 생깁니다.

| 파일 | 내용 |
|---|---|
| `report.html` | 보고서 (통과율, 분류별·실패 유형별 표, 프롬프트 수정 전후, 실패한 응답 원문) |
| `results.csv` | 엑셀로 여는 전체 결과 |
| `responses.jsonl` | 모델 응답 원문 (중간에 끊겨도 여기서 이어서 실행) |
| `judgments.jsonl` | AI 채점 기록 (같은 응답은 다시 채점하지 않음) |
| `human_labels.csv` | 사람이 AI 채점을 검증하는 칸 |

## 3. AI 채점 검증

1. `runs/1차/human_labels.csv` 를 엑셀로 열어 '사람판정' 칸에 O(통과) / X(실패)를 적습니다.
2. `python -m src.evalkit.cli report --run 1차` 로 보고서를 다시 만들면 **사람 판정과 AI 채점 일치율**이 나옵니다.

## 4. 프롬프트를 고쳐서 다시 비교

1. `prompts/v3.txt` 를 만들고 `config.json` 의 `prompts` 에 `"v3": "prompts/v3.txt"` 를 추가
2. `python -m src.evalkit.cli run --name 2차 --prompts v2 v3`
3. `python -m src.evalkit.cli compare 1차 2차`

## 5. 질문 추가

`data/questions.jsonl` 에 한 줄씩 추가합니다. 규칙 채점 종류: `contains_any`, `not_contains`, `regex`, `exact`, `starts_with`, `max_chars`, `line_count`, `no_line_prefix`, `no_latin`, `dash_list`, `numbered_list`, `json`, `json_array`, `md_table`, `csv`. AI 채점은 `{"type": "judge"}` 와 `expected_behavior`(`refuse` / `answer` / `correct_premise` / `admit_unknown`).

## 6. 테스트

```bash
python -m pytest -q                         # 64개: 규칙 채점 기준 검증 + 전체 흐름 (API 호출 없음)
python -m src.evalkit.cli run --mock        # API 없이 흐름만 확인 (보고서에 MOCK 표시)
```

## 폴더 구조

```
config.json            모델·프롬프트·채점 모델 설정
data/questions.jsonl   질문 44개와 채점 기준
prompts/v1.txt, v2.txt 비교할 시스템 프롬프트
src/evalkit/client.py  DeepSeek API 호출 (재시도 포함)
src/evalkit/checks.py  규칙 채점
src/evalkit/judge.py   AI 채점
src/evalkit/runner.py  응답 수집·채점 (이어하기)
src/evalkit/report.py  집계·HTML·CSV·사람 판정 비교
src/evalkit/cli.py     실행 명령
tests/                 자동 테스트
CRITERIA.md            평가 기준과 한계
```

## 만든 방식

요구사항과 평가 기준(질문, 정답, 실패 유형)은 직접 정하고, 코드는 AI 코딩 도구(Claude)로 작성했습니다. 채점 기준이 의도대로 동작하는지는 자동 테스트로 확인했습니다.
