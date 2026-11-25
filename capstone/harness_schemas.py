# -*- coding: utf-8 -*-
"""
harness_schemas.py - LLM 출력 스키마 (Pydantic)

스키마는 여기서 한 번만 정의하고, 두 곳에 함께 쓴다.
  - Ollama 에 넘기는 출력 형식(format)  -> 모델이 이 형식 밖으로는 답할 수 없다
  - 엔진이 받은 답을 다시 검사하는 기준 -> Ollama 는 출력이 중간에 끊긴 경우까지 보장하지 않는다

작성자 PC 의 Ollama(0.40.11)·qwen2.5-coder:7b 에서 시험한 결과
  선택지 제한(enum), 칸 순서, 배열 개수(minItems/maxItems), 문자열 최대 길이(maxLength) 모두 지켜짐.
  최대 길이는 문장 중간에서 끊길 수 있어, 지시문으로 짧게 쓰도록 함께 안내한다.

시험에서 확인한 주의점 (선택지 대체)
  원문에 선택지와 정확히 맞는 이름이 없으면 모델이 가장 비슷한 선택지를 고른다.
  예: 없는 "Apache License, Version 3.0" -> Apache-2.0. "맞는 것이 없으면 Other" 라고 안내해도 같았다.
  형식 강제는 글자를 하나씩 만들 때마다 선택지와 맞는 글자만 허용하므로, 모델이 "Apache" 부터 쓰기
  시작하면 선택지 중 그 글자로 시작하는 Apache-2.0 으로 끝까지 채워질 수밖에 없는 것으로 보인다.
  그래서 선택지 제한이 없는 license_as_written 칸에 원문 표현을 먼저 적게 하고, 답 검증
  (answer_check)에서 그 칸을 정리한 결과와 main_license 가 다르면 원문 쪽을 따른다.
"""

from functools import lru_cache
from typing import List, Literal

from pydantic import BaseModel, Field, ValidationError, create_model

from license_catalog import CATALOG

REASONING_MAX = 500          # 설명 길이 상한 (화면 보호, 응답 시간)
EVIDENCE_MAX = 300
AS_WRITTEN_MAX = 200
CONFLICT_REASON_MAX = 300

# 선택지 목록 파일(license_refs/license_choices.json)이 없을 때 쓰는 최소 목록
FALLBACK_CHOICES = (
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
    "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later",
    "AGPL-3.0-only", "AGPL-3.0-or-later", "MPL-2.0", "EPL-2.0", "Unlicense",
    "Unknown", "Other",
)
PAIR_RESULTS = ("OK", "RISK", "CONFLICT", "REVIEW")


def license_choices() -> tuple:
    """LLM 선택지. Unknown 과 Other 는 항상 마지막에 포함한다."""
    choices = [c for c in (CATALOG.choices() or FALLBACK_CHOICES) if c not in ("Unknown", "Other")]
    return tuple(choices) + ("Unknown", "Other")


# =============================================================================
# 파일 판정 출력
# =============================================================================

@lru_cache(maxsize=256)
def file_judgment_model(choices: tuple, packages: tuple):
    """
    파일마다 선택지와 패키지 이름이 달라서 모델을 그때그때 만든다 (같은 조합은 재사용).
    칸 순서: 근거 -> 원문에 적힌 라이선스 표현 -> 설명 -> 판정 -> 충돌 지적.
    모든 칸 필수 (빠짐 없이 이 순서로 출력됨을 시험으로 확인).
    """
    license_type = Literal[choices]
    if packages:
        conflict = create_model(
            "ConflictClaim",
            package=(Literal[packages], ...),
            reason=(str, Field(..., max_length=CONFLICT_REASON_MAX)),
        )
        conflicts_field = (List[conflict], Field(..., max_length=len(packages)))
    else:
        conflict = create_model("ConflictClaim", package=(str, ...), reason=(str, ...))
        conflicts_field = (List[conflict], Field(..., max_length=0))
    return create_model(
        "FileJudgment",
        evidence=(str, Field(..., max_length=EVIDENCE_MAX)),
        license_as_written=(str, Field(..., max_length=AS_WRITTEN_MAX)),
        reasoning=(str, Field(..., max_length=REASONING_MAX)),
        main_license=(license_type, ...),
        conflicts=conflicts_field,
    )


def file_judgment_schema(packages) -> tuple:
    """(Pydantic 모델, Ollama 에 넘길 JSON 스키마, 선택지)"""
    choices = license_choices()
    model = file_judgment_model(choices, tuple(sorted(set(packages or ()))))
    return model, model.model_json_schema(), choices


def file_format_instructions(choices, packages=None, concise=True) -> str:
    """
    지시문에 함께 적는 출력 형식 안내 (스키마는 형식만 막으므로 각 칸의 의미는 글로 알려 준다).
      packages=None : 파일마다 바뀌지 않는 system 메시지용 (패키지 목록 대신 <dependencies> 를 가리킴)
      concise=False : 하네스 적용 전 지시문에 붙일 때 (설명 길이 안내는 지시문 변경 단계에서 적용)
    """
    if packages is None:
        pkg_line = "package 는 <dependencies> 에 있는 패키지 이름 중에서만 고르세요. 없으면 빈 배열 [] 로 두세요."
    elif packages:
        pkg_line = f"제공된 패키지 이름 중에서만 고르세요: {', '.join(sorted(set(packages)))}"
    else:
        pkg_line = "제공된 패키지가 없으므로 빈 배열 [] 로 두세요."
    reasoning_line = ("evidence 를 바탕으로 한 판정 이유를 한국어 2~3문장으로 짧게." if concise
                      else "판정 이유를 한국어로.")
    return f"""[출력 형식]
아래 칸을 이 순서대로 모두 채운 JSON 으로만 답하세요.
- evidence: 판정 근거가 된 코드 원문 한 줄을 그대로 옮겨 적으세요. 근거가 없으면 빈 문자열 "".
- license_as_written: 원문에 적힌 라이선스 이름 표현을 고치지 말고 그대로 적으세요
  (예: "Apache License, Version 2.0", "GPLv3", "BSD 2-Clause"). 없으면 빈 문자열 "".
- reasoning: {reasoning_line}
- main_license: 다음 선택지 중 하나만 고르세요.
  {', '.join(choices)}
  · 원문과 정확히 일치하는 선택지가 없으면 비슷한 이름을 고르지 말고 Other 를 고르세요.
  · license_as_written 에 라이선스 표현을 적었다면 Unknown 이 아니라 맞는 선택지나 Other 를 고르세요.
  · 라이선스 근거가 전혀 없을 때만 Unknown 을 고르세요.
- conflicts: 의존 패키지 사이에 라이선스 충돌 위험이 있으면 [{{"package": 패키지 이름, "reason": 이유}}], 없으면 [].
  {pkg_line}"""


# =============================================================================
# 프로젝트 단위 충돌 판정 출력 (마지막 LLM 호출)
# =============================================================================

@lru_cache(maxsize=64)
def pair_judgment_model(pair_ids: tuple):
    """짝 번호는 입력에 있는 것만, 짝 개수만큼 정확히. 칸 순서: 짝 번호 -> 이유 -> 판정 -> 따라야 할 라이선스."""
    item = create_model(
        "PairResult",
        pair_id=(Literal[pair_ids], ...),
        reason=(str, Field(..., max_length=CONFLICT_REASON_MAX)),
        result=(Literal[PAIR_RESULTS], ...),
        must_follow=(str, Field(..., max_length=120)),
    )
    return create_model(
        "PairJudgments",
        results=(List[item], Field(..., min_length=len(pair_ids), max_length=len(pair_ids))),
    )


def pair_judgment_schema(pair_ids) -> tuple:
    model = pair_judgment_model(tuple(pair_ids))
    return model, model.model_json_schema()


# =============================================================================
# 검사
# =============================================================================

def validate(model, raw: str):
    """(데이터 dict, 오류 문자열). 통과하면 오류는 빈 문자열."""
    try:
        return model.model_validate_json(raw).model_dump(), ""
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", ()))
        return None, f"{first.get('type', 'invalid')} at {loc or '(root)'}: {first.get('msg', str(e))[:200]}"
    except Exception as e:
        return None, f"invalid: {e}"
