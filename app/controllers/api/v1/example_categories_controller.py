"""Example category read-only JSON:API resource controller."""

from pydantic import BaseModel

from app.controllers.concerns import CrudActions
from app.models import ExampleCategory
from app.schemas import EXAMPLE_CATEGORY_QUERY_POLICY
from app.serializers import ExampleCategorySerializer


class ExampleCategoriesController(CrudActions[ExampleCategory, BaseModel, BaseModel, BaseModel]):
    """Expose example categories as a read-only collection.

    분류는 서버가 관리하는 참조 데이터다. 쓰기 라우트를 열지 않으므로
    ``create_schema``·``update_schema``·``replace_schema``를 선언하지 않는다 —
    ``enable_writes = False``가 그 셋을 읽지 않게 한다.

    이 자원이 존재하는 이유는 관계 선택기다. 분류는 Example의 관계로만
    노출되어 있어서, 폼이 고를 목록을 가져올 곳이 없었다.
    """

    model_class = ExampleCategory
    serializer_class = ExampleCategorySerializer
    query_policy = EXAMPLE_CATEGORY_QUERY_POLICY
    enable_writes = False
