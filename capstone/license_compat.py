# -*- coding: utf-8 -*-
"""
license_compat.py - 프로젝트 전체 라이선스 충돌 판정 (스위치 CONFLICT_CHECK)

모든 파일의 판정이 끝난 뒤 프로젝트 전체에서 한 번 판정한다. 같은 파일 안의 짝도 포함된다.
(LLM 은 파일을 하나씩만 보므로 서로 다른 파일 사이의 충돌을 판단할 수 없다.)

처리 순서
  1) 라이선스 수집    파일 라이선스 + 의존 패키지 라이선스, 각각 어디서 나왔는지 기록
  2) OSADL 판정       두 라이선스를 양쪽 방향으로 조회
                      버전 없는 이름("GPL")은 그 계열의 모든 버전으로 조회해 결과가 같을 때만 사용
  3) 계열 추정        OSADL 에 없는 짝 중 확실한 경우만 (허용형+허용형=OK, 허용형+강한 카피레프트=RISK,
                      기타 계열 포함=REVIEW)
  4) 같은 파일 LLM 지적  파일 판정 때 LLM 이 conflicts 칸으로 지적한 충돌
  5) 마지막 LLM 호출   그래도 남은 라이선스를 상위 라이선스와만 비교
  6) 결과 정리        짝별 결과를 파일별로 연결하고, 가장 심각한 판정을 프로젝트 결과로

판정 값   OK 문제 없음 / RISK 충돌 위험 / CONFLICT 충돌 / REVIEW 확인 필요 / NOT_DETECTED 라이선스 미탐지
출처      OSADL / FAMILY(계열 추정) / LLM / RULE(내용 미상 등 규칙으로 정한 확인 필요)
결과 순서 CONFLICT > RISK(충돌 가능성 있음) > RISK > REVIEW > OK

OSADL 양방향 판정 (매트릭스 한 칸 = "주 라이선스 프로젝트 안에 종속 라이선스 코드를 넣어도 되는가")
  Yes+Yes -> OK, Yes+No -> RISK, No+No -> CONFLICT
  Yes+판단 보류 -> RISK, No+판단 보류 -> RISK + 충돌 가능성, 판단 보류+판단 보류 -> REVIEW
  판단 보류(Unknown, Check dependency)는 OSADL 이 검토 후 일반화할 수 없다고 본 것이므로 다음 단계로 넘기지 않는다.
"""

import json
from itertools import combinations
from pathlib import Path

from license_catalog import (CATALOG, OTHER, PERMISSIVE, STATUS_NON_STANDARD, STATUS_OK,
                             STATUS_UNKNOWN, STATUS_UNRECOGNIZED, STATUS_VERSIONLESS, STRONG,
                             VERSIONLESS_GROUPS, WEAK)

BASE_DIR = Path(__file__).resolve().parent
OSADL_PATH = BASE_DIR / "license_refs" / "osadl" / "matrixseqexpl.json"

OK, RISK, CONFLICT, REVIEW, NOT_DETECTED = "OK", "RISK", "CONFLICT", "REVIEW", "NOT_DETECTED"
SRC_OSADL, SRC_FAMILY, SRC_LLM, SRC_RULE = "OSADL", "FAMILY", "LLM", "RULE"
LLM_BATCH = 20                  # 마지막 LLM 호출 한 번에 넣을 짝 수
FOUND_IN_MAX = 5

FAMILY_KO = {PERMISSIVE: "허용형", WEAK: "약한 카피레프트", STRONG: "강한 카피레프트", OTHER: "기타"}


def severity(r: dict) -> float:
    if r is None:
        return 0
    base = {OK: 1, REVIEW: 2, RISK: 3, CONFLICT: 4}[r["result"]]
    return base + (0.5 if r["result"] == RISK and r.get("possible_conflict") else 0)


def _res(result, source, reason="", must_follow="", possible_conflict=False, osadl_values=None, note=""):
    return {"result": result, "source": source, "reason": reason, "must_follow": must_follow,
            "possible_conflict": possible_conflict, "osadl_values": osadl_values or [], "note": note}


# =============================================================================
# OSADL 매트릭스
# =============================================================================

class Osadl:
    def __init__(self, path: Path = OSADL_PATH):
        self.loaded, self.error, self.timestamp = False, None, None
        self.matrix = {}        # 소문자 주 라이선스 -> {소문자 종속 라이선스: (값, 설명)}
        self.names = {}         # 소문자 -> OSADL 표기
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.timestamp = data.get("timestamp")
            for lic in data.get("licenses", []):
                row = {c["name"].lower(): (c.get("compatibility"), c.get("explanation", ""))
                       for c in lic.get("compatibilities", [])}
                self.matrix[lic["name"].lower()] = row
                self.names[lic["name"].lower()] = lic["name"]
            # OSADL 이 폐기된 SPDX 표기를 쓰는 경우(예: bzip2-1.0.5)도 현재 표기로 찾을 수 있게 별칭을 둔다
            for low, name in list(self.names.items()):
                r = CATALOG.normalize(name)
                if r["status"] == STATUS_OK and r["spdx"].lower() != low and r["spdx"].lower() not in self.names:
                    self.names[r["spdx"].lower()] = name
            self.loaded = True
        except FileNotFoundError:
            self.error = f"OSADL 데이터 없음 ({path}) - license_refs/osadl/ 확인 필요"
        except Exception as e:
            self.error = f"OSADL 데이터 읽기 실패: {e}"

    def has(self, lic: str) -> bool:
        return lic.lower() in self.names

    def value(self, leading: str, subordinate: str):
        """(값, 설명). 둘 중 하나라도 매트릭스에 없으면 None."""
        lead = self.names.get(leading.lower())
        sub = self.names.get(subordinate.lower())
        if lead is None or sub is None:
            return None
        return self.matrix[lead.lower()].get(sub.lower())


OSADL = Osadl()


def _bucket(v: str) -> str:
    if v in ("Yes", "Same"):
        return "yes"
    if v == "No":
        return "no"
    return "hold"            # Unknown, Check dependency


def osadl_pair(a: str, b: str):
    """단일 라이선스 두 개의 OSADL 양방향 판정. 데이터가 없으면 None."""
    ab, ba = OSADL.value(a, b), OSADL.value(b, a)
    if ab is None or ba is None:
        return None
    va, vb = _bucket(ab[0]), _bucket(ba[0])
    values = [ab[0], ba[0]]
    expl = " / ".join(x for x in (ab[1], ba[1]) if x)[:400]
    absorber = a if va == "yes" else (b if vb == "yes" else "")
    if va == "yes" and vb == "yes":
        return _res(OK, SRC_OSADL, expl, osadl_values=values)
    if "yes" in (va, vb) and "no" in (va, vb):
        return _res(RISK, SRC_OSADL, expl, must_follow=absorber, osadl_values=values)
    if va == "no" and vb == "no":
        return _res(CONFLICT, SRC_OSADL, expl, osadl_values=values)
    if "yes" in (va, vb):                       # Yes + 판단 보류
        return _res(RISK, SRC_OSADL, expl, must_follow=absorber, osadl_values=values,
                    note="공존 가능은 확인됨, 세부 조건은 OSADL 판단 보류")
    if "no" in (va, vb):                        # No + 판단 보류
        return _res(RISK, SRC_OSADL, expl, possible_conflict=True, osadl_values=values,
                    note="충돌 가능성 있음: 엄격한 쪽을 따라도 해결되지 않을 수 있음")
    return _res(REVIEW, SRC_OSADL, expl, osadl_values=values, note="OSADL 판단 보류")


# =============================================================================
# 짝 판정 (규칙 단계: OSADL -> 계열 추정)
# =============================================================================

def _family_of_leaf(leaf):
    kind = leaf[0]
    if kind == "VERSIONLESS":
        return VERSIONLESS_GROUPS[leaf[1]][0]
    if kind in ("ID", "WITH"):
        return CATALOG.family_of(leaf[1])
    return None


def _leaf_name(leaf) -> str:
    if leaf[0] == "WITH":
        return f"{leaf[1]} WITH {leaf[2]}"
    return leaf[1]


def _family_estimate(a, b):
    fa, fb = _family_of_leaf(a), _family_of_leaf(b)
    if OTHER in (fa, fb):
        other = a if fa == OTHER else b
        reason = CATALOG.other_reason(other[1]) if other[0] in ("ID", "WITH") else ""
        return _res(REVIEW, SRC_FAMILY, f"기타 계열 포함: {reason}".strip(": "))
    if fa == PERMISSIVE and fb == PERMISSIVE:
        return _res(OK, SRC_FAMILY, "허용형끼리 (계열 추정)")
    if {fa, fb} == {PERMISSIVE, STRONG}:
        strong = a if fa == STRONG else b
        return _res(RISK, SRC_FAMILY, "허용형 + 강한 카피레프트 (계열 추정)", must_follow=_leaf_name(strong))
    return None     # 약한 카피레프트 포함, 서로 다른 강한 카피레프트, 계열 미상 -> 다음 단계


def _leaf_pair(a, b):
    """잎(단일 라이선스) 두 개의 규칙 판정. 판정하지 못하면 None."""
    # 버전 없는 이름: 그 계열의 모든 버전으로 OSADL 조회
    if a[0] == "VERSIONLESS" or b[0] == "VERSIONLESS":
        vl, other = (a, b) if a[0] == "VERSIONLESS" else (b, a)
        versions = [v for v in CATALOG.versionless_versions.get(vl[1], []) if OSADL.has(v)]
        if other[0] != "VERSIONLESS" and versions:
            results = [_leaf_pair(("ID", v), other) for v in versions]
            if all(r is not None and r["source"] == SRC_OSADL for r in results):
                kinds = {(r["result"], r.get("possible_conflict")) for r in results}
                if len(kinds) == 1:
                    r = dict(results[0])
                    r["note"] = (r.get("note", "") + f" / 버전 미상: {vl[1]} 모든 버전에서 같은 결과").strip(" /")
                    r["must_follow"] = vl[1] if r["must_follow"] in versions else r["must_follow"]
                    return r
                return _res(REVIEW, SRC_OSADL, f"{vl[1]} 버전에 따라 결과가 다름 (버전 미상)")
        return _family_estimate(a, b)

    # WITH: OSADL 에 통째로 있으면 그대로, 없으면 기본 라이선스로 비교하되 충돌·충돌 위험은 확인 필요로
    def lookup_name(leaf):
        if leaf[0] == "WITH":
            whole = _leaf_name(leaf)
            return whole if OSADL.has(whole) else leaf[1], (not OSADL.has(whole))
        return leaf[1], False

    na, wa = lookup_name(a)
    nb, wb = lookup_name(b)
    r = osadl_pair(na, nb)
    if r is not None:
        if (wa or wb) and r["result"] in (RISK, CONFLICT):
            exc = a[2] if wa else b[2]
            return _res(REVIEW, SRC_OSADL, r["reason"], osadl_values=r["osadl_values"],
                        note=f"예외 조항 {exc} 이 붙어 있어 확인 필요 (기본 라이선스 기준 {r['result']})")
        return r
    return _family_estimate(a, b)


def _tree_of(lic: str):
    """정리된 라이선스 문자열 -> 비교용 구조."""
    r = CATALOG.normalize(lic)
    if r["status"] == STATUS_OK:
        return CATALOG.tree(r["spdx"])
    if r["status"] == STATUS_VERSIONLESS:
        return ("VERSIONLESS", r["versionless"])
    return None


def _combine(results, mode):
    """AND: 가장 심각한 결과 (하나라도 판정 못 하면, 이미 충돌이 아닌 한 판정 못 함)
       OR : 가장 문제가 적은 결과 (하나라도 OK 면 OK)"""
    known = [r for r in results if r is not None]
    if mode == "AND":
        if any(r["result"] == CONFLICT for r in known):
            return max(known, key=severity)
        if len(known) < len(results):
            return None
        return max(known, key=severity)
    if any(r["result"] == OK for r in known):
        return min(known, key=severity)
    if len(known) < len(results):
        return None
    return min(known, key=severity)


def judge_trees(ta, tb):
    if ta[0] in ("AND", "OR"):
        parts = [judge_trees(c, tb) for c in ta[1]]
        r = _combine(parts, ta[0])
        if r is not None and ta[0] == "OR":
            idx = parts.index(r) if r in parts else 0
            r = dict(r, note=(r.get("note", "") + f" / 선택지 {_tree_str(ta[1][idx])} 기준").strip(" /"))
        return r
    if tb[0] in ("AND", "OR"):
        return judge_trees(tb, ta)
    return _leaf_pair(ta, tb)


def _tree_str(t):
    if t[0] in ("ID", "VERSIONLESS"):
        return t[1]
    if t[0] == "WITH":
        return f"{t[1]} WITH {t[2]}"
    return f" {t[0]} ".join(_tree_str(c) for c in t[1])


def judge_pair(a: str, b: str):
    """라이선스 두 개(정리된 문자열)의 규칙 판정 (OSADL -> 계열 추정). 판정하지 못하면 None."""
    ta, tb = _tree_of(a), _tree_of(b)
    if ta is None or tb is None:
        return None
    return judge_trees(ta, tb)


# =============================================================================
# 프로젝트 전체 판정
# =============================================================================

def _unit_key(lic: str):
    """같은 라이선스는 하나로 묶는다. 반환 (키, 표시 이름, 상태, 원문)."""
    r = CATALOG.normalize(lic)
    if r["status"] == STATUS_OK:
        return r["spdx"], r["spdx"], STATUS_OK, ""
    if r["status"] == STATUS_VERSIONLESS:
        return f"VERSIONLESS:{r['versionless']}", r["versionless"], STATUS_VERSIONLESS, ""
    if r["status"] == STATUS_NON_STANDARD:
        return "NON_STANDARD", "non-standard", STATUS_NON_STANDARD, ""
    if r["status"] == STATUS_UNKNOWN:
        return None, None, STATUS_UNKNOWN, ""
    text = str(lic).strip()
    return f"RAW:{text.lower()}", text, STATUS_UNRECOGNIZED, text


def _is_permissive_unit(u):
    return u["status"] == STATUS_OK and CATALOG.normalize(u["name"])["family"] == PERMISSIVE


def _is_copyleft_unit(u):
    fam = (CATALOG.normalize(u["name"])["family"] if u["status"] == STATUS_OK else
           VERSIONLESS_GROUPS[u["name"]][0] if u["status"] == STATUS_VERSIONLESS else None)
    return fam in (WEAK, STRONG)


def upper_licenses(units: dict) -> list:
    """
    상위 라이선스: OSADL 로 확인했을 때 나머지(OSADL 에 있는) 라이선스를 모두 종속으로 받아들일 수 있는 라이선스.
    판단에는 OSADL 결과만 쓴다 (계열 추정은 쓰지 않음). 여러 개면 모두, 없으면 충돌 짝 라이선스 모두.
    """
    singles = [k for k, u in units.items() if u["status"] == STATUS_OK and " " not in k and OSADL.has(k)]
    uppers = []
    for c in singles:
        if all(c == o or _bucket((OSADL.value(c, o) or ("",))[0]) == "yes" for o in singles):
            uppers.append(c)
    if uppers:
        return uppers
    conflicted = set()
    for a, b in combinations(singles, 2):
        r = osadl_pair(a, b)
        if r and r["result"] == CONFLICT:
            conflicted.update((a, b))
    return sorted(conflicted) or singles


def _llm_final(pending_units, units, uppers, llm_call):
    """마지막 프로젝트 단위 LLM 호출. 반환 {(x, upper): 판정 결과}"""
    import harness_prompts
    import harness_schemas
    pairs, meta = [], {}
    for x in pending_units:
        targets = [u for u in uppers if u != x] or [k for k in units if k != x][:10]
        for up in targets:
            pid = f"p{len(pairs) + 1}"
            ux = units[x]
            item = {"pair_id": pid, "compared_with": units[up]["name"]}
            if ux["status"] == STATUS_UNRECOGNIZED:
                item.update({"license": "Other", "original_text": ux["raw"]})
            else:
                fam = (CATALOG.normalize(ux["name"])["family"] if ux["status"] == STATUS_OK
                       else VERSIONLESS_GROUPS[ux["name"]][0])
                item.update({"license": ux["name"], "family": FAMILY_KO.get(fam, fam)})
            pairs.append(item)
            meta[pid] = (x, up)

    out = {}
    upper_info = [{"id": units[u]["name"],
                   "family": FAMILY_KO.get(CATALOG.normalize(units[u]["name"])["family"], "")} for u in uppers]
    for i in range(0, len(pairs), LLM_BATCH):
        batch = pairs[i:i + LLM_BATCH]
        ids = [p["pair_id"] for p in batch]
        model, schema = harness_schemas.pair_judgment_schema(ids)
        r = llm_call(model, schema, messages=harness_prompts.pair_messages(upper_info, batch))
        got = {}
        if r.get("ok"):
            for item in r["data"]["results"]:
                got.setdefault(item["pair_id"], item)        # 중복은 첫 답만
        for p in batch:
            x, up = meta[p["pair_id"]]
            item = got.get(p["pair_id"])
            if item is None:
                why = "판정 실패" if not r.get("ok") else "LLM 이 답하지 않은 짝"
                out[(x, up)] = _res(REVIEW, SRC_LLM, why)
                continue
            res = _res(item["result"], SRC_LLM, item["reason"], must_follow=item.get("must_follow", ""),
                       note=f"상위 라이선스 {units[up]['name']} 와 비교")
            names = {units[x]["name"], units[up]["name"]}
            if res["result"] == RISK and res["must_follow"] not in names:
                res = _res(REVIEW, SRC_LLM, item["reason"], note="따라야 할 라이선스가 짝에 없음")
            if res["result"] == OK and ((_is_copyleft_unit(units[x]) and _is_permissive_unit(units[up])) or
                                        (_is_copyleft_unit(units[up]) and _is_permissive_unit(units[x]))):
                res = _res(REVIEW, SRC_LLM, item["reason"],
                           note="카피레프트와 허용형을 문제 없음으로 답함 (일반 원칙과 어긋나 확인 필요)")
            out[(x, up)] = res
    return out


def project_conflicts(files: list, unanalyzed_code_files: int = 0, llm_call=None) -> dict:
    """
    files: [{
        "file_path": str,
        "license": str,                 # 최종 파일 판정 (ai_guessed_main_license)
        "judge_status": "OK"|"EMPTY"|"FAILED",
        "packages": {이름: 라이선스 문자열},
        "llm_conflicts": [{"package": 이름, "reason": 이유}],   # 파일 판정 때 LLM 이 지적한 충돌
    }]
    llm_call: 마지막 LLM 호출 함수 (llm_client.call_structured). None 이면 그 단계를 건너뜀.

    반환 {"summary": conflict_summary, "per_file": {file_path: [충돌]}}
    """
    units = {}           # 키 -> {name, status, raw, found_in:[...], files:set}
    unknown_files = empty_files = failed_files = 0

    def add(lic, origin, file_path):
        key, name, status, raw = _unit_key(lic)
        if key is None:
            return None
        u = units.setdefault(key, {"name": name, "status": status, "raw": raw, "found_in": [], "files": set()})
        if len(u["found_in"]) < FOUND_IN_MAX and origin not in u["found_in"]:
            u["found_in"].append(origin)
        u["files"].add(file_path)
        return key

    file_keys = {}
    for f in files:
        fp = f["file_path"]
        keys = set()
        status = f.get("judge_status") or "OK"
        if status == "EMPTY":
            empty_files += 1
        elif status == "FAILED":
            failed_files += 1
        else:
            k = add(f.get("license", ""), f"{fp} (파일)", fp)
            if k:
                keys.add(k)
            else:
                unknown_files += 1
        pkg_key = {}
        for pkg, lic in (f.get("packages") or {}).items():
            k = add(lic, f"{pkg} (패키지, {fp})", fp)
            if k:
                keys.add(k)
                pkg_key[pkg] = k
        file_keys[fp] = (keys, pkg_key, f.get("llm_conflicts") or [])

    results = {}             # (a, b) -> 판정
    singles = []             # 짝 없이 혼자 확인이 필요한 라이선스

    keys = sorted(units)
    # 내용 미상·기타 계열 라이선스는 혼자서도 확인 필요
    for k in keys:
        u = units[k]
        if u["status"] == STATUS_NON_STANDARD:
            singles.append({"license": u["name"], **_res(REVIEW, SRC_RULE, "라이선스 내용 미상 (non-standard)")})
        elif u["status"] == STATUS_UNRECOGNIZED and len(keys) == 1:
            singles.append({"license": u["name"], **_res(REVIEW, SRC_RULE, "라이선스 내용 미상")})
        elif u["status"] == STATUS_OK and CATALOG.normalize(u["name"])["family"] == OTHER:
            ids = CATALOG.normalize(u["name"])["ids"]
            singles.append({"license": u["name"],
                            **_res(REVIEW, SRC_RULE, f"기타 계열: {CATALOG.other_reason(ids[0]) if ids else ''}")})

    # 2)·3) 규칙 판정
    pending = []
    for a, b in combinations(keys, 2):
        ua, ub = units[a], units[b]
        if STATUS_NON_STANDARD in (ua["status"], ub["status"]):
            results[(a, b)] = _res(REVIEW, SRC_RULE, "한쪽 라이선스 내용 미상 (non-standard)")
            continue
        r = judge_pair(ua["name"], ub["name"]) if STATUS_UNRECOGNIZED not in (ua["status"], ub["status"]) else None
        if r is None:
            pending.append((a, b))
        else:
            results[(a, b)] = r

    # 4) 같은 파일 안에서 LLM 이 지적한 충돌 (규칙으로 판정하지 못한 짝에만)
    still = []
    for a, b in pending:
        claim = None
        for fp, (fkeys, pkg_key, claims) in file_keys.items():
            if a in fkeys and b in fkeys:
                for c in claims:
                    if pkg_key.get(c.get("package")) in (a, b):
                        claim = c
                        break
            if claim:
                break
        if claim:
            results[(a, b)] = _res(RISK, SRC_LLM, claim.get("reason", ""),
                                   note=f"파일 판정 때 LLM 이 지적 ({claim.get('package')})")
        else:
            still.append((a, b))

    # 5) 마지막 LLM 호출: 판정이 남은 라이선스를 상위 라이선스와만 비교
    compared = {}
    if still and llm_call is not None:
        uppers = upper_licenses(units)

        def lacks_data(k):          # OSADL 로 판정할 수 없는 쪽이 "판정이 남은 라이선스"
            u = units[k]
            return not (u["status"] == STATUS_OK and " " not in k and OSADL.has(k))
        pending_units = sorted({k for pair in still for k in pair if lacks_data(k)}) or \
            sorted({pair[0] for pair in still})
        compared = _llm_final(pending_units, units, uppers, llm_call)
        for (x, up), r in compared.items():
            key = tuple(sorted((x, up)))
            if key not in results:                  # 규칙으로 정한 판정은 덮어쓰지 않는다
                results[key] = dict(r, compared_with_upper=units[up]["name"])
        still = [p for p in still if p not in results]
    for a, b in still:
        if (a, b) not in results:
            covered = any(x in (a, b) for x, _ in compared)
            if not covered:
                results[(a, b)] = _res(REVIEW, SRC_RULE, "판단 근거 없음")

    # 6) 정리
    pairs = []
    for (a, b), r in results.items():
        same_file = bool(units[a]["files"] & units[b]["files"])
        pairs.append({"licenses": [units[a]["name"], units[b]["name"]], "same_file": same_file,
                      "found_in": units[a]["found_in"] + units[b]["found_in"],
                      "compared_with_upper": r.get("compared_with_upper", ""), **{
                          k: r[k] for k in ("result", "source", "possible_conflict", "osadl_values",
                                            "must_follow", "reason", "note")}})
    pairs.sort(key=lambda p: -severity(p))

    if not units:
        level = NOT_DETECTED
    else:
        worst = max(pairs + singles, key=severity, default=None)
        level = worst["result"] if worst else OK
    worst_possible = any(p["result"] == RISK and p["possible_conflict"] for p in pairs)

    per_file = {}
    for fp, (fkeys, _, _) in file_keys.items():
        items = []
        for (a, b), r in results.items():
            if r["result"] == OK or not ({a, b} & fkeys):
                continue
            mine, other = (a, b) if a in fkeys else (b, a)
            items.append({"license": units[mine]["name"], "with": units[other]["name"], "result": r["result"],
                          "source": r["source"], "possible_conflict": r["possible_conflict"],
                          "same_file": other in fkeys})
        if items:
            per_file[fp] = sorted(items, key=lambda x: -severity(x))

    summary = {
        "level": level,
        "possible_conflict": worst_possible,
        "pairs": [p for p in pairs if p["result"] != OK] + [p for p in pairs if p["result"] == OK],
        "single_licenses": singles,
        "licenses": [{"license": u["name"], "status": u["status"], "found_in": u["found_in"]}
                     for u in units.values()],
        "unknown_license_files": unknown_files,
        "empty_files": empty_files,
        "failed_files": failed_files,
        "unanalyzed_code_files": unanalyzed_code_files,
        "complete": unanalyzed_code_files == 0 and failed_files == 0,
        "osadl_timestamp": OSADL.timestamp,
    }
    return {"summary": summary, "per_file": per_file}


def status() -> dict:
    return {"osadl_loaded": OSADL.loaded, "osadl_error": OSADL.error, "osadl_timestamp": OSADL.timestamp,
            "osadl_licenses": len(OSADL.matrix)}


DEMO_PAIRS = [
    ("MIT", "BSD-3-Clause", "OK"),
    ("MIT", "GPL-3.0-only", "RISK"),
    ("GPL-2.0-only", "Apache-2.0", "CONFLICT"),
    ("GPL-2.0-only WITH Classpath-exception-2.0", "Apache-2.0", "OSADL 항목 기준"),
    ("GPL-2.0-only WITH Linux-syscall-note", "Apache-2.0", "REVIEW (예외 조항)"),
    ("GPL", "Apache-2.0", "REVIEW (버전에 따라 다름)"),
    ("GPL", "MIT", "RISK (버전 미상)"),
    ("AFL-2.0", "AGPL-3.0-only", "RISK + 충돌 가능성"),
    ("CC0-1.0", "GPL-3.0-only", "RISK (계열 추정)"),
    ("CC-BY-NC-4.0", "MIT", "REVIEW (기타 계열)"),
    ("OFL-1.1", "MIT", "None -> LLM 단계"),
    ("MIT OR Apache-2.0", "GPL-2.0-only", "RISK (MIT 선택 기준)"),
    ("MPL-2.0", "MIT", "RISK"),
]


def _demo():
    print(f"OSADL 기준일 {OSADL.timestamp}, 라이선스 {len(OSADL.matrix)}개\n")
    print(f"{'라이선스 1':<44} {'라이선스 2':<16} {'판정':<9} {'출처':<7} {'OSADL 값':<22} 기대")
    print("-" * 120)
    for a, b, expected in DEMO_PAIRS:
        r = judge_pair(a, b)
        if r is None:
            print(f"{a:<44} {b:<16} {'None':<9} {'':<7} {'':<22} {expected}")
            continue
        mark = r["result"] + ("+" if r.get("possible_conflict") else "")
        print(f"{a:<44} {b:<16} {mark:<9} {r['source']:<7} {str(r['osadl_values']):<22} {expected}")
        if r.get("note"):
            print(f"{'':<44} {'':<16} 비고: {r['note'][:70]}")


if __name__ == "__main__":
    import sys
    if not OSADL.loaded:
        sys.exit(f"[오류] {OSADL.error}")
    if "--demo" in sys.argv:
        _demo()
        sys.exit(0)
    args = sys.argv[1:] or ["MIT", "GPL-3.0-only"]
    if len(args) != 2:
        sys.exit("사용법: python license_compat.py 라이선스1 라이선스2   또는   python license_compat.py --demo")
    print(json.dumps(judge_pair(args[0], args[1]), ensure_ascii=False, indent=1))
