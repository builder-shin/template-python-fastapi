<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 설계 문서

## 목적

JWT 인증·Dramatiq 도입 설계와 구현 계획, category·tag 읽기 전용 API 구현 계획을 모은다. 이 디렉터리의 설계·계획과 `AGENTS.md`는 명시적으로 Git 추적에 포함한다. `.gitignore`의 `/docs/` 패턴은 새 미추적 파일에 적용되며, `.dockerignore`는 문서를 이미지 build context에서 제외한다.

## 하위 디렉터리

| 디렉터리 | 역할 |
| --- | --- |
| [superpowers/](superpowers/AGENTS.md) | 인증·worker 설계와 인증·참조 자원 구현 계획 |

## 작업·검증 지침

- 공개 실행·운영 예제는 루트 `README.md`가 담당한다. 기존 계획은 배경 자료로 사용하고 현재 구현·테스트와 대조한다.
- 계획의 체크박스만으로 구현 완료 여부를 판단하지 않는다. 문서 갱신 요청을 과거 계획의 실행 요청으로 해석하지 않는다.
- 문서의 파일명·상대 링크·명령을 확인한다. 실행 동작을 바꾼 경우에는 해당 코드 디렉터리의 검증 지침을 따른다.

## 의존성

- 내부: `README.md`, `app/`, `config/`, `db/migrations/`, `tests/`의 구현과 계약.
- 외부: 문서 자체의 실행 의존성은 없다.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
