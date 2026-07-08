from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class JSONRepairError(Exception):
    pass


async def extract_and_validate(
    text: str,
    model_cls: Type[T],
    llm_client=None,
    repair_prompt: str | None = None,
) -> T:
    raise NotImplementedError("Wave B2 fills this")
