<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 문서·CI·개발 도구 계약 테스트

## 목적과 주요 파일

README·AGENTS 지침·CI·개발 도구 설정의 정적 회귀 테스트다. 이 디렉터리의 테스트와 `AGENTS.md`는 저장소에서 추적하며 checkout과 CI에서 사용한다. 현재 `.gitignore`의 `/docs/` 규칙은 루트 경로에만 적용되고 `tests/docs`를 제외하지 않는다.

| 파일 | 역할 |
| --- | --- |
| `test_readme.py` | JWT 설명·JSON:API 인증 curl·stdin secret 전달·worker 절차·검증 명령과 GitHub Actions 계약 |
| `test_agents_guides.py` | concern API 이름, controller 테스트 네 파일, JSON 직렬화 계약과 문서 검증 명령의 literal 회귀 |
| `test_tooling.py` | strict mypy·namespace package 설정, 개발 의존성·poe task·pre-commit 타입 검사 계약 |

## 작업 규칙과 공통 패턴

- README는 module import 시 읽는다. 테스트는 안내문과 curl 예제의 문자열을 검사하며 실제 API 요청을 보내지 않는다.
- 인증 예제는 비밀번호를 조용히 읽고 private shell 변수에 응답을 보관하며, JSON encoder와 `--data-binary @-`로 비밀번호·refresh token을 stdin에 전달하는 계약을 유지한다.
- JSON:API의 Accept·Content-Type, Bearer 헤더, refresh token 보관과 logout 뒤 access token 잔여 수명 설명을 함께 검토한다.
- CI YAML은 저장소의 정적 파일을 `yaml.BaseLoader`로 읽어 push/pull_request, read-only contents 권한과 frozen sync → check → Compose 검사 → runtime 이미지 build 순서를 확인한다.
- AGENTS 검증은 문구와 코드 심벌을 직접 비교한다. controller 지침의 `이 디렉터리의 네 파일` 및 네 테스트 파일명, concern의 `document_parsing.reject_query_parameters`, JSON 응답의 `model_dump_json(by_alias=True, exclude_none=True)`·`pydantic-core`·`0.00001` 설명을 실제 구현과 함께 갱신한다.
- README의 검증 명령 목록과 관련 AGENTS의 좁은 테스트 명령은 literal 계약이다. 동작이 같아 보이는 문장 재구성으로 예제를 바꾸기 전에 테스트 기대값을 확인한다.
- mypy는 tests를 포함하며 `db` namespace package에는 `explicit_package_bases = true`와 `mypy_path = "."`가 필요하다. 개발 의존성의 poe·PyYAML·types-PyYAML, 중복 없는 poe task와 전체 check의 shell 진입점, pre-commit의 프로젝트 환경 `uv run mypy`를 함께 검사한다.

## 검증

상위 실행 규칙에 따라 `TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킨 상태에서 `uv run pytest --no-cov tests/docs -q`를 사용한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 전체 게이트를 사용한다. 이 디렉터리의 테스트 자체는 DB fixture를 요청하지 않으며 검사할 문서·설정 파일이 checkout에 있어야 한다.

## 의존성

- 내부: `README.md`, 검사 대상 `AGENTS.md`, `.github/workflows/ci.yml`, `pyproject.toml`, `.pre-commit-config.yaml`, 상위 pytest 설정.
- 외부: pytest, PyYAML. 테스트가 shell 예제를 실제 실행하는 것은 아니다.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
