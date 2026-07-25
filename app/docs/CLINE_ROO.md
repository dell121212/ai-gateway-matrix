# Cline / Roo Code 接入

## 配置

| 项 | 值 |
|----|-----|
| API Provider | OpenAI Compatible |
| Base URL | `http://127.0.0.1:4000/v1` |
| API Key | 专业控制台 `/console` → API Key 创建 |
| Model | `auto-route` |

## 推荐模式

- **编程 Agent**：API Key 默认 `agent-stream`，或请求头  
  `X-PrivateAPI-Mode: agent-stream`
- **需要完整质检**：`X-PrivateAPI-Mode: strict`（非流式）

## 可选头

```http
X-PrivateAPI-Task-ID: <uuid-or-string>
X-PrivateAPI-Session-ID: <session>
X-PrivateAPI-Client: cline
X-PrivateAPI-Workspace-ID: <path-or-id>
X-PrivateAPI-Mode: agent-stream
```

响应头：

```http
X-PrivateAPI-Request-ID: ...
X-PrivateAPI-Task-ID: ...
X-PrivateAPI-Mode: ...
```

积分事件 **不会** 混入 OpenAI SSE，避免破坏 Cline/Roo 解析。  
请在浏览器打开 `http://127.0.0.1:4000/console/tasks` 查看实时积分。

## 错误

| HTTP | 含义 |
|------|------|
| 402 | 积分不足（未调用上游） |
| 400 | 非法 Mode |
| 502 | 网关不可达 |
