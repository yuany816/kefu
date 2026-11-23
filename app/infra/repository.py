import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import Connection

from app.domain.models import AgentCommand, ConversationState, SearchHit, TextDocumentCreate
from app.domain.state_machine import ensure_transition
from app.infra.database import Database
from app.services.chunking import TextChunk


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.9g}" for value in vector) + "]"


class Repository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_tenant(self, name: str) -> dict[str, Any]:
        with self.database.system_connection() as connection:
            return connection.execute(
                "INSERT INTO tenants (name) VALUES (%s) RETURNING id, name, status, created_at",
                (name,),
            ).fetchone()

    def tenant_exists(self, tenant_id: UUID) -> bool:
        with self.database.system_connection() as connection:
            row = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM tenants WHERE id = %s AND status = 'active') AS ok",
                (tenant_id,),
            ).fetchone()
            return bool(row["ok"])

    def create_document(
        self,
        *,
        tenant_id: UUID,
        document: TextDocumentCreate,
        checksum: str,
        chunks: list[TextChunk],
        embeddings: list[list[float]],
        embedding_model: str,
        source_type: str = "text",
        object_key: str | None = None,
    ) -> dict[str, Any]:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have equal length")

        with self.database.tenant_connection(tenant_id) as connection:
            existing = connection.execute(
                """
                SELECT id, title, status, checksum
                FROM knowledge_documents
                WHERE tenant_id = %s AND checksum = %s AND status != 'archived'
                ORDER BY version DESC LIMIT 1
                """,
                (tenant_id, checksum),
            ).fetchone()
            if existing:
                count = connection.execute(
                    "SELECT count(*) AS count FROM knowledge_chunks WHERE document_id = %s",
                    (existing["id"],),
                ).fetchone()["count"]
                return {**existing, "chunk_count": count, "duplicate": True}

            document_row = connection.execute(
                """
                INSERT INTO knowledge_documents (
                    tenant_id, title, source_type, source_uri, object_key,
                    checksum, status, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, 'processing', %s::jsonb)
                RETURNING id, title, status, checksum
                """,
                (
                    tenant_id,
                    document.title,
                    source_type,
                    document.source_uri,
                    object_key,
                    checksum,
                    json.dumps(document.metadata, ensure_ascii=False),
                ),
            ).fetchone()

            for chunk, embedding in zip(chunks, embeddings, strict=True):
                content_hash = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
                connection.execute(
                    """
                    INSERT INTO knowledge_chunks (
                        tenant_id, document_id, chunk_index, section_title, content,
                        content_hash, token_count, embedding, embedding_model, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s::jsonb)
                    """,
                    (
                        tenant_id,
                        document_row["id"],
                        chunk.index,
                        chunk.section_title,
                        chunk.content,
                        content_hash,
                        chunk.token_count,
                        _vector_literal(embedding),
                        embedding_model,
                        json.dumps(document.metadata, ensure_ascii=False),
                    ),
                )

            final_row = connection.execute(
                """
                UPDATE knowledge_documents
                SET status = 'published', updated_at = now()
                WHERE id = %s
                RETURNING id, title, status, checksum
                """,
                (document_row["id"],),
            ).fetchone()
            return {**final_row, "chunk_count": len(chunks), "duplicate": False}

    def search_knowledge(
        self,
        *,
        tenant_id: UUID,
        query: str,
        query_embedding: list[float],
        limit: int,
    ) -> list[SearchHit]:
        candidate_limit = max(limit * 5, 30)
        with self.database.tenant_connection(tenant_id) as connection:
            rows = connection.execute(
                """
                WITH dense_raw AS (
                    SELECT kc.id, 1 - (kc.embedding <=> %s::vector) AS score
                    FROM knowledge_chunks kc
                    JOIN knowledge_documents kd ON kd.id = kc.document_id
                    WHERE kc.tenant_id = %s
                      AND kc.status = 'published'
                      AND kd.status = 'published'
                      AND (kc.effective_from IS NULL OR kc.effective_from <= now())
                      AND (kc.effective_to IS NULL OR kc.effective_to > now())
                    ORDER BY kc.embedding <=> %s::vector
                    LIMIT %s
                ),
                dense AS (
                    SELECT id, score,
                           row_number() OVER (ORDER BY score DESC) AS rank
                    FROM dense_raw
                ),
                lexical_raw AS (
                    SELECT kc.id, GREATEST(
                        similarity(kc.content, %s),
                        word_similarity(%s, kc.content)
                    ) AS score
                    FROM knowledge_chunks kc
                    JOIN knowledge_documents kd ON kd.id = kc.document_id
                    WHERE kc.tenant_id = %s
                      AND kc.status = 'published'
                      AND kd.status = 'published'
                      AND (kc.effective_from IS NULL OR kc.effective_from <= now())
                      AND (kc.effective_to IS NULL OR kc.effective_to > now())
                    ORDER BY score DESC
                    LIMIT %s
                ),
                lexical AS (
                    SELECT id, score,
                           row_number() OVER (ORDER BY score DESC) AS rank
                    FROM lexical_raw
                ),
                fused AS (
                    SELECT COALESCE(d.id, l.id) AS id,
                           COALESCE(d.score, 0) AS dense_score,
                           COALESCE(l.score, 0) AS lexical_score,
                           COALESCE(1.0 / (60 + d.rank), 0) +
                           COALESCE(1.0 / (60 + l.rank), 0) AS rrf_score
                    FROM dense d FULL OUTER JOIN lexical l ON d.id = l.id
                )
                SELECT kc.id AS chunk_id, kc.document_id, kd.title,
                       kc.section_title, kc.content, kc.metadata,
                       GREATEST(f.dense_score, f.lexical_score) AS score
                FROM fused f
                JOIN knowledge_chunks kc ON kc.id = f.id
                JOIN knowledge_documents kd ON kd.id = kc.document_id
                ORDER BY f.rrf_score DESC, score DESC
                LIMIT %s
                """,
                (
                    _vector_literal(query_embedding),
                    tenant_id,
                    _vector_literal(query_embedding),
                    candidate_limit,
                    query,
                    query,
                    tenant_id,
                    candidate_limit,
                    limit,
                ),
            ).fetchall()
        return [SearchHit.model_validate(row) for row in rows]

    def upsert_device(self, tenant_id: UUID, device_id: str, platform: str = "unknown") -> None:
        with self.database.tenant_connection(tenant_id) as connection:
            connection.execute(
                """
                INSERT INTO agent_devices (
                    tenant_id, device_external_id, platform, status, last_heartbeat_at
                ) VALUES (%s, %s, %s, 'online', now())
                ON CONFLICT (tenant_id, device_external_id) DO UPDATE
                SET status = 'online', platform = EXCLUDED.platform,
                    last_heartbeat_at = now()
                """,
                (tenant_id, device_id, platform),
            )

    def ingest_inbound_message(
        self,
        *,
        tenant_id: UUID,
        device_id: str,
        account_id: str,
        external_event_id: str,
        conversation_external_id: str,
        sender_external_id: str | None,
        message_type: str,
        content: str,
        occurred_at: datetime,
        metadata: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self.database.tenant_connection(tenant_id) as connection:
            conversation = connection.execute(
                """
                INSERT INTO conversations (
                    tenant_id, account_external_id, conversation_external_id,
                    state, last_customer_message_at, updated_at
                ) VALUES (%s, %s, %s, 'ai_active', %s, now())
                ON CONFLICT (tenant_id, account_external_id, conversation_external_id)
                DO UPDATE SET last_customer_message_at = EXCLUDED.last_customer_message_at,
                              updated_at = now()
                RETURNING *
                """,
                (tenant_id, account_id, conversation_external_id, occurred_at),
            ).fetchone()
            inserted = connection.execute(
                """
                INSERT INTO messages (
                    tenant_id, conversation_id, device_external_id, external_event_id,
                    sender_external_id, direction, message_type, content, content_hash,
                    metadata, occurred_at
                ) VALUES (%s, %s, %s, %s, %s, 'inbound', %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (tenant_id, external_event_id) DO NOTHING
                RETURNING id
                """,
                (
                    tenant_id,
                    conversation["id"],
                    device_id,
                    external_event_id,
                    sender_external_id,
                    message_type,
                    content,
                    content_hash,
                    json.dumps(metadata, ensure_ascii=False),
                    occurred_at,
                ),
            ).fetchone()
            return conversation, inserted is None

    def get_recent_history(
        self, tenant_id: UUID, conversation_id: UUID, limit: int = 12
    ) -> list[str]:
        with self.database.tenant_connection(tenant_id) as connection:
            rows = connection.execute(
                """
                SELECT direction, content
                FROM messages
                WHERE conversation_id = %s
                ORDER BY occurred_at DESC
                LIMIT %s
                """,
                (conversation_id, limit),
            ).fetchall()
        return [
            f"{'客户' if row['direction'] == 'inbound' else '客服'}：{row['content']}"
            for row in reversed(rows)
        ]

    def get_conversation(self, tenant_id: UUID, conversation_id: UUID) -> dict[str, Any] | None:
        with self.database.tenant_connection(tenant_id) as connection:
            return connection.execute(
                "SELECT * FROM conversations WHERE id = %s",
                (conversation_id,),
            ).fetchone()

    def transition_conversation(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        target: ConversationState,
        reason: str | None = None,
    ) -> dict[str, Any]:
        with self.database.tenant_connection(tenant_id) as connection:
            current = connection.execute(
                "SELECT * FROM conversations WHERE id = %s FOR UPDATE",
                (conversation_id,),
            ).fetchone()
            if not current:
                raise KeyError("conversation not found")
            ensure_transition(ConversationState(current["state"]), target)
            return connection.execute(
                """
                UPDATE conversations
                SET state = %s,
                    handoff_reason = CASE
                        WHEN %s::text IS NULL THEN handoff_reason
                        ELSE %s::text
                    END,
                    human_taken_over_at = CASE WHEN %s = 'human_active' THEN now()
                                                   ELSE human_taken_over_at END,
                    updated_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (target.value, reason, reason, target.value, conversation_id),
            ).fetchone()

    def request_handoff(
        self, *, tenant_id: UUID, conversation_id: UUID, reason: str
    ) -> dict[str, Any]:
        with self.database.tenant_connection(tenant_id) as connection:
            current = connection.execute(
                "SELECT * FROM conversations WHERE id = %s FOR UPDATE",
                (conversation_id,),
            ).fetchone()
            if not current:
                raise KeyError("conversation not found")
            current_state = ConversationState(current["state"])
            if current_state not in {
                ConversationState.HUMAN_PENDING,
                ConversationState.HUMAN_ACTIVE,
            }:
                ensure_transition(current_state, ConversationState.HUMAN_PENDING)
                connection.execute(
                    """
                    UPDATE conversations
                    SET state = 'human_pending', handoff_reason = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (reason, conversation_id),
                )
            existing = connection.execute(
                """
                SELECT * FROM handoffs
                WHERE conversation_id = %s AND status IN ('pending', 'accepted')
                ORDER BY requested_at DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if existing:
                return existing
            return connection.execute(
                """
                INSERT INTO handoffs (tenant_id, conversation_id, reason)
                VALUES (%s, %s, %s) RETURNING *
                """,
                (tenant_id, conversation_id, reason),
            ).fetchone()

    def take_over(
        self, *, tenant_id: UUID, conversation_id: UUID, operator_id: str, reason: str
    ) -> dict[str, Any]:
        with self.database.tenant_connection(tenant_id) as connection:
            current = connection.execute(
                "SELECT * FROM conversations WHERE id = %s FOR UPDATE",
                (conversation_id,),
            ).fetchone()
            if not current:
                raise KeyError("conversation not found")
            target = ConversationState.HUMAN_ACTIVE
            ensure_transition(ConversationState(current["state"]), target)
            row = connection.execute(
                """
                UPDATE conversations
                SET state = 'human_active', handoff_reason = %s,
                    human_taken_over_at = now(), updated_at = now()
                WHERE id = %s RETURNING *
                """,
                (reason, conversation_id),
            ).fetchone()
            connection.execute(
                """
                UPDATE handoffs
                SET status = 'accepted', accepted_at = now(), accepted_by = %s
                WHERE conversation_id = %s AND status = 'pending'
                """,
                (operator_id, conversation_id),
            )
            connection.execute(
                """
                UPDATE agent_commands
                SET status = 'cancelled', updated_at = now()
                WHERE conversation_id = %s AND status IN ('pending', 'leased')
                """,
                (conversation_id,),
            )
            return row

    def release_to_ai(
        self, *, tenant_id: UUID, conversation_id: UUID, operator_id: str
    ) -> dict[str, Any]:
        del operator_id
        with self.database.tenant_connection(tenant_id) as connection:
            current = connection.execute(
                "SELECT * FROM conversations WHERE id = %s FOR UPDATE",
                (conversation_id,),
            ).fetchone()
            if not current:
                raise KeyError("conversation not found")
            ensure_transition(ConversationState(current["state"]), ConversationState.COOLDOWN)
            connection.execute(
                "UPDATE conversations SET state = 'cooldown', updated_at = now() WHERE id = %s",
                (conversation_id,),
            )
            ensure_transition(ConversationState.COOLDOWN, ConversationState.AI_ACTIVE)
            row = connection.execute(
                """
                UPDATE conversations
                SET state = 'ai_active', handoff_reason = NULL, updated_at = now()
                WHERE id = %s RETURNING *
                """,
                (conversation_id,),
            ).fetchone()
            connection.execute(
                """
                UPDATE handoffs SET status = 'resolved', resolved_at = now()
                WHERE conversation_id = %s AND status IN ('pending', 'accepted')
                """,
                (conversation_id,),
            )
            return row

    def enqueue_command(
        self,
        *,
        tenant_id: UUID,
        device_id: str,
        conversation_id: UUID,
        command_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self.database.tenant_connection(tenant_id) as connection:
            return connection.execute(
                """
                INSERT INTO agent_commands (
                    tenant_id, device_external_id, conversation_id, command_type,
                    payload, idempotency_key
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (tenant_id, idempotency_key) DO UPDATE
                SET idempotency_key = EXCLUDED.idempotency_key
                RETURNING *
                """,
                (
                    tenant_id,
                    device_id,
                    conversation_id,
                    command_type,
                    json.dumps(payload, ensure_ascii=False),
                    idempotency_key,
                ),
            ).fetchone()

    def lease_commands(
        self,
        *,
        tenant_id: UUID,
        device_id: str,
        lease_seconds: int,
        limit: int = 20,
    ) -> list[AgentCommand]:
        lease_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        with self.database.tenant_connection(tenant_id) as connection:
            rows = connection.execute(
                """
                WITH candidates AS (
                    SELECT id
                    FROM agent_commands
                    WHERE tenant_id = %s
                      AND device_external_id = %s
                      AND (
                        status = 'pending'
                        OR (status = 'leased' AND lease_until < now())
                      )
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE agent_commands ac
                SET status = 'leased', lease_until = %s,
                    attempt_count = attempt_count + 1, updated_at = now()
                FROM candidates c
                WHERE ac.id = c.id
                RETURNING ac.id, ac.conversation_id, ac.command_type, ac.payload,
                          ac.idempotency_key, ac.created_at
                """,
                (tenant_id, device_id, limit, lease_until),
            ).fetchall()
        return [AgentCommand.model_validate(row) for row in rows]

    def ack_command(
        self,
        *,
        tenant_id: UUID,
        device_id: str,
        command_id: UUID,
        success: bool,
        external_message_id: str | None,
        error: str | None,
    ) -> bool:
        with self.database.tenant_connection(tenant_id) as connection:
            command = connection.execute(
                """
                UPDATE agent_commands
                SET status = %s, ack_error = %s, lease_until = NULL, updated_at = now()
                WHERE id = %s AND device_external_id = %s
                  AND status IN ('pending', 'leased')
                RETURNING *
                """,
                ("acked" if success else "failed", error, command_id, device_id),
            ).fetchone()
            if not command:
                return False
            if success and command["command_type"] == "send_message":
                payload = command["payload"]
                content = payload.get("content", "")
                event_id = external_message_id or f"command:{command_id}"
                connection.execute(
                    """
                    INSERT INTO messages (
                        tenant_id, conversation_id, device_external_id, external_event_id,
                        direction, message_type, content, content_hash, ai_generated,
                        ai_confidence, occurred_at
                    ) VALUES (%s, %s, %s, %s, 'outbound', 'text', %s, %s, true, %s, now())
                    ON CONFLICT (tenant_id, external_event_id) DO NOTHING
                    """,
                    (
                        tenant_id,
                        command["conversation_id"],
                        device_id,
                        event_id,
                        content,
                        hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        payload.get("confidence"),
                    ),
                )
                connection.execute(
                    """
                    UPDATE conversations
                    SET state = CASE WHEN state = 'ai_thinking' THEN 'ai_active' ELSE state END,
                        ai_message_count = ai_message_count + 1,
                        last_reply_at = now(), updated_at = now()
                    WHERE id = %s AND state NOT IN ('human_active', 'closed')
                    """,
                    (command["conversation_id"],),
                )
            return True

