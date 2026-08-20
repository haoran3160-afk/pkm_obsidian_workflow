# Architecture Decisions

只记录会改变多个模块、依赖、数据模型或信任边界的决定。状态为 `ACCEPTED`、`SUPERSEDED` 或 `PROPOSED`。

## D-001 — 从 v7.3 重型 TCB 收敛到单一 Python Orchestrator

状态：`ACCEPTED`（2026-08-17）

决定：首发使用短生命周期 Python orchestrator、一个 Task、全局锁、确定性阶段和 Python create-new publisher。Rust stage0/broker、AppContainer/WFP、CNG journal 和 PromotionLedger 保留但不在关键路径。

理由：当前是单用户可信 Windows 主机。最高概率、最高影响风险是用户数据覆盖、范围外读取、远程内容注入、证据错误和失败伪成功；重型路线主要解决不在当前威胁模型内的恶意本地进程、复杂代际 TCB 与合规审计，并阻塞真实内容验证。

后果：更快得到真实 shadow 和内容反馈；不宣称企业级隔离、不可抵赖审计或物理掉电事务保证。

## D-002 — 完整来源 proof 仍是硬门

状态：`ACCEPTED`

决定：轻量化不等于信任调用方传入的 source 子集。Coverage 必须由完整 baseline、用户批准的整数权重 policy、批准追加 generations 与精确 terminal observations 确定性派生并绑定 SHA-256；evaluator 只接受完整 proof object，不保留裸 entries 兼容入口。

理由：遗漏来源会直接制造虚假 Coverage A 和“可信无新闻”，这是内容产品的核心信任问题，不能降级。

## D-003 — 使用 `os.link` create-new，不使用覆盖写

状态：`ACCEPTED`

决定：runtime 与 Vault 同盘 NTFS 时，完整 backing flush/hash 后用 `os.link` 创建 target；目标存在则保留原文件并创建冲突副本。hard link 不可用时保持 shadow。

理由：它直接满足原子可见、create-new、不覆盖和进程崩溃后可 reconcile，复杂度远低于 native A/B/NT directory flush。

限制：不承诺真实掉电 durability；hard link 后 target 与 backing 共享 identity，因此独立 audit copy 与 receipt hash 才是发布时内容证据。

## D-004 — 模型预算集中在高价值内容

状态：`ACCEPTED`

决定：确定性 shortlist 后才调用 Generator；Reviewer 只审核 Top、critical/high-risk 和影响总体结论的 claim。低风险字段由确定性检查与 renderer 约束。

理由：第二个模型不是事实源。原始证据、数字/时间/版本校验和 evidence mapping 比对所有内容做模型投票更有效，也更省套餐额度。

## D-005 — 一个 Task、一个 action、多个 trigger

状态：`ACCEPTED`

决定：Task 只负责触发 `pkm run --due-only --cause task`；due 计算、顺序与补跑由 Python 纯逻辑决定。

理由：避免多 Task 代际、同步协议和状态分叉；Task Scheduler 已提供 StartWhenAvailable、IgnoreNew 与真实进程退出码。

## D-006 — 安全能力按真实威胁升级

状态：`ACCEPTED`

首发必须防：不可信 feed、SSRF、prompt injection、模型幻觉、路径逃逸、覆盖写、并发、认证/额度/网络失败和失败误报。

首发不防：本地管理员、能修改运行时/代码的恶意进程、多租户、合规不可抵赖、磁盘固件或设备级掉电。

出现任一条件时重新评估 native/AppContainer/WFP：

1. 机器开始运行不可信本地代码或由多个 Windows 用户共享。
2. 工作流处理受监管/高敏感数据，需要可证明的最小权限或不可抵赖审计。
3. disposable Windows 红队实证应用层网络/文件边界可被当前子进程绕过。
4. Python `os.link` + receipt + reconcile 无法通过既定进程强杀和数据完整性测试。

升级前必须新增 decision，列出观测证据、最小新增组件、撤销方案和新增验收成本；不得仅以“更安全”为理由引入。

## D-007 — 旧实现保留但不兼容激活

状态：`ACCEPTED`

决定：旧 root modules、UI/API、migration、`.agent/workflows` 历史正文和 native 实验不删除，但从 wheel、Task、canonical docs 和生产 closure 中排除，并在入口 fail-closed。

理由：同时满足不删文件、保留审计证据与阻止旧链路绕过新边界。兼容性不能凌驾于用户数据安全。

## D-008 — 将模型资格拆成确定性 curation 与真实 Codex 两个 Gate

状态：`ACCEPTED`（2026-08-17）

决定：原 T1.3 拆成顺序执行的 T1.3a 与 T1.3b。

- T1.3a 只实现确定性 curation/review contract。它验证 T1.1/T1.2 的完整
  `policy → snapshot → fetch batch → evidence package → coverage proof/result`
  闭包，解析唯一批准的 UserContext，生成稳定 event clusters、排序、排除理由和
  review input；不调用 Codex。
- T1.3b 才实现真实固定 Codex CLI 调用。只有原生 0.147.0 executable、官方 asset
  identity/SHA-256、专用 `CODEX_HOME` 套餐登录、固定参数、Windows read-only sandbox
  与真实模型 smoke 全部通过，才可从 `BLOCKED_PREQUALIFICATION` 进入实施。
- T1.3 qualification 阶段，所有可能进入 story 的 assertive claim 都必须由 Sol
  Reviewer 明确 `ACCEPT`。未审内容只能是隔离的 `source_excerpt`，不得进入标题、
  Top、排名、结论、建议或最终事实字段。D-004 的选择性 Reviewer 是后续优化目标；
  只有新 decision 和真实分类器证据才能降低审核范围。
- feed item URL 只是 `UNVERIFIED_LOCATOR`；T1.3 不声称已验证 article 正文、
  canonical URL、原始链接或最终日报质量。T1.3a/b 产物只属于
  curation/review qualification shadow，不计入 M3 内容验收样本。

理由：确定性证明可以在当前环境立即实现，而 Codex 真实资格依赖固定二进制、用户
登录和外部套餐能力。把两者混成一个 Goal 会让实现者在外部依赖缺失时伪造绿色；把
它们拆开又不降低 claim 审查强度，才能得到可执行、可证伪的开发单位。

安全边界：首发继续采用 D-001/D-006 的轻量 Windows 威胁模型，不恢复
AppContainer/WFP 或 `danger-full-access`。T1.3b 使用直接原生 executable、官方
Windows `read-only` sandbox、approval never、隔离 cwd、固定输入、Job Object
进程树终止和 JSONL/tool-event 审计。资格测试只声称证明冻结进程拓扑、canary
拒读和可观察事件；没有 ETW 时不声称看见所有系统文件读取。

批准的 UserContext canonical hash 算法为 UTF-8 JSON、`sort_keys=true`、
`ensure_ascii=false`、`separators=(",", ":")`；当前完整 digest 为
`sha256:ce307d029565fe20659ec9e43561d949ac1ec7d7ec67720bad25c7ad73460e3f`。

对抗审查记录：

- 接受：原 Goal 不可执行、真实 hash 格式不兼容、Coverage/evidence 可被调用方伪造、
  fresh thread/JSONL 生命周期不足、shortlist 与 UserContext 无确定性 oracle、Codex
  超时后进程树未闭合、overflow 不应自动重试、Top 与所有 assertive claims 必须审核。
- 接受：跨 origin 聚类必须保留全部成员；profile 排序必须引用精确 contract 字段；
  T1.3 只证明 qualification shadow，不能冒充最终日报质量或 article 原文验证。
- 拒绝：复用旧 `review_policy_v73` 弱 seam、由 Generator 自报 risk 来减少 Reviewer、
  把候选截到 cap 后继续、复制现有 auth cache、回退到 0.125/API/DeepSeek、恢复
  AppContainer/WFP/`danger-full-access`、让 T1.3 写 Vault/Task。
- 最终仲裁：上述修订后 `APPROVED`，无剩余 P0/P1；T1.3a 可立即执行，T1.3b 继续
  `BLOCKED_PREQUALIFICATION`。

## D-009 — DeepSeek-V4-Flash 一次性 Shadow Provider 例外

状态：`ACCEPTED`（2026-08-20，用户明确批准）

决定：在不改变 T1.3b Codex 路线的前提下，增加唯一可执行任务 T1.3c，用 DeepSeek
官方 Chat Completions API 跑通一次真实 Generator → Reviewer qualification shadow。

- Provider、base URL、endpoint、model、thinking mode 与 Credential Manager target
  全部固定，调用方不能覆盖；实现只使用 Python 标准库。
- 凭据只从当前 Windows 用户 Credential Manager 的 `PKM/DeepSeek/shadow-v1` 经
  `CredReadW` 读取。禁止把 key 放入 `.env`、代码、argv、日志、异常、测试、fixture、
  manifest、Git 或 ProviderResult。
- 真实 smoke 总预算最多 4 个请求：Generator 与 Reviewer 各一个独立请求，每次最多
  一次 schema repair。预算必须导入已有真实调用数，不得通过新建进程或 `RequestBudget`
  归零；超过预算时在网络前拒绝。
- 所有调用必须携带代码冻结且自哈希的 D-009 exception binding：scope 为
  `TRANSPORT_SCHEMA_CONTRACT_ONLY`，`production_eligible=false`、
  `fallback_eligible=false`、`independence_class=SAME_PROVIDER_SAME_MODEL`。该 binding
  同时进入 Generator 与 Reviewer sealed request，调用方任一改动都在零网络阶段失败。
- 只允许 `deepseek-v4-flash`、`thinking={"type":"disabled"}`、非 streaming JSON
  response；请求不发送 `tools` 字段，响应出现 `tool_calls` 或 `function_call` 立即拒绝。
- 单次请求固定 `max_tokens=4096`、sealed input 250,000 bytes、request 400,000 bytes、
  response 1,000,000 bytes、connect 10 秒和应用层统一 deadline 30 秒。无凭据 resolver、
  TCP、TLS、HTTP 共享剩余时间；只连接固定 host/path，使用 Windows ROOT store、TLS 1.2+、
  固定 SNI 与 hostname verification，成功后才创建 Authorization。不读取环境代理或
  `SSL_CERT_FILE`/`SSL_CERT_DIR`，不跟随 redirect，不启用 `SSLKEYLOGFILE`/HTTP debug，
  不自动 retry。它不等同于生产 Job/process 级硬终止资格。
- repair 是窄状态机：仅初次、单一、已解析 object 的纯结构缺失/类型/边界错误可进入；
  unknown field、tool/function/reasoning、坏 JSON、HTTP、timeout 或 Reviewer 语义闭包失败
  均单次终止。repair request 保留原角色完整安全 prompt、只追加冻结 repair 指令，不回传
  第一次原始模型输出；每个实际 prompt 与 request body 都进入独立 hash 审计。
- 401、402、429、5xx、timeout、坏 JSON、schema 不匹配和 review decision 不完整均
  fail-closed；不充值、不切换供应商或其他模型。
- 产物只能 create-new 写入
  `D:/personal/obsidian_workflow-runtime/scratch/deepseek-shadow-{run_id}`，仅保存脱敏
  hash、逻辑 request ID、供应商 envelope ID hash、execution/credential mode、usage、latency、
  terminal、tool/schema-repair 标志，不保存原始响应 header、自由推理或 Key。真实资格产物
  必须是 `DIRECT_FIXED_HTTPS + WINDOWS_CREDENTIAL_MANAGER`；测试替身结果不得冒充真实调用。
- Provider 不接入 orchestrator、CLI、renderer、publisher、Home/Status、Task 或生产
  activation closure；它不能成为 Codex 失败时的 fallback。T1.3b 继续
  `BLOCKED_PREQUALIFICATION`。

费用边界：用户只批准本次 smoke 的最多 4 个请求；不得检查余额后自动充值，不得因
402 降级，任何后续真实调用都需要新的明确批准。

解释边界：两个 fresh request 都是同一供应商、同一 `deepseek-v4-flash` 模型，只能证明
transport/schema/review-contract qualification 可运行，不构成“双模型验证”或独立事实保障。

退出条件：一次真实双请求链路、单元/静态/回归门、两轮独立对抗审查和仅任务文件 Git
提交完成后，T1.3c 结束。代码作为审计资产保留，但后续任务不可达；若未来希望复用，
必须新增 decision，重新评估费用、供应商相关错误、数据出境和生产资格。
