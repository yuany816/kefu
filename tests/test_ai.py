import math

import pytest

from app.services.ai import LocalHashEmbeddingProvider


@pytest.mark.asyncio
async def test_local_embedding_is_deterministic_and_normalized() -> None:
    provider = LocalHashEmbeddingProvider(dimensions=1024)
    first, second = await provider.embed(["签收后可以退货吗", "签收后可以退货吗"])

    assert first == second
    assert len(first) == 1024
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)

