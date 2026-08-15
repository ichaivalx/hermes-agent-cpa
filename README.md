# Hermes Agent CPA Image

这是一个很薄的 Hermes Agent 构建仓库：固定官方稳定版源码，应用一份可审计补丁，然后由 GitHub Actions 构建并发布 Docker 镜像。

## 当前版本

- Hermes Agent：`0.20.1`
- 官方标签：`v2026.8.13`
- 官方提交：`f80f453ae0679347e38abc917c7f94f717bf96c5`
- 自定义补丁版本：`4`
- 镜像：`ghcr.io/ichaivalx/hermes-agent-cpa:v2026.8.13-cpa.4`

## 补丁做了什么

官方 Hermes 已经包含完整的 Gemini Native 适配器，但默认只在 Google 官方域名上启用。本仓库补充五个能力：

1. 当 `gemini` Provider 的自定义 Base URL 以 `/v1beta` 结尾时，启用现有 Gemini Native 客户端。
2. 使用 Gemini 原生的 `GET /v1beta/models` 响应格式和 `x-goog-api-key` 鉴权读取模型列表。
3. Gemini 模型目录请求使用 Hermes 的 User-Agent，避免被常见 WAF 误判为默认 Python 抓取器。
4. Dashboard 为指定 Profile 刷新模型时加载该 Profile 自己的密钥作用域，避免自定义 Provider 因拿不到 `key_env` 而显示空列表。
5. API-key Provider 的自定义 Base URL 与 API Key 使用同一个 Profile 作用域，确保 Gemini 目录扫描命中所选 Profile 的 CPA `/v1beta`，而不是进程级默认地址。

Chat Completions、OpenAI Responses 和 Anthropic Messages 均继续使用 Hermes 官方实现，没有被这个补丁改动。

## 发布方式

推送标签 `v2026.8.13-cpa.4` 后，工作流会：

1. 按 SHA 下载官方 Hermes 源码并验证提交。
2. 使用 `git apply --check` 验证并应用补丁。
3. 用官方 Dockerfile 构建 `linux/amd64` 镜像。
4. 对完成的镜像执行 Gemini Native 运行时冒烟测试。
5. 将镜像推送到 GHCR，并创建同名 GitHub Release。
6. 在 Release 中保存上游锁定信息、补丁和镜像 digest。

工作流没有 `upload-artifact` 步骤，不会把大体积 Docker tar 包存进 Actions Artifact。Docker 镜像由 GHCR 保存，Release 负责版本记录和校验信息。

如果以后扩展为多个架构并确实需要在 Job 之间传递中间文件，Artifact 保留期固定为 1 天。

GHCR 包的公开或私有状态是 GitHub 账户级的一次性设置，工作流令牌只负责发布镜像，不尝试修改该设置。

## VPS 更新

Compose 中只需要把 Hermes 服务的镜像改为：

```yaml
image: ghcr.io/ichaivalx/hermes-agent-cpa:v2026.8.13-cpa.4
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
