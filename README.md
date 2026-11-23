# 商家 AI 客服与知识库 MVP

这是一个面向多商家的后端 MVP，已包含：

- PostgreSQL + pgvector 知识库存储
- 租户强制隔离（查询过滤 + PostgreSQL RLS）
- 文本、PDF、DOCX 文档解析与切片
- 向量检索 + 中文友好的 trigram 词法检索 + RRF 融合
- AI 自动回复、置信度判断和转人工状态机
- 企业微信本地 Agent 的事件/命令/回执协议
- 无外部模型凭证时可运行的本地确定性模拟模型

## 运行

```bash
cp .env.example .env
docker compose up -d postgres
python3 -m venv .venv
source .venv/bin/activate
pip install '.[dev]'
uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000/docs` 查看 API。

## 快速验证

1. 新建租户：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/dev/tenants \
  -H 'X-API-Key: change-me' \
  -H 'Content-Type: application/json' \
  -d '{"name":"测试商家"}'
```

2. 使用返回的 `tenant_id` 写入知识：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/knowledge/text \
  -H 'X-API-Key: change-me' \
  -H 'X-Tenant-ID: <tenant_id>' \
  -H 'Content-Type: application/json' \
  -d '{"title":"退货政策","content":"商品签收后7天内，在不影响二次销售的情况下可申请退货。"}'
```

3. 模拟企微收到消息：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/agents/events \
  -H 'X-API-Key: change-me' \
  -H 'X-Tenant-ID: <tenant_id>' \
  -H 'Content-Type: application/json' \
  -d '{
    "device_id":"mock-device-1",
    "account_id":"wecom-account-1",
    "external_event_id":"msg-001",
    "conversation_external_id":"customer-001",
    "sender_external_id":"customer-001",
    "message_type":"text",
    "content":"签收后可以退货吗？",
    "occurred_at":"2026-11-22T10:00:00+08:00"
  }'
```

4. Agent 拉取待发送命令：

```bash
curl 'http://127.0.0.1:8000/api/v1/agents/mock-device-1/commands' \
  -H 'X-API-Key: change-me' \
  -H 'X-Tenant-ID: <tenant_id>'
```

## 真实企微 Agent 边界

后端不依赖具体桌面自动化实现。本地 Agent 只需实现：

1. 读取新消息并调用 `POST /api/v1/agents/events`
2. 轮询 `GET /api/v1/agents/{device_id}/commands`
3. 校验当前会话后发送消息
4. 调用 `POST /api/v1/agents/{device_id}/commands/{command_id}/ack`
5. 人工接管时上报 `POST /api/v1/conversations/{id}/takeover`

待确认企业微信运行在 Windows 还是 macOS 后，再实现对应的 UI Automation 控制器。

