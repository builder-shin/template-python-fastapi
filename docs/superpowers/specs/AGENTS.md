<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 설계 명세

## 목적과 주요 파일

| 파일 | 역할 |
| --- | --- |
| `2026-07-15-jwt-dramatiq-design.md` | JWT 인증·refresh session·Example 쓰기 보호·Redis worker 도입의 목표와 계약 |

## 작업 지침과 공통 패턴

- User/RefreshSession, token claim·수명, JSON:API 인증 경로·type·오류, broker·actor, Compose·health, migration·seed와 테스트 전략을 설명한다.
- 기존 설계의 목표와 비목표를 구분해 보존한다. 별도 repository/service 계층, 자동 seed·enqueue, SQLite 우회는 루트 지침에 따라 추가하지 않는다.
- 현재 동작을 설명할 때는 이 과거 설계만 근거로 단정하지 않고 소스·테스트와 대조한다. 요구사항 변경은 대응 구현 계획과 README에 미치는 영향도 확인한다.

## 검증

- 문서 변경은 관련 코드 경로, HTTP method·resource type·오류 code와 설정 이름을 확인한다.
- 구현까지 변경되면 루트의 실제 PostgreSQL·전체 검증 명령을 적용한다. 문서 검토만으로 구현 검증을 대신하지 않는다.

## 의존성

- 내부: [구현 계획 지침](../plans/AGENTS.md), `app/auth`, `app/controllers`, `config`, `db/migrations`, `tests`.
- 외부: 설계 대상은 FastAPI·Pydantic·SQLAlchemy·PostgreSQL, PyJWT·Argon2, Dramatiq·Redis다.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
