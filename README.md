# AI Gateway Matrix

> 把多个免费/试用 LLM API 统一成一个 OpenAI 兼容端点，先按模态、工具调用、JSON Schema、数据政策和上下文做硬过滤，再按弱/中/强、额度、健康状态和优先级调度；自带仅本机免登录、可选令牌保护的仪表盘。

## 架构

```
客户端 (Vibe CLI / 任意 OpenAI SDK)
         │
         ▼
   ┌───────────────────────────────────────────────┐
   │  中文统一入口 / 透明反向代理 (host :4000)     │
   │  HTTP 流式转发 · WebSocket · 管理界面         │
   │                       │                        │
   │                       ▼                        │
   │  LiteLLM Proxy (Docker 内网 :4000)            │
   │  ┌───────────────────────────────────────┐    │
   │  │ gateway/custom_router_hook.py                  │    │
   │  │ (async_pre_call_hook)                  │    │
   │  │                                         │    │
   │  │ 0. 疑似敏感信息(key/密码/内网地址)      │    │
   │  │    ──→ trusted-pool（覆盖一切判断）    │    │
   │  │ 1. 关键词/正则/文件数命中（零成本）    │    │
   │  │    ──→ strong-model-pool（强）         │    │
   │  │ 2. 极短输入（零成本）──→ fast-pool（弱）│   │
   │  │ 3. 其余情况交给 gateway/llm_classifier.py      │    │
   │  │    指定的模型判断 弱/中/强              │    │
   │  │ 4. 分类器失败 ──→ 回退到纯规则启发式    │    │
   │  └──────────┬──────────────────────────────┘    │
   │             │ 改写 model=                        │
   │             ▼                                    │
   │  ┌───────────────────────────────────────┐    │
   │  │ LiteLLM Router (simple-shuffle)        │    │
   │  │ 按 RPM / 预算 / 优先级负载均衡           │    │
   │  └──────────┬──────────────────────────────┘    │
   └─────────────┼───────────────────────────────────┘
                 │                       │ 记录用量(async_log_*_event)
     ┌───────────┼───────────┐           ▼
     ▼           ▼           ▼         Redis
  弱(fast)    中(free)     强(strong)     │
  Groq/       GLM/Gemini/  405B/         │
  Cerebras/   Agnes/中转站  Gemini Pro/   │
  SambaNova   ...          官方大模型    │
                                          ▼
                              中文统一控制台 (:4000，:8080 兼容)
                              API 透明转发 · 填 Key · 分类展示 · 用量/重置倒计时
```

`trusted-pool` 不再由“官方直营”自动推导，只包含 `provider_manifest.yaml` 中显式设置 `sensitive_allowed: true` 的渠道。Gemini/Mistral 免费层默认需要另行审核数据条款，不进入敏感池。敏感池仍刻意不设 fallback。

## 快速开始

只需先安装并启动 Docker Engine 或 Docker Desktop（需包含 Docker Compose），
项目本身无需手工安装 Python、Redis 或 PostgreSQL 依赖。在项目目录执行：

```bash
./run.sh
```

首次运行会自动完成以下工作：

- 从 `.env.example` 创建权限为 `0600` 的 `.env`；
- 生成网关、Redis 和 PostgreSQL 的独立随机密钥；个人仪表盘默认仅本机免登录；
- 校验配置，下载/构建所需 Docker 镜像；
- 启动全部服务并等待健康检查通过。

脚本可重复执行，不会覆盖已有 `.env` 或数据卷。上游模型 API Key 仍需要
在 `.env` 或仪表盘中至少填写一个。升级后再次运行时，脚本会把新版
`.env.example` 中新增的配置项补入 `.env`，但绝不覆盖已有值。如果未安装 Docker，
脚本会给出对应安装入口。

手动启动流程如下：

```bash
# 1. 克隆项目
cd ai-gateway-matrix

# 2. 创建 .env 并填入 API key
cp .env.example .env
# 编辑 .env 填入上游 API key；GATEWAY/DASHBOARD/REDIS/POSTGRES 密钥
# 建议仍交给 ./run.sh 生成，不要使用公开默认值。

# 3. 严格校验、生成缺失的内部密钥并启动全部服务
./run.sh

# 4. 检查渠道健康状态
python3 -m scripts.health_check

# 5. 打开中文统一控制台，填 API Key、看各渠道用量和重置倒计时
open http://127.0.0.1:4000/    # macOS；Linux 用 xdg-open，Windows 直接浏览器打开

# 6. 调用网关
# 先在控制台点击“创建密钥”，把只显示一次的客户端密钥复制到下面
curl http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-你的客户端密钥" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto-route","messages":[{"role":"user","content":"你好"}]}'
```

> 通过仪表盘填写 Key、模型名或优先级后会自动热加载，无需再跑 `bash run.sh`（仅首次部署需要）。
> Docker 的普通 `restart` 不会重新读取 `.env`，因此不要用它代替启动脚本。

## 模型分组

| 分组 | 档位 | 用途 | 渠道 |
|------|------|------|------|
| `auto-route` | — | **统一入口**，由 hook 按分类结果自动改写 | 无固定上游；未配置渠道时明确拒绝 |
| `fast-pool` | 弱 | 简短问候/单行提问 | Groq, Cerebras, SambaNova（超快推理） |
| `free-pool` | 中 | 常规任务的默认档位 | GLM-4.7-Flash, Mistral×2, Gemini, SiliconFlow, 国内官方免费层, Agnes AI(观察期), 中转站(兜底)… |
| `strong-model-pool` | 强 | 复杂任务（重构/架构/安全审计…） | SambaNova 405B, Gemini 2.5 Pro, DeepSeek R1… |
| `trusted-pool` | — | **敏感内容专用** | 只含 manifest 显式允许敏感数据的渠道 |

客户端只需调用 `model="auto-route"`，`decide_pool_with_classifier()` 按优先级判断：

1. **能力提取**：先识别 vision / tools / JSON Schema / audio，未声明支持的渠道不会被猜测使用。
2. **敏感内容检测**：扫描消息、tool arguments 和函数定义，命中后只走 manifest 允许的渠道。
3. **限时优先**：只有能力匹配、凭据已配置、未熔断且原子额度预占成功才会直连。
4. **硬约束与复杂度**：长上下文先于外部分类器强制升档，再处理关键词和弱/中/强分类。
5. **智能模式两段式**：默认用强档可用 Key 调强模型快速判断提问强度（弱/中/强），再改写到对应池作答；无可用 Key 或分诊失败时回退本地启发式。**推荐**在仪表盘「智能分诊专用 API」指定稳定付费渠道（或 `CLASSIFIER_SOURCE_ENV` / `CLASSIFIER_API_KEY`），独占做任务分档；免费池只负责作答。回答默认 **hybrid 检验**（本地规则拦乱码/空输出；仅可疑时才用专用 API 短检），尽量少烧专用额度。

**关于 Agnes AI**：观察期渠道。免费账户以 **Effective RPM** 为准（文本 agnes-2.0-flash 实际约 **20 次/分钟**；Allowed 表可能写 30；图片/视频另有更低频率）。公开文档**未标日/周/月调用总量**，现行主要靠每分钟限流而非每日 Token 包。`config.yaml` 中 `rpm: 20`、`free-pool` 优先级 60；**不进** `trusted-pool` / `strong-model-pool`。

## 文件说明

项目按职责分为五层，根目录只保留配置、启动、依赖和文档：

```text
.
├── gateway/       # LiteLLM Hook、路由、额度、统计等运行时核心
├── scripts/       # 配置校验、体检、健康检查和运维命令
├── dashboard/     # FastAPI 管理面和静态前端
├── tests/         # pytest 回归测试
├── state/         # 上游模型目录审计状态
├── config.yaml
├── provider_manifest.yaml
├── docker-compose.yml
└── run.sh
```

| 文件 | 作用 |
|------|------|
| `config.yaml` | LiteLLM 配置：模型列表（四层路由 + trusted-pool）、路由策略、预算、fallback |
| `gateway/custom_router_hook.py` | 复杂度路由 hook：敏感检测 → 关键词 → 分类器 → 启发式兜底；同时负责把用量记录进 Redis |
| `gateway/llm_classifier.py` | 可选的独立额度任务分类器，未配 `CLASSIFIER_API_KEY` 时自动使用本地规则 |
| `gateway/usage_tracker.py` | 基于 Redis 的用量统计（这分钟/这一天用了几次、还有多久重置），供仪表盘读取 |
| `gateway/channel_ids.py` | 共享的渠道标识生成函数，config.yaml/hook/仪表盘三边共用 |
| `gateway/optimal_channels.py` | "限时优先"标记的存储与选择逻辑（基于 Redis，支持自动过期） |
| `provider_manifest.yaml` | 凭据信任等级、敏感数据政策、模型能力和多维额度的机器可读源 |
| `gateway/provider_registry.py` | 将 manifest 与 LiteLLM deployment 合并成运行时调度注册表 |
| `gateway/quota_manager.py` | Redis Lua 原子预占、凭据共享限额和被动熔断；付费渠道故障时 fail-closed |
| `gateway/runtime_launcher.py` | 启动时剔除空凭据 deployment，并把前端优先级转换成 LiteLLM `order` |
| `scripts/provider_discovery.py` | 定时审计上游 `/models`；`--autofix` 时对 model_missing 模糊匹配+强模型裁决并写回 config |
| `gateway/model_autofix.py` | 请求因模型名失效失败时自愈：拉目录 → 相似度 → 可选强模型裁决 → 改 config |
| `gateway/usage_tracker.py` | 用量 + **顺畅时段**（按本地小时累计成功/失败，仪表盘标注） |
| `scripts/validate_config.py` | 严格 YAML、派生 direct、manifest、env 引用与 fallback 无环校验 |
| `gateway/pricing.py` | 单价查询：先查 litellm 内置价格库，查不到用小估算表兜底，都查不到就诚实返回"未知" |
| `dashboard/config_editor.py` | 安全地在 config.yaml 里定位并修改单个渠道的 priority 字段 |
| `dashboard/` | 浏览器仪表盘（FastAPI 后端 + 静态前端），填 API Key、看用量、弱/中/强分类展示 |
| `docker-compose.yml` | Docker 编排：LiteLLM + Redis + Postgres + Dashboard |
| `scripts/test_gateway.py` | 本地体检脚本（不接真实 API，分类器全程 mock 掉） |
| `scripts/health_check.py` | 默认无消耗存活检查；显式 `--probe-upstreams` 才探测真实渠道 |
| `scripts/quickstart.py` | 快速启动向导 |
| `scripts/create_client_key.py` | 创建限模型/限 RPM/TPM/限 IP 的 LiteLLM 虚拟客户端 Key |
| `backup.sh` | 备份 PostgreSQL、Redis 和非密钥配置 |
| `.env.example` | 环境变量模板 |

## v2 修复清单（对照 Manus 验证报告）

### 关键 Bug 修复

1. **[CRITICAL] `call_type` 判断错误**
   - **问题**：LiteLLM Proxy 对 `/v1/chat/completions` 传入的 `call_type` 是 `"acompletion"`（异步），而非 `"completion"`（同步）。原版 hook 的 `if call_type not in ("completion", "text_completion")` 永远为 True，导致 hook 直接 return、复杂度路由逻辑根本没执行。
   - **修复**：显式覆盖所有补全类 call_type：`("completion", "acompletion", "text_completion", "atext_completion")`

2. **[CRITICAL] `auto-route` 未在 model_list 中**
   - **问题**：`auto-route` 不在 `model_list` 里，`/v1/models` 端点看不到它，且部分 LiteLLM 内部检查可能在 hook 改写前就拒绝未知 model_name。
   - **修复**：新增 `auto-route` 到 `model_list`（指向 GLM 免费渠道作为安全默认值），并新增 `model_group_alias` 让它在 `/v1/models` 可见。

3. **`async_pre_call_hook` 签名**
   - **问题**：原版缺少 `cache` 参数，在 LiteLLM 1.90.x 下会 TypeError。
   - **修复**：补全签名为 `(self, user_api_key_dict, cache, data, call_type)`。

4. **多模态 content 容错**
   - **问题**：`messages` 里 `content` 为 list（多模态格式）时，原版 `str(content)` 会把 list 变成 `"[{...}]"` 字符串，导致关键词匹配失效。
   - **修复**：新增 `_extract_text()` 正确提取文本部分。

### Docker 部署增强

5. **Redis / Postgres 健康检查**
   - 新增 `healthcheck`，并用 `depends_on: condition: service_healthy` 确保主服务在依赖就绪后才启动。

6. **PYTHONPATH=/app**
   - 确保 `gateway/custom_router_hook.py` 能被正确 import。

7. **主服务健康检查**
   - 新增 `/health/liveliness` 健康检查，方便编排工具判断状态。

### 配置增强

8. **`callbacks` 改为列表格式**
   - 更符合 LiteLLM 文档规范。

9. **`disable_telemetry: true`**
   - 关闭遥测上报（隐私 + 减少不必要的网络请求）。

10. **参数透传策略**
    - 当前使用 `drop_params: false`：不静默删除 tools、JSON Schema 等能力参数；不兼容的渠道会在能力过滤阶段排除或明确报错。

### 新增工具

11. **`scripts/health_check.py`** — 渠道健康检查 & 状态查询
12. **`scripts/quickstart.py`** — 快速启动向导
13. **hook 统计计数器** — 记录路由决策分布，方便调试

## 限时优先（活动额度 / 快过期额度优先烧）

对应需求：优先消耗快过期的 API 和活动 API，同时不让高难任务降档。

在仪表盘上给任意渠道点"⚡ 标记限时优先"，可以选填一个过期时间（比如"7 天试用额度"）：

- 标记生效后，只优先承接**不高于该渠道自身档位**的非敏感任务。例如标记中档后，弱档和中档任务优先给它，强档和顶级任务仍走上级池
- 标记强档可优先承接弱/中/强任务，但不会抢顶级任务；标记弱档只优先承接弱档任务
- 渠道无额度、不健康、处于熔断或能力不匹配时自动跳过，回到正常路由
- RPM 打满只是临时跳过（下一分钟窗口恢复后继续用），不是永久失效
- 多个渠道同时被标记时，按"最快过期的先用"排序
- 过期时间到了自动失效（用 Redis 的 TTL 实现，不需要额外的定时任务清理）
- **敏感内容检测依然是最高优先级**——哪怕某个渠道被标记了限时优先，命中密钥/密码/内网地址这类内容还是会被路由到 `trusted-pool`，不会因为"想省着用快过期的额度"就把敏感信息发出去

实现上，`config.yaml` 里给 fast/free/strong-pool 的每一个渠道都额外建了一个只含它自己的 `direct-xxxxxxxxxx` 分组（哈希值，见 `gateway/channel_ids.py`），保证命中限时优先时 Router 能 100% 精确路由到这一个 deployment，而不是又走一次池子内部的负载均衡。

## 手动设置优先级

每张渠道卡片上"优先级 N"的数字可以直接点击修改，保存后写入 `config.yaml`；再次执行 `bash run.sh` 后生效。

技术上只会去改这个渠道在它所属档位（fast/free/strong-pool）里的"主条目"：

- 如果这个渠道本来就有 YAML 锚点（大部分官方直营渠道都有），改的就是锚点定义本身，`trusted-pool`/`direct-xxxxxxxxxx` 里引用它的地方会在下次重启解析时自动跟着变。
- 如果没有锚点，`trusted-pool` 里本来就不该有这个渠道，`direct-` 分组里那份独立副本的优先级字段不影响任何实际路由（那个分组永远只有一个 deployment，优先级只有在"多个 deployment 选一个"时才起作用），不需要同步。
- 找不到能唯一定位的条目时直接放弃、不做任何修改，不会瞎猜改错地方。

## 消耗统计与金额估算

仪表盘顶部会显示所有渠道**累计消耗的 token 数**和**预计的累计花费**，每张卡片也会显示自己的 token 消耗和金额（今天 / 累计）。

单价怎么来的，按你的要求"先通过 API 查询，查不到再估算"，具体是三层顺序（`gateway/pricing.py`）：

1. **General Compute 先查单独维护的官方价表**——这是已充值的按量付费渠道；未知模型明确显示“未知”，不会套用另一家托管商的同名模型价格。
2. **其他渠道查 `litellm.completion_cost()`**——LiteLLM 自带、社区持续维护更新的价格库（`model_prices_and_context_window.json`），覆盖了大部分主流模型的官方定价。
3. **查不到，退回一个很小的估算表**（目前只收录 Together AI / Fireworks / DeepInfra 的已核对部署名）；估算只用于数量级参考。
4. **仍查不到**：显示“暂无定价数据”，绝不会把未知伪装成 `$0.00`。

如果你把自己的付费 API（比如自己的 OpenAI/Anthropic key）也接进来，只要 litellm 价格库里有这个模型，费用会被精确计算并显示；金额前面如果带"~"，代表这笔钱是用第 2 层的估算表算出来的，不是精确值。

## 浏览器仪表盘

`docker compose up -d` 之后访问 `http://127.0.0.1:4000/`。这是中文统一入口：
浏览器打开时显示控制台，OpenAI 兼容接口继续使用
`http://127.0.0.1:4000/v1`。旧地址 `http://127.0.0.1:8080/` 暂时保留兼容，
显示的是同一个界面。

个人自用默认启用 `DASHBOARD_AUTH=local`：仪表盘只监听 `127.0.0.1`，打开网页即可管理，不需要再输入第二个令牌。管理 API 会拒绝浏览器跨站请求，也不开放 CORS。如果以后要把仪表盘开放到局域网，请先把 `.env` 改为 `DASHBOARD_AUTH=token`，重启后使用独立的 `DASHBOARD_TOKEN` 登录。

- 按 弱/中/强 三档分类展示所有渠道，每个渠道一张卡片
- 支持按供应商、模型、环境变量和档位即时检索；每个档位可折叠，已配置渠道自动前置
- 每档默认只展示一家服务商；展开后每家公司仍只占一张卡，同公司的多个模型用下拉框切换
- 渠道卡片中的上游模型名称可由用户直接填写，不把初始目录中的模型 ID 锁死为内置值
- 圆环展示这分钟用了几次/RPM 上限，颜色随用量比例从绿变黄变红
- 显示今天累计调用次数、多久后重置（数据来自 Redis，`gateway/custom_router_hook.py` 每次请求成功/失败都会记一笔）
- 显示成功/失败数、平均延迟、最近错误类型和上游模型目录审计状态
- 信任等级徽章：官方直营 / 第三方托管 / 中转站 / 观察期·谨慎（Agnes AI 目前是唯一的"观察期"渠道）
- 直接在卡片里填写 API Key 和上游模型名；保存后再次执行 `bash run.sh`，由脚本重新加载 `.env` 与路由配置
- 控制台可一键创建仅授权 `auto-route` 的客户端密钥，主密钥不会发送到浏览器；新密钥只在创建时完整显示一次

跟 LiteLLM 自带的 Admin UI（`/ui`，按花费/请求数展示）不是一回事：那个是"花了多少钱"视角，对这批 `max_budget: 0.01` 的免费渠道意义不大；这个仪表盘是"还剩多少次调用/什么时候重置"视角，两者互补。

**诚实的限制**：
- 分钟用量是固定 60 秒窗口，日用量按 `USAGE_TIMEZONE` 的自然日统计；它们跟 LiteLLM Router 内部限流/冷却不是同一套账本，仪表盘数字不代表 Router 内部限流判断的精确依据。生产路由默认 `simple-shuffle`（官方推荐；避免 `usage-based-routing-v2` 的 #16060 误报）。
- 渠道卡片「额度」默认只展示 **当前周期剩余 %** 与 **总 token 消耗**（含今日 tokens），不再堆多模态多窗口说明；细节见文档链接。
- Redis 不可用时仪表盘会显示"暂无实时用量数据"，不会瞎显示 0 次误导你。
- **档位由用户自选**：每张渠道卡片上可直接切换 弱 / 中 / 强 / 顶级；写入 config 并持久化到 `state/tier-overrides.json`，智能路由按所选档位调度。

## 已知限制

- **普通池内调度**：能力直连/限时优先已使用 Redis Lua 做凭据级原子预占；纯文本的普通池内选择仍由 LiteLLM Router 负责，需要结合真实账号限额压测。
- **空凭据与优先级**：源配置保留完整渠道目录供编辑；每次 `bash run.sh` 都会生成仅含已配置渠道的运行时配置，优先级越高越先尝试。快速/中档可以向上借用强档，强档不会向下静默降级。
- **智能分诊默认开启（有强档 Key 时）**：自动选用已配置强档做强度判断；也可 `CLASSIFIER_*` 固定；失败回退启发式。
- **Docker iptables**：在某些沙盒环境（如 Manus 验证环境）中，iptables `raw` 表缺失会导致 Docker 网络初始化失败。这是环境问题，不是项目 bug。在正常 Linux 主机上不受影响。
- **Redis/Postgres 连接**：需要 Docker 启动后才能验证。`scripts/health_check.py` 可以帮助确认连接状态；Redis 同时也是仪表盘用量统计的数据来源，不通的话仪表盘会显示"暂无数据"而不是报错。
- **多账号 ToS 风险**：Mistral 用两个账号叠加免费额度这件事，大概率违反 Mistral 的服务条款（多数厂商都禁止"创建多个账号规避速率限制"）。这是个人白嫖习惯，请不要直接搬进有真实业务负载的项目里。
- **Agnes AI 是观察期渠道**：上线约一个月，缺乏第三方审计/长期口碑积累，其"Claw-Eval"评测榜单是自建/关联站点。已确保它不会处理敏感内容或强档任务，但仍建议定期关注它的条款变化。
- **限时优先依赖 Redis**：Redis 不可用时"限时优先"标记形同虚设（会静默失败/返回空），请求会退回正常的弱/中/强路由，不会报错，但也不会像预期那样优先烧额度——建议定期看一眼仪表盘顶部的横幅，确认标记确实生效了。
- **目录审计不等于真实 completion**：`provider-monitor` 主动检查 `/models`，被动失败会触发熔断；为了不耗尽低 RPD 免费额度，默认不对 102 个 deployment 定时发送真实 completion。
- **非聊天端点**：LiteLLM Proxy 仍提供其版本支持的 OpenAI-compatible 端点，但本项目的自动能力路由目前只覆盖聊天请求；尚未配置 embedding、图片生成和语音模型，因此不会伪造这些能力。
- **响应缓存默认关闭**：避免跨客户端复用含敏感数据的响应；如要启用，应先确定租户隔离、缓存键和数据保留政策。
- **密钥静态存储**：`.env` 会被限制为 `0600` 且不会进入备份，但仍是宿主机明文文件。生产环境应接入 Docker/Kubernetes secrets 或云端密钥管理服务。

## v5 变更记录（浏览器仪表盘 + 指定模型分类）

- 新增 `gateway/llm_classifier.py`：任务先交给指定模型判断弱/中/强档位；当前默认 Groq GPT-OSS 20B，失败自动降级到本地规则。
- 重构 `gateway/custom_router_hook.py`：抽出 `_quick_escalation_check()` 复用关键词/正则/文件数判断逻辑，修复了"短文本命中关键词却被短文本规则抢先分流"的问题；新增 `async_log_success_event`/`async_log_failure_event` 把用量记录进 Redis。
- 新增 `gateway/usage_tracker.py`：基于 Redis 的固定窗口用量统计，供仪表盘查询。
- 新增 `dashboard/`：FastAPI 后端（`backend.py` + `channel_loader.py` + `provider_catalog.py`）+ 苹果美学风格的静态前端（`static/index.html`）。
- 重新调整 Agnes AI 的优先级（`free-pool` 内从 25 提到 60），但保持不进 `trusted-pool`/`strong-model-pool`。
- 修了几个开发过程中实测发现的真实 bug：
  - `scripts/test_gateway.py` 的 dummy 环境变量列表漏了 `SAMBANOVA_API_KEY` 和 `HF_TOKEN`（`.env.example` 也漏了），`config.yaml` 里明明用到了。
  - 仪表盘按 `model+api_base` 给渠道分配 id 时，Mistral 的两个账号会撞车成同一条记录（它们的 model 字符串和 api_base 完全一样，只有账号不同）——改成 `model+api_base+env_var` 三元组做展示主键，用量查询则用 `model+api_base+api_key的哈希` 做区分。
  - `scripts/test_gateway.py` 里"中等长度→free-pool"和"分类器覆盖"两条测试用例的样例文本实际长度低于 `FAST_CHAR_THRESHOLD`，一直都会被错误分流到 fast-pool，只是没人跑出来过。

## v6 变更记录（限时优先 / 活动额度优先烧）

- 新增 `gateway/channel_ids.py`：共享的渠道标识生成函数（`make_display_id` / `make_direct_model_name`），config.yaml 生成脚本、`gateway/custom_router_hook.py`、`dashboard/channel_loader.py` 三边共用，保证算出来的 id 一致。
- 新增 `gateway/optimal_channels.py`：基于 Redis 的"限时优先"标记存储，支持可选过期时间（用 Redis TTL 实现自动失效），按最快过期排序。
- `config.yaml` 包含 43 个 `direct-xxxxxxxxxx` 分组（每个只含 1 个 deployment），给"限时优先"功能做精确寻址用；已有 YAML 锚点的渠道直接复用，没有的保留同步副本。
- `gateway/custom_router_hook.py`：启动时解析 config.yaml 建立渠道注册表；`decide_pool_with_classifier()` 新增"限时优先"检查，优先级在敏感内容检测之后、关键词升级判断之前。
- 仪表盘新增标记/取消限时优先的按钮、⚡ 徽章、顶部横幅提示。
- 顺手修了一个真实 bug：`dashboard/backend.py` 查询用量时之前传的是 `channel_id`（展示用的稳定主键）而不是 `usage_key`（哈希后、跟 hook 记录用量时一致的那个），会导致仪表盘永远查不到用量数据。

**关于参考的 Antigravity-Manager 项目**：调研后确认它的核心机制是逆向 Google Antigravity/Gemini CLI 的 OAuth 会话来伪造官方产品的免费额度访问，项目自己的 README 都写明这违反 Google 服务条款，社区在 2026 年 2 月出现过大规模封号潮，还有 issue 反映它请求了过度宽泛的 `cloud-platform` 权限、`refreshToken` 明文存储。这跟本项目"用各家官方发放的、文档写明的免费 API Key 做负载均衡"是完全不同的风险类别——仪表盘的设计语言（额度百分比、健康状态展示、自动切换）值得借鉴，但账号获取机制不会采用。

## v7 变更记录（手动优先级 + token/金额统计）

- 新增 `dashboard/config_editor.py`：安全定位并修改渠道的 priority/model 字段，只在能唯一定位到目标时才动手，改完再次执行 `bash run.sh` 生效。
- 新增 `gateway/pricing.py`：三层定价查询（litellm 内置价格库 → 小估算表 → 诚实返回"未知"），`compute_cost()` 直接对接 `litellm.completion_cost()`。
- `gateway/usage_tracker.py` 扩展：新增 day/total 两套 token 和金额累加计数器（`incrby`/`incrbyfloat`）；今天的窗口自动重置，累计键在渠道持续活跃时续期，停用 400 天后清理（可用 `USAGE_TOTAL_RETENTION_DAYS` 调整）。
- `gateway/custom_router_hook.py` 的 `async_log_success_event` 现在会顺手提取 token 用量、调用 `pricing.compute_cost()`，一起记进 `usage_tracker`。
- 仪表盘：优先级数字可点击直接编辑；每张卡片新增 token/金额展示（估算价格会标"~"和"估算价"标签，查不到定价的显示"暂无定价数据"而不是 $0）；顶部新增"累计消耗 tokens"和"预计累计花费"两个汇总数字。
- 所有新增逻辑都用内存版假 Redis + 假 `litellm.completion_cost` 做了端到端验证：免费渠道正确显示"暂无定价"、估算表命中渠道正确算出金额并标注来源、精确计价渠道正确累加、多次调用正确叠加。

## 客户端 Key 与备份

不要把网关 master key 配给普通客户端。网关启动后可创建限额虚拟 Key：

```bash
python3 -m scripts.create_client_key --name codex-laptop --models auto-route --rpm 30 --tpm 100000 --duration 30d
```

备份数据库和非密钥配置：

```bash
./backup.sh
```

`.env` 故意不进入备份包，避免制造第二份明文 API Key；请用密码管理器单独备份。

## v8 变更记录（调度正确性与安全加固）

- Dashboard API 默认采用仅本机免登录模式，可选独立令牌保护；不开放 CORS，并保留 CSP、防 iframe、跨站拦截、输入范围校验和加锁安全写入。
- LiteLLM 固定到 `v1.91.1`，Redis/PostgreSQL/Dashboard 密码由 `run.sh` 独立生成，Redis 开启密码。
- 新增 `provider_manifest.yaml` / `gateway/provider_registry.py`，路由前强制校验能力和敏感数据政策。
- 新增 Redis Lua 原子额度预占、凭据级共享限额、错误分类熔断和成功自动恢复。
- 分类器不再隐式消耗业务 Groq Key；长上下文硬约束先于分类器。
- 敏感检测扩展到 tool arguments，上游异常原文不再记录。

## v9 变更记录（统一入口与逆向回归修复）

- 宿主 `4000` 由中文控制台统一接入，完整转发 LiteLLM HTTP、流式请求/响应和 WebSocket；`8080` 保留兼容入口。
- 启动时动态过滤空 API Key，避免 Router 随机选中未配置 deployment；将界面 `priority` 映射到 LiteLLM 官方 `order`。
- 默认健康检查改用 `/health/liveliness`，不探测模型、不消耗 General Compute 等付费额度；真实探测必须显式传 `--probe-upstreams`。
- General Compute 标记为付费渠道：Redis 额度账本不可用时拒绝放行，未知模型不再套用通用价格。
- Dashboard 默认本机免登录，快速向导不再要求 `DASHBOARD_TOKEN`；只有切换到 `DASHBOARD_AUTH=token` 才需要令牌。
- 更新 Requests 安全修复版本，补回 CI、Dashboard 依赖更新检查、启动脚本回归和前端检索/折叠测试。
- 新增上游 `/models` 定期审计、成功/失败/延迟观测、严格配置验证、CI、Dependabot 与回归测试。

## 参考

- [LiteLLM 文档](https://docs.litellm.ai/)
- [LiteLLM Router 策略](https://docs.litellm.ai/docs/routing)
- [LiteLLM Proxy 配置](https://docs.litellm.ai/docs/proxy/configs)
