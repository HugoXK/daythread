# Capability memo: Auditable Reversible Execution

- **Date**: 2026-11-27
- **Author**: Daythread Product
- **Status**: draft v1.1（八段框架；竞品联网核实降级，见 §3 注记与 Assumptions）
- **Capability**: 把「策略判定 + 人工确认 + undo 配方 + 哈希链证据」做成所有工具/连接器/技能的一等公民契约。

---

## 1. Positioning

> **Daythread（朝夕）是给重视隐私与可追溯性的个人知识工作者 / 独立开发者的本地优先、可审计的个人 agent 参考实现；它明确不是通用企业 CRM/工单机器人，也不是默认全自主的云端助理。**

出处（仓库内逐字）：

- `README.md:9-13`：*"a local-first personal agent reference implementation … Everything runs on your machine with zero third-party dependencies."*
- `README.md:20-24`：目标是 *"a small, correct, auditable core … not feature breadth."*
- `README.md:96-97`：*"No telemetry. Network access only happens if you explicitly configure …"*
- `docs/DESIGN.md:17-35`：本地优先、可审计、可自托管、模型可插拔；「不做什么」排除全自主发送、医疗诊断、企业 RBAC、家庭共享记忆、屏幕控制。
- `docs/DESIGN.md:57-63`：非目标——完全自主、临床诊断、通用企业工单/CRM、社交/内容平台、绑定单一模型厂商。

**可证伪性**：任何提案若不能让「个人事务交给 agent 后仍可证明、可撤回」，就不强化这句定位。

---

## 2. Job to be done

> **当我**让 agent 替我改日历、发邮件或写文件时，**我想**在事后能证明它做了什么、谁批准的、能否原样撤回，**以便**我敢把更多真实事务交给它，而不是只让它回答问题。

---

## 3. Competitor set

> **核实状态（诚实记录）**：本轮 `web_search` 后端降级为 keyless Bing 回退，返回大量无关结果；`web_fetch` 对全部目标 URL 返回 `TypeError: fetch failed`（含代理 `r.jina.ai` 被解析为非公网 IP）。**因此 4 个竞品 0 条完成联网核对，4/4 标「未核实」**。日期与能力来自离线已知，仅作工作假设；**核实方式**：打开 URL 核对页面 `<time>`/发布日期与正文，若 404 用 `web.archive.org/web/2024*/<URL>` 或站内搜索标题。

| 产品 | 类型 | URL（日期） | 它擅长 | 它做不到 / 边界 |
|---|---|---|---|---|
| **Microsoft Recall**（Copilot+ PC） | 直接参照：OS 级本地记忆 | [blogs.windows.com/…/introducing-copilot-pcs](https://blogs.windows.com/windowsexperience/2024/05/20/introducing-copilot-pcs/)（2024-05-20 发布；2024-06 因隐私争议暂停；2024-11 起预览重发）**未核实** | 系统预装、零配置、本地截图/OCR 召回；分发极强 | 只覆盖本机应用；无逐动作策略/审批/可逆配方；审计不可导出为证明；跨应用写操作不可逆 |
| **Limitless**（原 Rewind AI） | 直接：全天候个人记忆 | [limitless.ai](https://www.limitless.ai/)（Rewind 2023 上线；2024-04 宣布转向 Limitless 吊坠）**未核实** | 连续采集、自然语言回忆、消费级体验 | 以「记录/回忆」为中心，不是「执行 + 可撤回」；云端处理与订阅；无开放执行契约 |
| **Mem（Mem Labs）** | 参照：AI 笔记 / 记忆工作区 | [mem.ai](https://mem.ai/)（Mem 2.0，2024 年，具体月份待核）**未核实** | 记忆优先的写作与检索、自动整理 | 无本地执行运行时；无审批门与哈希链账本；不可自托管审计 |
| **OpenAI ChatGPT**（记忆 + 连接器 + Operator/Atlas） | **相邻威胁**：通用助手吞掉「个人助理」任务 | [记忆](https://openai.com/index/memory-and-new-controls-for-chatgpt/)（2024-02-13）、[Operator](https://openai.com/index/introducing-operator/)（2025-01-23）、[Atlas](https://openai.com/index/introducing-chatgpt-atlas/)（2025-10-21）**均未核实** | 模型能力、分发、连接器生态、自然语言即界面 | 默认云端；动作日志面向产品而非审计；第三方无法重放/验证决策链；不可自托管 |

**旁证（同样是未核实的搜索碎片）**：36Kr 2025-12-08 报道称 Limitless 被 Meta 收购（[链接](https://36kr.com/p/3586631314996100)，`未核实`）——若属实则说明「个人记忆硬件」正被平台吞并，进一步抬高我们对「本地可审计执行」的差异化价值。

**参照基础设施（非竞品）**：Temporal 等 durable execution 把「崩溃后可恢复」做成基础设施常识，但没有把它做成面向个人的可导出审计凭证——这是我们的空位。

---

## 4. Capability axes（0–3）

> 我方证据取自仓库；竞品列基于离线印象（`未核实`），评分为主观工作假设，联网核对后需重打分。

| 轴 | Us | Recall | Limitless | Mem | ChatGPT | 每轴证据 |
|---|---:|---:|---:|---:|---:|---|
| 信任 / 可审计 | **3** | 1 | 1 | 1 | 1 | 哈希链 run steps、`dt ledger --verify`、审批门、审计日志、`dt compliance evidence`（README:35-37, 388-397） |
| 首次价值时间 | 2 | 3 | 2 | 3 | 3 | `python3 -m daythread demo` 一条命令；但需终端与自托管（README:99-109） |
| 组合性 / 可扩展 | **3** | 0 | 1 | 1 | 2 | 声明式 plans/skills、MCP client/server、连接器注册表（README:31-58） |
| 本地优先 / 隐私 | **3** | 2 | 2 | 1 | 1 | 零三方依赖、无遥测、`local_only` 出网策略、E2EE sync（README:13, 41-44, 63-65, 96-97） |
| 可逆性 / 可回滚 | **3** | 0 | 0 | 0 | 1 | 工具返回 undo recipe 或声明 irreversible；多步逆序 undo；taint 强制审批（README:394-397, 405-409） |
| 总拥有成本（TCO） | 2 | 3 | 2 | 2 | 2 | 自托管零依赖，但运维/学习成本归用户；竞品订阅或预装 |

**读法**：我们在「信任 / 可逆 / 组合性 / 本地」领先，落后在「首次价值 / 分发 / 成本」。领先项全是**执行运行时**属性，不是 UI 功能。

---

## 5. Gap

1. **分发差距**：OS 预装与云账号默认开启，我们靠 `git clone` + Python。差距**不会自动缩小**，只做功能会继续扩大。
2. **证明差距**：有 ledger 与 undo，但外部用户**看不见、带不走、无法独立验证**；可审计性停留在「仓库里有测试」，没有变成「用户可出示的凭证」。
3. **复制成本不对称**：竞品加一个「导出 receipt」按钮是一周；但要让凭证**为真**（每步哈希、审批记录、逆序 undo、对篡改拒绝），必须改执行运行时与数据模型。这是防御面。

**是否扩大**：会。平台方每多一个默认开启的记忆/代理入口，用户习惯与数据就多沉淀一分；我们不把「可验证性」变成可分发资产，差距只会扩大。

---

## 6. Capability thesis

> **If we make auditable, reversible execution — approval-gated actions with a signed, replayable receipt and a declared undo recipe — a first-class contract for every tool, connector, and skill, then every new capability compounds trust instead of risk, because the receipt, undo recipe, and hash chain are anchored in the execution runtime and data model, which chat-first competitors cannot copy without rebuilding both.**

---

## 7. Bets

### Bet 1 — Verifiable run receipt (P0)

- **Smallest slice**：`dt receipt <run>` 输出 JSON + Markdown（run id、策略判定、审批人与时间、每步 canonical payload SHA-256 与 prev hash、undo recipe / `irreversible`、复现命令）；`--verify` 重算链并 dry-run undo recipe，篡改样本非零退出。
- **Non-goals**：不做云端签名/时间戳；不加新连接器；不重设计 UI。
- **Kill criterion**：无法在 ≤3 个文件内完成，或 receipt 生成 p95 > 500ms，或需改 ledger/step schema 且无迁移方案。
- **Proof**：篡改样本 100% 被 `--verify` 拒绝；bench safety case 全绿；3 个外部用户中 ≥2 人能复述「谁批准、做了什么、怎么撤回」。

### Bet 2 — Undo-recipe conformance for write connectors (P1)

- **Smallest slice**：所有 `external` 写动作（先做一个日历 create）必须声明可执行的 undo recipe 或显式 `irreversible`，并有测试实际执行「写→undo→回到基线」。
- **Non-goals**：不新增连接器；不改 OAuth；不做批量回滚编排。
- **Kill criterion**：若一个写动作无法提供幂等 undo（第三方 API 不支持下），只记录 `irreversible` 并停止在该连接器上投入。
- **Proof**：连接器写动作的 undo 测试通过率 100%，且 receipt 中 100% 写明 undo 或 irreversible。

### Bet 3 — Public safety bench for receipts & undo (P1)

- **Smallest slice**：`dt bench` 增加 2 个 case：篡改 receipt 必须 verify 失败；undo 后状态必须回到基线；结果可导出 JSON。
- **Non-goals**：不做排行榜/云服务；不扩大 bench 覆盖面。
- **Kill criterion**：若两个 case 无法在稳定环境复现（flake > 2%），先修确定性再谈公开。
- **Proof**：CI 中 bench safety case 100% 通过；对外可复现（一条命令、零三方依赖）。

---

## 8. What we will NOT do

- 不做「更多连接器」作为竞争力——功能堆叠，不强化定位。
- 不做企业合规认证 / SOC 2 证书——边界外；`docs/COMPLIANCE.md` 自述是指南而非认证。
- 不做默认全自主、自动发送、无人值守写操作。
- 不做云端 receipt 服务——先证明本地可验证，再谈托管。
- 不因为 Recall / ChatGPT 有 X 就做 X。

---

## Proposed slices (for Lead)

> 约定：risk tier `T0` 只读/无副作用，`T1` 本地低风险写，`T2` 外部写/网络，`T3` 破坏性或安全边界。每条可独立合并；S2/S3/S4/S5 均以 S1 为前置。

### S1 — Add a verifiable run receipt (`dt receipt <run>`)

- **Scope**: new `daythread/receipt.py`; `daythread/cli.py`（subparser + dispatch）; reuse `daythread/ledger.py:get_steps/verify_run`; `tests/test_receipt.py`; `docs/CLI.md`.
- **Acceptance criteria**:
  1. `dt receipt <run>` 对已完成 run 输出 JSON + Markdown，含 run id、状态、每步 SHA-256、prev hash、审批记录、undo recipe 或 `irreversible`。
  2. 未完成/不存在 run 返回非零退出与明确错误码，不产生半成品文件。
  3. 输出不含 secret/health 事实（复用 sensitivity 过滤）。
  4. `dt receipt <run> --json` 可被 `python3 -m json.tool` 解析。
- **Non-goals**: 不做签名/时间戳；不改 ledger schema；不做 UI。
- **Priority / Risk**: **P0 / T1**（只读聚合现有数据，无外部副作用）· **S**
- **Depends on**: 无。

### S2 — Verify a receipt and reject tampering (`dt receipt <run> --verify`)

- **Scope**: `daythread/receipt.py`（canonicalization + chain recompute）; `daythread/cli.py`; `tests/test_receipt.py`（tamper fixtures）.
- **Acceptance criteria**:
  1. 正常 receipt `--verify` 退出 0。
  2. 篡改任一 step payload/hash 后 `--verify` 非零退出并指出首个断链 step。
  3. 缺字段/版本不匹配时拒绝并给出可操作错误。
  4. `dt ledger <run> --verify` 行为不回归（现有测试全绿）。
- **Non-goals**: 不做 Ed25519 签名；不做远端校验服务。
- **Priority / Risk**: **P0 / T1**（纯本地计算）· **S**
- **Depends on**: S1。

### S3 — Declare and test undo recipes for write connectors

- **Scope**: `daythread/tools.py`（recipe 校验）; `daythread/connectors.py`（calendar create 的 undo）; `tests/test_connectors.py` / `tests/test_tools.py`; `docs/CLI.md`.
- **Acceptance criteria**:
  1. 每个 `external` 写工具在注册表中必须声明 undo recipe 或 `irreversible`，否则注册失败。
  2. 日历 create 的写→undo 测试使状态回到基线（幂等）。
  3. `dt receipt` 对声明了 recipe 的 run 显示可执行 undo 命令。
  4. 不支持的 undo 明确标记 `irreversible`，且 receipt 中可见。
- **Non-goals**: 不新增连接器；不改 OAuth 流程；不做跨 run 批量回滚。
- **Priority / Risk**: **P1 / T2**（触及外部写动作，但已有审批门）· **M**
- **Depends on**: S1。

### S4 — Add receipt/undo safety cases to Daythread Bench

- **Scope**: `daythread/bench.py`; `tests/test_bench.py`; `docs/CLI.md`.
- **Acceptance criteria**:
  1. bench 新增 2 个 safety case：篡改 receipt 必须 verify 失败；undo 后状态回到基线。
  2. `dt bench --json` 输出包含两个 case 的 pass/fail 与耗时。
  3. 在临时 `DAYTHREAD_HOME` 中运行，不污染真实数据。
  4. 连续 10 次运行结果一致（无 flake）。
- **Non-goals**: 不做公开排行榜；不扩大 bench 到新能力面。
- **Priority / Risk**: **P1 / T1**（测试路径，无外部副作用）· **S**
- **Depends on**: S1, S2。

### S5 — Expose receipts through the local HTTP API

- **Scope**: `daythread/server.py`（`GET /v1/runs/{id}/receipt`）; `docs/API.md`; `tests/test_server.py`.
- **Acceptance criteria**:
  1. 带 bearer token 可获取 JSON receipt；无 token 返回 401。
  2. 未知 run 返回 404 且不泄露内部细节。
  3. 响应含 request id，且符合现有 OpenAPI 文档。
  4. receipt 的 `--verify` 结果以布尔字段返回。
- **Non-goals**: 不做公网暴露；不做 UI 下载按钮。
- **Priority / Risk**: **P2 / T2**（本地 API 面，已有 auth/限流）· **M**
- **Depends on**: S1, S2。

---

## Assumptions (draft v1.1)

- Competitor verification: 4/4 unverified because `web_search` is degraded and `web_fetch` failed. If this judgment is wrong, rollback cost = re-verify sources + adjust positioning; low.
- Re-verification is due within 24h after the tools recover; write a delta note then, do not edit old claims silently.
- Slices #3–#7 are Lead-set P2/P3 (Product priority was advisory only); #1 remains the only P0.
- Capability thesis and bets stand as written; no new evidence is implied by the missing verification.

---

## 附：本轮核实与限制

- `web_search`：后端降级 keyless Bing，返回无关结果；`web_fetch`：全部 `TypeError: fetch failed`。**0/4 竞品完成联网核实，4/4 标注「未核实」并给出 URL 与核实方式**。
- 未建 issue、未做任何 GitHub 写操作、未写代码、未改仓库文件；工作树干净（tip `695b11b`）。
- 建议 Lead 建 issue 前先跑一次本地门禁基线；S1/S2 可并行评审，S3 需 Security 过目（外部写 + undo 契约）。

Written-by: Daythread Product
