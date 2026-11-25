# -*- coding: utf-8 -*-
"""
harness_config.py - 하네스 기능 스위치

하네스 기능을 하나씩 켜고 끄기 위한 설정이다. 코드를 모두 작성한 뒤에도
기능을 한 단계씩 켜 가며 측정해야 어느 기능이 효과를 냈는지 구분할 수 있다.

기본값은 전부 켬이다. 아무 설정 없이 엔진을 켜면 하네스 기능이 모두 동작한다.

일부만 켜거나 끄는 방법 (엔진 서버를 켜기 전에 환경변수로 지정)
    PowerShell
        $env:HARNESS_FLAGS = "RULE_SPDX_TAG,RULE_LICENSE_TEXT"
        uvicorn main:app --port 8000
    전부 끄기:  $env:HARNESS_FLAGS = "NONE"
    기본값으로 되돌리기(전부 켬): Remove-Item Env:HARNESS_FLAGS

모두 끄면 하네스 적용 전 엔진과 같게 동작한다. 측정의 기준값은 "NONE" 으로 잰다.
현재 켜진 스위치는 엔진 /health 와 측정 결과의 run_conditions.json 에 기록된다.

확인:  python harness_config.py
"""

import os
import sys

# 적용 순서대로 나열 (측정도 이 순서로 하나씩 켠다)
FLAG_NAMES = (
    "RULE_SPDX_TAG",      # SPDX 태그·빈 파일을 규칙으로 판정 (LLM 생략)
    "RULE_LICENSE_TEXT",  # LICENSE 파일을 SPDX 원문과 비교해 규칙으로 판정
    "STRUCTURED_OUTPUT",  # 출력 형식 강제(스키마) + 판정 실패 처리
    "ANSWER_CHECK",       # 답 검증: 이름 정리, 근거 문장 확인, 버전 검사
    "INPUT_SEPARATION",   # 지시문(system)·자료(user) 분리 + 바뀐 지시문
    "CONFLICT_CHECK",     # 프로젝트 전체 충돌 판정 (OSADL)
)

FLAG_DESCRIPTIONS = {
    "RULE_SPDX_TAG": "SPDX 태그·빈 파일 규칙 판정",
    "RULE_LICENSE_TEXT": "LICENSE 원문 규칙 판정",
    "STRUCTURED_OUTPUT": "출력 형식 강제 + 판정 실패 처리",
    "ANSWER_CHECK": "답 검증 (이름 정리, 근거 확인, 버전 검사)",
    "INPUT_SEPARATION": "지시문·자료 분리 + 바뀐 지시문",
    "CONFLICT_CHECK": "프로젝트 전체 충돌 판정",
}

# 다른 스위치가 먼저 켜져 있어야 하는 스위치
#   ANSWER_CHECK     : 근거 문장(evidence) 칸은 STRUCTURED_OUTPUT 의 출력 형식에서 생긴다
#   INPUT_SEPARATION : 바뀐 지시문이 STRUCTURED_OUTPUT 의 출력 칸(evidence, conflicts)을 전제로 한다
REQUIRES = {
    "ANSWER_CHECK": ("STRUCTURED_OUTPUT",),
    "INPUT_SEPARATION": ("STRUCTURED_OUTPUT",),
}

ENV_NAME = "HARNESS_FLAGS"

# 환경변수가 없을 때의 기본값: 전부 켬
DEFAULT_FLAGS = "ALL"


class HarnessConfigError(ValueError):
    pass


def parse_flags(text: str) -> dict:
    """'A,B' / 'ALL' / 'NONE' / '' 를 {스위치: True/False} 로 바꾸고 검사한다."""
    raw = (text or "").strip()
    flags = {name: False for name in FLAG_NAMES}
    if raw.upper() in ("", "NONE", "OFF", "0"):
        return flags
    if raw.upper() in ("ALL", "ON", "1"):
        return {name: True for name in FLAG_NAMES}

    unknown = []
    for part in raw.replace(";", ",").split(","):
        name = part.strip().upper()
        if not name:
            continue
        if name not in flags:
            unknown.append(name)
        else:
            flags[name] = True
    if unknown:
        raise HarnessConfigError(
            f"알 수 없는 스위치: {', '.join(unknown)} (사용 가능: {', '.join(FLAG_NAMES)})")

    missing = [(name, need) for name, needs in REQUIRES.items() if flags[name]
               for need in needs if not flags[need]]
    if missing:
        detail = ", ".join(f"{n} 는 {m} 가 필요" for n, m in missing)
        raise HarnessConfigError(f"스위치 조합 오류: {detail}")
    return flags


def load() -> tuple:
    """환경변수(없으면 기본값)에서 스위치를 읽는다. (flags, 출처) 반환."""
    if ENV_NAME in os.environ:
        return parse_flags(os.environ[ENV_NAME]), f"환경변수 {ENV_NAME}={os.environ[ENV_NAME]!r}"
    return parse_flags(DEFAULT_FLAGS), f"기본값 DEFAULT_FLAGS={DEFAULT_FLAGS!r}"


FLAGS, FLAGS_SOURCE = load()


def enabled(name: str) -> bool:
    if name not in FLAGS:
        raise HarnessConfigError(f"알 수 없는 스위치: {name}")
    return FLAGS[name]


def describe() -> dict:
    """/health 와 측정 기록용."""
    return {
        "flags": dict(FLAGS),
        "enabled": [n for n in FLAG_NAMES if FLAGS[n]],
        "source": FLAGS_SOURCE,
    }


if __name__ == "__main__":
    print(f"출처: {FLAGS_SOURCE}")
    for n in FLAG_NAMES:
        print(f"  [{'ON ' if FLAGS[n] else 'off'}] {n:<18} {FLAG_DESCRIPTIONS[n]}")
    sys.exit(0)
