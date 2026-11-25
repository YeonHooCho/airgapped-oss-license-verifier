# -*- coding: utf-8 -*-
"""
license_db.py - 오프라인 패키지 라이선스 조회 모듈

deps.dev 에서 받아 만든 license_data/ 폴더의 파일(packages.sqlite3)만 읽는다.
이 모듈은 인터넷에 접속하지 않는다.

지원 형식 (있는 것을 자동으로 사용, 둘 다 있으면 SQLite 우선)
  1) license_data/packages.sqlite3
  2) license_data/pypi.json, license_data/npm.json, license_data/maven.json

get() 반환값
  {"name", "version", "licenses": [SPDX 표현식, ...], "summary", "project_url"}
  DB 에 없으면 None

단독 실행으로 조회 확인
  python license_db.py                  -> DB 상태 출력
  python license_db.py pypi requests    -> 한 패키지 조회
"""

import json
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Optional

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "license_data"
REGISTRIES = ("pypi", "npm", "maven")
SQLITE_NAME = "packages.sqlite3"


def normalize_name(registry: str, name: str) -> str:
    """
    레지스트리별 패키지 이름 정규화. 저장할 때와 조회할 때 같은 함수를 쓴다.
      - PyPI : PEP 503 규칙. 대소문자 무시, '-', '_', '.' 연속은 '-' 하나로
               (PyYAML -> pyyaml, python_dateutil -> python-dateutil)
      - npm  : 이름 그대로 (@scope/name 포함)
      - Maven: groupId:artifactId 그대로
    """
    name = (name or "").strip()
    if registry == "pypi":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name


class OfflineLicenseDB:
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self._lock = threading.Lock()
        self._json_tables = {}      # registry -> {name: [version, licenses, summary, url]}
        self._json_meta = {}        # registry -> meta
        self._conn = None
        self._sqlite_registries = set()

        sqlite_path = self.data_dir / SQLITE_NAME
        if sqlite_path.is_file():
            self.format = "sqlite"
            # 읽기 전용으로 연결 (실행 중 DB 파일이 바뀌지 않도록)
            uri = sqlite_path.resolve().as_uri() + "?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            # 레지스트리 목록은 meta 표에서 읽는다 (packages 전체를 훑지 않도록)
            rows = self._conn.execute("SELECT key FROM meta").fetchall()
            self._sqlite_registries = {r[0] for r in rows}
        elif any(self._json_path(r).is_file() for r in REGISTRIES):
            self.format = "json"
        else:
            self.format = None

    # ------------------------------------------------------------------
    def _json_path(self, registry: str) -> Path:
        return self.data_dir / f"{registry}.json"

    def _load_json(self, registry: str) -> dict:
        """JSON 은 처음 조회할 때 한 번만 읽어 메모리에 둔다."""
        if registry in self._json_tables:
            return self._json_tables[registry]
        with self._lock:
            if registry not in self._json_tables:
                path = self._json_path(registry)
                table, meta = {}, {}
                if path.is_file():
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    table = data.get("packages", {})
                    meta = data.get("_meta", {})
                self._json_tables[registry] = table
                self._json_meta[registry] = meta
        return self._json_tables[registry]

    # ------------------------------------------------------------------
    def has_registry(self, registry: str) -> bool:
        """해당 레지스트리 데이터 파일이 있는지 ('미등록'과 '파일 없음'을 구분하는 데 사용)."""
        if self.format == "sqlite":
            return registry in self._sqlite_registries
        if self.format == "json":
            return self._json_path(registry).is_file()
        return False

    def get(self, registry: str, name: str) -> Optional[dict]:
        key = normalize_name(registry, name)
        if not key:
            return None

        if self.format == "sqlite":
            with self._lock:
                row = self._conn.execute(
                    "SELECT name, version, licenses, summary, project_url "
                    "FROM packages WHERE registry = ? AND name = ?",
                    (registry, key),
                ).fetchone()
            if row is None:
                return None
            return {
                "name": row[0],
                "version": row[1] or "",
                "licenses": json.loads(row[2] or "[]"),
                "summary": row[3] or "",
                "project_url": row[4] or "",
            }

        if self.format == "json":
            rec = self._load_json(registry).get(key)
            if rec is None:
                return None
            version, licenses, summary, url = rec
            return {
                "name": key,
                "version": version or "",
                "licenses": list(licenses or []),
                "summary": summary or "",
                "project_url": url or "",
            }

        return None

    def status(self) -> dict:
        """/health 와 서버 시작 로그용 상태 정보."""
        info = {
            "format": self.format,
            "data_dir": str(self.data_dir),
            "registries": [r for r in REGISTRIES if self.has_registry(r)],
        }
        if self.format == "sqlite":
            rows = self._conn.execute("SELECT key, value FROM meta").fetchall()
            info["meta"] = {k: json.loads(v) for k, v in rows}
        elif self.format == "json":
            # JSON 은 메타만 보려고 전체를 읽지 않는다. 이미 읽은 것만 표시.
            info["meta"] = {r: self._json_meta[r] for r in self._json_meta}
        return info


if __name__ == "__main__":
    db = OfflineLicenseDB()
    if len(sys.argv) == 3:
        result = db.get(sys.argv[1], sys.argv[2])
        print(json.dumps(result, ensure_ascii=False, indent=2) if result else "DB 미등록")
    else:
        print(json.dumps(db.status(), ensure_ascii=False, indent=2))
