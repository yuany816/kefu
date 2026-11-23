CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS tenants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    title text NOT NULL,
    source_type text NOT NULL DEFAULT 'text',
    source_uri text,
    object_key text,
    checksum text NOT NULL,
    version integer NOT NULL DEFAULT 1,
    status text NOT NULL DEFAULT 'published'
        CHECK (status IN ('processing', 'published', 'failed', 'archived')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, checksum, version)
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    section_title text,
    content text NOT NULL,
    content_hash text NOT NULL,
    token_count integer NOT NULL,
    embedding vector(1024) NOT NULL,
    embedding_model text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'published'
        CHECK (status IN ('published', 'archived')),
    effective_from timestamptz,
    effective_to timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS knowledge_chunks_tenant_status_idx
    ON knowledge_chunks (tenant_id, status);
CREATE INDEX IF NOT EXISTS knowledge_chunks_document_idx
    ON knowledge_chunks (document_id, chunk_index);
CREATE INDEX IF NOT EXISTS knowledge_chunks_content_trgm_idx
    ON knowledge_chunks USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_hnsw_idx
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS agent_devices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    device_external_id text NOT NULL,
    platform text NOT NULL DEFAULT 'mock',
    client_version text,
    status text NOT NULL DEFAULT 'online'
        CHECK (status IN ('online', 'offline', 'disabled')),
    last_heartbeat_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, device_external_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    account_external_id text NOT NULL,
    conversation_external_id text NOT NULL,
    channel text NOT NULL DEFAULT 'wecom_client',
    state text NOT NULL DEFAULT 'ai_active'
        CHECK (state IN (
            'ai_active', 'ai_thinking', 'human_pending',
            'human_active', 'cooldown', 'closed'
        )),
    handoff_reason text,
    ai_message_count integer NOT NULL DEFAULT 0,
    last_customer_message_at timestamptz,
    last_reply_at timestamptz,
    human_taken_over_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, account_external_id, conversation_external_id)
);

CREATE INDEX IF NOT EXISTS conversations_tenant_state_idx
    ON conversations (tenant_id, state, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    device_external_id text,
    external_event_id text NOT NULL,
    sender_external_id text,
    direction text NOT NULL CHECK (direction IN ('inbound', 'outbound', 'internal')),
    message_type text NOT NULL,
    content text NOT NULL DEFAULT '',
    content_hash text NOT NULL,
    ai_generated boolean NOT NULL DEFAULT false,
    ai_confidence double precision,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, external_event_id)
);

CREATE INDEX IF NOT EXISTS messages_conversation_time_idx
    ON messages (conversation_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS handoffs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    reason text NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'resolved', 'cancelled')),
    requested_at timestamptz NOT NULL DEFAULT now(),
    accepted_at timestamptz,
    resolved_at timestamptz,
    accepted_by text
);

CREATE TABLE IF NOT EXISTS agent_commands (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    device_external_id text NOT NULL,
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    command_type text NOT NULL CHECK (command_type IN ('send_message', 'pause_conversation')),
    payload jsonb NOT NULL,
    idempotency_key text NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'leased', 'acked', 'failed', 'cancelled')),
    attempt_count integer NOT NULL DEFAULT 0,
    lease_until timestamptz,
    ack_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS agent_commands_poll_idx
    ON agent_commands (tenant_id, device_external_id, status, created_at);

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'knowledge_documents', 'knowledge_chunks', 'agent_devices',
        'conversations', 'messages', 'handoffs', 'agent_commands'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format(
            'DROP POLICY IF EXISTS tenant_isolation ON %I', table_name
        );
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I '
            'USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) '
            'WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
            table_name
        );
    END LOOP;
END
$$;

