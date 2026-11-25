# -*- coding: utf-8 -*-
"""
main.py - 다국어 오픈소스 라이선스 분석 엔진 (FastAPI)

주요 변경점 (기존 파이썬 전용 버전 대비)
----------------------------------------
1. 입력: 단일 파일 + ZIP 모두 지원 (ZIP 은 내부 파일을 순회하며 개별 분석)
2. 언어: Python / JavaScript / Java / C 지원 (tree-sitter 기반, 미설치 시 폴백)
3. 조회처: PyPI / npm / Maven 은 오프라인 DB(license_data/, deps.dev 기반)에서 조회,
           C 는 큐레이션 테이블로 best-effort. 실행 중 인터넷 접속 없음 (Ollama 는 로컬)
4. 프롬프트: 파일 단위로 '해당 파일의 상단 50줄 + 해당 파일의 의존성 JSON' 만 묶어 호출
             (파일 간 정보가 섞이지 않아 LLM 혼동 최소화)
             의존성 JSON 은 판정에 필요한 항목(license/registry/confidence/lookup)만 전달
5. 응답: 파일별 결과 목록 + 단일 파일일 때는 기존 키도 유지(test_runner.py 호환)
         프론트로 가는 detected_packages 형식은 기존과 동일
6. 상속: 프로젝트 루트 LICENSE/매니페스트 판정값을 Unknown 개별 파일에 상속 (2-Pass 분석)

오프라인 DB: license_data/packages.sqlite3 (생성된 파일을 받아 이 위치에 둔다)
"""

import io
import json
import os
import re
import sys
import zipfile
from typing import Optional

import requests          # 로컬 Ollama 호출에만 사용 (외부 인터넷 접속 없음)
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from license_db import OfflineLicenseDB
import harness_config            # 하네스 기능 스위치 (환경변수 HARNESS_FLAGS)
import code_window               # LLM 입력 범위 (엔진·근거 확인·측정 스크립트 공용)
import llm_client                # Ollama 호출 (호출 옵션, 서버 확인, 출력 형식 강제, 실패 처리)
from llm_client import (OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT, OLLAMA_NUM_CTX,   # noqa: F401
                        OLLAMA_NUM_PREDICT, OLLAMA_TEMPERATURE, OLLAMA_SEED, CTX_WARN_RATIO,
                        llm_options, LLMConnectionError)
from license_catalog import CATALOG
import rule_judge                # 규칙 우선 판정 (RULE_SPDX_TAG, RULE_LICENSE_TEXT)
import harness_schemas           # LLM 출력 스키마 (STRUCTURED_OUTPUT)
import harness_prompts           # LLM 입력 구성 (INPUT_SEPARATION)
import answer_check              # 답 검증 (ANSWER_CHECK)
import license_compat            # 프로젝트 전체 충돌 판정 (CONFLICT_CHECK)

app = FastAPI()

# React(3000) / Spring(8080) 에서 직접 호출할 수 있도록 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================================
# 설정
# =============================================================================

# LLM 주소·모델·호출 옵션(num_ctx 8192, num_predict 1024, temperature 0, seed 42)은 llm_client.py 에서만 정한다.
# (OLLAMA_URL, OLLAMA_MODEL 등의 이름은 측정 스크립트 호환을 위해 이 모듈에서도 그대로 쓸 수 있다)

REASONING_MAX_CHARS = 500     # 화면에 보일 판정 설명 길이 상한 (하네스 켰을 때)

# 분석 대상이 아닌 코드 파일 (결과가 완전하지 않음을 알리는 데 쓴다)
UNSUPPORTED_CODE_EXT = {
    ".ts", ".tsx", ".cts", ".mts", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".kt", ".kts",
    ".go", ".rs", ".rb", ".php", ".cs", ".swift", ".scala", ".m", ".mm", ".vue", ".svelte",
    ".dart", ".lua", ".pl", ".r", ".groovy", ".sh",
}

MAX_TOP_LINES = code_window.WINDOW_LINES   # LLM 에 넘길 코드 상단 줄 수 (범위는 code_window.py 에서만 정함)
MAX_PACKAGES_PER_FILE = 40    # 의존성이 비정상적으로 많을 때 프롬프트 폭주 방지

# ZIP 해제 시 제외할 폴더/파일
IGNORE_DIR_PARTS = {
    "venv", "node_modules", ".git", "__macosx", "build", ".gradle",
    ".idea", "dist", "__pycache__", ".settings", "target", "out",
}
IGNORE_FILE_NAMES = {".ds_store"}

# import 이름 -> 실제 PyPI 패키지명 보정표
PACKAGE_ALIASES = {
    "cv2": "opencv-python", "sklearn": "scikit-learn", "yaml": "pyyaml",
    "bs4": "beautifulsoup4", "dotenv": "python-dotenv", "PIL": "Pillow",
    "dateutil": "python-dateutil", "github": "PyGithub", "jwt": "PyJWT",
    "git": "GitPython",
}

# 파이썬 표준 라이브러리 (조회 대상에서 제외)
#   Python 3.10+ 는 sys.stdlib_module_names 로 공식 전체 목록을 제공한다 (인터넷 불필요).
#   예전 수작업 목록(37개)은 3.9 이하 대비용으로만 남겨 합친다.
#   argparse, dataclasses 처럼 PyPI 에 같은 이름의 옛 백포트 패키지가 있는 모듈이
#   외부 패키지로 잘못 조회되는 것을 막는다.
_PY_STDLIB_FALLBACK = {
    "os", "sys", "re", "json", "io", "time", "math", "random", "typing",
    "ast", "itertools", "functools", "collections", "datetime", "pathlib",
    "subprocess", "threading", "logging", "unittest", "zipfile", "abc",
    "asyncio", "csv", "hashlib", "shutil", "socket", "string", "traceback",
    "uuid", "warnings", "copy", "enum", "glob", "inspect", "pickle", "struct",
}
PY_STDLIB = set(getattr(sys, "stdlib_module_names", ())) | _PY_STDLIB_FALLBACK

# Java 표준 패키지 (외부 의존성이 아님)
JAVA_STDLIB_PREFIXES = ("java.", "javax.", "jdk.", "sun.", "com.sun.")

# Node 내장 모듈 (외부 의존성이 아님)
#   Node.js 공식 내장 모듈 목록 (하위 경로 fs/promises 등은 앞부분만 보므로 최상위 이름만)
#   'node:fs' 처럼 node: 접두어가 붙은 import 는 extract_javascript 에서 따로 제외한다.
NODE_BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
    "constants", "crypto", "dgram", "diagnostics_channel", "dns", "domain",
    "events", "fs", "http", "http2", "https", "inspector", "module", "net",
    "os", "path", "perf_hooks", "process", "punycode", "querystring",
    "readline", "repl", "stream", "string_decoder", "sys", "timers", "tls",
    "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads",
    "zlib",
}

# =============================================================================
# C 언어 대응 : 중앙 저장소가 없으므로 '헤더 -> 라이브러리/라이선스' 큐레이션 테이블
#   - C 는 PyPI/npm 같은 중앙 레지스트리가 없어 자동 조회가 불가능하다.
#   - 대신 실무에서 헤더 이름이 라이브러리와 사실상 1:1 대응되는 관례를 이용한다.
#   - 표준 헤더는 stdlib 로 분류해 의존성에서 제외한다.
#   - 미등록 헤더는 Unknown 으로 남기고 confidence 를 낮게 표기한다.
# =============================================================================

C_STDLIB_HEADERS = {
    "stdio.h", "stdlib.h", "string.h", "math.h", "time.h", "ctype.h",
    "stdint.h", "stddef.h", "stdbool.h", "limits.h", "errno.h", "assert.h",
    "signal.h", "locale.h", "setjmp.h", "stdarg.h", "float.h", "wchar.h",
    "unistd.h", "fcntl.h", "sys/types.h", "sys/stat.h", "sys/socket.h",
    "pthread.h", "dirent.h", "inttypes.h", "iso646.h", "complex.h",
}

C_HEADER_LIBRARY_MAP = {
    # 헤더(접두어)      : (라이브러리명, SPDX 라이선스, 프로젝트 URL)
    "openssl/":  ("OpenSSL",   "Apache-2.0",       "https://www.openssl.org"),
    "zlib.h":    ("zlib",      "Zlib",             "https://zlib.net"),
    "curl/":     ("libcurl",   "curl",             "https://curl.se"),
    "sqlite3.h": ("SQLite",    "blessing",         "https://sqlite.org"),
    "png.h":     ("libpng",    "libpng-2.0",       "http://libpng.org"),
    "jpeglib.h": ("libjpeg",   "IJG",              "https://ijg.org"),
    "json-c/":   ("json-c",    "MIT",              "https://github.com/json-c/json-c"),
    "cjson/":    ("cJSON",     "MIT",              "https://github.com/DaveGamble/cJSON"),
    "glib.h":    ("GLib",      "LGPL-2.1-or-later","https://gitlab.gnome.org/GNOME/glib"),
    "gtk/":      ("GTK",       "LGPL-2.1-or-later","https://www.gtk.org"),
    "SDL2/":     ("SDL2",      "Zlib",             "https://www.libsdl.org"),
    "SDL/":      ("SDL",       "Zlib",             "https://www.libsdl.org"),
    "ncurses.h": ("ncurses",   "X11",              "https://invisible-island.net/ncurses"),
    "curses.h":  ("ncurses",   "X11",              "https://invisible-island.net/ncurses"),
    "libxml/":   ("libxml2",   "MIT",              "http://xmlsoft.org"),
    "mysql.h":   ("MySQL",     "GPL-2.0-only",     "https://www.mysql.com"),
    "pcre.h":    ("PCRE",      "BSD-3-Clause",     "https://www.pcre.org"),
    "ffi.h":     ("libffi",    "MIT",              "https://sourceware.org/libffi"),
    "uv.h":      ("libuv",     "MIT",              "https://libuv.org"),
    "lua.h":     ("Lua",       "MIT",              "https://www.lua.org"),
    "zmq.h":     ("ZeroMQ",    "MPL-2.0",          "https://zeromq.org"),
    "ssl.h":     ("OpenSSL",   "Apache-2.0",       "https://www.openssl.org"),
    "bzlib.h":   ("bzip2",     "bzip2-1.0.6",      "https://sourceware.org/bzip2"),
    "lzma.h":    ("xz/liblzma","0BSD",             "https://tukaani.org/xz"),
    "gmp.h":     ("GMP",       "LGPL-3.0-or-later","https://gmplib.org"),
    "cairo.h":   ("cairo",     "LGPL-2.1-or-later","https://www.cairographics.org"),
    "freetype/": ("FreeType",  "FTL",              "https://freetype.org"),
    "GL/":       ("OpenGL",    "Unknown",          "https://www.opengl.org"),
    "vulkan/":   ("Vulkan",    "Apache-2.0",       "https://www.vulkan.org"),
}

# =============================================================================
# 언어 판별 (확장자 + 특수 파일명)
# =============================================================================

EXTENSION_LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".java": "Java",
    ".c": "C", ".h": "C",
}

SPECIAL_FILES = {
    "pom.xml": "Java-Maven",
    "build.gradle": "Java-Gradle",
    "package.json": "JavaScript-Manifest",
    "requirements.txt": "Python-Manifest",
    "license": "License-Text",
    "license.txt": "License-Text",
    "license.md": "License-Text",
    "copying": "License-Text",
}

# 실제로 라이선스 판별 의미가 있는 종류만 분석 대상으로 삼는다.
ANALYZABLE_LANGUAGES = {
    "Python", "JavaScript", "Java", "C",
    "Java-Maven", "Java-Gradle",
    "Python-Manifest", "JavaScript-Manifest",
    "License-Text",
}


def detect_language(file_path: str) -> str:
    """파일 경로로 언어(또는 특수 파일 종류)를 판정."""
    base = os.path.basename(file_path).lower()
    if base in SPECIAL_FILES:
        return SPECIAL_FILES[base]
    _, ext = os.path.splitext(base)
    return EXTENSION_LANGUAGE_MAP.get(ext, "Unknown")


# =============================================================================
# tree-sitter 준비 (미설치 시 폴백)
# =============================================================================

_TS_PARSERS = {}
_TS_AVAILABLE = None


def _get_parser(language: str):
    """언어별 tree-sitter 파서를 지연 로딩. 실패하면 None 반환(폴백 유도)."""
    global _TS_AVAILABLE
    if language in _TS_PARSERS:
        return _TS_PARSERS[language]
    try:
        from tree_sitter import Language, Parser
        if language == "Python":
            import tree_sitter_python as ts_mod
        elif language == "JavaScript":
            import tree_sitter_javascript as ts_mod
        elif language == "Java":
            import tree_sitter_java as ts_mod
        elif language == "C":
            import tree_sitter_c as ts_mod
        else:
            return None
        parser = Parser(Language(ts_mod.language()))
        _TS_PARSERS[language] = parser
        _TS_AVAILABLE = True
        return parser
    except Exception:
        _TS_AVAILABLE = False
        _TS_PARSERS[language] = None
        return None


def _walk(node):
    """트리 전체 노드를 순회하는 제너레이터."""
    yield node
    for child in node.children:
        yield from _walk(child)


# =============================================================================
# 언어별 의존성 추출 (tree-sitter)
# =============================================================================

def extract_python(code: str) -> list:
    """
    Python import 추출.
    tree-sitter 우선, 미설치 시 내장 ast, 그마저 실패하면 정규식 폴백.
    ast 와 동일하게: 점 표기는 첫 부분만, as 별칭은 원본명, 상대 임포트는 제외.
    """
    parser = _get_parser("Python")
    packages = set()

    if parser is not None:
        tree = parser.parse(code.encode("utf-8", errors="replace"))
        for node in _walk(tree.root_node):
            if node.type == "import_statement":
                # import a, b.c / import numpy as np
                for child in node.children:
                    if child.type == "dotted_name":
                        packages.add(child.text.decode().split(".")[0])
                    elif child.type == "aliased_import":
                        name = child.child_by_field_name("name")
                        if name is not None:
                            packages.add(name.text.decode().split(".")[0])
            elif node.type == "import_from_statement":
                module = node.child_by_field_name("module_name")
                # relative_import (from . import x) 는 외부 패키지가 아니므로 제외
                if module is not None and module.type == "dotted_name":
                    packages.add(module.text.decode().split(".")[0])
    else:
        # 폴백 1: 내장 ast
        try:
            import ast as _ast
            tree = _ast.parse(code)
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Import):
                    for alias in node.names:
                        packages.add(alias.name.split(".")[0])
                elif isinstance(node, _ast.ImportFrom):
                    if node.module and node.level == 0:
                        packages.add(node.module.split(".")[0])
        except SyntaxError:
            # 폴백 2: 정규식
            pattern = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z0-9_]+)", re.MULTILINE)
            packages.update(pattern.findall(code))

    return sorted(p for p in packages if p and p not in PY_STDLIB)


def extract_javascript(code: str) -> list:
    """JS import / require 추출. 상대경로와 Node 내장 모듈은 제외."""
    parser = _get_parser("JavaScript")
    raw = set()

    if parser is not None:
        tree = parser.parse(code.encode("utf-8", errors="replace"))
        for node in _walk(tree.root_node):
            if node.type == "import_statement":
                # import ... from 'pkg'  /  import 'pkg'
                for child in node.children:
                    if child.type == "string":
                        raw.add(child.text.decode().strip("'\"`"))
            elif node.type == "call_expression":
                # require('pkg')
                fn = node.child_by_field_name("function")
                args = node.child_by_field_name("arguments")
                if fn is not None and fn.text.decode() == "require" and args is not None:
                    for a in _walk(args):
                        if a.type == "string_fragment":
                            raw.add(a.text.decode())
    else:
        # 폴백: 정규식
        for pat in (
            re.compile(r"""import\s+.*?from\s+['"]([^'"]+)['"]""", re.DOTALL),
            re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
            re.compile(r"""import\s+['"]([^'"]+)['"]"""),
        ):
            raw.update(pat.findall(code))

    packages = set()
    for m in raw:
        if not m or m.startswith((".", "/", "~")) or "://" in m:
            continue                            # 상대/절대 경로, 홈 별칭, URL 은 외부 패키지 아님
        if m.startswith("node:"):               # node:fs 형태는 Node 내장 모듈
            continue
        if m.startswith("@"):
            parts = m.split("/")
            if parts[0] == "@":                 # '@/components' 같은 프로젝트 경로 별칭
                continue
            pkg = "/".join(parts[:2])           # @scope/name/sub -> @scope/name
        else:
            pkg = m.split("/")[0]               # lodash/fp -> lodash
        if pkg in NODE_BUILTINS:                # Node 내장 모듈 제외
            continue
        packages.add(pkg)
    return sorted(packages)


def extract_java_source(code: str) -> list:
    """
    .java 소스의 import 추출.
    주의: 여기서 나오는 값은 'org.springframework.boot' 같은 '패키지 경로'이며
          Maven 좌표(groupId:artifactId)가 아니다. 따라서 정확한 라이선스 조회가
          불가능하고, 어디까지나 보조 힌트로만 사용한다.
          실제 의존성은 pom.xml / build.gradle 에서 얻는다.
    """
    parser = _get_parser("Java")
    packages = set()

    if parser is not None:
        tree = parser.parse(code.encode("utf-8", errors="replace"))
        for node in _walk(tree.root_node):
            if node.type == "import_declaration":
                for child in node.children:
                    if child.type in ("scoped_identifier", "identifier"):
                        packages.add(child.text.decode())
    else:
        packages.update(re.findall(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", code, re.MULTILINE))

    result = set()
    for full in packages:
        if full.startswith(JAVA_STDLIB_PREFIXES):   # 표준 라이브러리 제외
            continue
        # 최상위 3단계까지만 취해 그룹 수준으로 묶음 (org.springframework.boot)
        parts = full.split(".")
        result.add(".".join(parts[:3]) if len(parts) >= 3 else full)
    return sorted(result)


def extract_c(code: str) -> list:
    """C 의 #include 헤더 추출. 표준 헤더는 제외."""
    parser = _get_parser("C")
    headers = set()

    if parser is not None:
        tree = parser.parse(code.encode("utf-8", errors="replace"))
        for node in _walk(tree.root_node):
            if node.type == "preproc_include":
                for child in node.children:
                    if child.type in ("system_lib_string", "string_literal"):
                        headers.add(child.text.decode().strip('<>"'))
    else:
        headers.update(re.findall(r'#\s*include\s*[<"]([^>"]+)[>"]', code))

    # 표준 헤더 제외, 사용자 로컬 헤더("my.h" 형태 중 매핑 없는 것)는 남기되 Unknown 처리
    return sorted(h for h in headers if h and h not in C_STDLIB_HEADERS)


# =============================================================================
# 매니페스트 파서 (의존성의 권위 있는 출처)
# =============================================================================

def extract_pom_xml(xml_text: str) -> list:
    """pom.xml 에서 groupId:artifactId 쌍을 추출. Maven 조회에 필요한 정식 좌표."""
    deps = []
    for block in re.findall(r"<dependency>(.*?)</dependency>", xml_text, re.DOTALL):
        g = re.search(r"<groupId>\s*([^<\s]+)\s*</groupId>", block)
        a = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", block)
        if g and a:
            deps.append(f"{g.group(1)}:{a.group(1)}")
        elif a:
            deps.append(a.group(1))
    return sorted(set(deps))


def extract_build_gradle(text: str) -> list:
    """build.gradle 의 'group:artifact:version' 문자열에서 group:artifact 추출."""
    deps = set()
    for m in re.findall(r"""['"]([\w.\-]+:[\w.\-]+)(?::[\w.\-]+)?['"]""", text):
        deps.add(m)
    return sorted(deps)


def extract_package_json(text: str) -> list:
    """package.json 의 dependencies 계열 키 추출."""
    packages = set()
    try:
        data = json.loads(text)
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            if isinstance(data.get(key), dict):
                packages.update(data[key].keys())
    except Exception:
        pass
    return sorted(packages)


def extract_requirements(text: str) -> list:
    """requirements.txt 에서 버전 지정자를 제거한 패키지명 추출."""
    packages = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name = re.split(r"[=<>!~\[; ]", line)[0].strip()
        if name:
            packages.add(name)
    return sorted(packages)


def extract_dependencies(language: str, content: str) -> list:
    """언어별 추출기 디스패처."""
    if language == "Python":
        return extract_python(content)
    if language == "JavaScript":
        return extract_javascript(content)
    if language == "Java":
        return extract_java_source(content)
    if language == "C":
        return extract_c(content)
    if language == "Java-Maven":
        return extract_pom_xml(content)
    if language == "Java-Gradle":
        return extract_build_gradle(content)
    if language == "JavaScript-Manifest":
        return extract_package_json(content)
    if language == "Python-Manifest":
        return extract_requirements(content)
    return []


# =============================================================================
# 의존 패키지 라이선스 조회 (오프라인)
#   - PyPI / npm / Maven : deps.dev 에서 미리 받아 만든
#     license_data/ 파일만 읽는다. 실행 중에는 인터넷에 접속하지 않는다.
#   - C : 중앙 레지스트리가 없으므로 기존 큐레이션 테이블을 그대로 사용한다.
#   - 오프라인 DB 에 없는 패키지는 온라인으로 다시 묻지 않고 '미등록'으로 남긴다.
#   - 같은 패키지를 여러 파일이 공유하므로 결과를 캐시한다.
#
#   반환 형식(프론트로 가는 detected_packages 항목)은 기존과 같다.
#     package_name, license, author, summary, project_url, registry, confidence
#   - author : deps.dev 에 작성자 정보가 없어 항상 "Unknown"
#   - license: deps.dev 의 SPDX 표현식. 여러 개면 AND 로 묶음 (_join_licenses 참고)
#
#   조회 상태(lookup)는 LLM 에만 전달하고 프론트 응답 형식에는 넣지 않는다.
# =============================================================================

OFFLINE_DB = OfflineLicenseDB()

STATUS_FOUND = "등록됨"
STATUS_NO_LICENSE = "등록됨(라이선스 정보 없음)"
STATUS_NOT_IN_DB = "오프라인 DB 미등록"
STATUS_DB_MISSING = "오프라인 DB 파일 없음"
STATUS_NOT_COORDINATE = "조회 불가(Java 패키지 경로)"
STATUS_CURATED = "큐레이션 표 등록(헤더 기반 추정)"
STATUS_NOT_CURATED = "큐레이션 표 미등록"

_LICENSE_CACHE = {}     # "언어::이름" -> (info, status)


def _blank_info(name: str) -> dict:
    return {
        "package_name": name, "license": "Unknown", "author": "Unknown",
        "summary": "No description", "project_url": "Unknown",
        "registry": "none", "confidence": "low",
    }


def _join_licenses(licenses: list) -> str:
    """
    deps.dev 의 Licenses 는 'SPDX 표현식의 배열'이다. 문서상 '이 버전이 사용하는
    라이선스들'이므로 여러 개면 AND 로 묶는다. 항목이 복합식이면 괄호로 감싼다.
      ["MIT"]                              -> "MIT"
      ["MIT", "Apache-2.0 OR BSD-3-Clause"] -> "MIT AND (Apache-2.0 OR BSD-3-Clause)"
    """
    if not licenses:
        return "Unknown"
    if len(licenses) == 1:
        return licenses[0]
    parts = [f"({x})" if re.search(r"\s(?:AND|OR)\s", x) else x for x in licenses]
    return " AND ".join(parts)


def _lookup_offline(registry_key: str, registry_label: str, name: str,
                    fallback_url: str = "Unknown") -> tuple:
    """오프라인 DB 조회 공통 처리. (info, status) 를 반환한다."""
    out = _blank_info(name)
    out["registry"] = registry_label

    if not OFFLINE_DB.has_registry(registry_key):
        out["summary"] = f"오프라인 DB 파일 없음 ({registry_key}) - license_data/packages.sqlite3 확인 필요"
        return out, STATUS_DB_MISSING

    rec = OFFLINE_DB.get(registry_key, name)
    if rec is None:
        out["summary"] = "오프라인 DB 미등록 패키지"
        return out, STATUS_NOT_IN_DB

    out["summary"] = rec["summary"] or "No description"
    out["project_url"] = rec["project_url"] or fallback_url
    if not rec["licenses"]:
        return out, STATUS_NO_LICENSE

    out["license"] = _join_licenses(rec["licenses"])
    # deps.dev 는 SPDX 목록에 없는 라이선스를 'non-standard' 로 표시한다
    nonstandard = any(x.strip().lower() == "non-standard" for x in rec["licenses"])
    out["confidence"] = "medium" if nonstandard else "high"
    return out, STATUS_FOUND


def lookup_pypi(package_name: str) -> tuple:
    real_name = PACKAGE_ALIASES.get(package_name, package_name)
    return _lookup_offline("pypi", "PyPI", real_name,
                           fallback_url=f"https://pypi.org/project/{real_name}/")


def lookup_npm(package_name: str) -> tuple:
    return _lookup_offline("npm", "npm", package_name,
                           fallback_url=f"https://www.npmjs.com/package/{package_name}")


def lookup_maven(coordinate: str) -> tuple:
    """
    coordinate 는 'groupId:artifactId' (pom.xml / build.gradle 에서 추출).
    .java import 로만 얻은 패키지 경로는 정식 좌표가 아니라 조회하지 않는다.
    """
    if ":" not in coordinate:
        out = _blank_info(coordinate)
        out["registry"] = "Maven Central"
        out["summary"] = "Java 패키지 경로만 확인됨 (pom.xml/build.gradle 필요)"
        return out, STATUS_NOT_COORDINATE
    return _lookup_offline("maven", "Maven Central", coordinate)


def lookup_c_header(header: str) -> tuple:
    """
    C 는 중앙 레지스트리가 없어 큐레이션 테이블로 best-effort 판별.
    매핑에 없으면 Unknown(로컬 헤더이거나 미등록 라이브러리).
    """
    out = _blank_info(header)
    out["registry"] = "curated-table"
    for key, (lib, lic, url) in C_HEADER_LIBRARY_MAP.items():
        if header == key or header.startswith(key):
            out["package_name"] = lib
            out["license"] = lic
            out["project_url"] = url
            out["summary"] = f"C library inferred from header '{header}'"
            out["confidence"] = "medium"     # 헤더 기반 추정이므로 medium
            return out, STATUS_CURATED
    out["summary"] = f"미등록 헤더 '{header}' (로컬 헤더이거나 매핑 테이블에 없음)"
    return out, STATUS_NOT_CURATED


def lookup_license(language: str, dep_name: str) -> tuple:
    """언어에 맞는 오프라인 조회를 실행한다. (info, status) 를 반환하며 결과는 캐시된다."""
    cache_key = f"{language}::{dep_name}"
    if cache_key in _LICENSE_CACHE:
        return _LICENSE_CACHE[cache_key]

    if language in ("Python", "Python-Manifest"):
        result = lookup_pypi(dep_name)
    elif language in ("JavaScript", "JavaScript-Manifest"):
        result = lookup_npm(dep_name)
    elif language in ("Java", "Java-Maven", "Java-Gradle"):
        result = lookup_maven(dep_name)
    elif language == "C":
        result = lookup_c_header(dep_name)
    else:
        result = (_blank_info(dep_name), "")

    _LICENSE_CACHE[cache_key] = result
    return result


# LLM 에 넘길 패키지 항목. 판정에 쓸모없는 author/summary/project_url 은 뺀다.
LLM_PACKAGE_FIELDS = ("license", "registry", "confidence")


def packages_for_llm(payload: dict) -> dict:
    """프론트용 전체 정보에서 LLM 판정에 필요한 항목만 골라낸다."""
    slim = {}
    for name, info in payload["packages"].items():
        item = {k: info.get(k) for k in LLM_PACKAGE_FIELDS}
        item["lookup"] = payload["package_status"].get(name, "")
        slim[name] = item
    return slim


# 서버 시작 안내. 측정·시험 스크립트가 설정을 읽으려고 이 모듈을 불러올 때는 출력하지 않는다
# (그 스크립트의 스위치 상태는 엔진 서버와 무관해 헷갈리기 때문). CAPSTONE_QUIET=1 이면 생략.
if not os.environ.get("CAPSTONE_QUIET"):
    print(f"[harness] 켜진 스위치: {harness_config.describe()['enabled'] or '없음 (하네스 적용 전 상태)'} "
          f"({harness_config.FLAGS_SOURCE})")
    print(f"[license_db] 오프라인 DB 형식={OFFLINE_DB.format}, "
          f"레지스트리={OFFLINE_DB.status()['registries']}, 경로={OFFLINE_DB.data_dir}")
    if not CATALOG.loaded:
        print(f"[license_catalog] {CATALOG.error}")
    if not license_compat.OSADL.loaded:
        print(f"[osadl] {license_compat.OSADL.error}")


# =============================================================================
# 입력 처리 : 단일 파일 / ZIP
# =============================================================================

def is_zip(filename: str, content: bytes) -> bool:
    """확장자와 매직넘버(PK) 둘 다로 ZIP 여부 판정."""
    if filename and filename.lower().endswith(".zip"):
        return True
    return content[:2] == b"PK"


def scan_zip(zip_bytes: bytes) -> tuple:
    """
    ZIP 을 풀어 분석 대상 파일을 추리고, 빠진 파일도 종류별로 센다.
- 잡음 폴더 제외, Zip Slip 방어
- 라이선스 판별에 의미 있는 확장자(ANALYZABLE_LANGUAGES)만 통과
    반환: ([{"path": str, "content": bytes}, ...], 빠진 파일 정보)
    """
    results = []
    skipped = {"ignored_dir_files": 0, "unsupported_code_files": 0, "unsupported_other_files": 0,
               "read_failed": 0, "unsupported_code_examples": []}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir():
                continue

            parts = [p.lower() for p in name.replace("\\", "/").split("/")]
            if any(p in IGNORE_DIR_PARTS for p in parts):
                skipped["ignored_dir_files"] += 1
                continue
            if parts[-1] in IGNORE_FILE_NAMES:
                skipped["unsupported_other_files"] += 1
                continue

            norm = os.path.normpath(name)
            if norm.startswith("..") or os.path.isabs(norm):   # Zip Slip 방어
                continue

            # 유효 확장자만 분석 대상으로 통과
            if detect_language(name) not in ANALYZABLE_LANGUAGES:
                if os.path.splitext(parts[-1])[1] in UNSUPPORTED_CODE_EXT:
                    skipped["unsupported_code_files"] += 1
                    if len(skipped["unsupported_code_examples"]) < 20:
                        skipped["unsupported_code_examples"].append(name)
                else:
                    skipped["unsupported_other_files"] += 1
                continue

            try:
                results.append({"path": name, "content": zf.read(info)})
            except Exception:
                skipped["read_failed"] += 1
                continue
    return results, skipped


def extract_zip_files(zip_bytes: bytes) -> list:
    """예전 이름 그대로 (분석 대상 파일 목록만)."""
    return scan_zip(zip_bytes)[0]


def decode_text(content: bytes) -> str:
    return code_window.decode_text(content)


# =============================================================================
# 파일별 분석 재료(payload) 구성
# =============================================================================

def build_file_payload(file_path: str, content: bytes) -> dict:
    """파일 하나에 대한 언어/상단코드/의존성 정보를 모은다. (LLM 호출 전)"""
    language = detect_language(file_path)
    text = decode_text(content)

    deps = extract_dependencies(language, text)
    if len(deps) > MAX_PACKAGES_PER_FILE:
        deps = deps[:MAX_PACKAGES_PER_FILE]     # 프롬프트 폭주 방지

    packages, package_status = {}, {}
    for dep in deps:
        info, status = lookup_license(language, dep)
        packages[info["package_name"]] = info
        package_status[info["package_name"]] = status

    return {
        "file_name": os.path.basename(file_path),
        "file_path": file_path,
        "extension": os.path.splitext(file_path)[1],
        "language": language,
        "top_code_lines": code_window.window(text),
        "packages": packages,                # 프론트 응답용 (기존 형식 그대로)
        "package_status": package_status,    # LLM 전달용 조회 상태
        "text": text,                        # 규칙 판정·답 검증용 (응답에는 넣지 않음)
    }


# =============================================================================
# LLM 프롬프트 (다국어 대응) + 호출
#   설계: 파일 1개당 프롬프트 1개.
#         해당 파일의 상단 50줄과 '그 파일의' 의존성 JSON 만 함께 넣는다.
#         -> 서로 다른 파일의 정보가 섞이지 않아 LLM 혼동이 최소화된다.
#         -> 프롬프트 길이도 파일 단위로 제한되므로 별도 분할이 불필요하다.
# =============================================================================

def build_prompt(payload: dict) -> str:
    language = payload["language"]
    lang_note = ""
    if language == "Java":
        lang_note = ("\n    참고: 이 파일은 Java 소스입니다. Java 는 소스의 import 만으로 외부 "
                     "라이브러리를 특정하기 어렵고, 실제 의존성은 pom.xml / build.gradle 에 "
                     "정의됩니다. 아래 의존성 정보는 보조 참고 자료로만 사용하세요.")
    elif language == "C":
        lang_note = ("\n    참고: 이 파일은 C 소스입니다. C 언어는 중앙 패키지 저장소가 없어 "
                     "아래 라이브러리 정보는 헤더 이름에 근거한 추정입니다. confidence 값을 "
                     "고려하여 신중히 판단하세요.")
    elif language == "License-Text":
        lang_note = ("\n    참고: 이 파일은 라이선스 원문 파일입니다. 본문에 명시된 라이선스를 "
                     "그대로 식별하세요.")

    return f"""
    당신은 엄격한 오픈소스 라이선스 판별 전문가입니다.
    아래는 하나의 소스 파일에 대한 정보입니다. 이 파일 하나만을 대상으로 판별하세요.

    [분석 대상 파일 정보]
- 파일명   : 
{payload['file_name']}
- 파일경로 : 
{payload['file_path']}
- 확장자   : 
{payload['extension']}
- 언어     : 
{payload['language']}{lang_note}

    [해당 파일의 코드 상단 {MAX_TOP_LINES}줄]
    ```
{payload['top_code_lines']}
    ```

    [해당 파일이 사용하는 외부 패키지/라이브러리 정보]
    (registry 는 조회한 저장소, confidence 는 신뢰도, lookup 은 라이선스 조회 결과입니다)
    {json.dumps(packages_for_llm(payload), ensure_ascii=False, indent=2)}

    [지시사항]
1. 위 정보를 바탕으로 '이 소스 파일 자체의 메인 라이선스'를 판별하세요.
       의존 패키지의 라이선스는 판단 근거일 뿐, 그 자체가 이 파일의 라이선스는 아닙니다.
    2. [중요 규칙] 코드 상단에 라이선스가 명시되어 있지 않다면 반드시 'Unknown' 으로
       판별하세요. 'Unlicense' 는 실제로 존재하는 특정 라이선스의 고유 명칭이므로,
       "라이선스가 없다" 는 의미로 절대 'Unlicense' 를 출력하지 마세요.
       (원문에 정확히 'The Unlicense' 라고 적혀 있을 때만 출력)
    3. 결과만 말하지 말고, 제공된 패키지 정보와 코드 내용을 종합해 판별 근거(reasoning)를
       상세히 작성하세요. 의존 패키지 간 라이선스 충돌 위험이 있으면 함께 지적하세요.
    4. [포맷 규칙] "main_license" 값은 반드시 표준 SPDX 식별자 형태로 짧게 답하세요.
       (예: GNU General Public License version 3 -> GPL-3.0,
            Apache License 2.0 -> Apache-2.0, MIT License -> MIT)
    5. 아래 JSON 형식으로만 답하세요. 다른 설명을 덧붙이지 마세요.

    [출력 포맷 (JSON)]
    {{
        "main_license": "판별된 라이선스명 (또는 Unknown)",
        "reasoning": "상세한 분석 근거와 패키지 충돌 위험성 등을 작성"
    }}
    """


def ask_llama3(prompt: str) -> tuple:
    """하네스 적용 전 방식의 LLM 호출: 프롬프트 하나, 형식 강제 없음. (응답 텍스트, 호출 통계)"""
    return llm_client.generate(prompt)


def parse_llm_response(text: str) -> tuple:
    """LLM 응답에서 JSON 부분만 뽑아 (라이선스, 근거) 반환."""
    text = text.strip()
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
        else:
            cleaned = text.replace("```json", "").replace("```", "").strip()
            result = json.loads(cleaned)
        return (result.get("main_license", "Unknown"),
                result.get("reasoning", "이유를 파싱할 수 없습니다."))
    except Exception as e:
        return "Unknown", f"LLM 응답 분석 실패 (오류: {e})"


def _base_result(payload: dict) -> dict:
    return {
        "filename": payload["file_name"],
        "file_path": payload["file_path"],
        "extension": payload["extension"],
        "language": payload["language"],
        "detected_packages": payload["packages"],
    }


def _print_llm_warnings(name: str, stats: dict):
    if stats.get("near_ctx_limit"):
        print(f"⚠️ [문맥 길이 경고] {name}: 입력 {stats.get('prompt_tokens')} + 출력 {stats.get('output_tokens')} "
              f"토큰이 num_ctx {OLLAMA_NUM_CTX} 의 {int(CTX_WARN_RATIO * 100)}% 이상")
    if stats.get("done_reason") == "length":
        print(f"⚠️ [출력 상한 도달] {name}: num_predict {OLLAMA_NUM_PREDICT} 에서 잘림")


def analyze_one_file_legacy(payload: dict) -> dict:
    """하네스 스위치가 모두 꺼졌을 때: 하네스 적용 전과 같은 방식."""
    prompt = build_prompt(payload)
    llm_stats = {"prompt_chars": len(prompt), "num_ctx": OLLAMA_NUM_CTX}
    try:
        raw, llm_stats = ask_llama3(prompt)
        print(f"\n==== [AI RAW RESPONSE: {payload['file_name']}] ====\n{raw}\n{'='*40}\n")
        _print_llm_warnings(payload["file_name"], llm_stats)
        main_license, reason = parse_llm_response(raw)
    except LLMConnectionError:
        raise
    except Exception as e:
        print(f"🚨 [오류] {payload['file_name']} 분석 중 예외: {e}")
        main_license, reason = "Unknown", f"LLM 호출 실패 (오류: {e})"

    return {**_base_result(payload),
            "ai_guessed_main_license": main_license,
            "ai_analysis_reason": reason,
            "llm_stats": llm_stats}          # 측정용 호출 통계 (새 칸, 기존 칸은 그대로)


def analyze_one_file_harness(payload: dict) -> dict:
    """하네스 스위치가 하나라도 켜졌을 때."""
    flags = harness_config.FLAGS
    text = payload.get("text", "")
    out = {**_base_result(payload), "judged_by": "llm", "judge_status": "OK", "failure_type": "",
           "evidence": "", "rule": "", "rule_notes": [], "llm_answer": None, "answer_check": None,
           "llm_stats": None}

    # 1) 규칙 우선 판정
    if flags["RULE_SPDX_TAG"] or flags["RULE_LICENSE_TEXT"]:
        rj = rule_judge.judge(payload["language"], text, flags)
        out["rule_notes"] = rj["skipped"]
        if rj["result"]:
            r = rj["result"]
            out.update({"ai_guessed_main_license": r["license"], "ai_analysis_reason": r["reason"],
                        "evidence": r["evidence"], "judged_by": "rule", "judge_status": r["judge_status"],
                        "rule": r["rule"]})
            out["family"] = CATALOG.normalize(r["license"])["family"]
            out["_llm_conflicts"] = []
            return out

    # 2) LLM 판정
    if not flags["STRUCTURED_OUTPUT"]:
        legacy = analyze_one_file_legacy(payload)
        out.update({k: legacy[k] for k in ("ai_guessed_main_license", "ai_analysis_reason", "llm_stats")})
        if legacy["ai_analysis_reason"].startswith(("LLM 호출 실패", "LLM 응답 분석 실패")):
            out.update({"judge_status": "FAILED", "failure_type": "FORMAT"})
        out["family"] = CATALOG.normalize(out["ai_guessed_main_license"])["family"]
        out["_llm_conflicts"] = []
        return out

    slim = packages_for_llm(payload)
    model, schema, _ = harness_schemas.file_judgment_schema(list(slim))
    if flags["INPUT_SEPARATION"]:
        call = llm_client.call_structured(model, schema, messages=harness_prompts.file_messages(payload, slim))
    else:
        call = llm_client.call_structured(
            model, schema, prompt=harness_prompts.legacy_with_format(build_prompt(payload), list(slim)))
    out["llm_stats"] = call["stats"]
    _print_llm_warnings(payload["file_name"], call["stats"] or {})
    print(f"\n==== [AI RESPONSE: {payload['file_name']}] ok={call['ok']} attempts={call['attempts']} ====\n"
          f"{call['raw']}\n{'='*40}\n")

    if not call["ok"]:
        out.update({"ai_guessed_main_license": "Unknown",
                    "ai_analysis_reason": f"판정 실패: {call['failure_type']} ({call['error'][:200]})",
                    "judge_status": "FAILED", "failure_type": call["failure_type"], "family": "unknown",
                    "_llm_conflicts": []})
        return out

    data = call["data"]
    out["llm_answer"] = {"main_license": data["main_license"], "license_as_written": data["license_as_written"],
                         "conflicts": data["conflicts"]}
    out["evidence"] = data["evidence"]
    out["ai_analysis_reason"] = (data["reasoning"] or "")[:REASONING_MAX_CHARS]
    out["_llm_conflicts"] = data["conflicts"]

    # 3) 답 검증
    if flags["ANSWER_CHECK"]:
        chk = answer_check.check(data, text)
        out["ai_guessed_main_license"] = chk["license"]
        out["answer_check"] = {"failed_rule": chk["failed_rule"], "changes": chk["changes"],
                               "status": chk["status"]}
        out["family"] = chk["family"]
    else:
        lic = data["main_license"]
        if lic == "Other":                       # 검증 없이: 원문 표현을 그대로 (없으면 Unknown)
            lic = data["license_as_written"] or "Unknown"
        out["ai_guessed_main_license"] = lic
        out["family"] = CATALOG.normalize(lic)["family"]
    return out


def analyze_one_file(payload: dict) -> dict:
    """payload 하나를 판정한다. 하네스 스위치가 모두 꺼져 있으면 예전 방식 그대로."""
    if not harness_config.describe()["enabled"]:
        return analyze_one_file_legacy(payload)
    return analyze_one_file_harness(payload)


def data_versions() -> dict:
    db = OFFLINE_DB.status()
    snapshot = None
    for reg_meta in (db.get("meta") or {}).values():
        if isinstance(reg_meta, dict) and (reg_meta.get("snapshot_at") or reg_meta.get("snapshot")):
            snapshot = reg_meta.get("snapshot_at") or reg_meta.get("snapshot")
            break
    choices_date = None
    path = CATALOG.refs_dir / "license_choices.json"
    if path.exists():
        try:
            choices_date = json.loads(path.read_text(encoding="utf-8")).get("generated_at")
        except Exception:
            pass
    return {"deps_dev_snapshot": snapshot, **CATALOG.data_versions(),
            "osadl_matrix": license_compat.OSADL.timestamp or CATALOG.data_versions().get("osadl_matrix"),
            "license_choices": choices_date}


# =============================================================================
# 프로젝트 라이선스 상속 엔진 (Root-to-Leaf Propagation)
#   - 1-Pass: 루트 디렉토리의 LICENSE / COPYING 또는 매니페스트 판정값 탐색
#   - 2-Pass: 단서가 없어 Unknown 으로 판정된 개별 소스코드에 루트 라이선스 상속
# =============================================================================

def resolve_project_licenses(results: list) -> tuple:
    """루트 라이선스를 찾아 Unknown 파일들에 상속하고, (보정된 results, 루트 라이선스)를 반환."""
    root_license = "Unknown"
    root_source_file = ""

    # 1-Pass: LICENSE, COPYING 등 라이선스 원문 파일 판정값 우선 탐색
    for r in results:
        fname = r["filename"].upper()
        if ("LICENSE" in fname or "COPYING" in fname) and r["ai_guessed_main_license"] != "Unknown":
            root_license = r["ai_guessed_main_license"]
            root_source_file = r["filename"]
            break

    # 라이선스 파일이 없다면 package.json, pom.xml 등 매니페스트 검토
    if root_license == "Unknown":
        for r in results:
            if r["language"] in ("JavaScript-Manifest", "Java-Maven") and r["ai_guessed_main_license"] != "Unknown":
                root_license = r["ai_guessed_main_license"]
                root_source_file = r["filename"]
                break

    # 2-Pass: 단서가 없어 Unknown으로 안전 격리된 소스 파일에 상속 적용
    for r in results:
        orig = r["ai_guessed_main_license"]
        if orig == "Unknown" and root_license != "Unknown":
            r["inherited_license"] = root_license
            r["is_inherited"] = True
            r["ai_analysis_reason"] += f" (본문 단서 부재로 프로젝트 루트 '{root_source_file}'의 {root_license} 라이선스를 상속 적용)"
        else:
            r["inherited_license"] = orig
            r["is_inherited"] = False

    return results, root_license


# =============================================================================
# 엔드포인트
# =============================================================================

@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    """
    단일 소스 파일 또는 ZIP 을 받아 라이선스를 분석한다.

    응답 구조
- results : 파일별 분석 결과 목록 (ZIP 이면 여러 개)
- summary : 전체 요약
- 단일 파일인 경우 기존 키(filename/detected_packages/ai_guessed_main_license/
        ai_analysis_reason)도 최상위에 그대로 포함 -> test_runner.py 호환 유지
    """
    content = await file.read()
    filename = file.filename or "unknown"

    # ---- 입력 분기 및 예외 처리 -------------------------------------------
    if not content:
        return {"status": "error", "message": "빈 파일입니다.", "results": []}

    skipped = None
    if is_zip(filename, content):
        try:
            targets, skipped = scan_zip(content)
        except zipfile.BadZipFile:
            return {"status": "error",
                    "message": "손상되었거나 올바른 ZIP 파일이 아닙니다.", "results": []}
        except Exception as e:
            return {"status": "error", "message": f"ZIP 처리 오류: {e}", "results": []}

        if not targets:
            return {"status": "error",
                    "message": ("ZIP 안에 분석 가능한 파일이 없습니다. "
                                "지원 대상: .py, .js, .java, .c, pom.xml, build.gradle, "
                                "package.json, requirements.txt, LICENSE"),
                    "results": []}
    else:
        language = detect_language(filename)
        if language not in ANALYZABLE_LANGUAGES:
            return {"status": "error",
                    "message": (f"지원하지 않는 파일 형식입니다: {filename} "
                                f"(감지된 종류: {language})"),
                    "results": []}
        targets = [{"path": filename, "content": content}]

    harness_on = bool(harness_config.describe()["enabled"])

    # ---- 분석 전 LLM 서버 확인 (꺼져 있으면 파일마다 실패하는 대신 요청 전체를 오류로) ------
    ok, msg = llm_client.check_server()
    if not ok:
        return {"status": "error", "message": msg, "results": []}

    # ---- 파일별 분석 (각 파일마다 독립된 프롬프트) --------------------------
    results = []
    try:
        for t in targets:
            payload = build_file_payload(t["path"], t["content"])
            results.append(analyze_one_file(payload))
    except LLMConnectionError as e:
        return {"status": "error", "message": f"분석 중 LLM 서버 연결이 끊겼습니다: {e}", "results": []}

    # 💡 [신규 추가] 2-Pass 상속 엔진 가동 (Unknown 79.9% 구제)
    results, root_project_license = resolve_project_licenses(results)

    # ---- 요약 -------------------------------------------------------------
    license_counts = {}
    for r in results:
        # 상속된 라이선스를 기준으로 전체 분포 집계
        lic = r.get("inherited_license") or r["ai_guessed_main_license"]
        license_counts[lic] = license_counts.get(lic, 0) + 1

    response = {
        "status": "success",
        "input_type": "zip" if len(targets) > 1 or is_zip(filename, content) else "single",
        "root_project_license": root_project_license,  # 프로젝트 대표 라이선스 명시
        "results": results,
        "summary": {
            "total_files": len(results),
            "languages": sorted({r["language"] for r in results}),
            "license_distribution": license_counts,
        },
    }

    if skipped is not None:
        response["skipped"] = skipped

    # ---- 하네스: 프로젝트 전체 충돌 판정, 데이터 기준 시점 ------------------------
    if harness_on:
        if harness_config.FLAGS["CONFLICT_CHECK"]:
            files = [{"file_path": r["file_path"],
                      "license": r.get("inherited_license") or r["ai_guessed_main_license"],
                      "judge_status": r.get("judge_status", "OK"),
                      "packages": {n: (info or {}).get("license", "Unknown")
                                   for n, info in (r.get("detected_packages") or {}).items()},
                      "llm_conflicts": r.get("_llm_conflicts") or []} for r in results]
            unanalyzed = (skipped or {}).get("unsupported_code_files", 0)
            try:
                cc = license_compat.project_conflicts(files, unanalyzed, llm_client.call_structured)
            except LLMConnectionError:
                cc = license_compat.project_conflicts(files, unanalyzed, None)
            response["conflict_summary"] = cc["summary"]
            for r in results:
                r["conflicts"] = cc["per_file"].get(r["file_path"], [])
        response["data_versions"] = data_versions()
    for r in results:
        r.pop("_llm_conflicts", None)

    # ---- 하위 호환 (단일 파일일 때 기존 키 유지) ---------------------------
    if len(results) == 1:
        first = results[0]
        response.update({
            "filename": first["filename"],
            "detected_packages": first["detected_packages"],
            "ai_guessed_main_license": first.get("inherited_license") or first["ai_guessed_main_license"],
            "ai_analysis_reason": first["ai_analysis_reason"],
        })

    return response


@app.get("/health")
def health():
    """의존성 설치 상태 점검용 (tree-sitter 사용 가능 여부, 오프라인 DB 상태 확인)."""
    _get_parser("Python")
    return {
        "status": "ok",
        "tree_sitter": bool(_TS_AVAILABLE),
        "cached_lookups": len(_LICENSE_CACHE),
        "offline_db": OFFLINE_DB.status(),
        "llm": {"model": OLLAMA_MODEL, "options": llm_options()},
        "harness": harness_config.describe(),
        "license_catalog": CATALOG.status(),
        "osadl": license_compat.status(),
    }