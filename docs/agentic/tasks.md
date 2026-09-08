# Implementation Tasks

## Current — Luna AI Daily migration (2026-09-08) — DONE

Specification: [Luna runbook](../ai-daily-luna.md). Shared lock lifecycle extracted
without changing existing production expectations (`de2e404`). Three-stage CLI,
structured role handoffs, approved source reuse, no DeepSeek API, and independent
Reviewer binding passed live validation. Historical task statuses below are
preserved; they do not authorize restoring retired infrastructure.

Evidence: runtime run `65115bfb3b344c9cb883d64e920e7d5d`, two real Luna role
sessions, one generator structural repair, 22 draft claims; one story dropped
by deterministic validation and one by Reviewer, two published. No additional
model generation during promotion. Repeated production prepare returned
ALREADY_EXISTS with zero API calls and no Vault write. Complete suite: 922 passed,
143 pre-existing environment/retired-contract skips; Ruff and Mypy passed.
The existing automation now targets the current obsidian_workflow project,
gpt-5.6-luna/medium, 08:00 daily, failed-runs-only notifications.
Daily token usage is unavailable through the task interface and is not invented.
This small first sample proves migration, not superior quality over DeepSeek;
seven-day coverage/readability/cost observation remains ongoing.

状态说明：`DONE`、`NEXT`、`PLANNED`、`BLOCKED`。一个 Agent 一次只认领一个任务；每个任务完成后追加证据路径，不删除历史记录。

## M0 — 统一事实源

### T0.1 Agentic 文档收敛 — DONE

目标：建立唯一入口、轻量规格、架构、开发规则和任务顺序；将重型 v7.3 与旧直写流程退出当前开发路径。

验收：

- `AGENTS.md` 与 `docs/agentic/*` 链接完整。
- 旧 Quickstart、UI/API、native 与 `.agent/workflows` 明确标为非规范/不可执行。
- 私有仓库新增轻量化决策文档，旧 v7.3 文件保留。

## M1 — 真实 shadow 端到端

### T1.1 Registry 与 Coverage proof — DONE

完成时使用的 Goal 归档：[goal.md](goal.md)。

目标：从完整 baseline + approved append generations 派生不可遗漏的 snapshot/observation/coverage hashes。

主要文件（≤5）：

- `src/pkm_workflow/source_import.py`
- `src/pkm_workflow/source_governance.py`
- `src/pkm_workflow/provenance.py`（新）
- `tests/test_v8_source_proof.py`（新）
- `tests/test_v73_source_governance.py`

验收：任意 source/endpoint observation 遗漏、重复替代、短窗口、未分类 baseline、未审批追加、调用方 approval/weight 自报或 runtime 改分母都 fail-closed；74 条 baseline 全部可追溯。CoverageEvaluator 只接受完整 proof object，不保留弱兼容入口。

验证：目标测试、Ruff、Mypy；使用 synthetic policy 在内存生成 registry snapshot
样本，不写 Vault。

完成证据（2026-08-17）：

- `tests/test_v73_source_import.py`、`tests/test_v73_source_governance.py`、
  `tests/test_v8_source_proof.py`：`59 passed`。
- Ruff：`All checks passed`；Mypy：`Success: no issues found in 3 source files`。
- `git diff --check` 成功，tracked delete/rename 为空。
- policy→snapshot→observation→proof→result 连续 hash 绑定及同步重算攻击均有行为测试；
  真实 74 条分类仍由用户后续批准，不在本任务中伪造。

### T1.2 固定来源安全抓取 — DONE

目标：实现 registry 驱动的 HTTPS fetch service 与无网络 parser seam。

主要文件（≤5）：`src/pkm_workflow/fetch_service.py`、`src/pkm_workflow/evidence.py`、各自测试和一个 fixture manifest。

验收：redirect/SSRF/私网/坏 MIME/压缩比/体积/超时/429/5xx 均有行为测试；feed 内容不能扩展抓取范围。

完成证据（2026-08-17）：

- `src/pkm_workflow/fetch_service.py` 提供 registry/snapshot-bound fetch、全量 DNS
  public-address 校验、固定 IP + 原 hostname TLS、逐跳 same-origin redirect、peer
  复核与受限 identity/gzip/deflate 解码；每个 endpoint 都保留 terminal record。
- `src/pkm_workflow/evidence.py` 仅消费内存 fetch batch，离线解析 RSS/Atom/JSON
  Feed；坏结构、URL、时间或字符边界映射为 `PARSE_ERROR`，不泄漏部分 evidence。
- `tests/test_v8_fetch_service.py`、`tests/test_v8_evidence.py` 与 fixture manifest：
  `123 passed`；新生产模块 line+branch：fetch `90%`、evidence `91%`，合计
  `90.34%`。
- T1.1 回归：`59 passed`；Ruff：`All checks passed`；Mypy：
  `Success: no issues found in 2 source files`；`git diff --check` 成功，tracked
  delete/rename 为空。
- 全量 suite 在可读 pytest 临时目录的沙箱外复核，只剩实施前已记录的 7 个 legacy
  `daily_curation`/`formatter`/旧写状态测试失败；T1.2 与 T1.1 没有新增失败。未通过
  修改旧 expected output 或放宽新安全策略掩盖这些基线失败。
- 测试未访问互联网；production resolver/pinned HTTPS transport 已存在；没有连接
  Codex、写 Vault 或启用任何 Task/旧入口。
- 2026-08-18 对抗复审补强：`FetchRecord` 绑定 adapter；非 `SUCCESS` 不能伪装成
  `COMPLETE_ZERO`；成功响应必须由生产 parser 现场重放并与 evidence 精确相等；每个
  endpoint 独立计时，opaque adapter 形成显式 terminal 而不终止批次，YouTube channel
  ID 映射到官方 Atom endpoint。相关 T1.1/T1.2 测试与相邻回归全绿。

### T1.3a 确定性 Curation 与 Review Contract — DONE

完成时使用的 Goal 归档：[goal.md](goal.md)。

目标：在不调用模型的情况下验证 T1.1/T1.2 完整 proof closure，冻结 UserContext、
event clustering、排序、overflow 与“所有 assertive claim 必审”的生产 contract。

主要文件（≤5）：`src/pkm_workflow/curation.py`（新）、
`src/pkm_workflow/review_contract.py`（新）、两个行为测试和一个 fixture manifest。

验收：真实 `policy/snapshot/batch/evidence/proof/result` 全量验证与现场 Coverage 重算；
`sha256:` 格式统一；相同输入字节级稳定；跨 origin cluster 不丢 evidence；每个排除项
有 reason；`POLICY_OVERFLOW` 不调用模型；item URL 不可引用；UserContext canonical
hash 精确匹配；任意 assertive claim 都被标记为 review-required。

完成证据（2026-08-18）：

- 仅新增约定的 2 个生产模块、2 个行为测试和 1 个 synthetic fixture；未修改 T1.1/T1.2
  seam、私有仓或 Vault，未调用 Codex/网络，未写 runtime。
- 24 个 T1.3a 目标测试通过；T1.1/T1.2 回归通过；新模块 line+branch
  合计 93.51%，Ruff 与 Mypy 通过。
- 对抗审查经三轮修复后无 P0/P1：cluster identity 绑定全部模型可见/排序字段；输入
  evidence 被精确、互斥、无遗漏分区；跨 cluster story/excerpt、重复 Top rank 和不可
  自验证 ReviewRequirement 均 fail-closed。
- 精确只读解析批准的 UserContext 文件，canonical digest 为
  `sha256:ce307d029565fe20659ec9e43561d949ac1ec7d7ec67720bad25c7ad73460e3f`；未输出正文、
  未读取同目录其他文件。
- 2026-08-18 最终对抗复审补强：cluster identity 绑定全部排序/profile/role 字段；
  evidence ID 跨 cluster 唯一且与 exclusions 精确互斥；Top rank 全局唯一且最多 3；
  similarity 预计算并受 50,000 次固定 work budget 约束，超限确定性
  `POLICY_OVERFLOW`，全部 evidence 仍留在审计 clusters。当前 canonical M1 gate
  （CLI + T1.1–T1.3a）231 passed，line+branch 90.97%。

### T1.3b 固定 Codex Generator/Reviewer 原生资格 — FROZEN_EXPERIMENTAL

T1.3b 已在 commit `8437a97` 完成 startup discovery 安全着陆并冻结；它不是 MVP 生产依赖，
不得继续阻塞 Python shadow，也不得标记整体完成。在该冻结 native 实验自身的资格语义中，
原生 Codex CLI 0.147.0、官方 release asset/signature/SHA-256、专用
`CODEX_HOME` 套餐登录、AppContainer/LPAC、WFP、sealed proxy、Job kill-tree 和完整
Windows trace 全部通过前仍返回 `BLOCKED_PREQUALIFICATION`；该 blocker 不适用于 v7.4 Python runner。

目标：以固定原生 Codex CLI 完成真实 Terra Generator + Sol Reviewer fresh-thread
qualification shadow；所有 assertive claim 均由 Reviewer 审核。

主要文件：`src/pkm_workflow/codex_runner.py`、`src/pkm_workflow/codex_job.py`（新）、
`src/pkm_workflow/model_contract.py`（新）、独立 native qualification launcher/proxy、
一个 offline 行为测试和一个显式 live qualification 测试。安全边界跨 Python/Rust，
本任务经 D-010 明确允许超过常规 5 文件预算；不得为满足数字删除 live test 或合并掉
可独立审计的 TCB。

验收：固定 executable/model/argv/prompt/schema/input hashes；完整 JSONL 生命周期；
不同真实 thread；零 tool event；AppContainer/LPAC + WFP + one-shot proxy；Job Object
超时/kill-tree；冻结 helper 拓扑和 canary 拒读；schema repair≤1；auth/quota/security/
timeout 互斥分类；真实套餐 smoke 不以 mock 替代。不使用 OpenAI API/DeepSeek/fallback，
不写 Vault/Home/Status/Task。

2026-08-24 环境资格进展：`DETACHED_PROCESS` 已替代 `CREATE_NO_WINDOW`；canonical
preflight 与拒绝型 child-policy canary 均通过真实 Windows 无认证验证，固定 qualifier/
canary candidate 和 build receipt 已 create-new 冻结。Cargo fmt/check/clippy/test 通过
`SCRATCH_TOOLCHAIN_ONLY` observer 门；固定 qualifier doctor 通过 strict no-delete harness，
仍为 `VERIFIED_EXPECTED_BLOCKED / production_qualification_eligible=false`。零 capability
AppContainer profile 的 create→independent derive→delete→same-name recreate→stable SID→
final delete 已通过真实本机诊断；WFP 非管理员入口已证明在任何 profile/filter/runtime
mutation 前 fail-closed。WFP elevated 精确 filter readback/cleanup 与真实 allow/deny 负向矩阵
仍需用户显式管理员授权；真实 backend 已把 checked engine close、filter ID retention、
post-drop filter/sublayer absence readback 与失败重试纳入清理状态机。
LPAC suspended token 诊断已证明 attribute accepted、AppContainer、zero capability、SID、
Job/child-policy 和 cleanup；Windows 26100 direct LPAC flag 不支持并保持 null。
Creator-kill reconciliation 已通过真实 Job kill、残留 delete、recreate/SID stable、final
delete 与独立 absence observer。Owner-process Phase A→fresh Phase B 也已证明 flushed intent
可驱动跨进程恢复；fresh Phase B 无 run_id 的 startup intent discovery 已观察，但 qualifier
restart 与 directory/power-loss durability 仍未证明。该原生预资格成果现已冻结且不属于 v7.4
MVP 生产链路；独立 build/release binding、direct LPAC token、完整 WFP/proxy/file trace、
qualifier crash recovery 等保留为后续增强，不再阻塞真实日报 shadow。

T1.3a/b 只产生 curation/review qualification shadow，不是用户日报或 M3 内容质量
样本；feed URL/摘要不能被表述为已验证 article 正文、canonical 或原始链接。

### T1.4 Python 密封入口与路径/激活策略 — DONE

目标：把 `scripts/run_workflow.py` 与 `src/pkm_workflow/cli.py` 迁移为 production/manual/
shadow/dry-run 共用的单一 engine seam；参数只有 workflow/mode/due-only 枚举，无自由 Vault、
输出、模型、prompt 或 command 路径。production 缺 exact activation generation 必须拒绝。

主要文件（≤5）：`scripts/run_workflow.py`、`src/pkm_workflow/cli.py`、一个小型 engine/policy
模块、一个新行为测试和必要的现有 CLI 测试。不得修改或暂存 `AGENTS.md` 和无关未跟踪资产。

验收：四种 mode 调用同一生产函数；doctor/dry-run/shadow 不写真实 Vault；production 缺失/
损坏/未知字段/hash/scope/stale/fork activation structure 全部 exit 3；完整祖先 reparse chain 与
handle 物理落点受检；caller 不能传入路径；wrapper 原样返回 2/3/4/5/8/10。结构通过只输出
`activation_structure_validated=true`，真实证据 domain validators 未接线前固定
`activation_validated=false / ACTIVATION_EVIDENCE_SEMANTICS_NOT_IMPLEMENTED`；旧 native blockers
和旧 9-task/stage0 seam 不再决定 MVP gate。

完成证据（2026-08-24）：

- flat CLI 与 wrapper 共用 `execute_workflow`；只接受 workflow/mode/due-only，所有自由路径和
  v7.3 subcommand 均由 argparse exit 2 拒绝。
- ActivationReader 验证完整祖先 reparse chain、Windows final-handle physical containment、
  file type/identity/size、连续 generation/fork/scope、每代 challenge+consume、冻结 UserContext
  raw/canonical identity，以及全部绑定 artifact 的存在/identity/hash。
- 未接线证据语义时固定 `ACTIVATION_EVIDENCE_SEMANTICS_NOT_IMPLEMENTED`，不得由自报 marker
  解锁；未分类异常在 direct/wrapper 边界脱敏为 JSON exit 10。
- 55 个目标测试通过；控制模块 line+branch 93%；目标 Ruff/Mypy 通过；T1.1–T1.4 closure 回归
  全绿；两轮代码红队 P0/P1=0。全仓仍有已记录的 retired v7.3 writer/path/harness 契约失败，
  未通过放宽新入口或修改期望掩盖。

### T1.5 确定性 DeepSeek API Generator/Reviewer pipeline — NEXT

目标：使用固定 DeepSeek Chat Completions endpoint 与 `deepseek-v4-flash` 完成 fresh
Generator→Reviewer；固定 prompt/schema/model/request hashes，thinking disabled、JSON mode、
无 tools、请求预算≤4。只从固定 Credential Manager target 读 key，不使用 Codex/OpenAI/fallback，
不自动充值。

验收：逐 claim structured review、高风险无 ACCEPT 即删除、Top 失真时整份 abstain；auth/quota/
timeout/schema/余额失败不产生 Markdown/terminal；真实 shadow 记录调用次数、token、时长和费用影响。

进展（2026-08-25，strategy checkpoint，T1.5 尚未 DONE）：

- 新增 D-018 strategy，版本化绑定未改写的 D-009 provider contract descriptor；descriptor 覆盖
  prompt schedule、Generator/Reviewer schemas、request config/limits、endpoint/model/credential target。
- T1.3b `model_contract` V1 与 D-009 binding/prompt/schema/hash 保持原样；正式 D-018 adapter
  仍为 `NOT_IMPLEMENTED`，provider smoke 不冒充完整 pipeline。
- 合成密封输入的真实 DeepSeek smoke：Generator 与 Reviewer 各 1 次请求均 `VALIDATED`，合计
  3928 tokens、无 tool call、secret redacted、未持久化正文；证据为 runtime create-new generation 03。

### T1.6 确定性输出契约 — PLANNED

目标：accepted stories 与渲染字段严格全等；no-news 不产生 digest。

主要文件（≤5）：`renderer.py`、renderer schema、两个测试。

验收：Top≤3、无填充、无模型 URL/Markdown、用户上下文 hash 精确匹配、重大故事不因 Top 配额丢失。

### T1.7 单一 Orchestrator shadow — PLANNED

目标：一个 Python 入口串联 T1.1–T1.6，使用全局锁、真实退出码和 scratch run root。

主要文件（≤5）：`orchestrator.py`（新）、`cli.py`、`scripts/run_workflow.py`、两个测试。

验收：AI shadow 生成完整 manifest/preview/status payload；不读写 Vault；重复/并发/阶段失败可复现。

M1 Gate：打通至少一份真实 AI shadow 样本；DeepSeek key/余额不可用时记录明确 blocker，不自动充值或偷偷降级。

## M2 — 安全发布与 Obsidian 体验

### T2.1 Python create-new publisher — PLANNED

目标：实现 backing → `os.link` → verify → receipt 和 reconcile。

主要文件（≤5）：`src/pkm_workflow/publisher.py`（新）、`src/pkm_workflow/path_policy.py`（迁入/适配）、publisher tests、reconcile tests、fixture helper。

验收：同盘 NTFS preflight、目标存在冲突副本、无 overwrite/delete/rename、每个切点强杀可恢复、用户编辑不被回写。

### T2.2 Status 与 Home 接入 — PLANNED

目标：broker-free publisher 消费现有 sealed payload，创建正确的 status/Home artifacts。

主要文件（≤5）：`status.py`、`home.py`、两个现有测试、一个集成测试。

验收：今日三 workflow latest、deadline 失联、no-news 无链接、验证失败 target 清空；真实 Obsidian Desktop 验证通过。

### T2.3 三个多触发器 Windows Task 与 activation launcher — PLANNED

目标：一个由 AI/IELTS/Weekly 三个 disabled Task 组成的 bundle；每个 Task 含 calendar/logon/
unlock triggers，action 固定到 content-addressed activation launcher，再 exec 同一 Python sealed
entry。global lock+demand 串行化跨 Task/manual/reconcile。

主要文件（≤5）：`task_bundle.py`、安装脚本、CLI due tests、Task XML tests。

验收：时间/登录/解锁、同时触发/manual 并发、IgnoreNew、global lock+demand、StartWhenAvailable、
no-wake、真实 exit 2/4/5/8、all-disabled register/readback/失败回退、同一 launcher action 的
N→N-1 activation rollback 均在 disposable namespace 通过。

M2 Gate：disposable 环境全部通过后，经用户单独批准才允许 AI Daily 在真实白名单目录做受控 pilot；Weekly/IELTS 保持 disabled。

## M3 — 内容与稳定性验收

### T3.1 历史回放 — PLANNED

50–100 条真实 evidence package；核心链接匹配 100%，critical 实质错误 0，高风险检测 recall≥95%。发现缺口时创建新 policy generation，不修改旧 expected output。

### T3.2 真实 shadow — PLANNED

AI 连续 7 个自然日、Weekly 2 个真实周期；记录调用次数、认证/额度、耗时、失败恢复和用户注意力评分。

### T3.3 两轮行为红队 — PLANNED

覆盖恶意 feed 指令、伪 canonical、数字/时间偷换、重复来源、公司自述、断网、冲突、并发和强杀。连续两轮无未处理 P0/P1 才进入激活评审。

## M4 — 分阶段激活

### T4.1 AI Daily — BLOCKED

依赖 M1–M3 Gate、用户验收与明确批准。批准后才启用 AI trigger。

### T4.2 Weekly — BLOCKED

依赖 AI 稳定运行和 2 个 Weekly shadow 周期；不要求等待 4 周。

### T4.3 IELTS — BLOCKED

依赖共享引擎稳定与独立内容样本验收。

## 预计节奏

- 5–8 个工作日：真实端到端 AI shadow。
- 再用 7–14 天：内容与稳定性观察。
- 3–5 周：达到个人生产候选；实际激活只由证据和用户批准决定，不由日期自动决定。
