import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings


class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class ChatProvider(Protocol):
    async def answer(self, *, question: str, contexts: list[str], history: list[str]) -> str: ...


class LocalHashEmbeddingProvider:
    """Deterministic, dependency-free embedding for development only.

    It hashes Chinese characters, character bigrams and latin words into a fixed
    vector. This is not a production semantic model, but keeps the entire ingest and
    retrieval path executable before model credentials are available.
    """

    model_name = "local-hash-v1"

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        normalized = re.sub(r"\s+", " ", text.lower()).strip()
        cjk = re.findall(r"[\u3400-\u9fff]", normalized)
        bigrams = ["".join(cjk[index : index + 2]) for index in range(len(cjk) - 1)]
        latin = re.findall(r"[a-z0-9][a-z0-9_.-]*", normalized)
        features = cjk + bigrams + latin
        vector = [0.0] * self.dimensions
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class LocalGroundedChatProvider:
    async def answer(self, *, question: str, contexts: list[str], history: list[str]) -> str:
        del question, history
        if not contexts:
            return "我暂时没有找到可靠信息，已为您转接人工客服。"
        # The local provider deliberately quotes retrieved knowledge rather than
        # pretending to be a language model. It makes grounding failures visible.
        best = contexts[0].strip()
        if len(best) > 500:
            best = best[:500].rstrip() + "…"
        return f"根据店铺的已发布规则：{best}"


@dataclass(slots=True)
class OpenAICompatibleProvider:
    base_url: str
    api_key: str
    chat_model: str
    embedding_model: str
    dimensions: int
    model_name: str = ""

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.model_name = self.embedding_model

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, object] = {"model": self.embedding_model, "input": texts}
        if self.dimensions:
            payload["dimensions"] = self.dimensions
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()["data"]
        ordered = sorted(data, key=lambda item: item["index"])
        return [item["embedding"] for item in ordered]

    async def answer(self, *, question: str, contexts: list[str], history: list[str]) -> str:
        context_text = "\n\n".join(
            f"[知识{index + 1}] {context}" for index, context in enumerate(contexts)
        )
        system_prompt = (
            "你是商家的AI客服。只能依据提供的已发布知识回答，不得编造。"
            "如果知识不足，明确说明无法确认并建议转人工。"
            "不得承诺退款、赔付、改价或其他未授权操作。"
        )
        user_prompt = (
            f"最近对话：\n{'\n'.join(history[-6:])}\n\n"
            f"可用知识：\n{context_text}\n\n"
            f"客户问题：{question}"
        )
        payload = {
            "model": self.chat_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
        return result["choices"][0]["message"]["content"].strip()


def build_ai_providers(settings: Settings) -> tuple[EmbeddingProvider, ChatProvider]:
    if settings.ai_provider == "local":
        return (
            LocalHashEmbeddingProvider(settings.ai_embedding_dimensions),
            LocalGroundedChatProvider(),
        )
    if settings.ai_provider == "openai_compatible":
        missing = [
            name
            for name, value in {
                "AI_BASE_URL": settings.ai_base_url,
                "AI_API_KEY": settings.ai_api_key,
                "AI_CHAT_MODEL": settings.ai_chat_model,
                "AI_EMBEDDING_MODEL": settings.ai_embedding_model,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"missing AI provider settings: {', '.join(missing)}")
        provider = OpenAICompatibleProvider(
            base_url=settings.ai_base_url,
            api_key=settings.ai_api_key,
            chat_model=settings.ai_chat_model,
            embedding_model=settings.ai_embedding_model,
            dimensions=settings.ai_embedding_dimensions,
        )
        return provider, provider
    raise ValueError(f"unsupported AI_PROVIDER: {settings.ai_provider}")

