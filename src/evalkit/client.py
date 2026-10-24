"""DeepSeek(OpenAI 호환) 채팅 API 호출. 외부 라이브러리 없이 표준 라이브러리만 사용."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Reply:
    text: str
    latency_sec: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    error: str | None = None


def load_dotenv(path: str = ".env") -> None:
    """.env 파일의 KEY=VALUE 를 환경변수로 읽는다 (이미 있으면 덮어쓰지 않음)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class ChatClient:
    def __init__(self, base_url: str, api_key: str, timeout_sec: int = 120, max_retries: int = 3):
        if not api_key:
            raise RuntimeError("API 키가 없습니다. .env 파일에 DEEPSEEK_API_KEY 를 넣어 주세요.")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = api_key
        self.timeout = timeout_sec
        self.max_retries = max_retries

    def chat(self, model: str, system: str | None, user: str,
             temperature: float = 0, max_tokens: int = 1024, json_mode: bool = False) -> Reply:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
        if "reasoner" not in model:  # reasoner 모델은 temperature 를 무시함
            body["temperature"] = temperature
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = json.dumps(body).encode("utf-8")

        last_err = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(self.url, data=data, method="POST", headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
            })
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                dt = time.time() - t0
                choice = payload["choices"][0]
                msg = choice["message"]
                usage = payload.get("usage") or {}
                return Reply(text=msg.get("content") or "", latency_sec=round(dt, 2),
                             prompt_tokens=usage.get("prompt_tokens"),
                             completion_tokens=usage.get("completion_tokens"),
                             finish_reason=choice.get("finish_reason"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                last_err = f"HTTP {e.code}: {detail}"
                if e.code in (400, 401, 402, 403, 404, 422):  # 다시 해도 안 되는 오류
                    break
            except Exception as e:  # 네트워크·타임아웃
                last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 ** attempt)
        return Reply(text="", latency_sec=0.0, error=last_err)


class MockClient:
    """API 키 없이 흐름만 확인하는 가짜 클라이언트. 결과는 실제 모델 성능이 아니다."""

    def __init__(self, answers: dict[str, str] | None = None, judge_verdict: str = "pass"):
        self.answers = answers or {}
        self.judge_verdict = judge_verdict

    def chat(self, model, system, user, temperature=0, max_tokens=1024, json_mode=False) -> Reply:
        if json_mode:  # 채점 요청
            return Reply(text=json.dumps({"verdict": self.judge_verdict, "failure_type": None,
                                          "reason": "mock"}, ensure_ascii=False), latency_sec=0.0)
        for key, ans in self.answers.items():
            if key in user:
                return Reply(text=ans, latency_sec=0.0)
        return Reply(text="(mock 응답)", latency_sec=0.0)
