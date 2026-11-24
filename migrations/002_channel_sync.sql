CREATE TABLE IF NOT EXISTS channel_sync_offsets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    channel text NOT NULL,
    account_external_id text NOT NULL,
    last_seq bigint NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, channel, account_external_id)
);

ALTER TABLE channel_sync_offsets ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_sync_offsets FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON channel_sync_offsets;
CREATE POLICY tenant_isolation ON channel_sync_offsets
USING (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
)
WITH CHECK (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
);
