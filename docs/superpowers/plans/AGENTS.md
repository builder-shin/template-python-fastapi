<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 구현 계획

## 목적과 주요 파일

| 파일 | 역할 |
| --- | --- |
| `2026-07-15-fastapi-jwt-dramatiq.md` | 인증 설정부터 토큰·모델·API·worker·health·CI까지 11개 작업과 검증 절차 |
| `2026-09-04-reference-resource-read-routes.md` | `enable_writes` 옵션, category·tag canonical 링크와 읽기 API, 통합 검증의 4개 작업 |

## 작업 지침과 공통 패턴

- 각 작업은 파일 목록, 실패 테스트·예상 결과, 구현, 통과 확인과 커밋 예시를 포함한다. 체크박스는 현재 코드의 완료 상태를 보증하지 않는다.
- 인증 계획의 대응 설계는 `../specs/2026-07-15-jwt-dramatiq-design.md`다. 참조 자원 계획의 Spec은 다른 저장소의 문서를 가리키므로 해당 파일의 존재를 먼저 확인한다. 요구사항과 실행 절차를 혼합해 변경하지 않는다.
- 문서 안의 migration downgrade, 컨테이너·volume 정리와 커밋 명령은 계획 예시다. 실제 실행 요청이 있을 때 현재 대상과 사용자 범위를 확인한다.
- 인증·CRUD는 동기식 PostgreSQL transaction과 서로 다른 인증/쓰기 세션을 사용한다는 계획의 경계를 유지한다. 현재 인증 조회의 factory·짧은 session 수명과 잠금 계약은 `app/auth`·`config/database.py` 및 해당 테스트를 기준으로 확인한다.
- 참조 자원은 `/api/v1/categories`·`/api/v1/tags`, 기존 `exampleCategories`·`exampleTags` type을 사용하고 쓰기·관계 route를 노출하지 않는다. 조회 허용 범위와 추가 인덱스를 만들지 않는 근거는 현재 schema 정책을 함께 읽는다.

## 검증

- 문서 편집은 파일 경로, 설계 링크, 작업 간 의존성, 명령의 저장소 루트 기준을 확인한다.
- 구현을 실행할 때 좁은 pytest에는 독립된 `*_test` PostgreSQL을 가리키는 `TEST_DATABASE_URL`이 필요하다. 없으면 `./scripts/check.sh`의 임시 PostgreSQL 전체 게이트를 사용한다.
- migration·seed의 DB 선택은 서로 다를 수 있으므로 각각 `db/migrations/AGENTS.md`, `db/AGENTS.md`를 먼저 따른다.

## 의존성

- 내부: [설계 문서 지침](../specs/AGENTS.md), 루트 및 코드·테스트별 `AGENTS.md`, `scripts/check.sh`.
- 외부: 문서는 uv, Bash, Docker Compose, PostgreSQL을 사용하는 실행 절차를 설명한다.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
