from typing import Callable

REGISTRY: dict[str, Callable] = {}


def register_tool(name: str):
    """Decorator: ``@register_tool('gmail.search_emails')``."""

    def decorator(fn: Callable) -> Callable:
        REGISTRY[name] = fn
        return fn

    return decorator


def get_tool(name: str) -> Callable:
    if name not in REGISTRY:
        raise KeyError(
            f"Tool '{name}' not registered. Available: {list(REGISTRY.keys())}"
        )
    return REGISTRY[name]
