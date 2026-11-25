# -*- coding: utf-8 -*-
"""
code_window.py - LLM 에 보내는 코드 범위 (분석 엔진·근거 확인·측정 스크립트 공용)

LLM 이 보는 범위, 엔진이 근거 문장을 찾는 범위, 측정 스크립트가 환각을 판정하는 범위가
반드시 같아야 한다. 셋이 어긋나면, 예를 들어 엔진이 80번째 줄을 근거로 정확히 답해도
측정 스크립트는 "50줄 안에 근거 없음"으로 보고 환각으로 센다.
그래서 범위를 정하는 코드를 이 파일 하나에 둔다. 범위를 바꿀 때는 여기만 고친다.

현재 범위: 파일 상단 50줄
(나중에 "상단 50줄 + 50줄 밖의 라이선스 관련 주석 블록" 으로 확장할 수 있음)
"""

import re

WINDOW_LINES = 50


def decode_text(content: bytes) -> str:
    """엔진과 같은 방식으로 바이트를 글자로 바꾼다 (UTF-8, 깨진 글자는 대체 문자)."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("utf-8", errors="replace")


def window(text: str) -> str:
    """LLM 에 보내는 코드 범위."""
    return "\n".join((text or "").split("\n")[:WINDOW_LINES])


def window_from_bytes(content: bytes) -> str:
    return window(decode_text(content))


def window_from_path(path) -> str:
    """파일 경로에서 바로 범위를 읽는다 (측정 스크립트용). 읽지 못하면 빈 문자열."""
    try:
        with open(path, "rb") as f:
            return window_from_bytes(f.read())
    except OSError:
        return ""


# SPDX 태그: 주석 줄에 있는 것만 인정한다 (코드 속 문자열 "SPDX-License-Identifier:" 는 제외)
_SPDX_TAG_LINE = re.compile(
    r"^\s*(?:#|//+|/\*+|\*+|<!--|--|;+|%|\"\"\"|\'\'\')\s*SPDX-License-Identifier:\s*(.+?)\s*$")
_TAG_TAIL = re.compile(r"\s*(\*/|-->|\"\"\"|\'\'\')\s*$")


def find_spdx_tags(text: str) -> list:
    """범위 안의 주석 줄에서 SPDX 태그 값을 순서대로 모은다. 엔진 규칙 판정과 측정 스크립트가 같이 쓴다."""
    tags = []
    for line in (text or "").split("\n"):
        m = _SPDX_TAG_LINE.match(line)
        if m:
            value = _TAG_TAIL.sub("", m.group(1)).strip()
            if value:
                tags.append(value)
    return tags


def describe() -> dict:
    return {"mode": "top_lines", "lines": WINDOW_LINES}
