# Daythread / 朝夕 · 工业级落地设计

> **Status**: Draft v0.1<br>
> **Owner**: Hugo Xu<br>
> **Scope**: 开源核心客户端/daemon/连接器 SDK/协议/评测<br>
> **One-liner**: *Your day, dawn to dusk — local-first, auditable, yours.*

> **文档边界**：本文件是**公开技术设计**，只包含架构、接口、数据模型、安全、评测、部署与社区治理。<br>
> 人员编制、融资、定价、毛利、GTM、商业里程碑属于内部计划，**不进入本仓库**。
>
> **实现状态**：v0.24 baseline 位于 [`../daythread`](../daythread)，是 Python 3.9 标准库参考实现（memory / policy / ledger / approval / undo / brief / CLI / plans / proactivity / connectors / skills / local API / model gateway / MCP / scheduler / backup）。Rust daemon + Tauri 为目标实现，接口与数据模型以此文档为准。

---

## 0. 结论摘要（TL;DR）

Daythread（中文：**朝夕**）是一个**本地优先、可审计、可自托管、模型可插拔**的个人智能体。

工业级落地要解决的不是「让模型更聪明」，而是五件事：

1. **状态**：跨天、跨设备、跨应用的个人记忆，且可编辑、可溯源、可删除。
2. **边界**：任何对外/不可逆动作必须经过策略引擎与人工确认，且可撤销。
3. **信号**：主动性必须有「打断预算」，误报率是第一优先级指标。
4. **执行**：长任务要能在设备离线、进程崩溃、模型超时后继续，且幂等。
5. **可运营**：评测、可观测、合规、成本、供应链安全，一个都不能少。

**核心架构决策**：
- 桌面端 **Tauri 2** + 本地 **Rust daemon**（可靠性/权限/沙箱友好）。
- 本地 **SQLite**（事实库 + 向量索引），Markdown 镜像，端到端可导出。
- 核心用 **Cedar** 做策略；参考托管实现（独立仓库）用 **Temporal** 跑持久化工作流、**microVM** 做执行沙箱。
- 所有连接器走 **MCP + OAuth broker**，连接器是「能力清单 + 风险分级」。
- 全链路 **Run Ledger**（哈希链、可回放），作为信任与审计底座。
- 开源核心 **Apache-2.0**；托管云/企业版独立许可。

**不做什么**（第一版明确排除）：全自主发送、医疗诊断、企业 RBAC、家庭共享记忆、Windows/Linux 屏幕控制。

---

## 1. 产品定义与范围

### 1.1 三支柱

| 支柱 | 定义 | 验收标准 |
|---|---|---|
| **活记忆** | 结构化事实（人物/项目/偏好/承诺/目标/节奏），跨会话跨设备 | 每条事实可读、可改、可删、可溯源；删除后立即不参与推理 |
| **主动** | 只在晨/暮两个锚点开口，贴着变化 | 主动信息「换成一个靠谱的人，会说这句话吗」；误报率 < 5% |
| **执行** | 规划、调用工具、检查、汇报 | 外部动作 100% 经确认门；幂等；可撤销 |

### 1.2 五属性（用于验收）

1. **个人范围**：优化一整天（工作+健康+生活），不是单一工作流。
2. **可审计记忆**：一行行事实，而非不可搜的聊天日志。
3. **日程优先**：首屏是今天，不是空白输入框。
4. **范围限定执行**：建议只覆盖智能体真能完成的动作。
5. **克制主动**：锚点上限 + 默认折叠 + 可行动窗口。

### 1.3 非目标

- 不做完全自主（autonomous-by-default）。
- 不做临床/医疗诊断，只做生活方式支持。
- 不做通用企业工单/CRM 机器人。
- 不做社交/内容平台。
- 不绑定单一模型厂商。

### 1.4 Personas / JTBD

| Persona | 痛点 | 关键任务 |
|---|---|---|
| 创始人/独立开发者 | 客户、投资人、产品截止日期分散 | 跨应用跟进、周报、日程保护 |
| 知识工作者 | 会议/邮件/笔记割裂 | 晨间简报、邮件草稿、会议纪要 |
| 创作者/研究者 | 长期线索、引用丢失 | 记忆检索、研究跟踪、证据留存 |
| 健身/健康人群 | 训练与恢复信号不联动 | 睡眠/HRV 驱动的日程调整（非诊断） |
| 年轻父母 | 家庭后勤与工作冲突 | 冲突检测、改期建议、提醒 |

---

## 2. 工业级目标（SLO / Scale）

### 2.1 SLO

| 层 | 指标 | 目标 |
|---|---|---|
| 本地 UI | 首屏可交互 | p95 < 150 ms |
| 本地 UI | 记忆检索（本地） | p95 < 50 ms |
| 对话 | 首 token | p95 < 1.5 s（云模型） |
| 简报 | 晨间简报生成 | p95 < 30 s，失败重试 2 次 |
| 云 API | 可用性 | 99.9%（月度） |
| 调度器 | 定时任务触发 | 99.5%，错过窗口 60 s 内补跑 |
| 连接器 | 动作成功率（不含第三方故障） | > 99% |
| 安全 | 未授权外部动作 | **0** |
| 安全 | 撤销成功率 | > 99% |
| 数据 | Ledger RPO | 0（同步写入后才确认） |
| 数据 | 恢复 RTO | < 1 h |

### 2.2 规模目标（容量规划）

| 阶段 | 活跃用户 | 并发 Agent Run | 事实总量 | 连接器 |
|---|---|---|---|---|
| MVP | 1k | 50 | 1M | 3 |
| 早期 | 100k | 2k | 50M | 20 |
| 规模化 | 1M | 10k | 1B | 60+ |

> 部署成本随「纯本地 / 自托管 / 托管云」差异极大，且与具体云厂商、模型价格强相关，不在本设计文档中给出数字。实现层面只约定了**预算与降级机制**（见 4.7、9.1）。

---

## 3. 总体架构

### 3.1 部署拓扑（三种形态）

```
A. Local-only（默认，开源）
┌──────────── Client (Tauri/RN) ────────────┐
│  UI  ◄──►  Local Daemon (Rust)            │
│             ├─ Agent Runtime              │
│             ├─ Memory (SQLite)            │
│             ├─ Policy (Cedar)             │
│             ├─ Connectors (MCP)           │
│             └─ Model Gateway → BYO API    │
└───────────────────────────────────────────┘

B. Hybrid（推荐默认）
Client + Local Daemon  ◄──E2EE sync──►  Cloud Control Plane
                                   ├─ Sync relay
                                   ├─ Scheduler (push)
                                   ├─ Cloud VM sandbox
                                   ├─ Model gateway
                                   └─ Ledger (encrypted)

C. Self-host / Enterprise
K8s: control plane + data plane（对象存储/Postgres/microVM）
```

### 3.2 逻辑分层

```
┌──────────────────────────────────────────────────────────┐
│ Experience: Desktop / Mobile / Web / Browser Ext / IM Bot │
├──────────────────────────────────────────────────────────┤
│ Agent Runtime: Orchestrator · Planner · DAG · Tool Router │
│                State Machine · Checkpointer · Idempotency │
├──────────────────────────────────────────────────────────┤
│ Cognition: Memory · Retrieval · Proactivity · Ranking     │
│            Model Gateway · Prompt/Skill Registry          │
├──────────────────────────────────────────────────────────┤
│ Governance: Policy (Cedar) · Approval · Consent · Ledger  │
├──────────────────────────────────────────────────────────┤
│ Execution: Connectors (MCP) · Sandbox (microVM) · Device  │
├──────────────────────────────────────────────────────────┤
│ Platform: Sync (CRDT/E2EE) · Auth · Billing · Observability│
└──────────────────────────────────────────────────────────┘
```

### 3.3 技术选型与 build-vs-buy

| 能力 | 选择 | 理由 |
|---|---|---|
| 桌面壳 | Tauri 2（Rust+React） | 体积小、系统权限细粒度、跨平台 |
| 本地 daemon | Rust（tokio + axum） | 内存安全、并发、可嵌入 |
| ML/评测 | Python worker（gRPC） | 生态、迭代速度 |
| 本地存储 | SQLite + sqlite-vec | 单文件、可导出、事务 |
| 云端 DB | Postgres + pgvector | 成熟、可托管 |
| 工作流 | Temporal | 长任务、重试、补偿、可恢复；自建成本高 |
| 沙箱 | Firecracker / gVisor | 强隔离、快照、低开销 |
| 策略 | Cedar（或 OPA/Rego） | 授权表达强、可审计 |
| 消息 | NATS / SQS | 解耦、背压 |
| 同步 | Automerge/Yjs + libsodium | CRDT + E2EE |
| 观测 | OpenTelemetry + Grafana/Tempo | 标准、可移植 |
| 模型路由 | 自建轻量 gateway（参考 LiteLLM） | 成本/降级/缓存可控 |
| 连接器 | MCP + OAuth2/PKCE | 生态、标准化 |

---

## 4. 关键子系统设计

### 4.1 Memory（活记忆）

**目标**：把事件流蒸馏成稳定、可审计、可撤销的事实。

**四层模型**

```
Raw Events (append-only, encrypted)
   │  normalize
   ▼
Evidence (source, snippet, hash, taint)
   │  extract + dedupe + merge
   ▼
Facts (subject, predicate, object, confidence, valid_from/to)
   │  project
   ▼
Views (Memory panel, brief context, search index, Markdown mirror)
```

**生命周期**

1. **Ingest**：连接器/设备/对话产生带来源的事件。
2. **Extract**：LLM + 规则抽取候选事实；每条带 `evidence_ids`。
3. **Resolve**：实体消歧（人/项目）、去重、冲突检测。
4. **Score**：置信度 = 来源可信度 × 抽取置信度 × 重复次数。
5. **Store**：写入事实表；旧事实 `superseded_by` 或 `valid_to`。
6. **Retrieve**：结构化过滤 + 全文 + 向量 + 时间衰减，混合排序。
7. **Decay**：低价值/过期事实降权或归档（不删除）。
8. **Forget**：用户删除 → 墓碑 → 同步到所有设备 → 从索引移除 → 推理上下文隔离。

**关键规则**

- 事实必须有 `provenance`（事件 ID 列表），否则不入库。
- 用户手动修改优先级最高（`created_by=user`），模型不得覆盖。
- 删除是**硬语义**：不再参与检索/推理，Ledger 保留删除记录（合规）。
- 健康类事实标记 `sensitivity=health`，单独加密与保留策略。
- 记忆冲突不静默覆盖，进入「待确认」队列或标注冲突。

**检索策略**

```
score = w1*structured_match + w2*lexical(BM25) + w3*vector_cosine
        + w4*recency_decay + w5*confidence - w6*staleness
```

- 默认 top-k=20，重排后取 5–8 条注入上下文。
- 每条注入上下文都带 `fact_id`，便于「为什么这样建议」溯源。

### 4.2 Agent Runtime

**状态机**

```
CREATED → PLANNING → AWAITING_APPROVAL → EXECUTING → (PAUSED) → COMPLETED
                                  └──────────────► FAILED
                                  └──────────────► CANCELLED
```

**核心机制**

- **DAG 计划**：节点 = 工具调用/子任务/人工确认；边 = 依赖。
- **Checkpoint**：每步持久化状态（本地 SQLite / 云端 Temporal）。
- **幂等**：每个工具调用带 `idempotency_key = hash(run_id, step_id, input)`；连接器侧去重。
- **重试**：指数退避 + 抖动；只对幂等/可安全重试的动作。
- **补偿**：不可逆动作必须有 `compensating_action` 或明确不可撤销标记。
- **超时**：步骤级 + 运行级预算；超时进入 PAUSED 并通知。
- **恢复**：进程重启/设备切换后从最近 checkpoint 继续。
- **人机检查点**：风险分级为 `ask` 的节点阻塞，推送审批。
- **预算**：token/时间/工具调用次数上限，超限降级或暂停。

**Planner 输出契约（示例）**

```json
{
  "goal": "reschedule design review",
  "steps": [
    {"id": "s1", "type": "tool", "tool": "calendar.find_slots", "input": {...}},
    {"id": "s2", "type": "tool", "tool": "calendar.update_event", "input": {...}, "depends_on": ["s1"], "risk": "write"},
    {"id": "s3", "type": "tool", "tool": "gmail.create_draft", "input": {...}, "depends_on": ["s2"], "risk": "external"}
  ],
  "requires_approval": ["s2", "s3"]
}
```

### 4.3 Proactivity Engine

**目标**：高信噪比，不打扰。

```
Signals (calendar diff, email, health, files, commitments)
   → Watchers / Cron
   → Candidate Generator (LLM + rules)
   → Scorer
   → Budget Gate
   → Delivery (anchor / immediate)
   → Feedback (accept / ignore / mute / undo)
   → Learning (bandit, per-user)
```

**打分**

```
priority = relevance × urgency × actionability × novelty × confidence
```

**打断预算（Interrupt Budget）**

- 每用户每天默认 3 条主动消息（晨间 1 + 晚间 1 + 紧急 1）。
- 紧急通道仅限：2 小时内冲突、航班/出行、已承诺的外部截止日期。
- 同类事件去重窗口 24h；被忽略 2 次同类 → 降权 7 天。
- 静默时段、专注时段、休假模式。

**反馈闭环**

- 采纳/忽略/静音/撤销全部入 Ledger。
- 用 contextual bandit 调权重，不做端到端 RL（可解释、可回滚）。
- 每周生成「主动性报告」给用户（透明）。

### 4.4 Policy & Approval（确认门）

**能力模型**

```yaml
capability:
  id: calendar.write
  scopes: [calendar.events.read, calendar.events.write]
  risk: write            # read | write | external | destructive
  reversibility: undoable
  default: ask
```

**风险分级**

| 风险 | 例子 | 默认策略 |
|---|---|---|
| read | 读日历/邮件 | allow（已授权） |
| write | 建任务、改本地笔记 | allow/ask 可配 |
| external | 发邮件、发消息、发帖 | **ask 强制** |
| destructive | 删除、付款、改权限 | **ask + 二次确认** |

**策略引擎**：Cedar，输入 `(principal, action, resource, context)`，输出 `allow|deny|ask`。
**Dry-run**：所有写操作先生成 diff 预览。
**Undo**：保存前态；支持一键撤销；不可逆动作明确标注。
**Consent Ledger**：每次授权/撤销记录时间、范围、版本、设备。

### 4.5 Connector Framework

**连接器 = 清单 + 适配器**

```yaml
manifest:
  id: gmail
  version: 1.2.0
  auth: oauth2
  scopes: [gmail.readonly, gmail.compose, gmail.send]
  actions:
    - name: search
      risk: read
    - name: create_draft
      risk: write
    - name: send
      risk: external
      requires_approval: true
  rate_limits: {rpm: 60, daily: 5000}
  data_classification: pii
  webhooks: [message.new]
```

**能力**
- OAuth broker（PKCE、短时 token、refresh、DPoP）。
- Token vault（本地 keychain / 云端 KMS，按租户加密）。
- Rate limiter + 退避 + 熔断。
- Webhook ingester（签名校验、重放保护）。
- 契约测试 + mock server + 录制回放。
- 连接器认证（签名、权限审查、安全扫描）。

**设备连接器（本地）**
- 通过 daemon 访问系统 API：日历、通讯录、提醒、健康、文件、剪贴板、屏幕、Shell。
- 每项独立授权、可撤销；高风险能力（屏幕、控制、Shell）默认关闭。
- Shell 每次执行都需审批；命令白名单 + 沙箱。

### 4.6 Execution Sandbox / Cloud VM

- **隔离**：microVM（Firecracker）或 gVisor；每会话独立。
- **快照**：任务开始前快照，失败可回滚；休眠降成本。
- **网络**：默认拒绝出站，按需 allowlist；代理记录所有请求。
- **文件**：overlay FS，任务结束回收；产物上传对象存储。
- **密钥**：短期注入，不落盘；任务结束销毁。
- **资源**：CPU/内存/时间/磁盘配额；超限终止。
- **可观测**：录屏（可选）、命令日志、网络日志入 Ledger。

### 4.7 Model Gateway

- **路由**：按任务类型/成本/延迟/隐私选择模型。
- **降级**：主模型失败 → 备用 → 本地小模型 → 明确告知用户。
- **缓存**：语义缓存（谨慎）+ prompt 缓存。
- **预算**：每用户/每任务 token 预算，超限暂停。
- **版本**：prompt/skill 版本化，可回滚；A/B 实验。
- **隐私**：按数据等级路由（健康/邮件可配置不出本地）。
- **护栏**：输入/输出过滤、PII 检测、注入检测。

### 4.8 Sync & Multi-device

- **CRDT**（Automerge/Yjs）同步事实、笔记、任务。
- **E2EE**：每设备密钥对；服务端只见密文；密钥恢复用助记词/设备配对。
- **冲突**：事实用 LWW + 手动合并；删除用墓碑。
- **离线**：本地优先，联网后合并；冲突可视化。
- **设备信任**：新设备需旧设备或恢复码批准。

### 4.9 Skills & Extensibility

- Skill = 声明式 manifest（输入/输出/工具/权限/示例）+ 可选代码。
- 沙箱执行，权限最小化；签名与来源校验。
- 版本化、可回滚、可审计；社区 registry（类似 npm + 审核）。
- 可选：AgentDescent 式 skill 自演化（隔离实验分支，需评测门禁）。

### 4.10 通知与简报

- 渠道：桌面、移动、邮件、IM（后续）。
- 模板化简报：3 行高亮 + 折叠详情 + 1–2 个可批准动作。
- 送达时间可调；时区/旅行感知。
- 失败降级：生成失败发「简版」而非静默。

---

## 5. 数据模型

### 5.1 事件（append-only）

```sql
CREATE TABLE events (
  id            TEXT PRIMARY KEY,
  tenant_id     TEXT NOT NULL,
  device_id     TEXT NOT NULL,
  ts            TEXT NOT NULL,
  source        TEXT NOT NULL,        -- gmail | calendar | device | chat
  type          TEXT NOT NULL,
  payload       BLOB NOT NULL,        -- encrypted at rest
  taint         TEXT NOT NULL,        -- trusted | untrusted
  prev_hash     TEXT,
  hash          TEXT NOT NULL,
  signature     TEXT
);
CREATE INDEX idx_events_tenant_ts ON events(tenant_id, ts);
```

### 5.2 事实

```sql
CREATE TABLE facts (
  id            TEXT PRIMARY KEY,
  tenant_id     TEXT NOT NULL,
  subject_type  TEXT NOT NULL,        -- person | project | self | org
  subject_id    TEXT,
  predicate     TEXT NOT NULL,        -- is_cofounder | due_date | prefers
  object        JSON NOT NULL,
  confidence    REAL NOT NULL,
  sensitivity   TEXT NOT NULL DEFAULT 'normal', -- normal | pii | health
  provenance    JSON NOT NULL,        -- event ids
  valid_from    TEXT,
  valid_to      TEXT,
  superseded_by TEXT,
  created_by    TEXT NOT NULL,        -- user | model | connector
  deleted_at    TEXT,
  updated_at    TEXT NOT NULL
);
CREATE INDEX idx_facts_lookup ON facts(tenant_id, subject_type, subject_id, predicate);
```

### 5.3 运行与账本

```sql
CREATE TABLE runs (
  id          TEXT PRIMARY KEY,
  tenant_id   TEXT NOT NULL,
  intent      TEXT NOT NULL,
  state       TEXT NOT NULL,
  plan        JSON,
  budget      JSON,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE run_steps (
  id              TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL,
  step_id         TEXT NOT NULL,
  type            TEXT NOT NULL,      -- tool | llm | approval | human
  input           JSON,
  output          JSON,
  risk            TEXT,
  status          TEXT NOT NULL,
  idempotency_key TEXT,
  started_at      TEXT,
  ended_at        TEXT,
  prev_hash       TEXT,
  hash            TEXT NOT NULL
);
CREATE INDEX idx_steps_run ON run_steps(run_id, step_id);
```

### 5.4 审批与授权

```sql
CREATE TABLE approvals (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL,
  step_id     TEXT NOT NULL,
  action      TEXT NOT NULL,
  diff        JSON,
  status      TEXT NOT NULL,          -- pending | approved | rejected | expired
  decided_at  TEXT,
  expires_at  TEXT
);

CREATE TABLE consents (
  id          TEXT PRIMARY KEY,
  tenant_id   TEXT NOT NULL,
  connector   TEXT NOT NULL,
  scopes      JSON NOT NULL,
  granted_at  TEXT NOT NULL,
  revoked_at  TEXT,
  device_id   TEXT,
  version     TEXT
);
```

### 5.5 同步

```sql
CREATE TABLE sync_ops (
  id          TEXT PRIMARY KEY,
  tenant_id   TEXT NOT NULL,
  device_id   TEXT NOT NULL,
  entity      TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  op          BLOB NOT NULL,          -- CRDT change (encrypted)
  ts          TEXT NOT NULL,
  applied     INTEGER NOT NULL DEFAULT 0
);
```

---

## 6. API 设计

### 6.1 本地 daemon

- 绑定 `127.0.0.1` 随机端口；启动时写入仅当前用户可读的发现文件。
- 认证：每次启动生成 bearer token，存 OS keychain；可升级 mTLS。
- 传输：内部 JSON-RPC（低开销），外部 REST + SSE。

```
POST   /v1/chat                 {messages, context_policy} → SSE
GET    /v1/today                → {morning|evening brief}
GET    /v1/facts?q=&type=       → facts[]
PATCH  /v1/facts/{id}           → fact
DELETE /v1/facts/{id}           → 204
POST   /v1/runs                 {intent} → run
GET    /v1/runs/{id}            → run + steps
POST   /v1/runs/{id}/approve    {step_id, decision}
POST   /v1/runs/{id}/undo       {step_id}
GET    /v1/ledger/{run_id}      → signed trace
GET    /v1/connectors           → manifests + status
POST   /v1/connectors/{id}/auth → oauth start
DELETE /v1/connectors/{id}      → revoke
POST   /v1/skills/{id}/invoke
```

### 6.2 云端

- REST + WebSocket/SSE；`Idempotency-Key` 头；cursor 分页；签名 webhook。
- 多租户：`tenant_id` 强制隔离；行级安全 + 每租户加密密钥。
- 版本：`/v1`，弃用策略 6 个月。

---

## 7. 安全与隐私

### 7.1 威胁模型（STRIDE 摘要）

| 威胁 | 场景 | 缓解 |
|---|---|---|
| Spoofing | 伪造连接器/webhook | 签名、OAuth、DPoP、mTLS |
| Tampering | 篡改记忆/账本 | 哈希链、签名、只追加 |
| Repudiation | 否认执行过动作 | Ledger + 审批记录 |
| Info disclosure | token/记忆泄露 | keychain/KMS、E2EE、最小化 |
| DoS | 任务风暴/成本攻击 | 预算、限流、配额 |
| Elevation | 越权工具调用 | Cedar、能力清单、沙箱 |
| **Prompt injection** | 邮件/网页注入指令 | **数据/指令分离 + taint 追踪** |
| **Tool poisoning** | 恶意 MCP server | 清单审查、签名、沙箱、默认拒绝 |
| **Exfiltration** | 诱导把数据发出去 | 出站 allowlist、DLP、external 动作强制确认 |

### 7.2 Prompt Injection 防御（重点）

1. **来源分层**：所有外部内容标记 `taint=untrusted`，注入上下文时用明确分隔与「仅作数据」声明。
2. **能力降级**：当上下文含 untrusted 内容时，禁止自动执行 external/destructive 动作。
3. **指令隔离**：系统提示、用户指令、外部数据分通道；外部数据不得改变工具权限。
4. **工具参数校验**：出站目标（收件人/URL/仓库）必须匹配白名单或显式确认。
5. **注入检测**：规则 + 小模型分类器 + 红队集持续回归。
6. **最小上下文**：只注入必要事实，减少攻击面。
7. **可观测**：记录每次工具调用的上下文来源，异常告警。

### 7.3 密钥与加密

- 本地：OS keychain；SQLCipher（可选）；E2EE 密钥不出设备。
- 云端：KMS 每租户 DEK；字段级加密（pii/health）；密钥轮换。
- Token：短期、最小 scope、可撤销；refresh token 绑定设备。
- 供应链：SLSA、SBOM、签名发布、依赖审计、锁文件。

### 7.4 权限最小化

- 首次只请求对话/本地记忆；连接器逐项申请。
- 高风险（屏幕、控制、Shell、短信）默认关闭 + 明确解释。
- 每项权限显示「能读什么/能写什么/如何撤销」。

---

## 8. 合规

| 法规/标准 | 要求 | 落地 |
|---|---|---|
| PIPL（中国） | 告知同意、最小必要、境内存储、跨境评估 | 国内独立栈；数据分类；同意记录 |
| GDPR（EU） | 合法性、DSAR、删除、数据可携 | 导出/删除 API；DPA；子处理者清单 |
| CCPA | 知情、删除、拒绝出售 | 不售卖数据；DSAR 流程 |
| 健康数据 | 敏感类别 | 单独同意；本地优先；不做诊断 |
| AI Act | 透明、风险分级 | 明确 AI 身份；人工确认；日志 |
| SOC 2 / ISO 27001 | 安全治理 | **仅适用于托管服务**，不属于开源核心；由托管/企业部署方自行认证 |

**DSAR 能力**：一键导出（JSON + Markdown + 附件清单）、删除（墓碑 + 备份过期）、处理时限 SLA。

**数据驻留**：CN / EU / US 区域化；跨区默认禁止，需显式开启。

---

## 9. 可靠性与性能

### 9.1 失败模式与处理

| 失败 | 影响 | 处理 |
|---|---|---|
| 模型超时/限流 | 任务卡住 | 重试 + 降级 + 暂停通知 |
| 连接器 5xx | 动作失败 | 退避 + 熔断 + 补偿 |
| 重复调用 | 重复发邮件 | 幂等键 + 去重表 |
| 进程崩溃 | 任务丢失 | checkpoint + 恢复 |
| 设备离线 | 本地任务停 | 云端接管（若允许）|
| 同步冲突 | 数据不一致 | CRDT + 可视化合并 |
| 成本失控 | 账单异常 | 预算 + 速率限制 + 告警 |

### 9.2 负载与容量

- 无状态 API 水平扩展；Temporal 分片；按租户限流。
- 调度器分片 + 幂等；热点用户隔离。
- 向量检索：本地 sqlite-vec；云端 pgvector + HNSW；分片/分区。
- 冷热分层：活跃事实内存/缓存，历史归档对象存储。

### 9.3 容量规划（示例）

- 1M MAU，20% 日活 = 200k DAU。
- 每 DAU 50 次模型调用/天 → 10M 调用/天 ≈ 115 QPS 峰值 3× = 350 QPS。
- 需要：gateway 20 实例、Temporal 集群、Postgres 主从 + 只读、对象存储。

---

## 10. 可观测性与评测

### 10.1 可观测

- OpenTelemetry：trace（run → step → tool）、metric、log。
- 关键指标：run 成功率、步骤延迟、工具错误率、审批时延、token 成本、主动采纳率。
- Run Ledger：不可篡改、可回放；支持「为什么这么做」解释。
- 告警：错误预算燃烧率、成本异常、注入检测命中、权限异常。

### 10.2 Daythread Bench（开源基准）

| 维度 | 指标 | 方法 |
|---|---|---|
| 记忆 | 抽取 P/R、时效正确性、删除传播 | 合成 + 脱敏真实场景 |
| 主动 | precision@anchor、采纳率、误报率 | 模拟日历/邮件事件流 |
| 执行 | 任务成功率、副作用正确性、幂等 | 沙箱 + mock 连接器 |
| 安全 | 注入抵抗率、越权拦截率、外泄拦截 | 红队集 |
| 连续性 | 跨设备一致、崩溃恢复 | 故障注入 |

**门禁**：每个 PR 跑离线集；发布前跑全量；线上影子评测。

---

## 11. 工程实践

### 11.1 仓库结构

**公开仓库（Apache-2.0）：`daythread`**

```
daythread/
├─ apps/
│  ├─ desktop/          # Tauri + React
│  ├─ mobile/           # React Native（审批/只读）
│  ├─ web/
│  └─ daemon/           # Rust 本地 daemon
├─ crates/
│  ├─ agent-core/
│  ├─ memory/
│  ├─ policy/
│  ├─ connectors/       # 连接器 SDK + 本地连接器
│  ├─ ledger/
│  └─ sync/             # CRDT/E2EE 客户端
├─ workers/
│  └─ ml/               # Python: 抽取 / 评测
├─ packages/
│  ├─ sdk-ts/
│  ├─ sdk-py/
│  └─ ui/
├─ connectors/          # 每个 MCP 连接器独立目录
├─ bench/
├─ docs/
└─ deploy/local/        # 本地打包、签名、更新
```

**闭源托管服务单独仓库（不在本仓库）：`daythread-cloud`**

```
daythread-cloud/
├─ control-plane/
├─ scheduler/
├─ model-gateway/
├─ sandbox/
├─ sync-relay/
└─ deploy/              # K8s / IaC / Terraform
```

核心与托管服务只通过**公开 SDK、协议与自托管接口**交互，避免「开源壳里藏商业逻辑」。

### 11.2 CI/CD

- PR：lint、类型、单测、契约测试、离线 bench 子集、安全扫描（SAST/依赖/secret）。
- 合并：构建、签名、SBOM、集成测试、镜像扫描。
- 发布：桌面签名/公证（macOS/Windows）、增量更新、灰度、回滚。
- 托管仓库（独立流水线）：IaC、蓝绿/金丝雀、Feature Flag（OpenFeature）。
- 数据库：迁移前向兼容、双写/回填、可回滚。

### 11.3 测试金字塔

- 单元：纯逻辑、策略、解析。
- 契约：连接器 manifest/action、API schema。
- 集成：daemon + 本地 DB + mock 连接器。
- E2E：真实 OAuth 沙箱（Google/Microsoft 测试租户）。
- 混沌：断网、超时、崩溃、重复消息。
- 红队：注入、越权、外泄。
- 评测：bench 回归 + 线上影子。

### 11.4 发布与版本

- 桌面：稳定/ Beta / Nightly；自动更新签名校验。
- 协议：本地 API 语义化版本；连接器 manifest 版本。
- 数据：schema 迁移带版本与备份；支持降级只读。

---

## 12. 交付路线图（技术）

> 只列**技术里程碑与退出标准**。人员、排期、融资、定价、GTM 不在本仓库。

| 阶段 | 交付 | 技术退出标准 |
|---|---|---|
| P0 | SPEC、威胁模型、Bench v0、ADR | 设计评审通过；Bench 可复现 |
| P1 | 桌面 MVP：记忆、聊天、晨/暮简报、3 个本地连接器、确认门 | 14 天无未授权外部动作；记忆纠错 < 10 s；崩溃可恢复 |
| P2 | 持久化运行、云 VM 沙箱、移动审批、同步 Beta、10 连接器 | 崩溃恢复 > 99%；重复副作用 = 0；跨设备一致 > 99.9% |
| P3 | MCP registry、Skills、自托管、可观测 | 5 个第三方连接器通过认证；Bench 公开 |
| P4 | 连接器覆盖与性能打磨 | 参考部署 SLO 达标；无 P0 安全缺陷 |

---

## 13. 技术风险登记册

| # | 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|---|
| R1 | 权限摩擦导致用户不愿授权 | 高 | 高 | 本地优先、逐项授权、可导出、权限说明 |
| R2 | 主动骚扰 | 高 | 中 | 打断预算、误报惩罚、默认折叠 |
| R3 | Prompt injection 导致外泄 | 极高 | 中 | taint、能力降级、出站 allowlist、红队 |
| R4 | 连接器维护成本 | 中 | 高 | MCP 生态、社区共建、认证 |
| R5 | 模型成本/延迟 | 高 | 中 | 路由、缓存、本地模型、预算 |
| R6 | 平台收紧（Apple/MS） | 高 | 中 | 桌面优先、多平台、Web 兜底 |
| R7 | 合规（跨境/健康） | 高 | 中 | 区域化、独立栈、法律审查 |
| R8 | 记忆错误侵蚀信任 | 高 | 中 | 溯源、置信度、用户优先、可撤销 |
| R9 | 供应链攻击 | 高 | 低 | SLSA、签名、SBOM、审计 |

---

## 14. 开源治理

- **许可**：Apache-2.0；DCO（非 CLA）；商标政策。
- **治理**：公开 RFC、roadmap、月度社区会；维护者分层。
- **安全**：SECURITY.md、私密披露、CVE 流程、赏金（后续）。
- **插件**：manifest 规范、认证徽章、签名、审核指南。
- **文档**：快速开始、架构、连接器开发、Bench、隐私说明。
- **社区**：Discord/GitHub Discussions；good-first-issue。

---

## 15. ADR（关键决策记录）

| ADR | 决策 | 理由 | 备选 |
|---|---|---|---|
| 001 | 本地优先 + 可选云 | 隐私/信任/离线 | 纯云 |
| 002 | Rust daemon | 安全/性能/嵌入 | Go/TS |
| 003 | SQLite + sqlite-vec | 单文件/可导出 | DuckDB/LanceDB |
| 004 | Temporal | 长任务可靠性 | 自建队列 |
| 005 | Cedar | 授权可审计 | OPA/自研 |
| 006 | MCP | 连接器生态 | 自研协议 |
| 007 | E2EE + CRDT | 多设备隐私 | 服务端明文 |
| 008 | Apache-2.0 | 采用最大化 | AGPL |
| 009 | 晨/暮双锚点 | 克制主动 | 实时推送 |
| 010 | external 强制确认 | 信任底线 | 可配置自主 |

---

## 16. 待定问题（Open Questions）

1. **移动端深度**：只做审批/只读，还是完整执行？
2. **云 VM 供应商**：Firecracker 自建 vs 托管（E2B/Modal 类）。
3. **E2EE 与服务端检索**：如何平衡隐私与云端能力？
4. **是否做自有小模型**：用于抽取/分类/注入检测，降低成本。
5. **连接器首发**：Google 全家桶（OAuth 审核）vs 纯本地（零审核）。
6. **健康数据边界**：地区差异与免责声明。
7. **Bench 数据**：合成 vs 众包脱敏，如何避免隐私与过拟合。

---

## 17. 下一步（建议）

1. 确认两个默认：**Tauri 桌面优先** + **本地连接器优先（零 OAuth）**。
2. 产出 `SPEC.md`、`THREAT_MODEL.md`、`BENCH.md`。
3. 起 monorepo 骨架：`apps/desktop`、`apps/daemon`、`crates/memory`、`crates/policy`。
4. 实现 P1 垂直切片：本地日历 → 晨间简报 → 改期建议 → 确认门 → 撤销 → Ledger。
5. 用 Daythread Bench v0 做第一轮回归。

> 工业级不是「功能多」，而是**边界清楚、失败可控、可审计、可运营**。Daythread 的第一版，只要把这四件事做扎实，就已经赢过大多数 demo。
