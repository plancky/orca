from abc import ABC, abstractmethod


class Provider(ABC):
    @abstractmethod
    async def search(self, service: str, query: str, filters: dict) -> list[dict]: ...
    @abstractmethod
    async def get(self, service: str, item_id: str) -> dict: ...
    @abstractmethod
    async def execute(self, service: str, action: str, args: dict) -> dict: ...


class BaseAgent:
    def __init__(self, provider: Provider):
        self.provider = provider

    async def search(self, query: str, filters: dict | None = None) -> list[dict]:
        raise NotImplementedError

    async def get_context(self, item_id: str) -> dict:
        raise NotImplementedError

    async def execute(self, action: str, args: dict) -> dict:
        raise NotImplementedError
