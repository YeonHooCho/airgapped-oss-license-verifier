# 🛡️ 폐쇄망 기반 온프레미스 오픈소스 라이선스 컴플라이언스 검증 시스템

> **2026 교내 캡스톤 디자인 경진대회 입상 (7위 / 24팀)**  
> 외부 인터넷 연결이 차단된 보안(Air-gapped) 환경에서도 소스코드와 의존성 패키지의 오픈소스 라이선스 충돌 및 의무사항 위반을 자동 검증하는 2-Track 하이브리드 분석 솔루션입니다.

---

## 📌 프로젝트 배경 및 해결 문제
- 기업 및 국방·금융권의 폐쇄망 개발 환경에서는 외부 SaaS형 오픈소스 검증 도구를 사용할 수 없어, 수작업 검증에 따른 휴먼 에러와 라이선스 위반 리스크가 존재합니다.
- 본 시스템은 **정적 구문 분석(Tree-sitter AST)**과 **온프레미스 LLM(Ollama)**을 결합한 2-Track 파이프라인을 구축하여, 외부 네트워크 없이도 고지문 누락과 코드 내 라이선스 표기를 정밀하게 판별합니다.

## 🏗️ 4계층 시스템 아키텍처
1. **Presentation Layer (React):** 프로젝트 업로드, 검출된 라이선스 충돌 리포트 시각화, 소스코드 뷰어 제공
2. **Application Layer (Java Spring Boot):** 사용자 요청 처리, 분석 작업 오케스트레이션, 이력 관리 및 REST API 제공
3. **Analysis & AI Engine (Python FastAPI):**
   - **Track 1 (Rule & AST 기반):** Tree-sitter AST 파싱을 통한 소스코드 헤더·주석 및 패키지 메타데이터 추출, 독일 **OSADL** 호환성 규칙 DB(SQLite) 매칭
   - **Track 2 (Local LLM 기반):** 로컬 **Ollama (`qwen2.5-coder:7b`)** 모델을 활용한 비정형 라이선스 문구 추론 및 Unknown 케이스 보완 판별
4. **Data Layer (SQLite):** 오프라인 환경에서 즉시 조회 가능한 OSADL 라이선스 규칙 및 검증 결과 저장소

## 📊 주요 검증 성과
- **초기 벤치마크 정확도:** 30개 실제 오픈소스 프로젝트 대상 검증에서 **93.3% 판별 정확도** 달성
- **확장성 검증:** 100개 오픈소스 프로젝트 데이터셋 기반 통합 파이프라인 안정성 및 처리 성능 검증 완료
- **대외 성과:** 2026년 11월 캡스톤 디자인 경진대회 전체 24개 팀 중 **7위 입상** 및 관련 연구 논문 작성

## 🛠️ 기술 스택
- **Frontend:** React, JavaScript/TypeScript
- **Backend:** Java, Spring Boot
- **AI & Static Analysis:** Python, FastAPI, Tree-sitter AST, Ollama (`qwen2.5-coder:7b`)
- **Database & Rules:** SQLite, German OSADL License Matrix

## 👥 팀 구성 (Team L.A.W)
- **조연후 (팀장):** 시스템 아키텍처 설계, 백엔드·AI 파이프라인 통합 및 성능 검증 총괄
- **송강민:** 프론트엔드 개발 및 UX/UI 설계
- **양지운:** AI 추론 및 핵심 라이선스 분석 엔진 개발
