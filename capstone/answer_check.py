# -*- coding: utf-8 -*-
"""
answer_check.py - LLM 답 검증·보정 (스위치 ANSWER_CHECK, STRUCTURED_OUTPUT 필요)

LLM 이 낸 답(harness_schemas 의 파일 판정 출력)을 코드로 다시 확인한다. 순서:

  1) 이름 정리        선택지(main_license) 또는 Other 일 때의 원문 표현을 라이선스 목록 모듈로 정리
  2) 선택지 대체 검사  원문에 적힌 표현(license_as_written)을 정리한 결과와 main_license 가 다르면
                      모델이 비슷한 선택지에 끌려간 것으로 보고 원문 쪽을 따른다
                      (예: 원문 "BSD 2-Clause", 답 BSD-3-Clause -> BSD-2-Clause)
  3) 근거 문장 확인    evidence 가 원문 범위에 실제로 있고, 10자 이상이며, 라이선스 선언 문구(license,
                      SPDX-License-Identifier, Copyright 등)를 포함하고, 답한 라이선스를 가리켜야 한다.
                      하나라도 통과하지 못하면 Unknown 으로 되돌린다.
                      "가리키는지"는 근거 문장이 속한 주석 블록(원문에서 가져옴)까지 함께 본다.
  4) 버전 검사        답에 버전이 있는데 근거·원문 표현에 그 버전 숫자가 없으면 계열만 남긴다 (버전 미상).
                      -or-later 인데 이후 버전을 허용하는 표현이 없으면 -only 로 바꾼다.

결과에는 어느 규칙이 답을 바꿨는지 남긴다 (측정 스크립트가 규칙별 건수를 센다).
근거 확인 기준은 측정 스크립트의 환각 판정 기준(라이선스 관련 단어가 있는가)과 다르다.
엔진이 측정 기준을 그대로 따라 환각률을 0% 로 만드는 일을 막기 위해서다.
"""

import re

import code_window
from license_catalog import CATALOG, STATUS_OK, STATUS_VERSIONLESS, VERSIONLESS_GROUPS, norm_text

EVIDENCE_MIN_CHARS = 10
LONG_PHRASE = 20          # 근거가 표준 문구의 일부일 때 인정하는 최소 길이

RULE_SUBSTITUTION = "choice_substitution"
RULE_EVIDENCE_EMPTY = "evidence_empty"
RULE_EVIDENCE_NOT_IN_SOURCE = "evidence_not_in_source"
RULE_EVIDENCE_TOO_SHORT = "evidence_too_short"
RULE_EVIDENCE_MISMATCH = "evidence_mismatch"
RULE_EVIDENCE_NOT_STATEMENT = "evidence_not_license_statement"   # 이름만 나오고 라이선스 선언 문구가 없는 줄
RULE_VERSION_GUESS = "version_guess"
RULE_OR_LATER = "or_later_unsupported"
RULE_UNKNOWN_WITH_TEXT = "unknown_with_text"     # Unknown 이라 답했지만 원문에 라이선스 표현이 확인됨
RULE_UNKNOWN_VERSION = "unknown_version"         # 정리되지 않는 표현이지만 계열 이름은 확인됨
RULE_DUAL_LICENSE = "dual_license_or"            # 원문이 이중 라이선스인데 AND 로 정리됨 -> OR

# 식별자 앞부분 -> 버전 없는 이름 그룹 (계열 확인과 버전 검사에 사용)
GROUP_PREFIXES = (("LGPL-", "LGPL"), ("AGPL-", "AGPL"), ("GPL-", "GPL"), ("MPL-", "MPL"),
                  ("EPL-", "EPL"), ("CDDL-", "CDDL"), ("EUPL-", "EUPL"), ("Apache-", "Apache"),
                  ("BSD-", "BSD"))
# 버전 숫자가 의미를 바꾸는 그룹만 버전 검사 (BSD 의 숫자는 조항 수라 제외)
VERSIONED_GROUPS = {"GPL", "LGPL", "AGPL", "MPL", "EPL", "CDDL", "EUPL", "Apache"}

_COMMENT_MARK = re.compile(r"^\s*(?:#+|//+|/\*+|\*+/?|<!--|--|;+|\"\"\"|''')\s?")
_COMMENT_TAIL = re.compile(r"\s*(?:\*/|-->|\"\"\"|''')\s*$")
# 근거 문장 자체에 있어야 하는 라이선스 선언 문구. "answer MIT" 처럼 이름만 언급된 줄,
# main_license·LICENSES 같은 코드 식별자에 들어간 'license' 는 근거가 아니다.
_LICENSE_STATEMENT = re.compile(
    r"licen[cs]ed\s+(?:under|to|as)\b"                         # Licensed under ...
    r"|under\s+(?:the\s+)?(?:terms\s+of\s+(?:the\s+)?)?[\w .,/-]{0,60}?licen[cs]e\b"   # under the X License
    r"|[A-Za-z0-9.+)-]\s+licen[cs]e\b"                          # MIT License, Public License, the License
    r"|(?<![\w])licen[cs]e\s*[:=]"                               # License: MIT, license = "MIT"
    r"|[\"']licen[cs]e[\"']\s*:"                                # "license": "MIT"
    r"|__license__"
    r"|spdx-license-identifier|permission is hereby granted|copyright|\(c\)|©|all rights reserved"
    r"|redistribution and use|public domain|dual[\s-]*licen"
    r"|라이선스|라이센스",
    re.IGNORECASE)
_OR_LATER_TEXT = re.compile(r"or\s+(?:\(at\s+your\s+option\)\s+)?(?:any\s+)?later|or-later|\+|or\s+newer",
                            re.IGNORECASE)


def _clean(text: str) -> str:
    """주석 기호를 지우고 줄바꿈을 포함한 공백을 하나로, 소문자로."""
    lines = []
    for ln in (text or "").split("\n"):
        ln = _COMMENT_TAIL.sub("", _COMMENT_MARK.sub("", ln))
        lines.append(ln)
    return re.sub(r"\s+", " ", " ".join(lines)).strip().lower()


BLOCK_MAX_LINES = 24


def _is_comment_line(line: str) -> bool:
    return bool(line.strip()) and bool(_COMMENT_MARK.match(line))


def evidence_block(evidence: str, window_text: str) -> str:
    """
    근거 문장이 들어 있는 주석 블록 전체 (연속된 주석 줄, 최대 24줄).
    표준 라이선스 헤더는 이름·버전·"or any later version" 이 서로 다른 줄에 있어서,
    근거 문장 한 줄만 보면 버전 정보를 놓친다. 블록은 원문에서 가져오므로 모델이 지어낼 수 없다.
    """
    lines = window_text.split("\n")
    ev = _clean(evidence)
    start = None
    for i, ln in enumerate(lines):
        c = _clean(ln)
        if c and (c in ev or ev[:30] in c):
            start = i
            break
    if start is None:
        return evidence
    if not _is_comment_line(lines[start]):
        return lines[start]
    a = b = start
    while a > 0 and _is_comment_line(lines[a - 1]) and (start - a) < BLOCK_MAX_LINES // 2:
        a -= 1
    while b + 1 < len(lines) and _is_comment_line(lines[b + 1]) and (b - a) < BLOCK_MAX_LINES:
        b += 1
    return "\n".join(lines[a:b + 1])


def _contains_word(hay: str, needle: str) -> bool:
    if not needle:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", hay) is not None


def group_of(spdx_id: str):
    for prefix, group in GROUP_PREFIXES:
        if spdx_id.startswith(prefix):
            return group
    return None


def _version_patterns(spdx_id: str):
    """GPL-3.0-only -> '3' 또는 '3.0' 을 찾는 패턴. 2.1 처럼 소수점 아래가 0 이 아니면 그대로."""
    m = re.search(r"-(\d+)\.(\d+)", spdx_id) or re.search(r"-(\d+)(?:$|-)", spdx_id)
    if not m:
        return None
    major = m.group(1)
    minor = m.group(2) if m.lastindex and m.lastindex >= 2 else "0"
    if minor == "0":
        return re.compile(rf"(?<![\d.]){major}(?:\.0)?(?![\d]|\.\d)")
    return re.compile(rf"(?<![\d.]){major}\.{minor}(?!\d)")


def _signature_ok(spdx_id: str, ev_norm: str, ev_only: str = "") -> bool:
    """
    근거가 이 라이선스를 가리키는지.
      ev_norm : 근거 문장 + 주석 블록 + 원문 표현 -> 이름·별칭·계열 이름이 들어 있으면 통과
      ev_only : 근거 문장만 -> 표준 원문 문구(예: MIT 의 "Permission is hereby granted...")의 일부이면 통과
    """
    ev_only = ev_only or ev_norm
    group = group_of(spdx_id)
    if group:
        for p in VERSIONLESS_GROUPS[group][1]:
            if _contains_word(ev_norm, norm_text(p)):
                return True
    for phrase in CATALOG.signature_phrases(spdx_id):
        pn = norm_text(phrase)
        if not pn:
            continue
        if len(pn) >= 3 and _contains_word(ev_norm, pn):
            return True
        if len(pn) >= LONG_PHRASE and len(ev_only) >= LONG_PHRASE and ev_only in pn:
            return True
    return False


def _versionless_signature_ok(group: str, ev_norm: str) -> bool:
    return any(_contains_word(ev_norm, norm_text(p)) for p in VERSIONLESS_GROUPS[group][1])


def check(answer: dict, source_text: str) -> dict:
    """
    answer      : 검사를 통과한 LLM 출력 (evidence, license_as_written, reasoning, main_license, conflicts)
    source_text : 파일 전체 텍스트 (LLM 에 보낸 범위는 code_window 로 다시 잘라 쓴다)

    반환
      license      : 최종 라이선스 (ai_guessed_main_license 로 쓸 값)
      status       : ok / versionless / unknown / unrecognized
      family       : 계열
      failed_rule  : 답을 Unknown 으로 되돌린 규칙 (없으면 "")
      changes      : [{rule, before, after}] 답을 바꾼 기록
      llm_answer   : LLM 이 고른 값 (측정용: Other 선택 비율)
    """
    choice = (answer.get("main_license") or "").strip()
    written = (answer.get("license_as_written") or "").strip()
    evidence = (answer.get("evidence") or "").strip()
    changes = []

    def change(rule, before, after):
        changes.append({"rule": rule, "before": before, "after": after})

    def done(license_, failed_rule=""):
        r = CATALOG.normalize(license_)
        return {"license": license_, "status": r["status"], "family": r["family"],
                "failed_rule": failed_rule, "changes": changes,
                "llm_answer": {"main_license": choice, "license_as_written": written}}

    window_clean = _clean(code_window.window(source_text))
    # 원문 표현은 실제로 원문 범위에 있을 때만 믿는다 (모델이 바꿔 적었으면 쓰지 않음)
    written_ok = bool(written) and _clean(written) in window_clean
    verified_written = written if written_ok else ""

    if choice in ("", "Unknown"):
        # 원문 표현과 근거 문장이 모두 원문에서 확인되면 라이선스 표기가 있는 것이므로 Other 로 보고 계속 검사
        ev_ok = bool(evidence) and len(_clean(evidence)) >= EVIDENCE_MIN_CHARS and _clean(evidence) in window_clean
        if written_ok and ev_ok:
            change(RULE_UNKNOWN_WITH_TEXT, "Unknown", "Other")
            choice_for_check = "Other"
        else:
            return done("Unknown")
    else:
        choice_for_check = choice

    # 1) 이름 정리, 2) 선택지 대체 검사
    w = CATALOG.normalize(written) if written_ok else None
    if choice_for_check == "Other":
        if w and w["status"] == STATUS_OK:
            candidate = w["spdx"]
        elif w and w["status"] == STATUS_VERSIONLESS:
            candidate = w["versionless"]
        else:
            candidate = written or "Unknown"
            # 정리되지 않는 표현이지만 계열 이름(Apache, GPL 등)이 들어 있으면 계열만 확정
            if written_ok:
                wn = norm_text(written)
                for group, (_, patterns) in VERSIONLESS_GROUPS.items():
                    if any(_contains_word(wn, norm_text(p)) for p in patterns):
                        change(RULE_UNKNOWN_VERSION, written, group)
                        candidate = group
                        break
    else:
        candidate = choice
        if w and w["status"] == STATUS_OK and CATALOG.scoring_key(w["spdx"])[0] != CATALOG.scoring_key(choice)[0]:
            change(RULE_SUBSTITUTION, choice, w["spdx"])
            candidate = w["spdx"]
        elif w and w["status"] == STATUS_VERSIONLESS and group_of(choice) != w["versionless"]:
            # 원문은 다른 계열(예: "GPL")인데 답은 엉뚱한 선택지 -> 원문 계열로
            # 같은 계열이면 버전 정보가 근거 문장에 있을 수 있으므로 아래 버전 검사에 맡긴다
            change(RULE_SUBSTITUTION, choice, w["versionless"])
            candidate = w["versionless"]

    if candidate == "Unknown":
        return done("Unknown")

    # 3) 근거 문장 확인
    if not evidence:
        change(RULE_EVIDENCE_EMPTY, candidate, "Unknown")
        return done("Unknown", RULE_EVIDENCE_EMPTY)
    ev_clean = _clean(evidence)
    if len(ev_clean) < EVIDENCE_MIN_CHARS:
        change(RULE_EVIDENCE_TOO_SHORT, candidate, "Unknown")
        return done("Unknown", RULE_EVIDENCE_TOO_SHORT)
    if ev_clean not in window_clean:
        change(RULE_EVIDENCE_NOT_IN_SOURCE, candidate, "Unknown")
        return done("Unknown", RULE_EVIDENCE_NOT_IN_SOURCE)
    if not _LICENSE_STATEMENT.search(evidence):
        change(RULE_EVIDENCE_NOT_STATEMENT, candidate, "Unknown")
        return done("Unknown", RULE_EVIDENCE_NOT_STATEMENT)

    # 근거 문장이 속한 주석 블록과 (원문에서 확인된) 원문 표현을 함께 근거로 본다.
    block = evidence_block(evidence, code_window.window(source_text))
    ev_norm = norm_text(f"{evidence} {block} {verified_written}")
    cand = CATALOG.normalize(candidate)
    if cand["status"] == STATUS_OK:
        ev_only = norm_text(evidence)
        ok = any(_signature_ok(i, ev_norm, ev_only) for i in cand["ids"])
    elif cand["status"] == STATUS_VERSIONLESS:
        ok = _versionless_signature_ok(cand["versionless"], ev_norm)
    else:
        wn = norm_text(verified_written)
        ok = bool(wn) and (wn in ev_norm or ev_norm in wn)
    if not ok:
        change(RULE_EVIDENCE_MISMATCH, candidate, "Unknown")
        return done("Unknown", RULE_EVIDENCE_MISMATCH)

    final = cand["spdx"] if cand["status"] == STATUS_OK else candidate

    # 이중 라이선스: 영어로 "MIT and Apache" 라고 써도 하나를 고르는 것이므로 OR
    if cand["status"] == STATUS_OK and " AND " in final and re.search(r"\bdual", f"{evidence} {block}", re.IGNORECASE):
        as_or = final.replace(" AND ", " OR ")
        if CATALOG.normalize(as_or)["status"] == STATUS_OK:
            change(RULE_DUAL_LICENSE, final, as_or)
            return done(CATALOG.normalize(as_or)["spdx"])

    # 4) 버전 검사 (정리된 SPDX 이름일 때만)
    if cand["status"] == STATUS_OK and len(cand["ids"]) == 1:
        spdx_id = cand["ids"][0]
        group = group_of(spdx_id)
        basis = f"{_clean(evidence)} {_clean(block)} {verified_written}"   # 주석 기호를 지워야 줄을 넘는 문구가 이어짐
        if group in VERSIONED_GROUPS:
            pat = _version_patterns(spdx_id)
            if pat is not None and not pat.search(basis):
                change(RULE_VERSION_GUESS, final, group)
                return done(group)
        if spdx_id.endswith("-or-later") and not _OR_LATER_TEXT.search(basis):
            only = spdx_id[: -len("-or-later")] + "-only"
            if CATALOG.normalize(only)["status"] == STATUS_OK:
                change(RULE_OR_LATER, final, only)
                final = only
    return done(final)
