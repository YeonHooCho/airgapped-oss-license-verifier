# -*- coding: utf-8 -*-
"""
rule_judge.py - 규칙 우선 판정 (LLM 을 부르기 전에 확정할 수 있는 파일)

  1) 빈 파일          공백밖에 없는 파일은 "내용 없음"(EMPTY). 주석만 있는 파일은 생략하지 않는다.
  2) SPDX 태그        상단 범위(code_window)의 주석 줄에 있는 SPDX-License-Identifier 로 확정.
                      태그가 여러 개인데 값이 다르거나, 값이 유효하지 않으면 확정하지 않는다.
  3) LICENSE 원문     LICENSE·COPYING 파일을 SPDX 표준 원문과 비교해, 가장 비슷한 라이선스의 일치도가
                      매우 높고 두 번째 후보와의 차이도 충분할 때만 확정한다.
                      원문만으로는 -only / -or-later 를 구분할 수 없으므로 GPL 계열은 -only 로 확정한다.

1)·2) 는 스위치 RULE_SPDX_TAG, 3) 은 RULE_LICENSE_TEXT 로 켠다 (harness_config.py).
확정하지 못하면 None 을 돌려주고, 그 파일은 기존처럼 LLM 이 판정한다.

LICENSE 원문 판정 기준값(LICENSE_MIN_SCORE, LICENSE_MIN_MARGIN)은 dataset2 로 시험해 정한다.
    python rule_judge.py --scan-dataset2        dataset2 의 LICENSE 파일별 후보와 점수 보기
    python rule_judge.py 경로/LICENSE           파일 하나 판정해 보기
"""

import json
import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

import code_window
import harness_config
from license_catalog import CATALOG, STATUS_OK

LICENSE_MIN_SCORE = 0.95      # 가장 비슷한 원문과의 일치도 (0~1) 가 이 값 이상이어야 확정
LICENSE_MIN_MARGIN = 0.02     # 두 번째 후보와의 차이가 이 값 이상이어야 확정
# 기준값은 dataset2 의 LICENSE 30개로 정했다 (2026-10-26).
#   1위가 정답인데 넘어간 경우의 차이: 0.026~0.030 (BSD-3-Clause, MIT, BSD-2-Clause)
#   1위가 오답인 경우의 차이:         0.015~0.016 (ISC->0BSD, GPL-2.0->AGPL-1.0)
#   0.02 는 두 무리 사이에 있어, 오답은 막고 정답은 확정한다 (30개 중 26개 확정, 오답 0).
#   측정에 쓰는 dataset2 로 정한 값이므로 결과 보고에 이 사실을 함께 적는다.
LICENSE_CANDIDATES = 8        # 단어 집합으로 먼저 추린 뒤 자세히 비교할 후보 수
LICENSE_MAX_CHARS = 200_000   # 이보다 긴 LICENSE 파일은 규칙 판정하지 않음

RULE_EMPTY, RULE_SPDX_TAG, RULE_LICENSE_TEXT = "empty", "spdx_tag", "license_text"


def _result(rule, license_, evidence, reason, judge_status="OK", details=None):
    r = CATALOG.normalize(license_) if license_ != "Unknown" else None
    return {
        "rule": rule,
        "license": license_,
        "judge_status": judge_status,
        "evidence": evidence,
        "reason": reason,
        "family": r["family"] if r else None,
        "details": details or {},
    }


# =============================================================================
# 1) 빈 파일
# =============================================================================

def judge_empty(text: str):
    if text.strip():
        return None
    return _result(RULE_EMPTY, "Unknown", "", "내용 없음 (공백만 있는 파일)", judge_status="EMPTY")


# =============================================================================
# 2) SPDX 태그
# =============================================================================

def judge_spdx_tag(text: str):
    """
    반환: (판정 결과 또는 None, 확정하지 못한 사유)
    사유는 LLM 으로 넘길 때 기록용 (예: 태그 여러 개, 값이 유효하지 않음).
    """
    window = code_window.window(text)
    tags = code_window.find_spdx_tags(window)
    if not tags:
        return None, ""
    distinct = sorted(set(t.strip() for t in tags))
    if len(distinct) > 1:
        return None, f"SPDX 태그가 여러 개이고 값이 다름: {distinct}"
    value = distinct[0]
    r = CATALOG.normalize(value)
    if r["status"] != STATUS_OK:
        return None, f"SPDX 태그 값이 유효하지 않음: {value!r} ({r['status']})"
    line = next((ln.strip() for ln in window.split("\n") if "SPDX-License-Identifier" in ln), "")
    note = f", 표기 변환 {r['converted']}" if r["converted"] else ""
    return _result(RULE_SPDX_TAG, r["spdx"], line,
                   f"SPDX-License-Identifier 태그로 확정: {r['spdx']}{note}",
                   details={"tag": value, "converted": r["converted"]}), ""


# =============================================================================
# 3) LICENSE 원문
# =============================================================================

_COPYRIGHT_LINE = re.compile(
    r"^\s*(copyright\b|\(c\)|©|all rights reserved|portions copyright)", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")
_OPTIONAL = re.compile(r"<<beginOptional>>.*?<<endOptional>>", re.DOTALL)
_VAR = re.compile(r"<<var;[^>]*?original=\"(.*?)\"[^>]*?>>", re.DOTALL)
_ANY_TAG = re.compile(r"<<[^>]*>>")


def _tokens(text: str) -> list:
    """저작권 줄을 빼고 소문자 단어 목록으로. 문장부호·줄바꿈·번호 형식 차이는 무시된다."""
    kept = [ln for ln in (text or "").splitlines() if not _COPYRIGHT_LINE.match(ln)]
    return _WORD.findall(" ".join(kept).lower())


def _template_core(template: str) -> str:
    """템플릿에서 선택 부분(제목, 부록 등)을 빼고, 바뀌는 부분은 원래 값으로 채운 본문."""
    t = template or ""
    for _ in range(3):                      # 중첩된 선택 부분까지
        t = _OPTIONAL.sub(" ", t)
    t = _VAR.sub(lambda m: m.group(1), t)
    return _ANY_TAG.sub(" ", t)


def _dice(a: Counter, b: Counter) -> float:
    """단어 다중집합 기준 일치도 (0~1). 순서는 보지 않지만 단어 수 차이는 반영된다."""
    total = sum(a.values()) + sum(b.values())
    if not total:
        return 0.0
    inter = sum((a & b).values())
    return 2 * inter / total


@lru_cache(maxsize=1)
def _reference_index():
    """
    SPDX 원문 색인: 원문이 똑같은 식별자(GPL-3.0-only / -or-later 등)는 한 무리로 묶는다.
    무리마다 전체 원문 판과 선택 부분을 뺀 판 두 가지 단어 목록을 만든다.
    """
    groups = {}
    for spdx_id in CATALOG.all_text_ids():
        full = _tokens(CATALOG.text(spdx_id))
        if len(full) < 10:
            continue
        key = " ".join(full)
        g = groups.setdefault(key, {"ids": [], "variants": []})
        g["ids"].append(spdx_id)
        if not g["variants"]:
            core = _tokens(_template_core(CATALOG.template(spdx_id)))
            variants = [Counter(full)]
            if core and len(core) < len(full):
                variants.append(Counter(core))
            g["variants"] = variants
            g["word_set"] = set(full)
    index = []
    for g in groups.values():
        index.append({"ids": sorted(g["ids"]), "representative": _representative(g["ids"]),
                      "variants": g["variants"], "word_set": g["word_set"]})
    return index


def _representative(ids: list) -> str:
    """원문이 같은 무리의 대표 식별자. -only 우선 (원문만으로는 구분 불가, 더 엄격한 쪽)."""
    only = [i for i in ids if i.endswith("-only")]
    if only:
        return sorted(only)[0]
    plain = [i for i in ids if not i.endswith("-or-later")]
    return sorted(plain or ids)[0]


def license_text_candidates(text: str, top: int = 3) -> list:
    """LICENSE 내용과 가장 비슷한 SPDX 원문 후보 [(대표 식별자, 점수, 무리 식별자들)]"""
    words = _tokens(text)
    if not words:
        return []
    bag, wset = Counter(words), set(words)
    index = _reference_index()

    def jacc(entry):
        inter = len(wset & entry["word_set"])
        return inter / (len(wset | entry["word_set"]) or 1)

    pre = sorted(index, key=jacc, reverse=True)[:LICENSE_CANDIDATES]
    scored = []
    for e in pre:
        score = max(_dice(bag, v) for v in e["variants"])
        scored.append((e["representative"], round(score, 4), e["ids"]))
    scored.sort(key=lambda x: -x[1])
    return scored[:top]


def _first_line(text: str) -> str:
    for ln in text.splitlines():
        s = ln.strip()
        if s and not _COPYRIGHT_LINE.match(s):
            return s[:200]
    return ""


def judge_license_text(text: str):
    """반환: (판정 결과 또는 None, 확정하지 못한 사유)"""
    if not CATALOG.loaded:
        return None, "라이선스 목록 모듈을 불러오지 못함"
    if len(text) > LICENSE_MAX_CHARS:
        return None, "파일이 너무 김"
    cands = license_text_candidates(text, top=2)
    if not cands:
        return None, "비교할 내용 없음"
    best_id, best, best_group = cands[0]
    second = cands[1][1] if len(cands) > 1 else 0.0
    details = {"best": best_id, "score": best, "second": cands[1][0] if len(cands) > 1 else None,
               "second_score": second, "group": best_group}
    if best < LICENSE_MIN_SCORE:
        return None, f"원문 일치도 부족 ({best_id} {best:.3f} < {LICENSE_MIN_SCORE})"
    if best - second < LICENSE_MIN_MARGIN:
        return None, f"후보 간 차이 부족 ({best_id} {best:.3f} / {cands[1][0]} {second:.3f})"
    note = ""
    if len(best_group) > 1:
        note = f" (원문이 같은 {', '.join(best_group)} 중 -only 로 확정)"
    return _result(RULE_LICENSE_TEXT, best_id, _first_line(text),
                   f"SPDX 표준 원문과 일치도 {best:.3f}로 확정: {best_id}{note}", details=details), ""


# =============================================================================
# 묶음: 엔진이 부르는 함수
# =============================================================================

def judge(language: str, text: str, flags: dict = None) -> dict:
    """
    켜진 규칙을 순서대로 적용한다.
    반환: {"result": 판정 결과 또는 None, "skipped": [확정하지 못한 사유들]}
    """
    flags = flags if flags is not None else harness_config.FLAGS
    skipped = []
    if flags.get("RULE_SPDX_TAG"):
        r = judge_empty(text)
        if r:
            return {"result": r, "skipped": skipped}
        r, why = judge_spdx_tag(text)
        if r:
            return {"result": r, "skipped": skipped}
        if why:
            skipped.append(why)
    if flags.get("RULE_LICENSE_TEXT") and language == "License-Text":
        r, why = judge_license_text(text)
        if r:
            return {"result": r, "skipped": skipped}
        if why:
            skipped.append(why)
    return {"result": None, "skipped": skipped}


# =============================================================================
# 시험용 실행
# =============================================================================

def scan_dataset2():
    """dataset2 의 LICENSE 파일과 SPDX 태그 파일을 규칙으로 판정해 보고, 기준값을 정할 수 있게 보여 준다."""
    import test_runner2 as tr
    samples = tr.load_samples()
    rows = []
    print("=" * 96)
    print(f"LICENSE 원문 판정 (확정 기준: 일치도 >= {LICENSE_MIN_SCORE}, 후보 간 차이 >= {LICENSE_MIN_MARGIN})")
    print("=" * 96)
    for s in samples:
        gt = s["label"].get("ground_truth")
        for rel in tr.local_analyzable_files(s["folder"]):
            if tr.detect_language(rel) != "License-Text":
                continue
            text = code_window.decode_text((s["folder"] / rel).read_bytes())
            cands = license_text_candidates(text, top=3)
            res, why = judge_license_text(text)
            decided = res["license"] if res else None
            ok = None
            if decided:
                ok = CATALOG.scoring_key(decided)[0] == CATALOG.scoring_key(gt)[0]
            rows.append({"sample": s["sid"], "file": rel, "ground_truth": gt, "decided": decided,
                         "correct": ok, "why_not": why, "candidates": cands})
            mark = "-" if decided is None else ("정답" if ok else "오답")
            top3 = ", ".join(f"{c[0]} {c[1]:.3f}" for c in cands)
            print(f"  {s['sid']:<9} {rel[:28]:<28} 정답 {str(gt)[:16]:<16} 판정 {str(decided)[:18]:<18} [{mark}]")
            print(f"            후보: {top3}")
            if why:
                print(f"            확정 안 함: {why}")
    n = len(rows)
    dec = [r for r in rows if r["decided"]]
    cor = [r for r in dec if r["correct"]]
    print(f"\n  LICENSE 파일 {n}개 중 규칙으로 확정 {len(dec)}개, 그중 정답 {len(cor)}개, "
          f"오답 {len(dec) - len(cor)}개, LLM 으로 넘김 {n - len(dec)}개")
    print("  오답이 있으면 기준값을 올리고, 정답인데 넘긴 파일이 많으면 기준값을 낮추는 것을 검토합니다.")

    tag_rows = []
    for s in samples:
        for rel in tr.local_analyzable_files(s["folder"]):
            text = code_window.decode_text((s["folder"] / rel).read_bytes())
            if code_window.find_spdx_tags(code_window.window(text)):
                r, why = judge_spdx_tag(text)
                tag_rows.append({"sample": s["sid"], "file": rel, "decided": r["license"] if r else None,
                                 "why_not": why})
    print(f"\n  SPDX 태그가 있는 파일 {len(tag_rows)}개 (확정 {sum(1 for r in tag_rows if r['decided'])}개)")
    for r in tag_rows[:10]:
        print(f"    {r['sample']}/{r['file']}: {r['decided'] or r['why_not']}")

    out = tr.DATASET_DIR / "rule_judge_scan.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"min_score": LICENSE_MIN_SCORE, "min_margin": LICENSE_MIN_MARGIN,
                   "license_files": rows, "spdx_tag_files": tag_rows}, f, ensure_ascii=False, indent=1)
    print(f"\n저장: {out}")


def main():
    if not CATALOG.loaded:
        sys.exit(f"[오류] {CATALOG.error}")
    if len(sys.argv) > 1 and sys.argv[1] == "--scan-dataset2":
        scan_dataset2()
        return
    if len(sys.argv) < 2:
        print(__doc__)
        return
    path = Path(sys.argv[1])
    text = code_window.decode_text(path.read_bytes())
    lang = "License-Text" if path.name.lower() in ("license", "license.txt", "license.md", "copying") else "source"
    flags = {"RULE_SPDX_TAG": True, "RULE_LICENSE_TEXT": True}
    out = judge(lang, text, flags)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if lang == "License-Text":
        print("후보:", license_text_candidates(text, top=5))


if __name__ == "__main__":
    main()
