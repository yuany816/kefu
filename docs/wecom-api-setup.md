# 企业微信 API 接入

本项目不使用 macOS 辅助功能、OCR 或客户端 UI 自动化。企业微信通道由两套官方能力组成。

## 1. 普通服务端 API

环境变量：

```dotenv
WECOM_CORP_ID=企业ID
WECOM_CORP_SECRET=客户联系或自建应用Secret
```

用于：

- 验证 access token
- 获取客户群列表
- 获取客户群详情和成员类型

只读探测：

```bash
python -m scripts.probe_wecom
```

如果返回 `60020`，需要在企业微信管理后台为对应 Secret 配置服务器可信 IP。

## 2. 会话内容存档 SDK

群聊正文不由客户群列表接口返回，需要开通“会话内容存档”，并配置：

```dotenv
WECOM_ARCHIVE_SECRET=会话内容存档页面的独立Secret
WECOM_ARCHIVE_PRIVATE_KEY_PATH=/run/secrets/wecom_archive_private_key.pem
WECOM_FINANCE_SDK_PATH=/opt/wecom/libWeWorkFinanceSdk_C.so
```

会话存档 Secret 与普通 `WECOM_CORP_SECRET` 不相同。SDK 官方提供 Linux/Windows 原生库，生产同步 Worker 建议部署在 Linux。

首次部署需要先执行 `migrations/002_channel_sync.sql`，然后启动增量同步：

```bash
python -m scripts.sync_wecom_archive --tenant-id <TENANT_ID>
```

Worker 使用 `seq` 增量拉取并持久化游标，只处理 `wm`/`wo` 开头的外部联系人群消息，过滤企业成员消息、撤回消息和非客户群消息，避免 AI 自回复循环。

## 3. 当前官方发送边界

官方服务端 API 没有“以成员身份向外部客户群实时发送任意回复”的接口。本项目会生成带 `【AI客服】` 标识的回复命令和转人工事件；发送端必须接入具备该能力的渠道 API。企业群发接口需要成员确认，不能替代实时回复接口。
