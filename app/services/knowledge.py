import hashlib
from uuid import UUID

from app.domain.models import DocumentOut, SearchHit, TextDocumentCreate
from app.infra.repository import Repository
from app.services.ai import EmbeddingProvider
from app.services.chunking import TextChunker


class KnowledgeService:
    def __init__(
        self,
        repository: Repository,
        embedding_provider: EmbeddingProvider,
        chunker: TextChunker,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.chunker = chunker

    async def ingest_text(
        self,
        *,
        tenant_id: UUID,
        document: TextDocumentCreate,
        source_type: str = "text",
        object_key: str | None = None,
    ) -> DocumentOut:
        chunks = self.chunker.split(document.content)
        if not chunks:
            raise ValueError("document has no indexable content")
        embeddings = await self.embedding_provider.embed([chunk.content for chunk in chunks])
        if any(len(vector) != 1024 for vector in embeddings):
            raise ValueError(
                "the current database index requires 1024-dimensional embeddings; "
                "set AI_EMBEDDING_DIMENSIONS=1024"
            )
        checksum = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        result = self.repository.create_document(
            tenant_id=tenant_id,
            document=document,
            checksum=checksum,
            chunks=chunks,
            embeddings=embeddings,
            embedding_model=self.embedding_provider.model_name,
            source_type=source_type,
            object_key=object_key,
        )
        return DocumentOut.model_validate(result)

    async def search(self, *, tenant_id: UUID, query: str, limit: int) -> list[SearchHit]:
        vectors = await self.embedding_provider.embed([query])
        return self.repository.search_knowledge(
            tenant_id=tenant_id,
            query=query,
            query_embedding=vectors[0],
            limit=limit,
        )

