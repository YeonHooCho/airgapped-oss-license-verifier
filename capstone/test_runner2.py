# -*- coding: utf-8 -*-
"""
test_runner2.py - dataset3 (프로젝트 단위) 라이선스 판별 성능 측정

개선 사항:
- 2-Pass 상속 엔진 응답 규격 수용 (root_project_license, inherited_license)
- 지표 3원화 집계 (Track 1: 루트 라이선스 식별율, Track 2: 가드레일 제어율, Track 3: 충돌 탐지율)
- 상속 파일의 환각 오판 방지 (원본 AI 추론 기준 환각 검증)
- OSADL 호환성 매트릭스 결과 연동 및 보고서 출력
"""

import argparse
import csv
import io
import json
import platform
import re
import statistics
import subprocess
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import requests

import code_window
from license_catalog import (CATALOG, FAMILY_LABELS_KO, PERMISSIVE, STATUS_OK, STATUS_VERSIONLESS,
                             STRONG, WEAK)

# =============================================================================
# 경로 / 기본 설정
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset3"
LABEL_DIR = DATASET_DIR / "labels"
SOURCE_DIR = DATASET_DIR / "source_codes"
RUNS_DIR = DATASET_DIR / "runs"
ENGINE_URL = "http://localhost:8000"

# =============================================================================
# 엔진(main.py) 설정과 맞추기 - Ollama 주소/모델, 상단 줄 수, 파일 판별 규칙
# =============================================================================

import os as _os
_os.environ.setdefault("CAPSTONE_QUIET", "1")   # 엔진 서버 안내 문구 생략
try:
    import main as engine
    OLLAMA_GENERATE_URL = engine.OLLAMA_URL
    OLLAMA_MODEL = engine.OLLAMA_MODEL
    ENGINE_OLLAMA_TIMEOUT = engine.OLLAMA_TIMEOUT
    TOP_LINES = engine.MAX_TOP_LINES
    detect_language = engine.detect_language
    ANALYZABLE = set(engine.ANALYZABLE_LANGUAGES)
    IGNORE_DIRS = set(engine.IGNORE_DIR_PARTS)
    IGNORE_FILES = set(engine.IGNORE_FILE_NAMES)
    ENGINE_LLM_OPTIONS = engine.llm_options() if hasattr(engine, "llm_options") else {}
except Exception as _e:
    print(f"[참고] main.py 를 불러오지 못해 기본값을 사용합니다 ({_e})")
    ENGINE_LLM_OPTIONS = {}
    OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
    OLLAMA_MODEL = "llama3"
    ENGINE_OLLAMA_TIMEOUT = 120
    TOP_LINES = 50
    _EXT = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
            ".java": "Java", ".c": "C", ".h": "C"}
    _SPECIAL = {"pom.xml": "Java-Maven", "build.gradle": "Java-Gradle",
                "package.json": "JavaScript-Manifest", "requirements.txt": "Python-Manifest",
                "license": "License-Text", "license.txt": "License-Text",
                "license.md": "License-Text", "copying": "License-Text"}

    def detect_language(path):
        base = path.rsplit("/", 1)[-1].lower()
        if base in _SPECIAL:
            return _SPECIAL[base]
        ext = "." + base.rsplit(".", 1)[-1] if "." in base else ""
        return _EXT.get(ext, "Unknown")

    ANALYZABLE = set(_EXT.values()) | set(_SPECIAL.values())
    IGNORE_DIRS = {"venv", "node_modules", ".git", "__macosx", "build", ".gradle",
                   ".idea", "dist", "__pycache__", ".settings", "target", "out"}
    IGNORE_FILES = {".ds_store"}

OLLAMA_BASE = OLLAMA_GENERATE_URL.split("/api/")[0]

SOURCE_LANGS = {"Python", "JavaScript", "Java", "C", "C++"}
MANIFEST_LANGS = {"Java-Maven", "Java-Gradle", "Python-Manifest", "JavaScript-Manifest"}
LICENSE_LANG = "License-Text"

# =============================================================================
# 라이선스 이름 정규화 / 계열 분류
# =============================================================================

UNKNOWN = "UNKNOWN"


def key_basic(raw):
    """기본 정확도용 키: -only/-or-later 같게, 표현식 순서 무시."""
    return CATALOG.scoring_key(raw)[0]


def key_detail(raw):
    """세부 정확도용 키: -only/-or-later 구분."""
    return CATALOG.scoring_key(raw, detail=True)[0]


def detail_eligible(gt_raw):
    """세부 정확도 채점 대상 여부."""
    return CATALOG.has_explicit_suffix(gt_raw)


def pretty(raw):
    """보고서용 표기."""
    if raw is None or raw == "-" or raw == "":
        return "-"
    r = CATALOG.normalize(raw)
    if r["status"] == STATUS_OK:
        return r["spdx"]
    if r["status"] == STATUS_VERSIONLESS:
        return f"{r['versionless']}(버전 미상)"
    if r["status"] == "unknown":
        return "Unknown"
    return str(raw).strip()[:60] or "-"


def family(raw):
    return CATALOG.normalize(raw)["family"]


COMPARABLE_FAMILIES = (PERMISSIVE, WEAK, STRONG)


def spdx_format_ok(raw):
    raw = (raw or "").strip()
    if raw == "Unknown":
        return True
    r = CATALOG.normalize(raw)
    return r["status"] == STATUS_OK and r["note"] == ""


HINT_RULES = {
    "v2": re.compile(
        r"licen[cs]e|copyright|\(c\)\s*(?:\d{4}|[A-Z][a-z])|©|spdx|permission is hereby|warranty|"
        r"\bgnu\s+(?:general|lesser|library|affero|gpl|lgpl|agpl)|\b[al]?gpl\b|"
        r"apache\s+(?:license|software\s+license|2|-2)|\bmit\b|\bbsd\b|"
        r"mozilla\s+public|public domain|unlicense", re.IGNORECASE),
    "legacy": re.compile(
        r"licen[cs]e|copyright|\(c\)|©|spdx|permission is hereby|warranty|gnu|gpl|apache|"
        r"\bmit\b|bsd|mozilla|public domain|unlicense", re.IGNORECASE),
}
HINT_RULE = "v2"
LICENSE_HINT_RE = HINT_RULES[HINT_RULE]


def set_hint_rule(name):
    global HINT_RULE, LICENSE_HINT_RE
    HINT_RULE, LICENSE_HINT_RE = name, HINT_RULES[name]


CODE_EXTS = {".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java", ".kt", ".scala",
             ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".go", ".rs", ".rb", ".php",
             ".cs", ".swift"}
PY_STDLIB_NAMES = set(getattr(sys, "stdlib_module_names", ()))

# =============================================================================
# 데이터셋 로더 / 헬퍼
# =============================================================================

def find_source_folder(sid):
    candidates = [sid]
    m = re.match(r"^(\D+?)_?(\d+)$", sid)
    if m:
        candidates += [f"{m.group(1)}{m.group(2)}", f"{m.group(1)}_{m.group(2)}"]
    for name in candidates:
        if (SOURCE_DIR / name).is_dir():
            return SOURCE_DIR / name
    return None


def load_samples():
    samples = []
    for path in sorted(LABEL_DIR.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            label = json.load(f)
        sid = path.stem
        folder = find_source_folder(sid)
        if folder is None:
            print(f"[경고] {sid}: 소스 폴더가 없어 제외합니다.")
            continue
        samples.append({"sid": sid, "label": label, "folder": folder})
    return samples


def local_analyzable_files(folder):
    out = []
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        parts = rel.lower().split("/")
        if any(x in IGNORE_DIRS for x in parts) or parts[-1] in IGNORE_FILES:
            continue
        if detect_language(rel) in ANALYZABLE:
            out.append(rel)
    return out


def local_code_files(folder):
    out = []
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in CODE_EXTS:
            rel = p.relative_to(folder).as_posix()
            if not any(x in IGNORE_DIRS for x in rel.lower().split("/")):
                out.append(rel)
    return out


def local_names(folder):
    py_modules, headers = set(), set()
    for p in folder.rglob("*"):
        if p.is_dir():
            py_modules.add(p.name)
        elif p.suffix == ".py":
            py_modules.add(p.stem)
        elif p.suffix.lower() in (".h", ".hpp", ".hh"):
            headers.add(p.name)
    return py_modules, headers


def zip_folder(folder):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(folder).as_posix())
    return buf.getvalue()


def read_top_lines(folder, rel):
    return code_window.window_from_path(folder / rel)

# =============================================================================
# 사전 점검
# =============================================================================

def preflight(engine_url, allow_no_treesitter=False):
    print("=" * 72)
    print("사전 점검")
    print("=" * 72)
    ok = True
    health = {}

    try:
        tags = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5).json()
        names = [m.get("name", "") for m in tags.get("models", [])]
        if any(n == OLLAMA_MODEL or n.startswith(OLLAMA_MODEL + ":") for n in names):
            print(f"  [OK] Ollama 실행 중, 모델 '{OLLAMA_MODEL}' 있음 ({OLLAMA_BASE})")
        else:
            print(f"  [X ] Ollama 는 실행 중이지만 '{OLLAMA_MODEL}' 모델이 없습니다.")
            print(f"       -> 터미널에서: ollama pull {OLLAMA_MODEL}")
            ok = False
    except Exception:
        print(f"  [X ] Ollama 에 연결할 수 없습니다 ({OLLAMA_BASE}).")
        ok = False

    if ok:
        t0 = time.perf_counter()
        warm_opts = {**ENGINE_LLM_OPTIONS, "num_predict": 1}
        try:
            requests.post(OLLAMA_GENERATE_URL, json={
                "model": OLLAMA_MODEL, "prompt": "ping", "stream": False,
                "options": warm_opts}, timeout=600).raise_for_status()
            print(f"  [OK] 모델 예열 완료 ({time.perf_counter() - t0:.1f}초, "
                  f"num_ctx {warm_opts.get('num_ctx', '기본값')})")
        except Exception as e:
            print(f"  [X ] 모델 예열 실패: {e}")
            ok = False

    try:
        health = requests.get(f"{engine_url}/health", timeout=5).json()
        if health.get("tree_sitter"):
            print(f"  [OK] 분석 엔진 응답 ({engine_url}, tree-sitter 사용)")
        elif allow_no_treesitter:
            print(f"  [!!] 분석 엔진 응답 ({engine_url}), tree-sitter 미사용 - --allow-no-treesitter 로 계속")
        else:
            print(f"  [X ] 분석 엔진이 tree-sitter 없이 동작 중입니다 (정규식 방식으로 바뀜).")
            print("       -> pip install -r requirements.txt 후 엔진을 다시 실행하세요.")
            ok = False
        hz = health.get("harness")
        if hz is not None:
            print(f"  [OK] 하네스 스위치: {', '.join(hz.get('enabled') or []) or '모두 꺼짐'}")
        opts = (health.get("llm") or {}).get("options")
        if opts:
            print(f"  [OK] 엔진 LLM 옵션 {opts}")
    except Exception:
        print(f"  [X ] 분석 엔진에 연결할 수 없습니다 ({engine_url}).")
        ok = False

    print()
    return ok, health


def _safe_get(url):
    try:
        return requests.get(url, timeout=5).json()
    except Exception as e:
        return {"error": str(e)}


def _git_info():
    def run(cmd):
        try:
            out = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=5)
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None
    commit = run(["git", "rev-parse", "--short", "HEAD"])
    status = run(["git", "status", "--porcelain"])
    return {"commit": commit, "uncommitted_changes": bool(status) if status is not None else None}


def ollama_snapshot():
    snap = {"version": _safe_get(f"{OLLAMA_BASE}/api/version").get("version")}
    tags = _safe_get(f"{OLLAMA_BASE}/api/tags")
    for m in tags.get("models", []) if isinstance(tags, dict) else []:
        if m.get("name") == OLLAMA_MODEL or m.get("model") == OLLAMA_MODEL:
            digest = m.get("digest", "")
            snap["model"] = {"name": m.get("name"), "id": digest[:12], "digest": digest,
                             "details": m.get("details")}
            break
    snap["loaded"] = ollama_ps()
    return snap


def ollama_ps():
    ps = _safe_get(f"{OLLAMA_BASE}/api/ps")
    loaded = []
    for m in ps.get("models", []) if isinstance(ps, dict) else []:
        size, vram = m.get("size") or 0, m.get("size_vram") or 0
        item = {"name": m.get("name"), "size_gb": round(size / 1024 ** 3, 2),
                "gpu_ratio": round(vram / size, 3) if size else None}
        for k in ("context_length", "expires_at"):
            if k in m:
                item[k] = m[k]
        loaded.append(item)
    return loaded


def record_conditions(run_dir, args, health, phase, session=None):
    path = run_dir / "run_conditions.json"
    data = {"sessions": []}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if phase == "start":
        session = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "args": {k: v for k, v in vars(args).items()},
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "git": _git_info(),
            "engine": {
                "tree_sitter": health.get("tree_sitter"),
                "harness": health.get("harness"),
                "llm": health.get("llm"),
                "offline_db": {k: health.get("offline_db", {}).get(k)
                               for k in ("format", "registries", "meta")},
            },
            "ollama": ollama_snapshot(),
        }
        data["sessions"].append(session)
    else:
        session = data["sessions"][-1] if data["sessions"] else {}
        session["finished_at"] = datetime.now().isoformat(timespec="seconds")
        session["ollama_loaded_at_end"] = ollama_ps()

    run_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return session

# =============================================================================
# 실행 (엔진 호출 + 원본 저장)
# =============================================================================

def run_samples(samples, raw_dir, engine_url, timeout, rerun):
    raw_dir.mkdir(parents=True, exist_ok=True)
    total = len(samples)
    for i, s in enumerate(samples, start=1):
        sid, label, folder = s["sid"], s["label"], s["folder"]
        raw_path = raw_dir / f"{sid}.json"
        head = f"[{i:02d}/{total:02d}] {sid} ({label.get('primary_language', '?')}, {label.get('ground_truth')})"
        if raw_path.exists() and not rerun:
            print(f"{head} 이미 결과 있음 - 건너뜀")
            continue

        n_local = len(local_analyzable_files(folder))
        req_timeout = max(timeout, n_local * ENGINE_OLLAMA_TIMEOUT + 60)
        print(f"{head} 분석 대상 {n_local}개 전송 ...", end=" ", flush=True)

        record = {"sample_id": sid, "started_at": datetime.now().isoformat(timespec="seconds")}
        t0 = time.perf_counter()
        try:
            resp = requests.post(f"{engine_url}/analyze",
                                 files={"file": (f"{sid}.zip", zip_folder(folder), "application/zip")},
                                 timeout=req_timeout)
            record["http_status"] = resp.status_code
            record["response"] = resp.json() if resp.ok else None
            if not resp.ok:
                record["error"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
        except Exception as e:
            record["http_status"] = None
            record["response"] = None
            record["error"] = str(e)
        record["elapsed_sec"] = round(time.perf_counter() - t0, 2)

        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        if record.get("error"):
            print(f"오류 ({record['error'][:80]})")
        else:
            proj, _ = score_sample(s, record)
            conflict_tag = f" | 충돌: {proj['conflict_level']}" if proj['conflict_level'] != "OK" else ""
            print(f"{record['elapsed_sec']:.1f}초 -> 판정 {proj['pred_integrated']} "
                  f"[{OUTCOME_KO[proj['outcome']]}{conflict_tag}]")

# =============================================================================
# 채점 (상속 엔진 + 지표 3원화 탑재)
# =============================================================================

OUTCOME_KO = {"correct": "정답", "wrong": "오답", "abstain": "보류(Unknown)",
              "versionless": "버전 미상(계열만 판정)", "error": "실행 오류"}


def classify(lang):
    if lang in SOURCE_LANGS:
        return "source"
    if lang in MANIFEST_LANGS:
        return "manifest"
    if lang == LICENSE_LANG:
        return "license"
    return "other"


def score_sample(sample, raw):
    """원본 응답 하나 -> (프로젝트 기록, 파일 기록 목록)"""
    sid, label, folder = sample["sid"], sample["label"], sample["folder"]
    gt_raw = label.get("ground_truth")
    gt_key = key_basic(gt_raw)
    gt_detail = key_detail(gt_raw)
    code_files = local_code_files(folder)
    py_local, header_local = local_names(folder)

    proj = {
        "sample_id": sid,
        "repo": label.get("repo", ""),
        "language": label.get("primary_language", ""),
        "ground_truth": pretty(gt_raw),
        "ground_truth_raw": gt_raw,
        "license_file": label.get("license_file", ""),
        "license_file_recognized": False,
        "pred_license_file": "-",
        "pred_integrated": "-",
        "decided_by": "-",
        "outcome": "error",
        "family_gt": family(gt_raw),
        "family_pred": "-",
        "family_ok": False,
        "detail_eligible": detail_eligible(gt_raw),
        "correct_detail": None,
        "pred_raw": "",
        "elapsed_sec": raw.get("elapsed_sec"),
        "files_analyzed": 0,
        "source_analyzed": 0,
        "code_files": len(code_files),
        "sec_per_file": None,
        "error": raw.get("error", ""),
        "license_file_reason": "",
        # [신규 SCA 3대 지표 필드]
        "root_match": False,
        "conflict_level": "OK",
        "conflict_detected": False,
        "guardrail_safe_files": 0,
        "no_hint_files": 0,
    }
    files = []
    resp = raw.get("response") or {}
    if raw.get("error") or resp.get("status") != "success":
        if not proj["error"]:
            proj["error"] = resp.get("message", "엔진 응답 없음")
        return proj, files

    # OSADL 충돌 매트릭스 결과 연동
    conflict_summary = resp.get("conflict_summary") or {}
    proj["conflict_level"] = conflict_summary.get("level", "OK")
    proj["conflict_detected"] = proj["conflict_level"] in ("CONFLICT", "RISK")

    results = resp.get("results", [])
    for r in results:
        lang = r.get("language", "")
        cat = classify(lang)
        orig_ai_raw = r.get("ai_guessed_main_license", "")
        inherited_raw = r.get("inherited_license", "")
        is_inherited = bool(r.get("is_inherited", False))

        # 최종 유효 라이선스: 상속받은 값이 있으면 우선 적용
        effective_raw = inherited_raw if (is_inherited and inherited_raw) else orig_ai_raw
        key = key_basic(effective_raw)
        pred_status = CATALOG.normalize(effective_raw)["status"]

        orig_key = key_basic(orig_ai_raw)
        reason = r.get("ai_analysis_reason", "") or ""
        judged_by = r.get("judged_by") or "llm"
        judge_status = r.get("judge_status") or ""
        failure_type = r.get("failure_type") or ""

        if judge_status == "FAILED":
            fail = (failure_type or "failed").lower()
        elif reason.startswith("LLM 호출 실패"):
            fail = "call"
        elif reason.startswith("LLM 응답 분석 실패"):
            fail = "parse"
        else:
            fail = ""

        top = read_top_lines(folder, r.get("file_path", "")) if cat == "source" else ""
        tags = sorted(set(code_window.find_spdx_tags(top))) if top else []
        tag = tags[0] if len(tags) == 1 else ""
        has_hint = bool(LICENSE_HINT_RE.search(top)) if cat == "source" else None

        # 환각 판정: 상속으로 인한 누명을 방지하기 위해 '순수 AI 추론값(orig_key)'으로 검증
        hallucination = None
        if cat == "source" and not has_hint and not fail and judge_status != "EMPTY":
            hallucination = (orig_key != UNKNOWN)
            proj["no_hint_files"] += 1
            if orig_key == UNKNOWN:
                proj["guardrail_safe_files"] += 1

        pkgs = []
        for pk in (r.get("detected_packages") or {}).values():
            name, reg = pk.get("package_name", ""), pk.get("registry", "")
            kind = "external"
            if reg == "Maven Central" and ":" not in name:
                kind = "java_import_path"
            elif reg == "PyPI" and name in PY_STDLIB_NAMES:
                kind = "stdlib"
            elif reg == "PyPI" and name in py_local:
                kind = "local"
            elif reg == "curated-table" and name.rsplit("/", 1)[-1] in header_local:
                kind = "local"
            pkgs.append({"registry": reg, "package_name": name,
                         "license": pk.get("license", "Unknown"), "kind": kind})

        files.append({
            "sample_id": sid,
            "file_path": r.get("file_path", ""),
            "language": lang,
            "category": cat,
            "predicted_raw": effective_raw,
            "predicted": pretty(effective_raw),
            "pred_key": key,
            "pred_status": pred_status,
            "orig_ai_raw": orig_ai_raw,
            "orig_ai_key": orig_key,
            "is_inherited": is_inherited,
            "inherited_license": inherited_raw,
            "judged_by": judged_by,
            "judge_status": judge_status,
            "failure_type": failure_type,
            "llm_choice": (r.get("llm_answer") or {}).get("main_license", ""),
            "check_rule": (r.get("answer_check") or {}).get("failed_rule", ""),
            "spdx_format_ok": spdx_format_ok(effective_raw) if not fail else None,
            "llm_failure": fail,
            "has_license_hint": has_hint,
            "hallucination": hallucination,
            "spdx_tag": tag,
            "tag_correct": (key_basic(tag) == key) if tag else None,
            "packages": pkgs,
            "reason": reason,
            "llm_prompt_tokens": (r.get("llm_stats") or {}).get("prompt_tokens"),
            "llm_output_tokens": (r.get("llm_stats") or {}).get("output_tokens"),
            "llm_done_reason": (r.get("llm_stats") or {}).get("done_reason"),
            "llm_near_ctx_limit": (r.get("llm_stats") or {}).get("near_ctx_limit"),
            "llm_sec": (r.get("llm_stats") or {}).get("elapsed_sec"),
        })

    # --- 프로젝트 통합 판정 ------------------------------------------------
    # 1순위: 백엔드 상속 엔진의 root_project_license (신규 규격)
    server_root = resp.get("root_project_license")
    if not server_root and resp.get("input_type") == "single":
        server_root = resp.get("ai_guessed_main_license")

    # 라이선스 원문 파일 파악
    lic_files = sorted((f for f in files if f["category"] == "license"),
                       key=lambda f: (f["file_path"].count("/"), f["file_path"]))
    pred_lf, pred_lf_raw = None, ""
    if lic_files:
        proj["license_file_recognized"] = True
        pred_lf_raw = lic_files[0]["predicted_raw"]
        pred_lf = key_basic(pred_lf_raw)
        proj["pred_license_file"] = pretty(pred_lf_raw)
        proj["license_file_reason"] = lic_files[0]["reason"][:200]

    # 소스 다수결 사전 계산
    votes, raw_of = Counter(), {}
    for f in files:
        if f["category"] == "source" and not f["llm_failure"] and f["pred_key"] != UNKNOWN:
            votes[f["pred_key"]] += 1
            raw_of.setdefault(f["pred_key"], Counter())[f["predicted_raw"]] += 1

    # 최종 판정 주체 결정 (엔진 root 우선 -> 라이선스 파일 -> 소스 다수결 순)
    if server_root and key_basic(server_root) != UNKNOWN:
        pred_raw = server_root
        pred = key_basic(pred_raw)
        proj["decided_by"] = "engine_root"
    elif pred_lf and pred_lf != UNKNOWN:
        pred, pred_raw, proj["decided_by"] = pred_lf, pred_lf_raw, "license_file"
    elif votes:
        pred = votes.most_common(1)[0][0]
        pred_raw, proj["decided_by"] = raw_of[pred].most_common(1)[0][0], "source_majority"
    else:
        pred, pred_raw, proj["decided_by"] = UNKNOWN, "Unknown", "none"

    proj["pred_raw"] = pred_raw
    proj["pred_integrated"] = pretty(pred_raw)
    proj["family_pred"] = family(pred_raw)
    proj["family_ok"] = proj["family_pred"] == proj["family_gt"] and proj["family_gt"] in COMPARABLE_FAMILIES

    # 일치 판정 (유연 일치 및 기본 일치)
    if pred == gt_key:
        proj["outcome"] = "correct"
        proj["root_match"] = True
    elif pred == UNKNOWN:
        proj["outcome"] = "abstain"
        proj["root_match"] = False
    elif pred.startswith("VERSIONLESS:"):
        proj["outcome"] = "versionless"
        proj["root_match"] = False
    else:
        proj["outcome"] = "wrong"
        proj["root_match"] = False

    if proj["detail_eligible"]:
        if proj["decided_by"] == "source_majority":
            dv = Counter()
            for f in files:
                if f["category"] == "source" and not f["llm_failure"] and f["pred_key"] == pred:
                    dv[key_detail(f["predicted_raw"])] += 1.5 if f.get("judged_by") == "rule" else 1
            pred_detail = dv.most_common(1)[0][0] if dv else key_detail(pred_raw)
        else:
            pred_detail = key_detail(pred_raw)
        proj["correct_detail"] = (pred_detail == gt_detail)

    proj["pred_lf_correct"] = (pred_lf == gt_key)
    proj["files_analyzed"] = len(results)
    proj["source_analyzed"] = sum(1 for f in files if f["category"] == "source")
    if proj["elapsed_sec"] and results:
        proj["sec_per_file"] = round(proj["elapsed_sec"] / len(results), 2)
    return proj, files


def ratio(a, b):
    return (a / b) if b else None


def pct(x):
    return "  -  " if x is None else f"{x * 100:5.1f}%"


def aggregate(projects, files):
    n = len(projects)
    ok_projects = [p for p in projects if p["outcome"] != "error"]
    outcomes = Counter(p["outcome"] for p in projects)
    m = {"n_projects": n, "outcomes": dict(outcomes)}

    # Track 1: 루트 라이선스 식별율
    m["track1_root_acc"] = ratio(sum(1 for p in ok_projects if p["root_match"]), len(ok_projects))
    m["accuracy_integrated"] = m["track1_root_acc"]

    # Track 2: 안전 가드레일 제어율 (단서 없는 파일의 Unknown 안전 격리)
    total_no_hint = sum(p["no_hint_files"] for p in ok_projects)
    total_safe_unknown = sum(p["guardrail_safe_files"] for p in ok_projects)
    m["track2_guardrail_safety_rate"] = ratio(total_safe_unknown, total_no_hint)
    m["track2_safe_files"] = total_safe_unknown
    m["track2_no_hint_files"] = total_no_hint

    # Track 3: OSADL 충돌 탐지율
    m["track3_conflict_projects"] = sum(1 for p in ok_projects if p["conflict_detected"])
    m["track3_conflict_rate"] = ratio(m["track3_conflict_projects"], len(ok_projects))
    m["track3_conflict_breakdown"] = dict(Counter(p["conflict_level"] for p in ok_projects))

    # 프로젝트 단위 기존 지표 호환
    m["accuracy_license_file"] = ratio(sum(1 for p in projects if p.get("pred_lf_correct")), n)
    recognized = [p for p in ok_projects if p["license_file_recognized"]]
    m["license_file_recognition"] = ratio(len(recognized), len(ok_projects))
    m["accuracy_license_file_when_recognized"] = ratio(
        sum(1 for p in recognized if p.get("pred_lf_correct")), len(recognized))
    m["family_accuracy"] = ratio(sum(1 for p in projects if p["family_ok"]), n)
    detail = [p for p in projects if p.get("detail_eligible")]
    m["accuracy_detail"] = ratio(sum(1 for p in detail if p.get("correct_detail")), len(detail))
    m["accuracy_detail_denominator"] = len(detail)
    m["versionless_rate"] = ratio(outcomes["versionless"], n)
    m["wrong_rate"] = ratio(outcomes["wrong"], n)
    m["abstain_rate"] = ratio(outcomes["abstain"], n)

    by_lang, by_lic = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for p in projects:
        by_lang[p["language"]][1] += 1
        by_lic[p["ground_truth"]][1] += 1
        if p["outcome"] == "correct":
            by_lang[p["language"]][0] += 1
            by_lic[p["ground_truth"]][0] += 1
    m["by_language"] = {k: {"correct": v[0], "total": v[1]} for k, v in sorted(by_lang.items())}
    m["by_license"] = {k: {"correct": v[0], "total": v[1]} for k, v in sorted(by_lic.items())}
    m["confusions"] = Counter(f"{p['ground_truth']} -> {p['pred_integrated']}"
                              for p in projects if p["outcome"] in ("wrong", "abstain", "versionless")).most_common()

    # 파일 단위 지표
    m["n_files"] = len(files)
    llm_fail = [f for f in files if f["llm_failure"]]
    m["llm_failure_rate"] = ratio(len(llm_fail), len(files))
    m["llm_failure_breakdown"] = dict(Counter(f["llm_failure"] for f in llm_fail))
    answered = [f for f in files if not f["llm_failure"]]
    m["spdx_format_rate"] = ratio(sum(1 for f in answered if f["spdx_format_ok"]), len(answered))
    m["file_unknown_rate"] = ratio(sum(1 for f in answered if f["predicted"] == "Unknown"), len(answered))

    no_hint = [f for f in files if f["hallucination"] is not None]
    m["hallucination_rate"] = ratio(sum(1 for f in no_hint if f["hallucination"]), len(no_hint))
    m["hallucination_denominator"] = len(no_hint)

    tagged = [f for f in files if f["tag_correct"] is not None]
    m["spdx_tag_accuracy"] = ratio(sum(1 for f in tagged if f["tag_correct"]), len(tagged))
    m["spdx_tagged_files"] = len(tagged)
    tagged_llm = [f for f in tagged if f.get("judged_by") != "rule"]
    m["spdx_tag_accuracy_llm"] = ratio(sum(1 for f in tagged_llm if f["tag_correct"]), len(tagged_llm))
    m["spdx_tagged_files_llm"] = len(tagged_llm)

    m["judged_by"] = dict(Counter(f.get("judged_by") or "llm" for f in files))
    m["judge_status"] = dict(Counter(f.get("judge_status") or "-" for f in files))
    no_hint_llm = [f for f in no_hint if f.get("judged_by") != "rule"]
    m["hallucination_rate_llm"] = ratio(sum(1 for f in no_hint_llm if f["hallucination"]), len(no_hint_llm))
    m["versionless_files"] = sum(1 for f in answered if f.get("pred_status") == STATUS_VERSIONLESS)
    with_choice = [f for f in files if f.get("llm_choice")]
    m["other_choice_rate"] = ratio(sum(1 for f in with_choice if f["llm_choice"] == "Other"), len(with_choice))
    m["other_choice_denominator"] = len(with_choice)
    m["answer_check_rules"] = dict(Counter(f["check_rule"] for f in files if f.get("check_rule")))
    m["inherited_files"] = sum(1 for f in files if f.get("is_inherited"))

    # 의존성 분석 지표
    external, misrouted = {}, {}
    for f in files:
        for pk in f["packages"]:
            key = (pk["registry"], pk["package_name"])
            if pk["kind"] == "external":
                external[key] = pk["license"]
            elif pk["kind"] in ("stdlib", "local"):
                misrouted[(f["sample_id"],) + key] = (pk["kind"], pk["license"])
    by_reg = defaultdict(lambda: [0, 0])
    for (reg, _), lic in external.items():
        by_reg[reg][1] += 1
        by_reg[reg][0] += int(lic != "Unknown")
    m["dependency_resolution"] = {k: {"resolved": v[0], "total": v[1]} for k, v in sorted(by_reg.items())}
    m["dependency_resolution_rate"] = ratio(sum(v[0] for v in by_reg.values()),
                                            sum(v[1] for v in by_reg.values()))
    resolved = [lic for lic in external.values() if lic != "Unknown"]
    dep_status = Counter(CATALOG.normalize(lic)["status"] for lic in resolved)
    m["dependency_spdx_rate"] = ratio(dep_status.get(STATUS_OK, 0), len(resolved))
    m["dependency_status"] = dict(dep_status)
    m["dependency_nonspdx_examples"] = sorted({" ".join(lic.split())[:40] for lic in resolved
                                               if CATALOG.normalize(lic)["status"] != STATUS_OK})[:8]
    m["misrouted_lookups"] = {
        "stdlib": sum(1 for k, _ in misrouted.values() if k == "stdlib"),
        "local": sum(1 for k, _ in misrouted.values() if k == "local"),
        "got_wrong_license": sum(1 for _, lic in misrouted.values() if lic != "Unknown"),
        "examples": sorted({f"{key[2]}({kind})" for key, (kind, _) in misrouted.items()})[:10],
    }

    # 커버리지 및 소요 시간
    m["source_coverage"] = ratio(sum(p["source_analyzed"] for p in ok_projects),
                                 sum(p["code_files"] for p in ok_projects))
    times = [p["elapsed_sec"] for p in ok_projects if p["elapsed_sec"]]
    m["time_total_sec"] = round(sum(times), 1) if times else None
    m["time_per_project_mean"] = round(statistics.mean(times), 1) if times else None
    m["time_per_project_max"] = round(max(times), 1) if times else None
    analyzed = sum(p["files_analyzed"] for p in ok_projects)
    m["time_per_file_mean"] = round(sum(times) / analyzed, 2) if times and analyzed else None

    # LLM 호출 통계
    called = [f for f in files if f.get("llm_prompt_tokens") is not None]
    pt = [f["llm_prompt_tokens"] for f in called]
    ot = [f["llm_output_tokens"] for f in called if f.get("llm_output_tokens") is not None]
    secs = [f["llm_sec"] for f in called if f.get("llm_sec")]
    m["llm_calls"] = len(called)
    m["llm_prompt_tokens_mean"] = round(statistics.mean(pt), 1) if pt else None
    m["llm_prompt_tokens_max"] = max(pt) if pt else None
    m["llm_output_tokens_mean"] = round(statistics.mean(ot), 1) if ot else None
    m["llm_output_tokens_max"] = max(ot) if ot else None
    m["llm_sec_per_call_mean"] = round(statistics.mean(secs), 2) if secs else None
    m["llm_near_ctx_limit"] = sum(1 for f in called if f.get("llm_near_ctx_limit"))
    m["llm_output_truncated"] = sum(1 for f in called if f.get("llm_done_reason") == "length")
    return m

# =============================================================================
# 결과 출력
# =============================================================================

def print_report(m, projects):
    line = "=" * 72
    print("\n" + line)
    print("dataset3 측정 결과 (3원화 SCA 평가)")
    print(line)
    n = m["n_projects"]
    oc = m["outcomes"]
    print(f"  프로젝트 {n}개  (정답 {oc.get('correct', 0)} / 오답 {oc.get('wrong', 0)} / "
          f"보류 {oc.get('abstain', 0)} / 버전 미상 {oc.get('versionless', 0)} / 실행 오류 {oc.get('error', 0)})")
    sc = m.get("scoring") or {}
    if sc:
        print(f"  채점 기준: SPDX {sc.get('spdx_version')}, 환각 단서 기준 {sc.get('hint_rule')}, "
              f"입력 범위 {sc.get('window')}")
    print()

    print("[*] SCA 3대 핵심 평가 지표 (3-Track Evaluation)")
    print(f"  Track 1. 루트 라이선스 식별율 (대표 라이선스 정확도) : {pct(m['track1_root_acc'])}")
    print(f"  Track 2. 안전 가드레일 제어율 (단서 부재 시 안전 격리) : {pct(m['track2_guardrail_safety_rate'])}  "
          f"({m['track2_safe_files']}/{m['track2_no_hint_files']}개, 환각률 {pct(m['hallucination_rate'])})")
    print(f"  Track 3. OSADL 충돌 탐지율 (비호환/위험 적발 프로젝트)  : {pct(m['track3_conflict_rate'])}  "
          f"({m['track3_conflict_projects']}/{n}개, 상태: {m['track3_conflict_breakdown']})")

    print("\n[1] 프로젝트 라이선스 세부 판정")
    print(f"  통합 판정 정확도 (상속 엔진 적용)   {pct(m['accuracy_integrated'])}")
    print(f"  상속 엔진을 통해 구제된 서브 파일  {m['inherited_files']}개")
    print(f"  라이선스 파일 판정 정확도           {pct(m['accuracy_license_file'])}")
    print(f"    - 파일을 인식한 경우만            {pct(m['accuracy_license_file_when_recognized'])}")
    print(f"  세부 정확도 (-only/-or-later 구분)  {pct(m['accuracy_detail'])}  "
          f"(정답에 명시된 {m['accuracy_detail_denominator']}개만)")
    print(f"  위험 계열 일치율                    {pct(m['family_accuracy'])}  (허용/약한 카피레프트/강한 카피레프트)")
    print(f"  버전 미상 판정률 (계열만 판정)      {pct(m['versionless_rate'])}")
    print(f"  오답률 (틀린 라이선스 단정)         {pct(m['wrong_rate'])}")
    print(f"  보류율 (Unknown 으로 회피)          {pct(m['abstain_rate'])}")
    print(f"  라이선스 파일 인식률                {pct(m['license_file_recognition'])}")

    print("\n[2] 언어별 정확도")
    for k, v in m["by_language"].items():
        print(f"  {k:<12} {v['correct']:>2}/{v['total']:<2}  {pct(ratio(v['correct'], v['total']))}")
    print("\n[3] 정답 라이선스별 정확도")
    for k, v in m["by_license"].items():
        print(f"  {k:<14} {v['correct']:>2}/{v['total']:<2}  {pct(ratio(v['correct'], v['total']))}")
    if m["confusions"]:
        print("\n  자주 틀린 조합 (정답 -> 판정)")
        for pair, c in m["confusions"]:
            print(f"    {pair}  x{c}")

    print("\n[4] 파일 단위 (LLM 응답 품질 및 안전 가드)")
    print(f"  분석된 파일 수                      {m['n_files']}")
    print(f"  환각률 (근거 없는 파일에 라이선스 단정) {pct(m['hallucination_rate'])}  "
          f"(대상 {m['hallucination_denominator']}개)")
    print(f"  SPDX 태그 파일 정확도               {pct(m['spdx_tag_accuracy'])}  "
          f"(대상 {m['spdx_tagged_files']}개, LLM 판정분만 {pct(m['spdx_tag_accuracy_llm'])} / "
          f"{m['spdx_tagged_files_llm']}개)")
    print(f"  판정 방식                           {m['judged_by']}  상태 {m['judge_status']}")
    if m["judged_by"].get("rule"):
        print(f"  환각률 (LLM 판정 파일만)            {pct(m['hallucination_rate_llm'])}")
    print(f"  버전 미상으로 답한 파일             {m['versionless_files']}개")
    if m["other_choice_denominator"]:
        print(f"  LLM 이 Other 를 고른 비율           {pct(m['other_choice_rate'])}  (대상 {m['other_choice_denominator']}개)")
    if m["answer_check_rules"]:
        print(f"  답 검증에서 걸린 규칙               {m['answer_check_rules']}")
    print(f"  SPDX 형식 준수율                    {pct(m['spdx_format_rate'])}")
    print(f"  파일 판정 Unknown 비율              {pct(m['file_unknown_rate'])}")
    print(f"  LLM 실패율 (호출/파싱)              {pct(m['llm_failure_rate'])}  {m['llm_failure_breakdown'] or ''}")

    print("\n[5] 의존성 라이선스 조회 성공률 (중복 제거)")
    for k, v in m["dependency_resolution"].items():
        print(f"  {k:<14} {v['resolved']:>3}/{v['total']:<3}  {pct(ratio(v['resolved'], v['total']))}")
    print(f"  전체           {pct(m['dependency_resolution_rate'])}")
    print(f"  조회된 라이선스 중 SPDX 로 정리된 비율 {pct(m['dependency_spdx_rate'])}  {m['dependency_status']}")
    if m["dependency_nonspdx_examples"]:
        print(f"    비정형 예: {m['dependency_nonspdx_examples']}")
    mr = m["misrouted_lookups"]
    print(f"  표준/내부 모듈을 외부 패키지로 조회  표준 {mr['stdlib']}건, 내부 {mr['local']}건 "
          f"(그중 엉뚱한 라이선스가 붙은 것 {mr['got_wrong_license']}건)")

    print("\n[6] 처리량")
    print(f"  소스 커버리지 (저장소 코드 파일 중 분석 비율)  {pct(m['source_coverage'])}")
    print(f"  총 소요 시간                        {m['time_total_sec']}초")
    print(f"  프로젝트당 평균 / 최대              {m['time_per_project_mean']}초 / {m['time_per_project_max']}초")
    print(f"  파일당 평균                         {m['time_per_file_mean']}초")
    if m.get("llm_calls"):
        print(f"  LLM 호출 {m['llm_calls']}회, 호출당 평균 {m['llm_sec_per_call_mean']}초")
        print(f"  입력 토큰 평균/최대 {m['llm_prompt_tokens_mean']}/{m['llm_prompt_tokens_max']}, "
              f"출력 토큰 평균/최대 {m['llm_output_tokens_mean']}/{m['llm_output_tokens_max']}")

    bad = [p for p in projects if p["outcome"] != "correct"]
    if bad:
        print("\n[7] 틀리거나 보류된 프로젝트")
        for p in bad:
            print(f"  {p['sample_id']} {p['repo']} ({p['language']})")
            print(f"     정답 {p['ground_truth']} / 판정 {p['pred_integrated']} [{OUTCOME_KO[p['outcome']]}] "
                  f"/ 근거: {p['decided_by']} / 라이선스 파일 {p['license_file']} "
                  f"{'인식' if p['license_file_recognized'] else '미인식'} / 충돌: {p['conflict_level']}")
            if p["error"]:
                print(f"     오류: {p['error'][:150]}")
            elif p["license_file_reason"]:
                print(f"     LLM 근거: {p['license_file_reason'][:150]}")

    if m["llm_failure_rate"] and m["llm_failure_rate"] > 0.1:
        print(f"\n[참고] LLM 실패가 많습니다. OLLAMA_TIMEOUT({ENGINE_OLLAMA_TIMEOUT}초)을 늘려 보세요.")
    print(line)


def save_outputs(run_dir, m, projects, files):
    with open(run_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump({"metrics": m, "projects": projects}, f, ensure_ascii=False, indent=2)

    p_cols = ["sample_id", "repo", "language", "ground_truth", "ground_truth_raw", "pred_integrated", "pred_raw",
              "outcome", "root_match", "conflict_level", "conflict_detected", "guardrail_safe_files", "no_hint_files",
              "detail_eligible", "correct_detail",
              "decided_by", "pred_license_file", "license_file", "license_file_recognized",
              "family_gt", "family_pred", "family_ok", "code_files", "source_analyzed", "files_analyzed",
              "elapsed_sec", "sec_per_file", "error"]
    with open(run_dir / "projects.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=p_cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(projects)

    f_cols = ["sample_id", "file_path", "language", "category", "predicted_raw", "predicted", "pred_status",
              "orig_ai_raw", "is_inherited", "inherited_license",
              "judged_by", "judge_status", "failure_type", "llm_choice", "check_rule",
              "spdx_format_ok", "llm_failure", "has_license_hint", "hallucination",
              "spdx_tag", "tag_correct", "n_packages",
              "llm_prompt_tokens", "llm_output_tokens", "llm_done_reason", "llm_near_ctx_limit", "llm_sec",
              "reason"]
    with open(run_dir / "files.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=f_cols, extrasaction="ignore")
        w.writeheader()
        for row in files:
            w.writerow({**row, "n_packages": len(row["packages"]), "reason": row["reason"][:300]})

# =============================================================================
# 메인 진입점
# =============================================================================

def resolve_run_dir(name):
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if name == "latest":
        runs = sorted(d for d in RUNS_DIR.iterdir() if d.is_dir())
        if not runs:
            sys.exit("[오류] 이어서 진행할 run 이 없습니다.")
        return runs[-1]
    return RUNS_DIR / (name or datetime.now().strftime("run_%Y%m%d_%H%M%S"))


def main():
    ap = argparse.ArgumentParser(description="dataset3 정확도 측정 (3원화 채점 반영)")
    ap.add_argument("--engine-url", default=ENGINE_URL)
    ap.add_argument("--run-name", default="", help="결과 폴더 이름. 'latest' 는 가장 최근 run 이어서 진행")
    ap.add_argument("--only", default="", help="측정할 샘플 (예: sample01,sample05)")
    ap.add_argument("--limit", type=int, default=0, help="앞에서부터 N개만 측정")
    ap.add_argument("--timeout", type=int, default=1800, help="프로젝트 1개 요청 타임아웃(초)")
    ap.add_argument("--rescore", action="store_true", help="엔진 호출 없이 저장된 결과로 지표만 재계산")
    ap.add_argument("--rerun", action="store_true", help="이미 결과가 있는 샘플도 다시 측정")
    ap.add_argument("--skip-preflight", action="store_true")
    ap.add_argument("--hint-rule", choices=sorted(HINT_RULES), default="v2",
                    help="환각 판정의 라이선스 단서 기준 (legacy 는 예전 결과와 비교할 때만)")
    ap.add_argument("--allow-no-treesitter", action="store_true",
                    help="tree-sitter 없이 동작하는 엔진으로도 측정")
    args = ap.parse_args()

    if not CATALOG.loaded:
        sys.exit(f"[오류] 라이선스 목록 모듈을 불러오지 못했습니다: {CATALOG.error}")
    set_hint_rule(args.hint_rule)

    samples = load_samples()
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        samples = [s for s in samples if s["sid"] in wanted]
    if args.limit:
        samples = samples[: args.limit]
    if not samples:
        sys.exit(f"[오류] 측정할 샘플이 없습니다. {LABEL_DIR} 와 {SOURCE_DIR} 를 확인하세요.")

    run_dir = resolve_run_dir(args.run_name)
    raw_dir = run_dir / "raw"
    if args.rescore:
        have = sum(1 for smp in samples if (raw_dir / f"{smp['sid']}.json").exists())
        print(f"run 폴더: {run_dir}\n저장된 결과가 있는 샘플 {have}개 (전체 {len(samples)}개 중) 다시 채점\n")
    else:
        print(f"run 폴더: {run_dir}\n샘플 {len(samples)}개\n")

    if not args.rescore:
        health = {}
        if not args.skip_preflight:
            ok, health = preflight(args.engine_url, args.allow_no_treesitter)
            if not ok:
                sys.exit("[중단] 사전 점검 실패. 안내대로 준비한 뒤 다시 실행하세요.")
        else:
            health = _safe_get(f"{args.engine_url}/health")
        session = record_conditions(run_dir, args, health, "start")
        gpu = [x.get("gpu_ratio") for x in session["ollama"].get("loaded", [])]
        print(f"실행 조건 기록: {run_dir / 'run_conditions.json'} "
              f"(모델 ID {session['ollama'].get('model', {}).get('id')}, "
              f"Ollama {session['ollama'].get('version')}, GPU 비율 {gpu})\n")
        run_samples(samples, raw_dir, args.engine_url, args.timeout, args.rerun)
        record_conditions(run_dir, args, health, "end")

    projects, files = [], []
    for s in samples:
        raw_path = raw_dir / f"{s['sid']}.json"
        if not raw_path.exists():
            continue
        with open(raw_path, encoding="utf-8") as f:
            raw = json.load(f)
        p, fl = score_sample(s, raw)
        projects.append(p)
        files.extend(fl)
    if not projects:
        sys.exit("[오류] 채점할 결과가 없습니다.")

    m = aggregate(projects, files)
    m["scoring"] = {"spdx_version": CATALOG.spdx_version, "hint_rule": HINT_RULE,
                    "window": code_window.describe()}
    print_report(m, projects)
    save_outputs(run_dir, m, projects, files)
    print(f"\n저장: {run_dir / 'summary.json'}, projects.csv, files.csv")


if __name__ == "__main__":
    main()