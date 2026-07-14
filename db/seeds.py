"""Deterministic seed data for local and development environments."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Example, ExampleCategory, ExampleStatus, ExampleTag, example_tags
from config.database import SessionFactory

SEED_CATEGORY_ID = UUID("00000000-0000-4000-8000-000000000001")
SEED_TAG_ID = UUID("00000000-0000-4000-8000-000000000002")
SEED_EXAMPLE_ID = UUID("00000000-0000-4000-8000-000000000003")


def seed(session: Session) -> None:
    """Insert the example seed graph within the caller-owned transaction."""

    session.execute(
        insert(ExampleCategory)
        .values(id=SEED_CATEGORY_ID, name="기본 카테고리")
        .on_conflict_do_update(
            index_elements=[ExampleCategory.id],
            set_={"name": "기본 카테고리", "updated_at": func.now()},
            where=ExampleCategory.name.is_distinct_from("기본 카테고리"),
        )
    )
    session.execute(
        insert(ExampleTag)
        .values(id=SEED_TAG_ID, name="기본 태그")
        .on_conflict_do_update(
            index_elements=[ExampleTag.id],
            set_={"name": "기본 태그", "updated_at": func.now()},
            where=ExampleTag.name.is_distinct_from("기본 태그"),
        )
    )
    session.execute(
        insert(Example)
        .values(
            id=SEED_EXAMPLE_ID,
            title="JSON:API 예시",
            description="JSON:API와 CRUD 동작을 확인하기 위한 기본 데이터입니다.",
            status=ExampleStatus.ACTIVE,
            score=90,
            category_id=SEED_CATEGORY_ID,
        )
        .on_conflict_do_update(
            index_elements=[Example.id],
            set_={
                "title": "JSON:API 예시",
                "description": "JSON:API와 CRUD 동작을 확인하기 위한 기본 데이터입니다.",
                "status": ExampleStatus.ACTIVE,
                "score": 90,
                "category_id": SEED_CATEGORY_ID,
                "updated_at": func.now(),
            },
            where=or_(
                Example.title.is_distinct_from("JSON:API 예시"),
                Example.description.is_distinct_from("JSON:API와 CRUD 동작을 확인하기 위한 기본 데이터입니다."),
                Example.status.is_distinct_from(ExampleStatus.ACTIVE),
                Example.score.is_distinct_from(90),
                Example.category_id.is_distinct_from(SEED_CATEGORY_ID),
            ),
        )
    )
    session.execute(
        insert(example_tags)
        .values(example_id=SEED_EXAMPLE_ID, tag_id=SEED_TAG_ID)
        .on_conflict_do_nothing(index_elements=[example_tags.c.example_id, example_tags.c.tag_id])
    )


def main() -> None:
    """Seed the configured database from the command line."""

    with SessionFactory.begin() as session:
        seed(session)


if __name__ == "__main__":
    main()
