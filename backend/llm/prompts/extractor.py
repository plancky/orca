import json
from typing import Any

from pydantic import BaseModel, Field


class ExtractedValue(BaseModel):
    value: Any | None = Field(default=None, description="The extracted value")


EXTRACTOR_SYSTEM_PROMPT = """You are a precision extraction assistant.
You are given a JSON context from an upstream tool output and a field name to extract.
Find the requested field or concept in the context and return its exact value.
If it is not present, return null for the value.
"""


def build_extractor_prompt(context: Any, field_name: str) -> list[dict]:
    return [
        {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Context:\n{json.dumps(context, default=str)}\n\n"
            f"Extract: {field_name}\nReturn as JSON.",
        },
    ]
