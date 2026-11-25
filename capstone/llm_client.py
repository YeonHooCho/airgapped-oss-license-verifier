# -*- coding: utf-8 -*-
"""
llm_client.py - Ollama 호출 하네스

  - 호출 옵션 고정 (num_ctx 8192, num_predict 1024, temperature 0, seed 고정)
  - 분석 전 서버 확인: Ollama 연결과 모델 설치를 요청당 한 번 확인
  - 출력 형식 강제: Pydantic 스키마를 Ollama format 으로 넘기고, 받은 답을 같은 스키마로 다시 검사
  - 판정 실패 처리
      연결 실패  -> LLMConnectionError (엔진이 요청 전체를 오류로 응답)
      시간 초과  -> 재시도 없이 실패 (TIMEOUT)
      형식 오류  -> "이전 응답이 형식에 맞지 않았다"는 안내를 덧붙여 1회 재시도, 그래도 안 되면 실패 (FORMAT)
    temperature 0 과 고정 seed 에서는 같은 요청을 그대로 다시 보내면 같은 답이 나오므로,
    입력을 조금 바꿔 한 번만 다시 묻는다.
  - 호출 통계: 입력·출력 토큰 수, 종료 사유, 문맥 길이 90% 초과 여부, 걸린 시간

두 가지 호출 방식
  generate(prompt)         프롬프트 문자열 하나 (하네스 적용 전 지시문 그대로 쓸 때)
  chat(messages)           지시문(system)과 자료(user)를 나눠 보낼 때 (입력 분리)
"""

import json
import time

import requests

OLLAMA_BASE = "http://localhost:11434"
OLLAMA_URL = f"{OLLAMA_BASE}/api/generate"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE}/api/chat"
OLLAMA_MODEL = "qwen2.5-coder:7b"
OLLAMA_TIMEOUT = 600

OLLAMA_NUM_CTX = 8192
OLLAMA_NUM_PREDICT = 1024
OLLAMA_TEMPERATURE = 0.0
OLLAMA_SEED = 42
CTX_WARN_RATIO = 0.9

FAIL_TIMEOUT, FAIL_FORMAT = "TIMEOUT", "FORMAT"

RETRY_NOTE = ("이전 응답이 요청한 JSON 형식에 맞지 않았습니다 ({error}). "
              "설명 없이 형식에 맞는 JSON 만 다시 답하세요.")


class LLMConnectionError(RuntimeError):
    """Ollama 에 연결할 수 없거나 모델이 없음. 파일 하나의 문제가 아니라 실행 환경 문제."""


def llm_options() -> dict:
    return {
        "num_ctx": OLLAMA_NUM_CTX,
        "num_predict": OLLAMA_NUM_PREDICT,
        "temperature": OLLAMA_TEMPERATURE,
        "seed": OLLAMA_SEED,
    }


# =============================================================================
# 서버 확인
# =============================================================================

def check_server(model: str = OLLAMA_MODEL, timeout: float = 5) -> tuple:
    """(정상 여부, 안내 문구). 분석을 시작하기 전에 한 번 부른다."""
    try:
        tags = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=timeout).json()
    except Exception:
        return False, f"LLM 서버 연결 불가 ({OLLAMA_BASE}). Ollama 를 실행했는지 확인하세요."
    names = [m.get("name", "") for m in tags.get("models", [])]
    if not any(n == model or n.startswith(model + ":") for n in names):
        return False, f"LLM 모델 없음: {model} (터미널에서 ollama pull {model})"
    return True, ""


# =============================================================================
# 호출
# =============================================================================

def _stats(data: dict, prompt_chars: int, elapsed: float) -> dict:
    prompt_tokens = data.get("prompt_eval_count")
    output_tokens = data.get("eval_count")
    used = (prompt_tokens or 0) + (output_tokens or 0)
    return {
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "prompt_chars": prompt_chars,
        "done_reason": data.get("done_reason"),
        "num_ctx": OLLAMA_NUM_CTX,
        "near_ctx_limit": used >= OLLAMA_NUM_CTX * CTX_WARN_RATIO,
        "elapsed_sec": round((data.get("total_duration") or 0) / 1e9, 2) or round(elapsed, 2),
    }


def _post(url: str, payload: dict) -> dict:
    try:
        r = requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        raise
    except requests.exceptions.ConnectionError as e:
        raise LLMConnectionError(f"LLM 서버 연결 불가: {e}") from e


def generate(prompt: str, schema: dict = None, model: str = OLLAMA_MODEL) -> tuple:
    """프롬프트 문자열 하나로 호출. (응답 텍스트, 통계)"""
    payload = {"model": model, "prompt": prompt, "stream": False, "options": llm_options()}
    if schema is not None:
        payload["format"] = schema
    t0 = time.perf_counter()
    data = _post(OLLAMA_URL, payload)
    return data.get("response", ""), _stats(data, len(prompt), time.perf_counter() - t0)


def chat(messages: list, schema: dict = None, model: str = OLLAMA_MODEL) -> tuple:
    """지시문·자료를 나눈 메시지로 호출. (응답 텍스트, 통계)"""
    payload = {"model": model, "messages": messages, "stream": False, "options": llm_options()}
    if schema is not None:
        payload["format"] = schema
    t0 = time.perf_counter()
    data = _post(OLLAMA_CHAT_URL, payload)
    chars = sum(len(m.get("content", "")) for m in messages)
    return data.get("message", {}).get("content", ""), _stats(data, chars, time.perf_counter() - t0)


# =============================================================================
# 출력 형식 강제 + 실패 처리
# =============================================================================

def _merge_stats(all_stats: list) -> dict:
    """여러 번 호출한 경우: 토큰은 마지막 호출, 시간은 합계, 경고는 하나라도 있으면."""
    if not all_stats:
        return {}
    last = dict(all_stats[-1])
    last["elapsed_sec"] = round(sum(s.get("elapsed_sec") or 0 for s in all_stats), 2)
    last["near_ctx_limit"] = any(s.get("near_ctx_limit") for s in all_stats)
    last["calls"] = len(all_stats)
    return last


def call_structured(model_cls, schema: dict, prompt: str = None, messages: list = None) -> dict:
    """
    출력 형식을 강제해 호출하고 검사한다. prompt(문자열) 또는 messages(대화) 중 하나를 준다.

    반환
      ok            : 검사를 통과했는지
      data          : 통과한 답 (dict)
      raw           : 마지막 응답 원문
      failure_type  : "" / TIMEOUT / FORMAT
      error         : 실패 사유
      attempts      : 호출 횟수 (1 또는 2)
      stats         : 호출 통계
    연결 실패는 LLMConnectionError 로 올려 보낸다.
    """
    from harness_schemas import validate      # 순환 참조 방지

    if (prompt is None) == (messages is None):
        raise ValueError("prompt 와 messages 중 하나만 주세요")
    all_stats, raw, error = [], "", ""
    for attempt in (1, 2):
        try:
            if prompt is not None:
                p = prompt if attempt == 1 else f"{prompt}\n\n{RETRY_NOTE.format(error=error)}"
                raw, st = generate(p, schema)
            else:
                ms = messages if attempt == 1 else messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": RETRY_NOTE.format(error=error)}]
                raw, st = chat(ms, schema)
        except requests.exceptions.Timeout:
            return {"ok": False, "data": None, "raw": raw, "failure_type": FAIL_TIMEOUT,
                    "error": f"응답 시간 초과 ({OLLAMA_TIMEOUT}초)", "attempts": attempt,
                    "stats": _merge_stats(all_stats)}
        all_stats.append(st)
        data, error = validate(model_cls, raw)
        if data is not None:
            return {"ok": True, "data": data, "raw": raw, "failure_type": "", "error": "",
                    "attempts": attempt, "stats": _merge_stats(all_stats)}
        if st.get("done_reason") == "length":
            error = f"출력 길이 상한({OLLAMA_NUM_PREDICT}토큰)에서 끊김; {error}"
    return {"ok": False, "data": None, "raw": raw, "failure_type": FAIL_FORMAT,
            "error": error, "attempts": 2, "stats": _merge_stats(all_stats)}


def describe() -> dict:
    return {"model": OLLAMA_MODEL, "options": llm_options(), "timeout": OLLAMA_TIMEOUT,
            "generate_url": OLLAMA_URL, "chat_url": OLLAMA_CHAT_URL}


def _selftest():
    """실제 Ollama 로 파일 판정 형식 강제 호출을 한 번 해 본다."""
    import harness_schemas as hs
    code = "# Licensed under the Apache License, Version 3.0\nimport requests\n"
    packages = ["requests"]
    model, schema, choices = hs.file_judgment_schema(packages)
    prompt = (f"다음 코드 파일의 라이선스를 판정하세요.\n<code>\n{code}</code>\n"
              f"<dependencies>{json.dumps({'requests': {'license': 'Apache-2.0'}})}</dependencies>\n\n"
              + hs.file_format_instructions(choices, packages))
    r = call_structured(model, schema, prompt=prompt)
    print(json.dumps({k: r[k] for k in ("ok", "failure_type", "error", "attempts", "data", "stats")},
                     ensure_ascii=False, indent=1))
    if r["ok"]:
        from answer_check import check
        c = check(r["data"], code)
        print("\n답 검증 결과:", json.dumps({k: c[k] for k in ("license", "status", "failed_rule", "changes")},
                                         ensure_ascii=False))


if __name__ == "__main__":
    import sys
    ok, msg = check_server()
    print("서버 확인:", "정상" if ok else msg)
    print(json.dumps(describe(), ensure_ascii=False, indent=1))
    if ok and "--selftest" in sys.argv:
        _selftest()
