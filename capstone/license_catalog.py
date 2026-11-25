# -*- coding: utf-8 -*-
"""
license_catalog.py - 라이선스 목록 모듈 (분석 엔진과 측정 스크립트 공용)

license_refs/ 폴더의 SPDX·ScanCode 데이터를 읽어,
라이선스 이름을 하나의 기준으로 정리한다. 실행 중에는 인터넷을 쓰지 않는다.

하는 일
  - 이름 정규화      대소문자, 별칭("MIT License", "GPLv3"), SPDX 이름, 레지스트리 표기
  - 폐기 표기 변환   GPL-2.0 -> GPL-2.0-only, GPL-2.0+ -> GPL-2.0-or-later (원래 값 보관)
  - 표현식 해석      MIT OR Apache-2.0, (A OR B) AND C, A WITH 예외  (license-expression)
  - 버전 없는 이름   "GPL", "BSD" 는 버전을 추측하지 않고 계열만 확정 (SPDX 이름은 비움)
  - 계열 분류        ScanCode 분류 -> 허용형 / 약한 카피레프트 / 강한 카피레프트 / 기타
  - 원문 접근        SPDX 원문·템플릿·표준 헤더, 근거 확인용 대표 문구
  - 채점용 키        기본 정확도(-only/-or-later 같게)와 세부 정확도(구분), 표현식 순서 무시

사용
    from license_catalog import CATALOG
    r = CATALOG.normalize("GPLv3")
    r["spdx"]      -> "GPL-3.0-only"
    r["family"]    -> "strong_copyleft"

확인
    python license_catalog.py "GPLv3" "MIT OR Apache-2.0" "BSD License"
"""

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

from license_expression import (ExpressionError, LicenseSymbol, LicenseWithExceptionSymbol,
                                Licensing)

BASE_DIR = Path(__file__).resolve().parent
REFS_DIR = BASE_DIR / "license_refs"

# =============================================================================
# 계열
# =============================================================================

PERMISSIVE, WEAK, STRONG, OTHER, UNKNOWN_FAMILY = (
    "permissive", "weak_copyleft", "strong_copyleft", "other", "unknown")
FAMILY_LABELS_KO = {
    PERMISSIVE: "허용형", WEAK: "약한 카피레프트", STRONG: "강한 카피레프트",
    OTHER: "기타", UNKNOWN_FAMILY: "알 수 없음",
}

# ScanCode 분류 -> 계열. 기타는 원래 분류를 함께 남긴다.
CATEGORY_TO_FAMILY = {
    "Permissive": PERMISSIVE,
    "Public Domain": PERMISSIVE,
    "Copyleft Limited": WEAK,
    "Copyleft": STRONG,
}
CATEGORY_LABELS_KO = {
    "Free Restricted": "사용·배포 제한 있음",
    "Non-Commercial": "상업적 사용 금지",
    "Source-available": "소스 공개형 (오픈소스 아님)",
    "Proprietary Free": "무료 독점 라이선스",
    "Commercial": "상업 라이선스",
    "Unstated License": "라이선스 없음 (저작권 표시만 있음)",
    "Patent License": "특허 라이선스",
    "CLA": "기여자 동의서 (라이선스 아님)",
}

# =============================================================================
# 폐기된 SPDX 식별자 중 ScanCode 데이터에 대응이 없는 것
# 옛 식별자는 원래 "그 버전만"을 뜻했으므로 -only 로 옮긴다.
# =============================================================================

DEPRECATED_MANUAL = {
    "eCos-2.0": "GPL-2.0-or-later WITH eCos-exception-2.0",
    "GPL-2.0-with-autoconf-exception": "GPL-2.0-only WITH Autoconf-exception-2.0",
    "GPL-2.0-with-bison-exception": "GPL-2.0-only WITH Bison-exception-2.2",
    "GPL-2.0-with-classpath-exception": "GPL-2.0-only WITH Classpath-exception-2.0",
    "GPL-2.0-with-font-exception": "GPL-2.0-only WITH Font-exception-2.0",
    "GPL-2.0-with-GCC-exception": "GPL-2.0-only WITH GCC-exception-2.0",
    "GPL-3.0-with-autoconf-exception": "GPL-3.0-only WITH Autoconf-exception-3.0",
    "GPL-3.0-with-GCC-exception": "GPL-3.0-only WITH GCC-exception-3.1",
    "wxWindows": "LGPL-2.0-or-later WITH WxWindows-exception-3.1",
}

# =============================================================================
# 버전 없는 이름: 버전을 추측하지 않고 계열만 확정한다.
#   이름 패턴(정규화 후 완전 일치), 계열, 충돌 판정 때 비교해 볼 버전 목록
# =============================================================================

VERSIONLESS_GROUPS = {
    "GPL": (STRONG, ("gpl", "gnu gpl", "gnu general public", "gnu public")),
    "AGPL": (STRONG, ("agpl", "gnu agpl", "gnu affero general public", "affero general public", "affero gpl")),
    "LGPL": (WEAK, ("lgpl", "gnu lgpl", "gnu lesser general public", "gnu library general public",
                    "gnu library or lesser general public")),
    "MPL": (WEAK, ("mpl", "mozilla public", "mozilla")),
    "EPL": (WEAK, ("epl", "eclipse public", "eclipse")),
    "CDDL": (WEAK, ("cddl", "common development and distribution")),
    "EUPL": (STRONG, ("eupl", "european union public")),
    "BSD": (PERMISSIVE, ("bsd", "bsd style", "bsd like", "bsd type", "berkeley software distribution")),
    "Apache": (PERMISSIVE, ("apache", "apache software", "asl")),
}
VERSIONLESS_VERSION_PATTERNS = {
    "GPL": r"^GPL-\d\.\d-(only|or-later)$",
    "AGPL": r"^AGPL-\d\.\d-(only|or-later)$",
    "LGPL": r"^LGPL-\d\.\d-(only|or-later)$",
    "MPL": r"^MPL-\d\.\d$",
    "EPL": r"^EPL-\d\.\d$",
    "CDDL": r"^CDDL-\d\.\d$",
    "EUPL": r"^EUPL-\d\.\d$",
    "BSD": r"^BSD-[1-4]-Clause$",
    "Apache": r"^Apache-\d\.\d$",
}

# =============================================================================
# 사람이 쓰는 표기 -> SPDX (SPDX 식별자·공식 이름·ScanCode 데이터로 안 잡히는 것만)
# 왼쪽은 정규화 전 문자열이며, 불러올 때 같은 규칙으로 정규화한다.
# 버전만 있고 only/or later 가 없으면 -only (SPDX 의 옛 식별자 의미와 같음)
# =============================================================================

MANUAL_ALIASES = {
    "MIT License": "MIT", "The MIT License": "MIT", "Expat": "MIT", "MIT/X11": "MIT",
    "Apache 2": "Apache-2.0", "Apache 2.0": "Apache-2.0", "Apache License 2.0": "Apache-2.0",
    "Apache License, Version 2.0": "Apache-2.0", "The Apache Software License, Version 2.0": "Apache-2.0",
    "Apache Software License 2.0": "Apache-2.0", "ASL 2.0": "Apache-2.0", "Apache-2": "Apache-2.0",
    "GPLv1": "GPL-1.0-only", "GPLv2": "GPL-2.0-only", "GPLv3": "GPL-3.0-only",
    "GPLv2+": "GPL-2.0-or-later", "GPLv3+": "GPL-3.0-or-later",
    "GNU GPL v2": "GPL-2.0-only", "GNU GPL v3": "GPL-3.0-only",
    "GNU General Public License v2": "GPL-2.0-only", "GNU General Public License v3": "GPL-3.0-only",
    "GNU General Public License v2 or later": "GPL-2.0-or-later",
    "GNU General Public License v3 or later": "GPL-3.0-or-later",
    "LGPLv2": "LGPL-2.0-only", "LGPLv2.1": "LGPL-2.1-only", "LGPLv3": "LGPL-3.0-only",
    "LGPLv2+": "LGPL-2.0-or-later", "LGPLv2.1+": "LGPL-2.1-or-later", "LGPLv3+": "LGPL-3.0-or-later",
    "GNU Lesser General Public License v2": "LGPL-2.0-only",
    "GNU Lesser General Public License v2.1": "LGPL-2.1-only",
    "GNU Lesser General Public License v3": "LGPL-3.0-only",
    "GNU Lesser General Public License v2 or later": "LGPL-2.0-or-later",
    "GNU Lesser General Public License v3 or later": "LGPL-3.0-or-later",
    "AGPLv3": "AGPL-3.0-only", "AGPLv3+": "AGPL-3.0-or-later",
    "GNU Affero General Public License v3": "AGPL-3.0-only",
    "GNU Affero General Public License v3 or later": "AGPL-3.0-or-later",
    "BSD 2-Clause": "BSD-2-Clause", "BSD-2": "BSD-2-Clause", "Simplified BSD": "BSD-2-Clause",
    "FreeBSD License": "BSD-2-Clause", "BSD 3-Clause": "BSD-3-Clause", "BSD-3": "BSD-3-Clause",
    "New BSD": "BSD-3-Clause", "Modified BSD": "BSD-3-Clause", "Revised BSD": "BSD-3-Clause",
    "BSD 4-Clause": "BSD-4-Clause", "Original BSD": "BSD-4-Clause",
    "MPL 2.0": "MPL-2.0", "MPLv2": "MPL-2.0", "Mozilla Public License 2.0": "MPL-2.0",
    "EPL 2.0": "EPL-2.0", "Eclipse Public License 2.0": "EPL-2.0", "EPL 1.0": "EPL-1.0",
    "ISC License": "ISC", "ISCL": "ISC",
    "Python Software Foundation License": "PSF-2.0", "PSF": "PSF-2.0", "PSFL": "PSF-2.0",
    "The Unlicense": "Unlicense", "CC0": "CC0-1.0", "Boost": "BSL-1.0",
    "Boost Software License": "BSL-1.0", "zlib/libpng": "Zlib", "WTFPL": "WTFPL",
    "Public Domain": "LicenseRef-scancode-public-domain",
    "Proprietary": "LicenseRef-scancode-proprietary-license",
    "All rights reserved": "LicenseRef-scancode-proprietary-license",
    "Commercial": "LicenseRef-scancode-commercial-license",
}

# 판정 근거가 없다는 뜻의 값
UNKNOWN_VALUES = {"", "unknown", "noassertion", "none", "n/a", "na", "not specified", "unspecified",
                  "no license", "nolicense", "null"}
NON_STANDARD_VALUES = {"non-standard", "nonstandard"}
# npm 의 "UNLICENSED" 는 Unlicense 가 아니라 "사용 허락 없음(독점)" 이다
UNLICENSED_VALUES = {"unlicensed"}

STATUS_OK = "ok"                    # SPDX 이름(또는 표현식)으로 정리됨
STATUS_VERSIONLESS = "versionless"  # 버전 없는 이름: 계열만 확정
STATUS_UNKNOWN = "unknown"          # 근거 없음 (Unknown, NOASSERTION 등)
STATUS_NON_STANDARD = "non_standard"  # deps.dev 의 non-standard: 내용 미상
STATUS_UNRECOGNIZED = "unrecognized"  # 어떤 규칙으로도 정리되지 않은 문자열

_SUFFIX_RE = re.compile(r"-(only|or-later)$")
_LEAD_RE = re.compile(
    r"^\s*(this\s+(file|project|software|code)\s+is\s+)?(dual[\s-]*licen[sc]ed|licen[sc]ed|released|"
    r"distributed|available|made available)\s+(under|with)\s+(the\s+)?(terms\s+of\s+(the\s+)?)?",
    re.IGNORECASE)


def _strip_lead(text: str) -> str:
    """'Licensed under the MIT License' -> 'MIT License' 처럼 앞의 안내 문구를 뗀다."""
    return _LEAD_RE.sub("", str(text or "")).strip().rstrip(".")


def norm_text(s: str) -> str:
    """별칭 비교용 정규화. 'GNU General Public License v3 (GPLv3)' -> 'gnu general public 3.0'"""
    s = str(s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)                      # 괄호 속 약칭 제거
    s = s.replace("_", " ").replace("/", " ")
    s = re.sub(r"or\s+(any\s+)?later(\s+version)?", " + ", s)
    s = re.sub(r"[\"'`,;:!?]", " ", s)
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)          # 글자 사이 하이픈
    s = re.sub(r"-", " ", s)
    s = re.sub(r"\b(the|license|licence|licensed|version|ver)\b", " ", s)
    s = re.sub(r"([a-z])v(\d)", r"\1 \2", s)            # gplv3 -> gpl 3
    s = re.sub(r"\bv(\d)", r"\1", s)                    # v2.0 -> 2.0
    s = re.sub(r"(\d)\+", r"\1 +", s)                   # 3+ -> 3 +
    s = re.sub(r"\b(\d+)\b(?!\.\d)(?!\s*clause)", r"\1.0", s)   # 3 -> 3.0 (3-clause 제외)
    s = re.sub(r"\s+", " ", s).strip()
    return s


class LicenseCatalog:
    def __init__(self, refs_dir: Path = REFS_DIR):
        self.refs_dir = Path(refs_dir)
        self.loaded = False
        self.error = None
        self._texts = None
        self._exception_texts = None
        try:
            self._load()
            self.loaded = True
        except FileNotFoundError as e:
            self.error = f"참조 데이터 없음 ({e.filename}) - license_refs/ 폴더 확인 필요"
        except Exception as e:                 # 데이터가 깨진 경우에도 엔진은 켜지도록
            self.error = f"참조 데이터 읽기 실패: {e}"

    # -------------------------------------------------------------------------
    # 불러오기
    # -------------------------------------------------------------------------
    def _read(self, rel):
        with open(self.refs_dir / rel, encoding="utf-8") as f:
            return json.load(f)

    def _load(self):
        spdx = self._read("spdx/licenses.json")
        exc = self._read("spdx/exceptions.json")
        index = self._read("scancode/index.json")
        self.meta = self._read("meta.json") if (self.refs_dir / "meta.json").exists() else {}

        self.ids = {}                # 소문자 -> 정식 표기 (현재 + 폐기)
        self.deprecated = set()
        self.names = {}              # 정식 표기 -> SPDX 이름
        for l in spdx["licenses"]:
            self.ids[l["licenseId"].lower()] = l["licenseId"]
            self.names[l["licenseId"]] = l.get("name", "")
            if l.get("isDeprecatedLicenseId"):
                self.deprecated.add(l["licenseId"])
        self.exceptions = {e["licenseExceptionId"].lower(): e["licenseExceptionId"]
                           for e in exc["exceptions"]}
        self.spdx_version = spdx.get("licenseListVersion")

        # ScanCode: SPDX 식별자 -> 분류. 같은 식별자가 여럿이면 폐기되지 않은 항목 우선
        self.scancode = {}
        self.licenserefs = {}
        other_keys = {}
        for e in index:
            key = e.get("spdx_license_key")
            if not key:
                continue
            prev = self.scancode.get(key)
            if prev is None or (prev.get("is_deprecated") and not e.get("is_deprecated")):
                self.scancode[key] = e
            if key.startswith("LicenseRef-"):
                self.licenserefs[key.lower()] = key
            for ok in e.get("other_spdx_license_keys") or []:
                other_keys.setdefault(ok, set()).add(key)

        # 폐기된 식별자 -> 현재 표기
        self.deprecated_map = {}
        for d in self.deprecated:
            targets = {t for t in other_keys.get(d, set()) if t.lower() in self.ids or t in self.scancode}
            if len(targets) == 1:
                self.deprecated_map[d] = targets.pop()
        for d, target in DEPRECATED_MANUAL.items():
            if d in self.deprecated and d not in self.deprecated_map and self._expr_known(target):
                self.deprecated_map[d] = target

        # 표현식 해석기: 현재 SPDX 식별자 + 예외 + ScanCode 의 LicenseRef
        symbols = [LicenseSymbol(i) for i in self.ids.values() if i not in self.deprecated]
        symbols += [LicenseSymbol(i, is_exception=True) for i in self.exceptions.values()]
        symbols += [LicenseSymbol(i) for i in self.licenserefs.values()]
        self.licensing = Licensing(symbols)

        # 버전 없는 이름 그룹의 버전 목록
        current = [i for i in self.ids.values() if i not in self.deprecated]
        self.versionless_versions = {
            g: sorted(i for i in current if re.match(p, i)) for g, p in VERSIONLESS_VERSION_PATTERNS.items()}

        self._build_aliases(index, other_keys)

    def _expr_known(self, expr: str) -> bool:
        for tok in re.findall(r"[A-Za-z0-9.\-+]+", expr):
            if tok.upper() in ("AND", "OR", "WITH"):
                continue
            if tok.lower() not in self.ids and tok.lower() not in self.exceptions:
                return False
        return True

    def _build_aliases(self, index, other_keys):
        """정규화한 이름 -> 대상. 서로 다른 대상과 겹치는 이름은 쓰지 않는다 (모호함)."""
        cand = {}

        def add(text, target):
            k = norm_text(text)
            if k:
                cand.setdefault(k, set()).add(target)

        for low, canonical in self.ids.items():
            target = self.deprecated_map.get(canonical, canonical)
            add(canonical, target)
            if self.names.get(canonical):
                add(self.names[canonical], target)
        for alias, ids in other_keys.items():
            if alias.startswith("LicenseRef-") or len(ids) != 1:
                continue
            add(alias, next(iter(ids)))
        spdx_norms = {norm_text(i) for i in self.ids.values()}
        for e in index:
            key, lk = e.get("spdx_license_key"), e.get("license_key")
            if key and lk and not e.get("is_deprecated") and norm_text(lk) not in spdx_norms:
                add(lk, key)
        for text, target in MANUAL_ALIASES.items():
            add(text, target)

        self.aliases = {}
        self.ambiguous_aliases = {}
        for k, targets in cand.items():
            if len(targets) == 1:
                self.aliases[k] = next(iter(targets))
            else:
                self.ambiguous_aliases[k] = sorted(targets)
        # 수동 별칭은 모호해도 우선 적용 (사람이 확인한 대응)
        for text, target in MANUAL_ALIASES.items():
            self.aliases[norm_text(text)] = target

        self.versionless = {}
        for group, (family, patterns) in VERSIONLESS_GROUPS.items():
            for p in patterns:
                self.versionless[norm_text(p)] = group

    # -------------------------------------------------------------------------
    # 정규화
    # -------------------------------------------------------------------------
    def _result(self, raw, status, spdx=None, tree=None, note="", versionless=None, converted=None):
        ids, excs = [], []
        if tree is not None:
            self._collect(tree, ids, excs)
        if status == STATUS_OK:
            family = self._tree_family(tree)
        elif status == STATUS_VERSIONLESS:
            family = VERSIONLESS_GROUPS[versionless][0]
        else:
            family = UNKNOWN_FAMILY
        return {
            "input": raw,
            "status": status,
            "spdx": spdx,
            "family": family,
            "family_ko": FAMILY_LABELS_KO[family],
            "ids": ids,
            "exceptions": excs,
            "categories": sorted({c for c in (self.category(i) for i in ids) if c}),
            "versionless": versionless,
            "versions": self.versionless_versions.get(versionless, []) if versionless else [],
            "converted": converted or [],
            "note": note,
        }

    def normalize(self, raw) -> dict:
        return dict(self._normalize_cached(str(raw if raw is not None else "")))

    @lru_cache(maxsize=20000)
    def _normalize_cached(self, raw: str):
        return tuple(self._normalize(raw).items())

    def _normalize(self, raw: str) -> dict:
        text = raw.strip()
        low = text.lower()
        if not self.loaded:
            return self._result(raw, STATUS_UNRECOGNIZED, note=self.error or "참조 데이터 없음")
        if low in UNKNOWN_VALUES:
            return self._result(raw, STATUS_UNKNOWN)
        if low in NON_STANDARD_VALUES:
            return self._result(raw, STATUS_NON_STANDARD, note="SPDX 에 없는 라이선스 (내용 미상)")
        if low in UNLICENSED_VALUES:
            return self._parse_ok(raw, "LicenseRef-scancode-proprietary-license",
                                  note="UNLICENSED: 사용 허락 없음 (독점)")

        # 1) SPDX 식별자·표현식으로 해석 (폐기 표기 변환 포함)
        expr, converted = self._convert_deprecated(text)
        parsed = self._try_parse(expr)
        if parsed is not None:
            return self._result(raw, STATUS_OK, spdx=str(parsed), tree=self._tree(parsed),
                                converted=converted)

        # 2) 사람이 쓰는 이름 (별칭, 버전 없는 이름)
        hit = self._lookup_name(text)
        if hit is not None:
            return hit

        # 3) 자연어로 쓴 "A or B", "A and B"
        parts = re.split(r"\s+(or|and)\s+|\s*/\s*|\s*;\s*", _strip_lead(text), flags=re.IGNORECASE)
        if len(parts) >= 3:
            names = parts[0::2]
            ops = [p.upper() if p else "OR" for p in parts[1::2]]
            if re.search(r"dual", text, re.IGNORECASE):
                ops = ["OR"] * len(ops)         # 이중 라이선스는 영어로 and 라고 써도 OR
            resolved = []
            for n in names:
                r = self._lookup_name(n.strip()) or self._result(n, STATUS_UNRECOGNIZED)
                if r["status"] != STATUS_OK:
                    break
                resolved.append(r["spdx"] if " " not in r["spdx"] else f"({r['spdx']})")
            else:
                joined = resolved[0]
                for op, nxt in zip(ops, resolved[1:]):
                    joined = f"{joined} {op} {nxt}"
                parsed = self._try_parse(joined)
                if parsed is not None:
                    return self._result(raw, STATUS_OK, spdx=str(parsed), tree=self._tree(parsed),
                                        note="자연어 표현을 표현식으로 변환")

        return self._result(raw, STATUS_UNRECOGNIZED, note="정리되지 않은 라이선스 표기")

    def _parse_ok(self, raw, spdx, note=""):
        parsed = self._try_parse(spdx)
        if parsed is None:
            return self._result(raw, STATUS_UNRECOGNIZED, note=note)
        return self._result(raw, STATUS_OK, spdx=str(parsed), tree=self._tree(parsed), note=note)

    def _lookup_name(self, text):
        k = norm_text(_strip_lead(text))
        if not k:
            return None
        # 버전 없는 이름을 먼저 본다. ScanCode 는 "GPL" 을 GPL-1.0-or-later 로 보지만,
        # 버전을 추측하지 않기로 했으므로 계열만 확정한다.
        if k in self.versionless:
            return self._result(text, STATUS_VERSIONLESS, versionless=self.versionless[k],
                                note="버전 미상: 계열만 확정")
        if k in self.aliases:
            target = self.aliases[k]
            expr, converted = self._convert_deprecated(target)
            parsed = self._try_parse(expr)
            if parsed is not None:
                return self._result(text, STATUS_OK, spdx=str(parsed), tree=self._tree(parsed),
                                    converted=converted, note="별칭으로 변환" if target != text else "")
        return None

    def _convert_deprecated(self, expr: str):
        """식 안의 폐기된 식별자와 '+' 표기를 현재 표기로 바꾼다."""
        converted = []

        def sub(m):
            tok = m.group(0)
            if tok.upper() in ("AND", "OR", "WITH"):
                return tok
            canonical = self.ids.get(tok.lower())
            if canonical in self.deprecated_map:
                new = self.deprecated_map[canonical]
                converted.append((tok, new))
                return f"({new})" if " " in new else new
            if tok.endswith("+") and len(tok) > 1:
                base = self.ids.get(tok[:-1].lower())
                if base:
                    later = self.ids.get((base + "-or-later").lower())
                    new = later or base
                    converted.append((tok, new))
                    return new
            return tok

        out = re.sub(r"(?<![\w.\-])[A-Za-z0-9][A-Za-z0-9.\-]*\+?(?![\w.\-])", sub, expr)
        return out, converted

    def _try_parse(self, expr):
        if not expr or not re.search(r"[A-Za-z0-9]", expr):
            return None
        try:
            parsed = self.licensing.parse(expr, validate=True)
            return parsed
        except (ExpressionError, Exception):
            return None

    # -------------------------------------------------------------------------
    # 표현식 구조
    # -------------------------------------------------------------------------
    def _tree(self, node):
        if isinstance(node, LicenseWithExceptionSymbol):
            return ("WITH", node.license_symbol.key, node.exception_symbol.key)
        if isinstance(node, LicenseSymbol):
            return ("ID", node.key)
        op = node.operator.strip().upper()
        return (op, [self._tree(a) for a in node.args])

    def tree(self, spdx_expr: str):
        """정리된 SPDX 표현식의 구조. ("ID", x) / ("WITH", x, 예외) / ("AND"|"OR", [자식])"""
        parsed = self._try_parse(spdx_expr)
        return self._tree(parsed) if parsed is not None else None

    def _collect(self, t, ids, excs):
        kind = t[0]
        if kind == "ID":
            ids.append(t[1])
        elif kind == "WITH":
            ids.append(t[1])
            excs.append(t[2])
        else:
            for c in t[1]:
                self._collect(c, ids, excs)

    # -------------------------------------------------------------------------
    # 계열
    # -------------------------------------------------------------------------
    def category(self, spdx_id: str):
        e = self.scancode.get(spdx_id)
        return e.get("category") if e else None

    def family_of(self, spdx_id: str) -> str:
        cat = self.category(spdx_id)
        if cat is None:
            return UNKNOWN_FAMILY
        return CATEGORY_TO_FAMILY.get(cat, OTHER)

    def _tree_family(self, t) -> str:
        kind = t[0]
        if kind == "ID":
            return self.family_of(t[1])
        if kind == "WITH":
            return self.family_of(t[1])
        fams = [self._tree_family(c) for c in t[1]]
        if kind == "AND":                       # 모두 적용: 가장 엄격한 쪽
            for f in (STRONG, WEAK, OTHER, UNKNOWN_FAMILY):
                if f in fams:
                    return f
            return PERMISSIVE
        for f in (PERMISSIVE, WEAK, STRONG, OTHER):   # OR: 고를 수 있으므로 가장 느슨한 쪽
            if f in fams:
                return f
        return UNKNOWN_FAMILY

    def other_reason(self, spdx_id: str) -> str:
        """기타 계열일 때 화면·사유에 쓸 원래 분류 설명."""
        cat = self.category(spdx_id)
        return CATEGORY_LABELS_KO.get(cat, cat or "")

    # -------------------------------------------------------------------------
    # 채점용 키
    # -------------------------------------------------------------------------
    def _key_of_tree(self, t, detail: bool) -> str:
        kind = t[0]
        if kind == "ID":
            return t[1] if detail else _SUFFIX_RE.sub("", t[1])
        if kind == "WITH":
            base = t[1] if detail else _SUFFIX_RE.sub("", t[1])
            return f"{base} WITH {t[2]}"
        parts = sorted(self._key_of_tree(c, detail) for c in t[1])
        parts = [f"({p})" if (" AND " in p or " OR " in p) else p for p in parts]
        return f" {kind} ".join(parts)

    def scoring_key(self, raw, detail: bool = False):
        """
        채점 비교용 키. 순서만 다른 표현식은 같은 키가 된다.
          detail=False : 기본 정확도 (-only / -or-later 를 같게 취급)
          detail=True  : 세부 정확도 (구분)
        반환: (키, 상태)  상태가 versionless 면 키는 "VERSIONLESS:<계열 이름>"
        """
        r = self.normalize(raw)
        if r["status"] == STATUS_OK:
            t = self.tree(r["spdx"])
            return self._key_of_tree(t, detail), r["status"]
        if r["status"] == STATUS_VERSIONLESS:
            return f"VERSIONLESS:{r['versionless']}", r["status"]
        if r["status"] == STATUS_UNKNOWN:
            return "UNKNOWN", r["status"]
        return f"RAW:{norm_text(raw)}", r["status"]

    def has_explicit_suffix(self, raw) -> bool:
        """-only / -or-later 가 명시적으로 적혀 있는지 (세부 정확도 채점 대상 여부)."""
        return bool(re.search(r"-(only|or-later)\b|\+", str(raw or ""), re.IGNORECASE))

    # -------------------------------------------------------------------------
    # 원문
    # -------------------------------------------------------------------------
    def _texts_db(self):
        if self._texts is None:
            self._texts = self._read("spdx/license_texts.json")
        return self._texts

    def text(self, spdx_id: str) -> str:
        return (self._texts_db().get(spdx_id) or {}).get("text", "")

    def template(self, spdx_id: str) -> str:
        return (self._texts_db().get(spdx_id) or {}).get("template", "")

    def header(self, spdx_id: str) -> str:
        return (self._texts_db().get(spdx_id) or {}).get("header", "")

    def all_text_ids(self):
        return [i for i in self._texts_db() if i not in self.deprecated]

    @staticmethod
    def _first_sentence(text: str) -> str:
        """제목·저작권 줄을 건너뛴 첫 문장 (근거 문장 확인용)."""
        lines = []
        for line in (text or "").splitlines():
            s = line.strip()
            if not s:
                if lines:
                    break
                continue
            if re.match(r"^(copyright|\(c\)|©)", s, re.IGNORECASE):
                continue
            if not lines and len(s) < 60 and not s.endswith("."):
                continue                       # 제목 줄 (예: "MIT License")
            lines.append(s)
        joined = " ".join(lines)
        m = re.match(r"(.+?[.;:])(\s|$)", joined)
        return (m.group(1) if m else joined)[:300].strip()

    def signature_phrases(self, spdx_id: str) -> list:
        """
        근거 문장이 이 라이선스를 가리키는지 확인할 때 쓰는 문구 목록.
        식별자, SPDX 이름, 별칭, 표준 원문 첫 문장, 표준 헤더 첫 문장.
        """
        phrases = {spdx_id, self.names.get(spdx_id, "")}
        base = _SUFFIX_RE.sub("", spdx_id)
        phrases.add(base)
        for k, target in self.aliases.items():
            if target == spdx_id and len(k) >= 3:
                phrases.add(k)
        phrases.add(self._first_sentence(self.text(spdx_id)))
        phrases.add(self._first_sentence(self.header(spdx_id)))
        return sorted(p for p in phrases if p and len(p) >= 2)

    # -------------------------------------------------------------------------
    # 기타
    # -------------------------------------------------------------------------
    def choices(self) -> list:
        """LLM 선택지 목록 (license_refs/license_choices.json). 없으면 빈 목록."""
        path = self.refs_dir / "license_choices.json"
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("choices", [])

    def data_versions(self) -> dict:
        m = getattr(self, "meta", {}) or {}
        sc = (m.get("scancode") or {}).get("generated") or {}
        return {
            "spdx_license_list": (m.get("spdx") or {}).get("version"),
            "scancode_licensedb": sc.get("generated_on") or (m.get("scancode") or {}).get("downloaded_at"),
            "osadl_matrix": (m.get("osadl") or {}).get("timestamp"),
        }

    def status(self) -> dict:
        return {
            "loaded": self.loaded,
            "error": self.error,
            "refs_dir": str(self.refs_dir),
            "spdx_version": getattr(self, "spdx_version", None),
            "n_ids": len(getattr(self, "ids", {})),
            "n_aliases": len(getattr(self, "aliases", {})),
            "n_deprecated_mapped": len(getattr(self, "deprecated_map", {})),
            "data_versions": self.data_versions() if self.loaded else {},
        }


CATALOG = LicenseCatalog()


if __name__ == "__main__":
    if not CATALOG.loaded:
        sys.exit(f"[오류] {CATALOG.error}")
    st = CATALOG.status()
    print(f"SPDX {st['spdx_version']}, 식별자 {st['n_ids']}개, 별칭 {st['n_aliases']}개, "
          f"폐기 식별자 대응 {st['n_deprecated_mapped']}개")
    for arg in sys.argv[1:] or ["GPLv3", "MIT OR Apache-2.0", "BSD License", "GPL-2.0+"]:
        r = CATALOG.normalize(arg)
        print(f"\n{arg!r}")
        print(f"  상태 {r['status']}, SPDX {r['spdx']}, 계열 {r['family_ko']}"
              + (f", 버전 미상 그룹 {r['versionless']} ({len(r['versions'])}개 버전)" if r["versionless"] else ""))
        if r["converted"]:
            print(f"  변환 {r['converted']}")
        if r["note"]:
            print(f"  비고 {r['note']}")
