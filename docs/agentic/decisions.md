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

## D-010 — T1.3b 使用 AppContainer/LPAC + WFP 做 Codex 本机资格隔离

状态：`ACCEPTED`（2026-08-20，用户明确要求并批准实施）

决定：仅对 T1.3b qualification shadow，D-008 中“Windows `read-only` sandbox、
不使用 AppContainer/WFP/`danger-full-access`”的隔离段被本决定取代。固定官方 Windows
原生 Codex CLI `0.147.0` 在专用 AppContainer/LPAC 中运行；Codex 内部 sandbox 固定为
`danger-full-access`，避免第二套 helper sandbox 扩大或混淆进程拓扑。真正的安全边界是
外层 package SID、动态 WFP 策略、单用途 loopback CONNECT proxy、精确文件 ACL 和 Job
Object，不是 Codex 自报的工具策略。

边界和资格顺序：

1. 官方 release ZIP SHA-256 固定为
   `a1c073ede409178502b1ff11bed635d2b644424352f9d4f3999de8272c6aebbd`；解包后的
   `codex-x86_64-pc-windows-msvc.exe` SHA-256 固定为
   `935a1911ed2556e4ffcec995f4886ac2ac425863ba26fed264df62e30272ad9d`。必须同时验证
   Authenticode、PE 身份和真实 `codex-cli 0.147.0` 输出；不得使用 PATH、wrapper、
   0.125、自动升级或调用方自报 identity。
2. 先完成无认证 parser preflight，再完成 AppContainer/LPAC、WFP、proxy、文件拒读、
   Job kill-tree 与 trace 资格；只有全部通过才允许把套餐认证暴露给该 package SID，随后
   最多执行一次 Terra Generator 和一次 fresh Sol Reviewer。任一 gate 失败都保持
   `BLOCKED_PREQUALIFICATION`，不得放宽、回退或先做模型请求。
3. WFP 使用动态 session，按 package SID 在 ALE v4/v6 层默认拒绝，只允许固定 IPv4
   loopback proxy 端口；DNS、UDP、QUIC、其他 loopback 端口、私网和直连全部拒绝。proxy
   只接受冻结 audience 的 CONNECT，验证 CONNECT authority、ClientHello SNI、解析结果和
   实际 peer；策略安装、child resume 或 proxy readiness 任一步失败时不得创建携带认证的
   Codex child。Windows 对 AppContainer→未打包本机 proxy 还要求 package-specific loopback
   exemption：它只能作为网络隔离的使能层，必须在维护锁内保留系统已有 SID 列表、追加并
   读回本次 package SID，随后仍由上述动态 WFP 精确限制到单一端口；运行结束或崩溃恢复后
   撤销本次 SID 并恢复既有列表。宽泛、长期或未配套端口级 WFP 的
   `CheckNetIsolation LoopbackExempt` 不能替代本设计，也不能计为资格通过。
   AppContainer 默认不授予 `internetClient` 或其他网络 capability；loopback exemption 与
   package-SID WFP 必须先在无网络 capability 的真实 smoke 中证明足够。只有系统 trace
   证明固定 CLI 无法到达本机 proxy 时，才能把最小必需 capability 作为新的、哈希绑定的
   qualification 输入，且仍须证明 WFP 撤除时该 capability 不会形成直连窗口。
4. child 必须 `CREATE_SUSPENDED`，先纳入 kill-on-close Job、应用 AppContainer security
   capabilities 并确认 WFP transaction 已提交，再 resume。统一 hard deadline 后调用
   `TerminateJobObject` 并确认所有 descendants 归零；breakaway、额外 child 或未知 helper
   都使资格失败。
   `TerminateJobObject` 或 descendants drain 未确认归零时，不能撤销 WFP、loopback
   exemption、文件边界或 profile；必须保留隔离资源和维护锁并进入需人工处理状态。
   只有进程树归零后才允许按逆序清理，避免清理动作反而给存活 child 放开网络或文件。
5. package SID 只可读取固定 executable、系统运行库、专用 `CODEX_HOME`、本次 sealed
   evidence/schema/prompt 和 isolated run root。仓库、私有仓、Vault、环境配置、AGENTS、
   skills、plugins 与其他用户文件必须在 trace 中为零成功读取；真实目录不改 ACL，所有
   grant 只发生在 create-new qualification runtime。
6. T1.3a 的 synthetic fixture 仍是输入事实源；T1.3b 另建
   `LIVE_CODEX_TRANSPORT_QUALIFICATION` binding，绑定 fixture、curation result、prompt、
   schema、模型、binary、argv、package SID、WFP 和 Windows build。它不把 T1.3a 的
   `SYNTHETIC_QUALIFICATION` policy 冒充 production generation，也不进入 renderer、
   publisher、Home、Status、Task 或生产 activation。
7. T1.3b 采用当前产品的可信单用户主机威胁模型：由固定 Authenticode qualifier 在同一
   进程内构建 plan、持有文件 handle 并重算全部 hash，资格产物只能称
   content-addressed/hashed，不能称 signed。没有预置 CNG 信任根时不得用 ephemeral 或
   self-signed key 制造“已签名”证明；未来生产 broker 的 key provisioning/rotation/
   recovery 必须另立决定和用户批准。

权限后果：WFP/profile/trace 的真实资格需要管理员授权和 disposable Windows 环境。
当前非 elevated 会话可以完成官方资产、严格模型契约和离线负向测试，但不得伪造绿色；
必须以结构化 blocker 停止，并把需要用户完成的管理员动作列为显式下一步。

范围后果：D-001/D-006 的轻量生产架构在 T1.3b 之外继续有效；本决定不是重新启用旧
stage0/broker、native publisher、CNG journal、PromotionLedger、Vault 发布或 Task。
D-009 继续不可达且不能成为 fallback。

## D-011 — Windows Codex console 启动固定使用 DETACHED_PROCESS

状态：`ACCEPTED`（2026-08-24，无认证真实 Windows 诊断）

决定：T1.3b 的 Codex child 和 child-policy canary 使用 `DETACHED_PROCESS`，不得使用
`CREATE_NO_WINDOW`。官方 0.147.0 executable 是 `WINDOWS_CUI`；真实 B/D 控制组证明
`CREATE_NO_WINDOW` 仍创建 `conhost.exe`，且与 `CHILD_PROCESS_RESTRICTED` 组合时以
`0xC0000142` 终止。E/F 单变量组仅改为 `DETACHED_PROCESS`：E 无 child restriction，
F 保留 atomic Job 与 `CHILD_PROCESS_RESTRICTED`；两者均退出 0、Job 累计进程为 1、
descendants 归零。canonical preflight 因而可以验证固定 `codex-cli 0.147.0`，但仍在
`PREFLIGHT_FULL_TOKEN_AUTH_ISOLATION_NOT_QUALIFIED` 停止。

证明要求：child 在 suspended 状态、resume 前必须通过
`GetProcessMitigationPolicy(ProcessChildProcessPolicy)` 精确回读；无 restriction 的 E
必须为 flags=0，F/canonical 必须为 flags=1。另用固定、无认证、无网络 canary 证明：
未施加 policy 时固定 child 创建成功并使诊断失败；施加 policy 后 Windows 必须返回
`ERROR_CHILD_PROCESS_BLOCKED (367)`，Job 累计进程仍为 1。只观察到“没有 child”或只写入
PROC_THREAD_ATTRIBUTE 均不算资格证明。

证据边界：当前 create-new candidate 位于
`D:/personal/obsidian_workflow-runtime/durable/t13b/qualifier/0.1.0-prequal-20260824-04`；
qualifier SHA-256 为 `45384ace4410ddb493a53fb3a3d5674b1afbbbd3aadef9c77245be1ae9515ad5`，
canary SHA-256 为 `f58adb00d5a3817d01216d266060f0c8d1f1110724756460fc449ee7673a9ac6`。
二者未 Authenticode 签名、未在独立环境复现、未绑定 Windows build，因此只能称
content-addressed prequalification candidate，不能称 production/release-qualified。

验证后果：Cargo 可在用户明确允许清理的 runtime scratch 中更新/删除编译缓存；这些门
必须标为 `SCRATCH_TOOLCHAIN_ONLY / production=false`。strict no-delete harness 直接运行
固定 SHA 的 qualifier `doctor`，不得用 `cargo run` 冒充运行 authority。该决定不解除
AppContainer/LPAC、WFP、loopback proxy、文件 ETW、套餐认证、真实 Terra/Sol 或用户批准门。

## D-012 — AppContainer identity 与 WFP 只能按逐层真实观测推进

状态：`ACCEPTED`（2026-08-24，AppContainer 已实测；WFP elevated 门待用户授权）

决定：T1.3b 不允许用配置布尔值、mock 或“API 调用返回成功”替代 Windows 安全边界证明。
固定 qualifier 新增两个无认证、无网络、永远 exit 3 的诊断入口：

- `diagnose-appcontainer-profile` 使用 create-new 的唯一 profile name，以零 capability 创建
  profile；独立 derive SID 后逐字节比较；删除并以同名重建，要求 SID 稳定；最终删除后才
  写 create-new、hash-only receipt。它只关闭 profile lifecycle 的观察缺口，不证明 LPAC
  token、capability readback、崩溃恢复或 release binding。
- `diagnose-wfp-policy` 必须先确认 elevated；非管理员时不得创建 runtime root、profile、
  sublayer 或 filter，只返回 `ELEVATION_REQUIRED`。管理员路径用 package SID 安装一个
  IPv4 loopback TCP permit、IPv4 deny 和 IPv6 deny；对 layer、sublayer、action、weight、
  condition field/match/type/value 做精确回读；关闭 dynamic session 后再次查询并要求三条
  filter 与 sublayer 全部不存在。即使通过也保持 `WFP_NEGATIVE_MATRIX_NOT_RUN`，直到真实
  allow/deny 网络矩阵和 trace 完成。

证据后果：本机普通用户已真实证明零 capability profile 的 create/derive/delete/recreate/
stable SID/final delete；WFP 只证明了“非管理员零变更阻断”及离线精确匹配逻辑。没有用户
明确的管理员授权，不弹 UAC、不借用高权限进程、不把 elevated 分支标绿。下一 build
generation 必须把整个 Cargo workspace manifest closure、源码、测试、工具链、argv、环境
覆盖、日志和二进制 hash 一并冻结，并继续标注非 production、非 live。

本决定的首个 create-new candidate 为
`D:/personal/obsidian_workflow-runtime/durable/t13b/qualifier/0.1.0-prequal-20260824-06`；
qualifier SHA-256 `3aebfaa9fa0e67b73b56b97ed956493dad7de2f98edf1211126308449d000554`，
canary SHA-256 `eb5b509fea498327531c72563f79da027dccf978064091d79f006291893cde93`，
build receipt SHA-256 `fd9992a92ccbf94161e98158dfb92908a712864deebe0fcef536202e46e37ef2`。
receipt 的 13 项 source closure 已提交并绑定 Git `ee67acf`；它仍明确记录 ambient build、
不可复现、未签名、未绑定 Windows build、LPAC/WFP 未完成与未获用户激活批准，因此只能
作为 prequalification candidate。真实 backend 保留三条 filter ID；checked engine close
失败保留 handle 且不运行 absence verifier，close 成功后才用独立 engine 回读 filter/sublayer
全部不存在；任一失败保留状态并阻断后续清理。

范围后果：该决定不授权认证、模型调用、Task、Vault、Home、Status、发布、删除、移动、
重命名或覆盖；也不解除 loopback exemption、sealed proxy、文件/进程 trace、真实
Terra→Sol、覆盖/mutation、两轮红队和用户最终批准门。

## D-013 — LPAC token 资格必须区分属性接受、已观察事实与未知值

状态：`ACCEPTED`（2026-08-24，Windows 11 build 26100 实测）

决定：T1.3b 增加固定、无参数、无认证、无网络且永远 exit 3 的
`diagnose-lpac-token`。诊断按微软 [AppContainer/LPAC 启动规范](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)
同时提交 zero-capability `SECURITY_CAPABILITIES`、
`PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT`、atomic Job list 与
`CHILD_PROCESS_RESTRICTED`；child 以 `CREATE_SUSPENDED | DETACHED_PROCESS` 创建且永不
resume。`LOCALAPPDATA` 只使用官方
[`GetAppContainerFolderPath`](https://learn.microsoft.com/en-us/windows/win32/api/userenv/nf-userenv-getappcontainerfolderpath)
返回值，并分别按 API 约定使用 `LocalFree`/`CoTaskMemFree`。

观察要求：在 child suspended 时从真实 primary token 回读 AppContainer=true、零 capability、
package SID exact match；从 OS 回读 child policy=1、Job 1/1，随后 TerminateJob、Wait 和 Job
accounting 必须证明 1/0，才允许删除 profile。partial `CreateProcessW` 只要报告成功或任一
process/thread handle 非空，就视为可能已创建 child；缺少 process handle或任何 drain 证据时
保留 profile。失败状态分别携带 child-created、descendants-zero 与 attribute-accepted，禁止
用一个布尔值代替三个事实。

低信边界：当前 build 26100 对
[`TokenIsLessPrivilegedAppContainer`](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ne-winnt-token_information_class)
经 `GetTokenInformation` 返回 unsupported，直接 `NtQueryInformationToken` 也只允许把
`STATUS_NOT_IMPLEMENTED`、`STATUS_INVALID_INFO_CLASS`、`STATUS_NOT_SUPPORTED` 三个精确值
归为平台不支持；其他 NTSTATUS 均是故障。unsupported 时 `token_is_lpac=null`，不能写 false
或 true。因而本诊断证明 LPAC attribute 已被 CreateProcess 接受，并证明 AppContainer/SID/
zero-capability/Job/child-policy/cleanup；它不证明当前 token 的 LPAC flag，仍保持
`LPAC_TOKEN_DIAGNOSTIC_NOT_RELEASE_BOUND` 与 `BLOCKED_PREQUALIFICATION`。

本决定的首个 create-new candidate 为
`D:/personal/obsidian_workflow-runtime/durable/t13b/qualifier/0.1.0-prequal-20260824-07`；
qualifier SHA-256 `20895555532cb67058410fa55b5e5b515c0d50342ee7d868b335d7e5265e06a2`，
canary SHA-256 `e959c5e5e889ae2c036a3bdb502caab416737f4f8f48f47c005f8d43ac113098`，
build receipt SHA-256 `96126f51af922c5c1fc0719cd74bcfc7e168215d5de892c9b386a4c86dcf10f7`。
13 项 source closure 已提交并绑定 Git `fa8b2a5`；ambient build、不可复现、未签名、Windows
build/release binding、direct LPAC readback、elevated WFP、proxy/trace 和用户批准仍是 blocker。

范围后果：本决定不授权 child resume、认证、Codex、模型、网络、Vault、Task 或发布；也不
把 AppContainer diagnostic receipt 当成 production OS-observed closure。

## D-014 — Creator-kill reconciliation 不等于 owner-restart crash recovery

状态：`ACCEPTED`（2026-08-24，真实 Job kill 与独立 absence observer）

决定：新增固定 CLI `diagnose-appcontainer-creator-kill-reconciliation` 与独立
`pkm-appcontainer-crash-canary.exe`，只证明以下窄链路：creator canary 在父 qualifier 的
atomic Job 中创建 zero-capability profile、flush exact READY 后等待；父进程在固定 750ms
deadline 通过 Job kill，Wait + accounting 证明进程树 1→0；仍存活的 recovery authority 使用
已知 run_id 删除残留、同名重建、比较 SID、最终删除。测试再从 receipt 的 run_id 独立调用
`CreateAppContainerProfile`，只有成功时才证明最终 profile 当时确实不存在，并立即清理观察者
自己创建的 profile。

canary 在任何 profile mutation 前必须同时证明：当前进程已在 Job、
`ProcessChildProcessPolicy.Flags == 1`、stdout 是继承的 pipe；合法 run_id 单独不足以启动。
profile delete 只把 `S_OK` 视为删除成功，只把 `HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)` 与
`HRESULT_FROM_WIN32(ERROR_NOT_FOUND)` 视为原本不存在；其他 HRESULT 全部失败。

低信边界：本诊断没有杀死 qualifier/owner 本身，没有 durable create-intent、abandoned mutex
reconciliation、启动时枚举或 fresh-process phase-B，因此固定输出
`owner_restart_recovery_observed=false`。公开 CLI/type/schema/blocker 均使用 creator-kill
reconciliation 名称；广义 `APPCONTAINER_PROFILE_CRASH_RECOVERY_NOT_QUALIFIED` 与
`MAINTENANCE_LOCK_LIFECYCLE_NOT_QUALIFIED` 继续保留，不能被本证据关闭。

本决定的首个 create-new candidate 为
`D:/personal/obsidian_workflow-runtime/durable/t13b/qualifier/0.1.0-prequal-20260824-08`；
qualifier SHA-256 `0cfb5646cbe307d498f083740d28c3d4ef2e978253661d8f7ed63cae4d7b2c56`，
child-policy canary SHA-256 `a8316ad74c48dd4cf3cc25e91cc99186318aa8e351dc1a3f378a2b50c91cb469`，
creator-kill canary SHA-256 `16d62304a9b7fd4e22272f5d3ba21aade340c97403ffd35bd118618e4a66998a`，
build receipt SHA-256 `f4e541b5d685d96581600a9fc786a5e4f3e5968c10898c0b5346c52f4fd73b67`。
14 项 source closure、三份 binary 与 Git `3e4fd4a` 已绑定；三份 binary 仍 unsigned，build
仍 ambient/non-reproducible，owner restart、LPAC direct flag、elevated WFP、proxy/file trace
与用户批准仍是 blocker。

范围后果：本决定不授权 owner-crash recovery 标绿，不授权认证、网络、模型、Vault、Task 或
发布；runtime 产物与 receipt 继续 create-new。

## D-015 — Owner-process restart recovery 与 qualifier startup recovery 分开计证

状态：`ACCEPTED`（2026-08-24，真实 Phase A kill + fresh Phase B）

决定：固定 CLI `diagnose-appcontainer-owner-restart-recovery` 使用两个独立
`ChildTransaction`/Job。Phase A owner process 必须在 profile mutation 前 create-new intent，
写入 exact canonical bytes、flush 并 `FlushFileBuffers(file)`；随后创建 profile、flush READY，
被 750ms Job deadline 强杀且证明 1→0。Phase B 是全新 process，只读取固定 run_id 对应的
exact intent，删除残留、同名重建、比较 SID、最终删除，并 create-new+flush Phase-B receipt。
Supervisor 不执行成功路径的 profile 恢复，只用 held read-only handles 验证 intent 与 Phase-B
receipt 的 no-reparse、same-volume、parent/file ID、link_count=1、exact length/SHA/content，并把
两份 identity 嵌入 overall receipt。测试最后按 run_id 独立 recreate→delete，证明 Phase B
结束时 profile 不存在。

失败语义：未证明 descendants=0 时不删除 profile并设置
`profile_retained_for_recovery=true`；已 drain 后才调用 checked reconciliation delete，delete
失败成为 primary blocker。Intent 只称 `file_flushed_and_observed`，固定输出
`directory_metadata_durability_observed=false`；Phase B 由 supervisor 提供 run_id，固定输出
`startup_intent_discovery_observed=false`；supervisor/qualifier 没有被杀，固定输出
`qualifier_restart_recovery_observed=false`。因此本证据只允许
`owner_process_restart_recovery_observed=true`，不能关闭广义 profile crash、maintenance/startup
discovery 或 power-loss durability 门。

当前 create-new candidate 为
`D:/personal/obsidian_workflow-runtime/durable/t13b/qualifier/0.1.0-prequal-20260824-09`；
qualifier SHA-256 `9cdea7e3f148b8ddecac72f49795b5efda987d50c58c2e05b2b465f21f99903b`，
child-policy canary SHA-256 `7aeac03c60db7c6de747496309f2f76bde300444366cebc13a476608308e9ca1`，
recovery canary SHA-256 `b98d911fbe8c1ce154a42585620d053250ac05233fe694be76276578e9bbba99`，
build receipt SHA-256 `0dc30f86cb52f30b566ebc32ae26fe1f9c9659a3a5b717d198b07262be3f282d`。
14 项 source closure、三份 binary 与 Git `17ae296` 已绑定；build/签名、qualifier restart、
startup discovery、directory durability、LPAC direct flag、elevated WFP、proxy/file trace 和用户
批准仍是 blockers。

范围后果：本决定不授权 production、认证、模型、网络、Vault、Task 或发布；intent 与两级
receipt 永久 append-only 保留，不删除、不覆盖。

## D-016 — Startup intent discovery 仅作为实验性预资格成果冻结

状态：`ACCEPTED_EXPERIMENTAL_FROZEN`（2026-08-24，非 MVP 生产依赖）

决定：fresh Phase B 不再接收 run_id，而是在固定 scratch root 中按确定性顺序发现
owner-restart intent。Scanner 持有 scratch 与候选目录的 no-share-delete 句柄；intent 与
Phase-B receipt 必须是有界、单链接、非 reparse 的普通文件，并与 run_id 及允许的 canonical
bytes 精确全等。已有 terminal receipt 不能仅凭文件名跳过，必须再用原子
`CreateAppContainerProfile` presence probe 证明 profile 不存在；receipt 损坏或 terminal/profile
冲突一律 fail-closed，且不修改、不覆盖、不删除原文件。历史 no-profile receipt 仅按一份固定
legacy canonical bytes 兼容，仍须通过同一 presence probe。

存在性语义：`CreateAppContainerProfile == S_OK` 只表示 probe 入口无同名 profile；校验返回
SID 后删除本次 probe。`HRESULT_ALREADY_EXISTS` 表示入口存在 profile；恢复分支随后执行受控
删除、同名重建、SID 稳定性比较与最终删除。其他结果全部阻断。不得用
`DeleteAppContainerProfile == S_OK` 推断 profile 原本存在，也不依赖未文档化注册表映射。

证据边界：只允许声明 `owner_process_restart_recovery_observed=true` 与
`startup_intent_discovery_observed=true`。`qualifier_restart_recovery_observed=false`、
`directory_metadata_durability_observed=false`、`production=false`、`live=false` 固定保留。
真实测试覆盖 no-profile 独立 OS absence observer、current/legacy terminal 与残留 profile 冲突、
legacy 兼容、marker file identity/SHA 不变以及 fixed CLI 成功路径。

冻结后果：本成果不属于 v7.4 MVP 生产链路，不授权认证、模型、网络、真实 Vault、Home、Status、
Task 或发布。WFP elevation qualification、完整 LPAC/WFP 生产隔离、qualifier 自身重启、目录
metadata/掉电持久性、stage0 自升级和 Task bundle 代际切换仍未完成。T1.3b 整体不得标记完成；
除非用户重新授权，不再扩张上述原生研究。

## D-017 — v7.4 MVP 以可信单机 Python 链路取代 native 首发门

状态：`ACCEPTED`（2026-08-24，生产仍需用户批准）

本决定取代 D-001/D-005 的“单一 Task”作为当前调度设计；旧决定保留为历史，不删除。

决定：当前唯一产品关键路径为三个 Windows Task→固定最小 activation launcher→同一 Python 密封入口→完整来源/Coverage→
零工具 Codex Generator/Reviewer→确定性 renderer→独立审计 A + 发布 backing B + PREPARED +
`os.link` create-new→create-new event/可重建 JSONL projection→Obsidian。Python create-once 全局
文件锁与持久 demand 串行化 Task/manual/reconcile；Task 必须全部 disabled 注册并精确 readback，
Gate D 后才按 workflow 启用。

可信边界：信任用户 Windows 账户、Python/NTFS/Task Scheduler、用户批准的 content-addressed
release/config。远程 feed 与模型输出不可信；Codex 使用独立 CODEX_HOME、isolated cwd、
sanitized environment 和零工具配置，真实 shadow 必须证明零 tool event、零额外子进程和密封根外
成功读取为零。AppContainer/WFP、stage0/broker、qualifier restart、native 目录 flush 和掉电证明
保留为后续增强，不进入 MVP denominator。

激活不是自由文本文件：Task action 固定到不可自动升级的 content-addressed launcher；launcher
只读最高有效 activation chain，核验 release/config 后 exec 所选业务 release，不能写 activation
或 Vault。release/config 使用内容寻址 create-new manifest；独立交互入口在用户
明确批准 exact challenge 后追加链式 ActivationGeneration，绑定 workflow scope、release/config、
source policy、prompt/schema/model、Task、UserContext、shadow 与红队证据 hashes。normal engine、
Task、Codex 均不能创建 activation；回滚只追加更高 generation 并重新 shadow/批准。

分层语义：T1.4 只验证 activation structure（chain/scope/approval/UserContext/artifact identity+hash），
不接受 `QUALIFIED`、`P0=0/P1=0` 等自报 marker 作为内容 Gate。真实 source/schema/Task/shadow/
red-team domain validators 未接线前固定 `activation_validated=false` 与
`ACTIVATION_EVIDENCE_SEMANTICS_NOT_IMPLEMENTED`。

范围后果：本决定不授权当前代码写真实 Vault、注册 Task、调用模型或进入 production。先完成
T1.4 sealed entry 的真实负向测试和小步提交，再按 v7.4 任务顺序推进。

## D-018 — v7.4 模型路径改为 DeepSeek 官方 API

状态：`ACCEPTED`（2026-08-25，用户明确要求；真实调用仍限 shadow）

决定：v7.4 当前调用图移除 Codex CLI，以独立 D-018 strategy 复用版本化、保持不变的 D-009
fail-closed DeepSeek HTTPS transport/schema provider。
Generator 与 Reviewer 均固定 `deepseek-v4-flash`，使用两个 fresh Chat Completions 请求；固定
endpoint、thinking disabled、JSON Output、stream false、无 tools、prompt/schema/input/request
hash 和总请求预算。same-provider/same-model 不称为独立模型验证。

凭据与费用：只读 Windows Credential Manager 的 `PKM/DeepSeek/shadow-v1`；不接受环境变量、
明文文件或自由 target。只使用既有余额，不自动充值；401、402、429、5xx、timeout、length、
tool_calls、空/坏 JSON 或 schema mismatch 全部 fail-closed。正常双审 2 次请求，含修复最多 4 次。

范围后果：D-009 历史 binding/prompt/schema/hash 不原位改写；D-018 strategy 单独绑定其 hash。
当前正式 model-contract adapter 为 `NOT_IMPLEMENTED`，因此真实 provider smoke 不得称为完整
Generator→Reviewer pipeline。D-010/T1.3b 保留为冻结历史但不进入 v7.4 gate。Provider 仍
`production_eligible=false`；不写 Vault、Task、Home/Status。

## D-019 — AI Daily V4 使用证据丰富度优先的自然编辑契约

状态：`ACCEPTED`（2026-08-30，真实 shadow 与双红队通过）

决定：保留现有来源治理、抓取、Coverage、DeepSeek 双请求、逐 claim evidence mapping 与
确定性链接渲染；只收敛证据取材、编辑契约和读者 Markdown。候选保留有界完整正文，模型前按
段落选择导语、方法、结果和限制，全部 packet 合计不超过 24,000 字符。证据丰富的长文先于
短引用进入 Generator；每日候选按独立 canonical origin 最多保留一条并回填其他来源。
引用整理页只能显示为“引用页”，link post 显示为“来源页”，不能冒充官方原文。

输出最多 5 条，正常为 4–5 条。前两条允许展开，后位条目保持简析；不再渲染“今日深读”、
“分层情报雷达”、“Takeaway”、“对你的启发”、“慢层回顾”或 Coverage 说明。标题、事件、
机制、重要性与显式个人影响属于物质 claim，任一未获 Reviewer `ACCEPT` 即删除整条；可选的
不确定性、观察信号和行动未通过时只省略。行动全篇最多 1 条。

正常 Generator + Reviewer 为 2 个 fresh DeepSeek 请求。只有结构修复，或 5 个以上合格候选却
少于 4 条这一种 underfill，允许一次额外 Generator；全链仍不超过 4 次，不切换 provider，
不自动充值。4–5 条是软编辑目标，不是发布门；补选后的 1–3 条有效故事仍进入 Reviewer，
不能仅因 underfill 丢弃整期或降低证据要求。pillar 只影响选择，不能制造个人连接；显式个性化只允许 project、prior knowledge
或用户逐项批准的 knowledge anchor。

真实验收样本：
`D:/personal/obsidian_workflow-runtime/scratch/runs/306602053b5242f2b4673decb37da534/AI-Daily-2026-08-30-shadow.md`。
内容红队评分为注意力价值 9/10、清晰度 8/10、深度 8/10、证据边界 9/10、个性化诚实度
8/10；最终内容与工程复审均无剩余 P0/P1。该决定不授权覆盖 Vault 文件、修改 `.obsidian`
或切换模型供应商。
