# PKM Workflow v7.4 MVP Specification

状态：`READY_FOR_DEVELOPMENT`

实现状态：`NOT_YET_PRODUCTION_ELIGIBLE`

## 0. v7.4 工程收敛覆盖条款

2026-08-24 起，个人可信 Windows 单机 MVP 不再以 Rust stage0/broker、AppContainer/LPAC、
WFP、qualifier restart、目录掉电证明或 Task 多代切换作为 shadow/生产前置条件。T1.3b 已在
`8437a97` 安全着陆并冻结为非生产实验。当前实现必须优先打通固定 Python 入口、来源与
Coverage、DeepSeek API 双审、最小 A/B create-new 发布、append-only 状态、三个 Task 和
Obsidian 阅读体验。本文后续保留的 D-009/D-010 原生条款仅是历史资格记录，不得重新进入
v7.4 生产调用图。

## 1. 不可破坏约束

### 1.1 文件与 Vault

- 不删除、移动或重命名两个仓库与 Vault 中的任何文件。
- 不覆盖既有 Vault 文件；所有工作流产物使用 create-new。
- 不遍历或读取 Vault 中未列入白名单的内容。
- 不修改 `.obsidian`。
- 允许写入的精确 namespace：

  - `01-Raw/daily-feeds`
  - `30-Daily/AI-News`
  - `30-Daily/Weekly`
  - `30-Daily/IELTS-Preview`
  - `30-Daily/PKM-Workflow-Status`
  - create-once 的 `30-Daily/PKM-Workflow-Home.md` 与配套 `.base`

- 禁止写入 `10-Notes/*`、`20-Sources/*` 和其他 Vault 路径。
- 所有路径必须由 `workflow + content_date + artifact_kind + run_id` 的枚举规则生成；模型、feed 和 CLI 不得提供路径或文件名。

### 1.2 来源

- `Source-Baseline-v7.3.json` 中的 74 条记录全部保留，包含重复、disabled、null ID、失败和 IELTS 资源。
- 旧来源的 endpoint、状态和原始记录不得原位改写；变化通过 append-only generation 记录。
- 新来源只能追加，经人工审批后才进入生产 registry；不得自动删除、禁用、降权、移出覆盖分母或重分类既有来源。
- 新来源至少经过 14 天 probe，记录可用率、时效、正文率、重复率和独立性；probe 数据不进入生产 Coverage，审批后才创建追加 generation。
- 一次人工完成 baseline 的 workflow、lane、origin group、evidence role 和整数权重分类；开发代码不得猜测或自动填充。分类未完成时必须返回 `REGISTRY_CLASSIFICATION_REQUIRED`，不能计算 A/B。
- 完整 registry 永远包含全部 74 条 baseline。现有 `enabled=false` 记录保留为 `LEGACY_DISABLED_PRESERVED`，不得自动抓取、恢复或计为健康；它们只有经用户批准的新 generation 才能重新启用。
- 每个现有 `enabled=true` 记录必须映射到至少一个 workflow，不能用 `not_applicable`、零权重或缺失 lane 变相移出该 workflow 的批准集合。IELTS-only 记录可以没有 AI 四车道 lane，但必须显式映射到 IELTS。
- Coverage denominator 只能由用户批准的 policy generation 派生；运行时健康、失败、重复率和模型判断都不能改变它。继承的 legacy-disabled 状态也必须进入 policy hash 与完整性报告，而不是从 registry 消失。

### 1.3 外部服务

- D-018 是当前 v7.4 provider 决定：只调用 DeepSeek 官方
  `https://api.deepseek.com/chat/completions`，模型固定 `deepseek-v4-flash`。Generator 与
  Reviewer 是两个 fresh 请求；`thinking=disabled`、`response_format=json_object`、
  `stream=false`，不提供 tools。凭据只从 Windows Credential Manager 固定 target
  `PKM/DeepSeek/shadow-v1` 读取，不接受 CLI/env/文件明文 key。
- 不使用 Codex CLI 或 OpenAI API；只消费现有 DeepSeek 余额，绝不自动充值。每个完整
  Generator→Reviewer pipeline 正常 2 次请求，含结构修复最多 4 次；auth、余额、429、5xx、
  timeout、空 JSON、length/tool_calls 均 fail-closed，不发布半成品。
- 以下 D-009/T1.3c 和 D-010/T1.3b 条款保留为历史资格记录；与本节冲突时以 D-018 为准。
- 历史 D-010 Codex 实验曾要求独立 `CODEX_HOME` 与零工具配置；该要求不属于 D-018 当前
  DeepSeek 调用图，保留仅供审计。
- 以下 D-009/T1.3c 与 D-010/T1.3b 条款只描述冻结的历史 shadow 资格，不授权调用 DeepSeek，
  也不要求 v7.4 恢复 AppContainer/WFP。
- D-009 仅为 T1.3c 增加一次用户明确批准的 DeepSeek-V4-Flash shadow 资格例外：
  固定从当前 Windows 用户 Credential Manager 的 `PKM/DeepSeek/shadow-v1` 读取凭据，
  固定调用 `https://api.deepseek.com/chat/completions` 与
  `deepseek-v4-flash` 非思考模式。它不接入 orchestrator、CLI、renderer、publisher、
  Home/Status 或 Task，不具备生产资格，也不得成为 T1.3b 的自动或人工 fallback。
- T1.3c 单次真实 smoke 最多 4 个 API 请求，Generator 与 Reviewer 是两个独立请求，
  每次至多一次 schema repair；不得自动充值或切换其他模型/供应商。401、402、429、
  5xx 与 timeout 均 fail-closed，不产生 accepted story、Markdown、Vault target 或成功
  terminal。T1.3c 完成后，后续任务不得调用该 Provider，除非用户追加新的 decision。
- D-009 binding 固定为 `TRANSPORT_SCHEMA_CONTRACT_ONLY`、`production_eligible=false`、
  `fallback_eligible=false` 和 `SAME_PROVIDER_SAME_MODEL`。Generator 与 Reviewer 虽为 fresh
  request，但不是独立供应商或独立模型验证；结果不得计入生产内容质量或冒充现有
  Terra/Sol curation generation。
- 每个请求固定 `max_tokens=4096`；sealed input ≤250,000 bytes、完整 HTTP request
  ≤400,000 bytes、response ≤1,000,000 bytes；connect timeout ≤10 秒、T1.3c 应用层统一
  deadline 30 秒。固定 DNS 解析在不持有凭据的 daemon resolver 中等待到 deadline；TCP、
  TLS 与 HTTP 每阶段复用剩余时间，TLS 以 `api.deepseek.com` 为固定 SNI/hostname，验证成功
  后才构造 Authorization。它不继承环境代理、`SSL_CERT_FILE`/`SSL_CERT_DIR`、
  `SSLKEYLOGFILE`、redirect、HTTP debug 输出或自动 retry。该 Python shadow deadline 不替代
  生产所需的 Job/process 级硬终止资格，不能据此解除生产阻断。
- schema repair 只允许“已解析为单一 JSON object、无未知字段、无 tool/function/reasoning、
  仅缺字段或字段类型/边界错误”的初次响应。非 JSON、多 JSON、重复 key、NaN/Infinity、
  unknown field、HTTP/timeout 和 Reviewer decision/evidence/hash 语义不一致都不得 repair。
  repair 必须保留原角色的完整安全 prompt，只追加冻结的窄修复指令；每次实际 prompt 与
  request body 分别记录 hash，且不回传第一次坏输出。
- DeepSeek 认证或余额不可用时分别返回 `AUTH_REQUIRED`/`INSUFFICIENT_BALANCE`，不得 fallback。
- 历史 Codex CLI `0.147.0` 不删除但运行时不可达；工作流不得自动安装或调用 Codex CLI。
- 当前 Generator/Reviewer 都固定为 `deepseek-v4-flash`；二者 fresh request 只能降低上下文耦合，
  不能冒充独立模型/供应商验证。模型变化必须创建新 policy generation并重跑评估。
- 历史 T1.3b 曾固定使用 D-010 的 AppContainer/LPAC + package-SID WFP + one-shot
  loopback proxy + Job Object；该实验现已冻结，始终 `production=false`，不再阻塞 v7.4
  Terra/Sol shadow。D-018 已取代它作为当前 provider；未完成项不再进入 v7.4 Gate。

## 2. 工作流行为

### 2.1 调度

- Windows Task Scheduler 是唯一生产触发技术；恰好三个 Task（AI、IELTS、Weekly），每个
  Task 含 calendar、logon、unlock triggers 和一个固定 Python action。
- action 固定调用 content-addressed、不可自动升级的最小 activation launcher；launcher 只读最高
  有效 ActivationGeneration，核验 release/config hash 后 exec 同一个密封入口：
  `scripts/run_workflow.py --workflow <enum> --mode production --due-only`。launcher 不解析业务
  内容，也不能写 activation 或 Vault。
- trigger：AI 07:10、IELTS 08:00、Weekly 周日 09:30，以及登录/解锁补跑。
- `WakeToRun=false`、`StartWhenAvailable=true`、`MultipleInstances=IgnoreNew`。
- Python 使用 create-once lock anchor 的跨 workflow 全局 OS 文件锁，并以
  workflow/content_date demand 记录避免 Task、手动运行和补跑竞态。
- due-only 先处理今天，最多补昨天；更早缺口只显示状态，不批量制造历史日报。
- 同时到期时顺序为 AI → IELTS → Weekly。
- Task 同步等待命令结束并保留真实退出码。

### 2.2 时间窗口

- AI Daily 的 `content_date=D` 对应上海时区 `[D-1 07:00, D 07:00)`。
- `published_at`、`observed_at` 和 `updated_at` 分开保存。
- Weekly 按上海时区自然周聚合工作流自己的 accepted story records，不扫描 Vault 日报。
- 离线超过两天，只处理最近 30 天内的高严重度、决策相关实质变化，并显著披露回溯不完整。

### 2.3 端到端流程

1. 从已批准 registry 生成本次完整 source snapshot 和 SHA-256。
2. 固定来源抓取器采集，记录每个 source 的成功、失败、时间和内容 hash。
3. parser 在无网络阶段把原文规范化为 evidence items。
4. Coverage evaluator 从完整 snapshot 与 observation set 计算 A/B/不足，调用方不得传入任意子集作为分母。
5. 确定性 curation 验证完整上游闭包，按冻结 policy 形成无损 event clusters、稳定排序、
   明确排除理由和有界候选；overflow 是需要新 policy/input 的非瞬态阻断，不能截断后继续。
6. DeepSeek Generator 只处理候选的密封 feed evidence 与批准 UserContext，输出结构化 JSON，
   不输出链接或 Markdown；`item_url` 只是不可引用的 `UNVERIFIED_LOCATOR`。
7. 程序校验时间、数字、版本、地区、availability 与 evidence mapping。T1.3 没有 article
   正文或已验证 canonical link，因此需要正文才能确认的 claim 必须 abstain。
8. DeepSeek Reviewer 审核所有可能进入 story 的 assertive claim；
   未审内容只能保留为隔离 `source_excerpt`。选择性 Reviewer 只有在后续 decision 和真实
   分类器证据通过后才能启用。
9. 程序根据 review decision 删除 claim/story，重新确定性排序与渲染。
10. 通过 create-new 发布器写 backing、链接 Vault、验证并创建 receipt/status。

任何阶段失败都不得发布部分日报。

## 3. 来源与 Coverage

### 3.1 抓取安全

- 只请求 registry 中固定的 HTTPS endpoint。
- 手动处理 redirect，每一跳重新校验 scheme、host、resolved peer 与 allowlist。
- 拒绝 loopback、link-local、私网、文件 URI、非 HTTP(S) scheme 和凭据型 URL。
- 设置连接/读取/总超时、最大 redirect、压缩后与解压后体积、压缩比、MIME 和字符数上限。
- feed 内容不能创建新 URL 抓取任务；正文跟随只允许同一批准 origin 或明确的 endpoint generation。
- parser 无网络，不执行 HTML、脚本、模板或 feed 中的指令。

### 3.2 Coverage proof

每次结果必须绑定：

- `source_registry_hash`
- `source_policy_hash`
- `source_snapshot_hash`
- `observation_set_hash`
- `coverage_result_hash`
- `content_date` 与精确窗口

evaluator 必须证明 snapshot 等于“完整 baseline + 已批准追加 generations”。遗漏任一应出现的 source 都使 Coverage 无效。

- `CoverageEvaluator` 不得再接受调用方直接传入的任意 `entries`、approval 集合或浮点权重；唯一输入是由完整 baseline 与批准 policy 构造并验证的 proof object。
- production snapshot 必须精确包含该 workflow policy 期望的 source/endpoint generations；probe observations 使用独立 proof，不能混入生产 numerator。
- 每个预期 endpoint 在当前窗口必须有且只有一个 terminal observation，即使结果是超时、HTTP 错误、parse 错误或零新内容。缺失、额外、重复或 generation/window 不匹配都是 proof 无效，不是普通低 Coverage。
- source policy 权重使用 `1..10000` 的正整数 basis points，禁止 bool、float、NaN、Infinity、零、负数或越界值。
- 相同 canonical endpoint 的重复记录必须映射到同一 origin group，不能伪装成独立来源；不同 endpoint 属于同一组织时由人工 policy 合并 origin group。
- `CoverageResult` 必须回显全部输入 proof hashes，并提供自身 `coverage_result_hash`；任一输入字段变化都必须改变相应 hash。

Coverage 车道：官方实验室、科研论文、开源/产品 release、产业/政策。

- A：每车道至少 2 个当前健康独立 origin group，critical 加权覆盖率 ≥85%。只有 A 可产生可信 `NO_SIGNIFICANT_NEWS`。
- B：每车道至少 1 个健康 group，加权覆盖率 ≥70%。只发布逐条独立验证的故事并展示缺口。
- 低于 B：`COVERAGE_INSUFFICIENT`，不生成日报。

健康状态只描述本次观测，不能改变 registry、分母或来源权重。

## 4. 证据和内容质量

### 4.1 证据等级

- 核心结论优先由官方文档、论文、代码、release 或监管原文支持。
- 预印本与公司自述必须标注。
- 独立专家分析用于解释，不替代原始证据。
- 新闻通讯、VC 和营销内容只用于发现，不能单独支持 critical/high-risk claim。
- 价格、安全、法律政策、可用性和重大能力结论尽量需要两个独立原始来源；只有一个权威原始来源时必须明确单源限制。

### 4.2 模型职责

- 模型不是事实来源，置信度不是证据。
- Generator 只从输入 evidence 提取候选 claim、机制、影响和不确定性。
- Reviewer 只判断 evidence 是否支持 claim、是否夸大或错配；不得凭常识补证据。
- 模型不得提供 URL、HTML、wikilink、文件路径或自定义 URI。
- DeepSeek API 不接收 tools；模型只能返回 JSON data。Provider 不接收 Vault 路径、不写文件，
  响应中的 tool/function call 或未知字段一律拒绝。
- 下列 DETACHED_PROCESS/AppContainer/WFP 条款是已冻结 T1.3b 的历史证据边界，不是 v7.4
  MVP 调用要求；其未完成项不得重新阻塞 Python shadow。
- 任意 tool event、未知文件访问、schema 失败或额外子进程使本次模型输出无效。
- Windows Codex child 必须使用 `DETACHED_PROCESS`，不得使用会创建 `conhost.exe` 的
  `CREATE_NO_WINDOW`。配置 child-process attribute 还不够：resume 前必须精确回读
  ProcessChildProcessPolicy flags=1，并由无认证 negative canary 实测固定 child 创建返回
  `ERROR_CHILD_PROCESS_BLOCKED (367)`；任一步不符继续 `BLOCKED_PREQUALIFICATION`。
- T1.3b 的外层隔离是强制资格条件：Codex package SID 只能读取 create-new runtime 中
  显式授权的 executable、`CODEX_HOME`、sealed prompt/evidence/schema 和 run root，只能
  连接固定 loopback proxy 端口。package-specific loopback exemption 只能临时使能该
  AppContainer 到本机 proxy 的连接，必须保留并恢复系统既有 exemption 列表，且不能绕过
  package-SID WFP 对单一端口的精确限制。exemption/WFP/filter/profile/proxy/Job 任一失败时
  必须在 child resume 或认证暴露前停止。
- AppContainer 默认不携带 `internetClient` 或其他网络 capability；任何新增 capability
  必须由真实 loopback/WFP trace 证明为最小必要项并绑定资格闭包。若 child/descendant
  未确认归零，WFP、loopback exemption、ACL/profile 和维护锁均不得提前撤销。

### 4.3 发布判断

- Top 最多 3 条；不为了凑数补内容。
- 每个 Top/critical/high-risk claim 必须有规范化 evidence、全部确定性检查通过、Reviewer 明确 `ACCEPT`。
- 无法确认的普通 claim 删除后重新渲染；如果删除改变标题、排名或核心结论，则删除 story。
- 争议涉及 Top、价格、安全、可用性、法律政策或模型能力时删除 story；若因此总体判断失真，整份 digest 返回 `INSUFFICIENT_EVIDENCE`。
- 没有达到发布价值但 Coverage A 时，只创建 `NO_SIGNIFICANT_NEWS` terminal status，不创建 digest 文件。
- 同一事件只有能力、价格、API、可用性、安全或政策结论实质变化时才能重复出现。

### 4.4 个性化

- renderer 必须验证 `user_context_hash` 等于当前批准的 `UserContextContract-v7.3.json` hash。
- 个性化结论必须引用白名单 profile field；否则只能标记为“一般可能影响”。
- 不从 Vault、历史日报或模型记忆推断个人情况。

## 5. 确定性输出

- Renderer 是纯函数，只接收 accepted stories、validated evidence registry、coverage proof 和 user context hash。
- Renderer 只使用 fetcher 已验证的 HTTPS URL。
- AI Daily：Top≤3，30 秒扫描，正文通常 5–8 分钟，0–5 个详解。
- Weekly：12–20 分钟，聚焦一周结构性变化与工程决策。
- IELTS：10–15 分钟，科学/技术主题轻量训练。
- 每条结构：事件 → 机制 → 原始证据 → 具体影响 → 不确定性 → 观察信号。
- 没有行动时写“无需行动”；不生成空章节、通用建议或填充新闻。

## 6. 发布、冲突与恢复

### 6.1 运行时

`D:/personal/obsidian_workflow-runtime`：

- `durable/backing/`：最终内容 backing，create-new，保留。
- `durable/receipts/`：manifest、publish receipt、status event，create-new，保留。
- `durable/registry/`：批准的 registry/policy generations，append-only。
- `scratch/runs/{run_id}/`：可清理的抓取、解析和模型临时文件。
- `locks/`：全局锁状态。

### 6.2 create-new 发布协议

1. doctor 证明 runtime 与 Vault 位于同一 NTFS volume，且 hard link 受支持；否则保持 shadow，不降级为覆盖或复制写。
2. create-new 写审计 A=`runs/<run_id>/rendered.md`，再 create-new 写相同 bytes 的发布 backing
   B；A/B 是不同 file ID、长度/hash 全等并分别 flush。A 永不链接 Vault。
3. create-new 写 `PREPARED` event，绑定 A/B identity/hash 与 canonical/conflict target。
4. 用 `os.link(B, target)` 创建 canonical Vault 文件；该调用必须是 create-new，最终 Vault
   leaf 绝不能作为流式写入目标。
5. 如果 canonical target 已存在，先判断是否为同一 run 的 B/link；匹配则 reconcile 原 run，
   不匹配才尝试包含 `run_id` 的冲突副本，冲突副本也只由 B 原子 link 创建。
6. 重新打开 target，核验路径、大小、hash 和 file identity，然后 create-new 写 terminal event。
   link 后、terminal 前强杀由同一 PREPARED run 补 terminal，不制造重复或假冲突。

本协议承诺防止进程级崩溃造成半文件、覆盖和丢失追踪；不宣称对物理掉电、磁盘固件或恶意本地管理员提供事务保证。

### 6.3 reconcile

- 根据 backing、target、hash 和 receipt 恢复进程强杀后的中断状态。
- target 与 PREPARED/B identity 匹配时补建缺失 terminal/status；不重新生成内容。
- target 存在但不匹配时进入 `CONFLICT`。
- backing、receipt 和冲突产物都不删除。
- 用户编辑 B/target 时，独立审计 A 保持发布时内容；只记录 drift，不回写或恢复旧内容。

## 7. 状态与 Home

用户可见状态：`NOT_DUE`、`RUNNING`、`PUBLISHED`、`NO_SIGNIFICANT_NEWS`、`CATCH_UP_DELAYED`、`COVERAGE_INSUFFICIENT`、`QUOTA_DELAYED`、`AUTH_REQUIRED`、`CONFLICT`、`STATUS_UNKNOWN`。

- 每次状态都是带递增 seq 的 create-new projection；同日同 workflow 的 latest view 按 seq 降序取 1。
- Home 的 AI、IELTS、Weekly view 必须同时过滤精确 status folder、`content_date == today()` 和 namespace。
- deadline 后没有今日状态时，动态 SLA view 显示“状态无法确认/需处理”，不得用昨日状态推断原因。
- `PUBLISHED` 才能携带已验证 target/hash；验证失败和其他状态不得显示“打开日报”链接。
- `NO_SIGNIFICANT_NEWS` 只绑定 manifest/coverage receipt，不携带日报 target。
- Home/Bases create-once；不自动修改 `.obsidian`，由用户首次手动启用/固定。

## 8. CLI 与退出码

目标 CLI：

```text
pkm doctor --json
pkm shadow --workflow {ai,ielts,weekly} [--date YYYY-MM-DD] --json
pkm run --due-only --cause task --json
pkm run --workflow {ai,ielts,weekly} --cause manual [--date YYYY-MM-DD] --json
pkm reconcile [--workflow {ai,ielts,weekly}] --json
```

在对应实现任务完成前，现有 CLI 必须继续返回 `BLOCKED`。

退出码：

- `0`：已发布、可信无新闻或已有同一 terminal receipt。
- `3`：安全/边界阻断或冲突。
- `4`：可重试的覆盖、证据、网络或上游错误。
- `5`：套餐额度或外部服务不可用。
- `8`：DeepSeek 认证需要用户处理。
- `10`：内部错误。

非零不得转成成功；日历 reconciler 不能伪造 terminal。

## 9. 开发与上线验收

### 9.1 必须通过

- 50–100 条真实历史 evidence package 回放。
- 7 个自然日 AI Daily shadow；2 个真实 Weekly 周期。
- 核心证据链接可达与内容匹配率 100%；accepted critical claim 实质错误 0。
- 覆盖写、删除、移动、重命名和范围外 Vault 读取为 0。
- 认证失效、额度不足、断网、429/5xx、坏 MIME、压缩炸弹、路径逃逸、目标冲突、并发和每个关键切点强杀均安全失败。
- `os.link` 同盘资格、Task 触发/补跑/真实退出码、Obsidian Desktop Home/Bases 展示通过目标机验证。
- 两轮针对 feed prompt injection、伪 canonical、数字偷换、时间错误、重复 origin 与模型夸大的红队，无未处理 P0/P1。
- 用户连续一周认为至少 80% Top 值得注意。

### 9.2 不作为首发发布阻断

- Rust stage0/native publisher、CNG MAC chain、PromotionLedger、DPAPI-NG 恢复包。
- 物理掉电矩阵、全模块 mutation≥85%、Weekly 等待四个真实周、所有内容双模型审核。

AppContainer/LPAC 与 WFP 不属于 v7.4 首发要求；T1.3b 已冻结为实验性、非生产且未整体完成。
MVP DeepSeek shadow 的硬门是固定 endpoint/model/prompt/schema/input/request hashes、TLS/SNI、
Credential Manager、无 tools、fresh requests、预算≤4，以及 auth/balance/429/5xx/timeout/schema
fail-closed。D-009 transport/schema V1 与 D-018 strategy 必须分别版本化，不能原位改写历史。

关键 delete/overwrite/path-escape/evidence-mismatch 测试必须通过；mutation 只用于这些高风险模块，不追求全仓数字。

### 9.3 激活

按 AI Daily → Weekly → IELTS 顺序。每阶段只有验收证据完成且用户明确批准后才能安装/启用相应生产 trigger。任何程序、测试或 Agent 不得自行批准上线。

## 10. 第一性原理对抗结论

最坏的高概率损失是：覆盖用户笔记、读到范围外私人内容、把恶意 feed 当指令、无证据地制造重要新闻、任务失败却显示成功。当前规格为每项提供了直接、可测试的控制。

重型 v7.3 路线主要防御恶意本地进程、复杂多代 TCB、设备级掉电与合规审计；这些不属于当前单用户可信主机的首要威胁。把它们留在首发关键路径会延迟真实内容验证，并增加更多难以验证的代码。因此它们被明确延后，但保留升级条件：出现多用户/不可信本地代码、合规要求，或实测证明 Python create-new 无法满足约束时，先写新 decision，再重新评估 native 隔离。
