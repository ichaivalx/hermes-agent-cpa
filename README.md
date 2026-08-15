# Hermes Agent CPA Image

这是一个很薄的 Hermes Agent 构建仓库：固定官方稳定版源码，按顺序应用可审计补丁，然后由 GitHub Actions 构建并发布 Docker 镜像。

## 当前版本

- Hermes Agent：`0.20.1`
- 官方标签：`v2026.8.13`
- 官方提交：`f80f453ae0679347e38abc917c7f94f717bf96c5`
- 自定义补丁版本：`7`
- 镜像：`ghcr.io/ichaivalx/hermes-agent-cpa:v2026.8.13-cpa.8`

## 补丁做了什么

本仓库只保留两组与当前部署直接相关的补丁。

CPA / Gemini Native 补丁补充五个能力：

1. 当 `gemini` Provider 的自定义 Base URL 以 `/v1beta` 结尾时，启用现有 Gemini Native 客户端。
2. 使用 Gemini 原生的 `GET /v1beta/models` 响应格式和 `x-goog-api-key` 鉴权读取模型列表。
3. Gemini 模型目录请求使用 Hermes 的 User-Agent，避免被常见 WAF 误判为默认 Python 抓取器。
4. Dashboard 为指定 Profile 刷新模型时加载该 Profile 自己的密钥作用域，避免自定义 Provider 因拿不到 `key_env` 而显示空列表。
5. API-key Provider 的自定义 Base URL 与 API Key 使用同一个 Profile 作用域，确保 Gemini 目录扫描命中所选 Profile 的 CPA `/v1beta`，而不是进程级默认地址。

Chat Completions、OpenAI Responses 和 Anthropic Messages 均继续使用 Hermes 官方实现，没有被这个补丁改动。

QQ 全群上下文补丁补充以下能力：

1. 接收腾讯新版统一事件 `GROUP_MESSAGE_CREATE`，但保持默认仅 @ 回复，不会因升级镜像自动放宽触发范围。
2. 提供 `mention`、`observe`、`direct` 三种群消息模式。
3. `observe` 只把普通群消息写入共享会话，不调用模型、不下载附件、不发送回复；模型在之后被 @ 时看到的上下文条数和字符数均可配置，也可以取消裁剪。
4. `direct` 每条允许的普通群消息都直接启动完整 Hermes Agent，不经过轻量模型或二次路由。流式片段、工具进度、状态、Clarify、审批提示和普通最终回复全部保持私有；只有请求作用域内的 `qq_group_send(message=...)` 工具可以把内容发到当前群。
5. `qq_group_send` 没有群 ID、用户 ID 或其他目标参数，目标由当前入站事件固定；脱离该普通群消息的 Agent turn 就会失败关闭，因此不能跨群或从私聊误发。
6. 对腾讯可能重复投递的普通事件与 @ 事件做升级式去重；先写入的被动副本会在显式 @ 到达前原子删除。
7. Agent 的工具调用、发送内容和私有最终回复均保存在正常会话历史中；腾讯回流的机器人事件只作为重复副本丢弃，不会让机器人对自己的消息再次触发 Agent。
8. 全群模式强制要求精确群 ID 白名单和共享群会话；`*` 通配符不会开启普通消息采集。
9. 群聊可以设置独立 `group_toolsets`，包括显式空列表；普通群文本不能执行 Hermes 斜杠命令。

## QQ 全群消息配置

先在 QQ 群的机器人设置中开启“接收全部消息”，然后在目标 Profile 的 `config.yaml` 中设置：

```yaml
group_sessions_per_user: false

platforms:
  qqbot:
    enabled: true
    extra:
      group_policy: allowlist
      group_allow_from:
        - "精确的群 OpenID"
      group_message_mode: observe
      group_toolsets: []
      group_context_message_limit: 50
      group_context_char_limit: 12000
```

三种模式含义：

- `mention`：默认行为，只处理明确 @ 机器人的消息。
- `observe`：记录允许群里的普通聊天，但不调用模型、不回复；这是首次上线验证应使用的模式。
- `direct`：每条普通消息直接运行完整 Agent。普通 final 永远不会发到群里；Agent 只有主动调用当前群专用的 `qq_group_send` 才会可见发言，不调用即保持安静。

`group_toolsets: []` 表示群聊不提供通用 Agent 工具；QQ 群的这份列表按严格白名单处理，不会自动混入默认 MCP 或新安装插件。`direct` 的内部 `qq_group_send` 是固定目标的投递能力，不属于这份通用工具白名单，因此仍可让 Agent 在当前群发言。之后可以按需把白名单改为例如 `web`、`vision`、`no_mcp`；私聊的工具配置不受影响。

`group_context_message_limit` 和 `group_context_char_limit` 控制完整 Agent 收到的普通群聊历史。两项都接受任意非负整数，设为 `0` 表示取消对应裁剪。取消这里的裁剪并不会绕过模型自身上下文窗口以及 Hermes 原有的会话压缩机制。

`direct` 会让每条允许的普通群消息都运行一次完整 Agent，因此延迟和 Token 高于另外两种模式。`group_toolsets` 仍然是独立硬边界；首次启用 `direct` 时建议保持空列表，确认静默与工具发言行为后再逐项开放通用工具。

固定当前群的限制只约束 `qq_group_send` 本身，并不把任意通用工具变成沙箱。若开放 `terminal`、`delegation`、`cronjob` 或具有外部写入能力的 MCP/插件，模型也会取得这些工具原本拥有的副作用能力；公用群应只逐项开放确实需要的低风险工具。

`direct` 只支持网关本地运行 Agent。若设置了 `GATEWAY_PROXY_URL`，远端 API Server 目前无法接受每请求的 QQ `group_toolsets` 边界；为避免远端工具越权和流式内容提前泄漏，普通群消息会安全静默并只写入会话，不会转发给远端。只要群聊显式配置了 `group_toolsets`（包括空列表），显式 @ 消息也不会绕过该边界转发，而会返回一条明确的安全拒绝；未配置群专属工具边界的显式 @ 消息仍沿用 Hermes 原有代理模式。

如果还不知道群 OpenID，可以先把机器人加入群并发送一条普通消息。网关会记录一次安全警告，其中包含实际群 ID；把该 ID 加入 `group_allow_from` 后重启网关即可。未命中精确白名单时不会保存消息。

## 发布方式

推送标签 `v2026.8.13-cpa.8` 后，工作流会：

1. 按 SHA 下载官方 Hermes 源码并验证提交。
2. 使用 `git apply --check` 验证并应用补丁。
3. 使用官方测试入口运行 QQ 适配器、当前群发送工具、完整 Agent 私有 final 与会话去重回归测试，并执行 Ruff。
4. 用官方 Dockerfile 构建 `linux/amd64` 镜像。
5. 对完成的镜像执行 CPA Gemini Native，以及 QQ 全群适配器入口、工具投递契约和去重组件冒烟测试；完整 Agent 的私有 final 由上一步的集成测试覆盖。
6. 将镜像推送到 GHCR，并创建同名 GitHub Release。
7. 在 Release 中保存上游锁定信息、全部补丁和镜像 digest。

工作流没有 `upload-artifact` 步骤，不会把大体积 Docker tar 包存进 Actions Artifact。Docker 镜像由 GHCR 保存，Release 负责版本记录和校验信息。

如果以后扩展为多个架构并确实需要在 Job 之间传递中间文件，Artifact 保留期固定为 1 天。

GHCR 包的公开或私有状态是 GitHub 账户级的一次性设置，工作流令牌只负责发布镜像，不尝试修改该设置。

## VPS 更新

Compose 中只需要把 Hermes 服务的镜像改为：

```yaml
image: ghcr.io/ichaivalx/hermes-agent-cpa:v2026.8.13-cpa.8
```

保留原有持久化挂载：

```yaml
volumes:
  - ~/.hermes:/opt/data
```

然后在 Compose 文件所在目录执行：

```bash
docker compose pull
docker compose up -d --force-recreate
```

配置、Profile、会话和其他状态位于宿主机的 `~/.hermes`，更换镜像不会重新初始化这些数据。更新前仍建议备份一次该目录。

## 一次配置 CPA 四协议端点

Release 附带一个按 Profile 运行的配置脚本。下载两个同目录文件后执行：

```bash
chmod +x configure-cpa-profile.sh
./configure-cpa-profile.sh qq-main --restart
```

脚本会交互询问 CPA 根地址和 API Key，并配置四条逻辑路由：

- `CPA · Chat Completions` → `/v1/chat/completions`
- `CPA · OpenAI Responses` → `/v1/responses`
- `CPA · Anthropic Messages` → `/v1/messages`
- Hermes 内置 `Gemini` Provider → CPA `/v1beta/models/...:generateContent`

同一个 CPA Key 只写入该 Profile 的 `.env`，不会进入仓库、命令行参数或脚本日志。脚本可重复运行，每次修改前都会备份该 Profile 的 `config.yaml` 和 `.env`。

它同时落实当前 QQ Bot Profile 的安全基线：关闭内置 Memory 和 USER Profile 注入、禁用 `memory`/`skills` Toolset、关闭 Curator，并启用 Memory/Skill 写入审批作为后备保护。已有 Memory 和 Skill 文件只停用、不删除。

脚本只注册 Provider，不擅自选择默认模型。执行后先在 Dashboard 左上角选择目标 Profile，再进入“模型”，点击主模型的“Change”，然后点“Refresh Models”。Provider 列会显示三条 CPA 自定义路由和内置 Gemini，点选任意一条即可查看它实际扫描到的模型。

Gemini Provider 的 Base URL 应填写 CPA 的 Gemini 原生入口，例如：

```text
https://你的-CPA-域名/v1beta
```

CPA 的 Antigravity 模型前缀建议使用 `ag/` 或 `antigravity/`；不要使用 `google/` 或 `gemini/`，因为 Hermes 的原生 Gemini 适配器会剥离这两个前缀。

## 升级上游 Hermes

更新 `upstream.env` 中的稳定版标签、提交 SHA、Hermes 版本和补丁修订号，在本地执行 `scripts/prepare-source.sh` 检查补丁是否仍可应用，通过后创建新的发布标签。补丁不兼容时构建会明确失败，不会静默发布错误镜像。

## 许可证

Hermes Agent 上游使用 MIT License。本仓库的构建脚本和补丁同样使用 MIT License。
