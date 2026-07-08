import hashlib
import math
import random

from backend.config import settings


def _deterministic_vector(text: str, dim: int = 1024) -> list[float]:
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    # Gaussian components give a uniformly-random direction; L2-normalizing to a
    # unit vector makes cosine ordering reproducible offline (same text ⇒ same
    # vector, no model server).
    values = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0.0:
        return values
    return [v / norm for v in values]


class FakeEmbedder:
    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim if dim is not None else settings.EMBED_DIM

    async def embed_query(self, text: str, user_id: str | None = None) -> list[float]:
        return _deterministic_vector(text, self.dim)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_deterministic_vector(t, self.dim) for t in texts]
