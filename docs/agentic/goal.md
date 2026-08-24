# Frozen Experimental Goal — T1.3b Codex Generator → Reviewer 本机资格

状态：`FROZEN_EXPERIMENTAL / NOT_PRODUCTION / NOT_COMPLETE`

2026-08-24 工程收敛决定：T1.3b 在 startup intent discovery 安全着陆后冻结，不再阻塞
v7.4 个人单机 MVP。后续生产链路不得依赖本原生 qualifier；除非用户重新授权，不继续 WFP、
完整 LPAC、qualifier 自身重启、目录掉电证明、stage0 自升级或 Task bundle 代际切换。

只执行 D-010 的 qualification shadow：固定官方 Windows 原生 Codex CLI `0.147.0`，
先证明 AppContainer/LPAC、package-SID WFP、one-shot loopback proxy、精确文件边界、
Job Object kill-tree、零 tool/额外 child/未知文件读取，再用用户现有 Codex 套餐执行
T1.3a sealed synthetic fixture → `gpt-5.6-terra` Generator → 现有 `ReviewContract` →
fresh `gpt-5.6-sol` Reviewer。

完成条件：

1. binary/archive/signature/version/argv/config/prompt/schema/model/fixture/input 全部冻结并
   绑定 SHA-256；PATH、wrapper、ambient config、AGENTS/skills/plugins 均不可影响结果。
2. WFP 只允许 package SID 到固定 loopback proxy 端口；proxy 精确验证 CONNECT audience、
   SNI、DNS 与 peer，其他网络全拒绝。
3. package 只读取 create-new qualification runtime 的密封输入和专用 `CODEX_HOME`；
   仓库、私有仓、Vault 和其他用户文件成功读取为零。
4. child suspended→Job/WFP→resume；timeout/parent exit 后 descendants 为零；任意 tool、
   helper、unknown event/read/network 使结果失败。
5. GeneratorDraft 严格解析后才进入 `ReviewContract`；Reviewer 的 claim、requirement、
   decision、evidence IDs 与 hashes 精确全等，不允许部分成功。
6. 目标模块 line+branch≥90%，Ruff/Mypy/相关回归通过；两轮独立对抗审查连续无 P0/P1。
7. create-new qualification artifact 记录真实调用数、usage、时长、认证/额度影响和全部
   trace/hash。只有用户明确批准后才可解除 T1.3b 阻断；不得自动进入生产、Task 或 Vault。

非目标：不调用 OpenAI API；不调用 DeepSeek 或其他 fallback；不购买额度；不写 Vault、
Home、Status；不安装/启用 Task；不恢复旧 stage0/broker/publisher；不删除、移动、重命名
或覆盖用户资产。所有 runtime 产物 create-new。当前未跟踪资产是用户资产，必须按实施前
baseline 保留。

执行顺序：环境/资产 parser preflight → 离线契约 TDD → 原生隔离 TDD/资格 → 真实双审 →
artifact/红队/Git。隔离资格之前不得执行真实模型请求；当前非 elevated 会话若无法安装
WFP/trace，必须诚实停在结构化 blocker，不得放宽或伪造通过。

当前实证（2026-08-24）：固定 0.147.0 已通过 `DETACHED_PROCESS + atomic Job +
CHILD_PROCESS_RESTRICTED` 无认证 preflight；OS readback flags=1，拒绝型 canary 返回
`ERROR_CHILD_PROCESS_BLOCKED (367)`，进程树始终为 1→0。`CREATE_NO_WINDOW` 控制组仍产生
`conhost.exe`，仅保留为反事实证据，不得进入 canonical launch。零 capability
AppContainer profile lifecycle 与 SID 稳定性也已由真实 OS API 证明并完成最终清理；
LPAC suspended diagnostic 进一步证明 Windows 接受 opt-out attribute，且真实 token 为
AppContainer、零 capability、package SID exact；Job/child-policy/zero descendants/profile
cleanup 通过。Windows 26100 的 direct LPAC token class 不受支持，因此 LPAC flag 仍是 null，
不能标绿。Creator-kill reconciliation 也已证明 profile creator 被 Job kill 后，同一存活
authority 可删除残留、重建并验证 SID；它明确不证明 owner/qualifier 整体崩溃后的 durable
discovery 与 fresh-process recovery。随后 owner-process Phase A/Phase B 诊断又证明：intent
file 先 flush、Phase A 被 kill、fresh Phase B 不接收 run_id，能扫描固定 root、精确验证
intent/receipt，并通过原子 Create probe 处理 pending 与核验 completed terminal；因此
owner-process recovery 与 startup intent discovery 已观察。qualifier restart 与 directory
metadata/power-loss durability 仍明确为 false。
非管理员 WFP 入口已证明先于任何系统 mutation 返回
`ELEVATION_REQUIRED`。当前仍阻断在
frozen candidate 未独立复现/签名、release-bound direct LPAC token/crash recovery、
管理员 WFP 精确回读与网络负向矩阵、loopback proxy、文件 ETW、专用认证和真实双审。

---

# Historical Goal — T1.3c DeepSeek Flash Shadow Provider

状态：`DONE`（2026-08-20，shadow qualification 完成；生产保持阻断）

目标编号：`T1.3c`

T1.3b 保持 `BLOCKED_PREQUALIFICATION`。本 Goal 只实现并验证 D-009 的一次性
DeepSeek-V4-Flash shadow Provider，完成一条真实 Generator → Reviewer qualification
链路；不得进入生产、fallback、Vault、Home/Status 或 Task。

## T1.3c 公开 seam

```python
result = DeepSeekShadowProvider.invoke(
    role,
    sealed_request,
    output_schema,
    request_budget,
)
```

`ProviderResult` 必须包含固定 provider/model、role、execution/credential mode、fresh
逻辑 request ID、input/prompt-schedule/schema/每次实际 prompt/request-body/供应商 envelope
ID/全部 raw-response hashes、validated structured result 及其 hash、token usage、latency、
terminal classification 和 `secret_redacted=True`。structured result 每次读取都返回新的
hash-bound copy。审计 serializer 只输出 hash 和允许的计量字段，不输出 structured result
正文。它不得包含 Key、Authorization header、完整敏感响应头或自由推理。

## T1.3c 冻结边界

- API：`https://api.deepseek.com/chat/completions`。
- model：`deepseek-v4-flash`；thinking：`disabled`；非 streaming JSON object。
- 凭据：只由 `CredReadW` 读取固定 target `PKM/DeepSeek/shadow-v1`，不接受明文或自由
  target；Provider 不能继承宿主 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY` 或代理凭据。
- Generator 与 Reviewer 是两个独立 HTTP 请求。Reviewer sealed request 的输入只能是
  sealed evidence、Generator 已验证结构化结果和冻结 rubric。
- 两个请求都必须携带自哈希 D-009 binding：`TRANSPORT_SCHEMA_CONTRACT_ONLY`、
  `production_eligible=false`、`fallback_eligible=false`、`SAME_PROVIDER_SAME_MODEL`。
  这证明 fresh session，不代表独立供应商/模型事实验证，也不能复用 Terra/Sol synthetic
  policy 身份。
- 请求 body 不发送 `tools`；响应出现 `tool_calls`、`function_call`、thinking content 或
  未知结构字段时 fail-closed。
- 固定上限：`max_tokens=4096`、sealed input 250,000 bytes、HTTP request 400,000 bytes、
  response 1,000,000 bytes、connect 10 秒、应用层统一 deadline 30 秒；无凭据 resolver、
  TCP、TLS、HTTP 共用剩余时间。只使用 Windows ROOT store、固定 SNI/hostname verification，
  TLS 成功后才构造 Authorization；不使用环境代理/CA override、redirect、自动 retry、
  HTTP debug 或 ambient TLS key logging。它不替代生产 Job/process 级硬终止资格。
- 单次 invoke 最多一次 schema repair；共享 budget 对整个真实 smoke 最多允许 4 个网络
  请求，并导入本次资格此前已消耗的真实请求，不能因进程重启归零。budget 不足、
  credential 缺失或输入/schema 非法时网络调用次数为零。
- repair 仅允许首次单一 JSON object 的纯结构缺失/类型/边界错误；unknown field、坏 JSON、
  tool/function/reasoning、HTTP/timeout 与 Reviewer claim/evidence/hash 语义不一致都不可
  repair。repair 保留原角色完整安全 prompt，只追加冻结的窄 repair 指令；不包含第一次
  坏输出，并记录每次实际 prompt/request body hash。
- 401=`AUTH_REQUIRED`；402=`INSUFFICIENT_BALANCE`；429=`RATE_LIMITED`；5xx=
  `EXTERNAL_SERVICE_UNAVAILABLE`；timeout=`TIMEOUT`。不充值、不重试到其他模型/供应商。
- 任意失败都不能生成 accepted story、Markdown、Vault target 或成功 terminal。

## T1.3c 允许修改的 5 个文件

1. `docs/agentic/spec.md`
2. `docs/agentic/decisions.md`
3. `docs/agentic/goal.md`
4. `src/pkm_workflow/deepseek_provider.py`
5. `tests/test_v8_deepseek_provider.py`

不得纳入或修改其他现有 tracked/untracked 资产，不新增依赖，不删除、移动、重命名或
覆盖文件。

## T1.3c TDD 与验证

测试只通过公开 seam，先红后绿，覆盖 credential 缺失、固定请求参数、调用方不可覆盖、
HTTP/timeout 分类、严格 JSON/schema、tool/function 拒绝、fresh request ID、Reviewer
输入白名单、所有 assertive claim decision 完整性、secret redaction、budget 与 repair
上限、失败无发布产物。

目标测试、Ruff、Mypy 和 T1.3a 相关回归全绿后，使用真实 Credential Manager 凭据运行
一次 T1.3a synthetic fixture → Generator → 本地 schema/ReviewContract → Reviewer →
review completeness 的 shadow。真实产物只能 create-new 写入：

`D:/personal/obsidian_workflow-runtime/scratch/deepseek-shadow-{run_id}`

报告只记录请求数、两个 request ID、usage、latency、hash、terminal、脱敏错误、
tool_calls/schema-repair 标志。总请求不得超过 4。

完成前进行两轮独立对抗审查，覆盖凭据泄露、费用失控、同供应商相关错误、prompt
injection、schema repair 绕过、宿主环境继承和 shadow 误发布。P0/P1 必须修复；secret
scan 与 tracked delete/rename 必须为零。只提交上述 5 个任务文件，建议提交信息：
`feat: add temporary deepseek flash shadow provider`。

## T1.3c 完成定义

- 真实 Generator 与 Reviewer 均成功且 request ID 不同；所有 assertive claims 恰有一个
  Reviewer decision，并通过现有 `ReviewContract` 绑定。
- 真实请求总数 ≤4；没有 tool call、Key/Authorization 泄漏、schema repair 绕过、
  生产发布或 Vault/Task 副作用。
- 两轮独立审查无剩余 P0/P1；测试/静态/回归/secret/delete gates 全绿。
- T1.3c 结束后 Provider 保留为不可自动调用的 shadow 审计资产；T1.3b 与生产仍 BLOCKED。

## T1.3c 完成证据（2026-08-20）

- 最终真实链路产物为
  `D:/personal/obsidian_workflow-runtime/scratch/deepseek-shadow-5e323a4f2da344a9be348cf129b01c64/shadow-report.json`，
  SHA-256 为
  `sha256:cf7c19fa9241d571f231cadf0a3b00a0887fb5a1340e0ac06dec57a9cf5876b5`。
  Generator 与 Reviewer 均为 `DIRECT_FIXED_HTTPS + WINDOWS_CREDENTIAL_MANAGER`、首次
  `VALIDATED`、各 1 个请求、无 schema repair、无 tool call；16 个 assertive claim、
  requirement 与 decision 的 ID set/hash 和 binding set/hash 全等，两个逻辑 request ID 与
  provider envelope ID hash 均不同。
- 本资格累计真实请求严格为 4/4：preliminary 2 次、最终 Generator/Reviewer 各 1 次，
  final artifact 记录 `prior_used=2`、`current_dispatched=2`、`cumulative_used=4`、
  `remaining=0`。preliminary 产物保留并标记 retired：
  `D:/personal/obsidian_workflow-runtime/scratch/deepseek-shadow-ad4a738afc0d492687611988e43eec08/shadow-report.json`，
  SHA-256 为
  `sha256:05ce5f07210e688c4c66a61f7f3aafa288a2d5a37c4e0800ad9fe2e0cf4f9a2a`。
- 最终调用后发现并修复了一个仅位于 schema 已失败分支的 nested unknown-field repair
  分类漏洞。修复把失败路径单调收紧为不可 repair；真实两次首次成功路径不会调用该
  classifier。修复前 5 文件 byte snapshot、精确 delta 与离线继承证明 create-new 保存在
  `D:/personal/obsidian_workflow-runtime/scratch/deepseek-shadow-security-delta-ae83f5624e234836918609296012156e/`；
  不追加 API 请求，不把修复前代码冒充最终代码。
- `tests/test_v8_deepseek_provider.py` 共 132 项通过，目标模块 line+branch 覆盖率 90.49%；
  T1.1→T1.3c 六模块 322 项通过；排除 7 个明确 retired legacy node 的生产测试门通过，
  `src/pkm_workflow` 总 line+branch 覆盖率 93.37%。commit-tree Ruff、format check 与 CI
  等价 Mypy（32 个 source files）均通过。
- 最终报告是 canonical JSON，仅含脱敏 hash/计量/状态；未包含 raw body、message、
  structured result 正文、Authorization 或 key。Provider 未接入 CLI/orchestrator/renderer/
  publisher/Home/Status/Task，未写 Vault；T1.3b、fallback 与生产 activation 继续
  `BLOCKED_PREQUALIFICATION`。
- 已知非阻断残余仅限 shadow TCB：secret-echo 拒绝路径仍可保留不可逆 response/ID hash，
  以及 TLS 成功后极罕见本地异常可能延迟 socket 回收。它们不暴露明文、不改变 fail-closed
  结果，也不解除生产所需 Job/process 隔离与真实系统资格门。

---

## 历史 T1.3a Goal 归档（非当前可执行 Goal）

## 先读

1. `AGENTS.md`
2. `docs/agentic/spec.md` 的 2.2、2.3、3.2 节
3. `docs/agentic/architecture.md` 的“数据流与证明”“Codex 调用策略”
4. `docs/agentic/decisions.md` 的 D-002、D-004、D-006、D-008
5. `src/pkm_workflow/provenance.py`
6. `src/pkm_workflow/source_governance.py`
7. `src/pkm_workflow/fetch_service.py`
8. `src/pkm_workflow/evidence.py`

## Goal

实现一个纯确定性的 curation/review contract，把 T1.1/T1.2 的完整、真实 proof
closure 转换为稳定 event clusters、模型安全 evidence view、可解释排序、排除记录和
review requirements。

正常 seam 不能在不失败的情况下接受伪造 Coverage、遗漏上游对象、跨窗口 evidence、
错误 UserContext、任意 policy、自报“无需 review”、候选静默截断或把 feed item URL
伪装成已验证原始链接。

本任务不调用模型。它使用 synthetic `CurationPolicyGeneration` 和 synthetic
Generator draft 验证未来 T1.3b 所需的生产 contract；synthetic generation 永远没有
live qualification 资格。

## 公开 seam

```python
result = CurationEngine().curate(
    source_policy=policy,
    snapshot=snapshot,
    fetch_batch=batch,
    evidence_package=package,
    coverage_proof=proof,
    claimed_coverage=coverage,
    user_context_json=user_context_bytes,
    curation_policy=curation_policy,
)

requirements = ReviewContract().derive(
    curation=result,
    draft=synthetic_generator_draft,
)
```

公开对象至少包括：

- `UserContextContract`
- `CurationPolicyGeneration`
- `CurationExclusion`
- `EventCluster`
- `CurationResult`
- `ModelEvidenceItem`
- `SourceExcerpt`
- `AssertiveClaim`
- `ReviewRequirement`

所有 canonical digest 统一使用 `sha256:<64 lowercase hex>`。

## 必须实现

### 1. 完整 proof closure

按以下顺序使用生产验证器，不复制第二套弱校验：

1. `fetch_batch.validate(source_policy, snapshot)`
2. `evidence_package.validate(source_policy, snapshot, fetch_batch)`
3. `coverage_proof.validate()`
4. proof 内的 policy、snapshot、observation set 必须分别与传入对象及 evidence package
   全等。
5. `CoverageEvaluator().evaluate(coverage_proof)` 现场重算，结果必须与
   `claimed_coverage` 全等，不能只比较 hash。
6. registry、source policy、snapshot、fetch batch、observation、coverage proof/result
   和 evidence registry hashes 全部进入 curation payload/hash。

Coverage 不足时返回 `COVERAGE_INSUFFICIENT` 且 model candidates 为空；本任务不创建
no-news，也不把失败 endpoint 从 denominator 移除。

### 2. UserContext 与 curation policy

UserContext canonical JSON 算法固定为：

```python
json.dumps(
    parsed_object,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

当前用户批准 contract 的 expected digest 是：

`sha256:ce307d029565fe20659ec9e43561d949ac1ec7d7ec67720bad25c7ad73460e3f`

只允许对显式提供的 JSON bytes 做 schema/字段验证和 hash 重算；不得接收任意文件路径，
不得扫描 Vault。真实只读资格核对只读取
`D:\personal\obsidian_workflow\UserContextContract-v7.3.json` 这一精确文件。

`CurationPolicyGeneration` 必须 canonical 化并绑定：

- policy state（本任务只能是 `SYNTHETIC_QUALIFICATION`）
- expected UserContext hash
- 上海窗口规则与 event-time precedence
- title normalization、clustering algorithm/version 和 72 小时阈值
- profile topic matching 规则
- evidence-role 完整顺序
- cluster 排序键与 cap=24
- `POLICY_OVERFLOW` 行为
- model names、prompt/schema hashes 和 repair budget 的 synthetic placeholders
- story/claim 上限和 all-assertive-review 规则
- policy generation hash

每个 active source 的 `evidence_role` 必须存在于 policy 的完整 role ordering；未知 role
不得被静默放到末尾。T1.3b 冻结真实 prompt/schema/model/CLI 后必须创建新的 live
generation，禁止复用本任务的 synthetic generation。

### 3. 时间资格与完整记账

对每个 evidence item：

1. 若 `updated_at` 在 snapshot 的上海窗口
   `[window_start, cutoff_at)`，event time 取 `updated_at`。
2. 否则若 `published_at` 在窗口，取 `published_at`。
3. 否则排除，reason 必须是 `MISSING_EVENT_TIME` 或
   `EVENT_TIME_OUTSIDE_WINDOW`。

每个 evidence ID 必须恰好出现一次：属于一个 cluster，或属于一个 exclusion。不得因
去重、cap、来源、topic 不匹配而消失。

### 4. 跨 origin 无损 event clusters

title normalization 固定为 Unicode NFKC、casefold、非字母数字字符替换为空格并折叠
空白。候选按 `evidence_id` 排序后，用稳定 pair order 与 union-find 聚类；满足任一
条件即可合并：

- 相同非空 `item_url`；
- 相同且 `content_text` 非空的 `content_hash`；
- 相同 normalized title；
- event time 相差不超过 72 小时，且 title token similarity ≥85%。

Latin title token 是空白分词后的集合；含 CJK 的 title token 是去空白后所有重叠
3-character grams（不足 3 字时用整个 title）。similarity 使用整数 Jaccard：
`100 * intersection >= 85 * union`，不使用 float。

cluster 必须保留所有成员 evidence、source、origin group 和 locator，不能只留代表项。
启发式误合并不得导致数据丢失；未来 Generator 可以把一个 cluster 拆成多个 story。

### 5. 可解释排序与 overflow

只允许以下稳定排序键，按顺序比较：

1. UserContext `priorities[*].topics[*]` 在 normalized title/content 中的精确
   casefold substring 命中，priority rank 越小越前。
2. curation policy 中 evidence-role rank 的最佳值。
3. cluster 最新 event time 降序。
4. `cluster_hash` 升序。

profile 命中必须生成 `PROFILE_TOPIC_MATCH` 和精确字段路径（例如
`priorities[0].topics[2]`）。没有命中时不得获得“对你重要”的排序加成，只能标为
`GENERAL_POSSIBLE_IMPACT`。

cap 按 cluster 计数，固定为 24。超过 24 时：

- disposition 为非瞬态 `POLICY_OVERFLOW`；
- model candidates 为空；
- 完整有序 clusters、数量和 reason 保留在 audit payload；
- 相同 policy/input 不进行自动重试；
- 只有新 policy generation 或输入窗口变化后才可再次尝试。

不得截取前 24 后继续模型阶段。

### 6. 模型安全 evidence view

`ModelEvidenceItem` 只能从已验证 EvidenceItem 确定性派生，包含 evidence/source/
origin identity、title、feed content、event time 和 proof hashes。

`item_url` 不进入模型可引用 evidence 字段；它只在 audit sidecar 中以
`UNVERIFIED_LOCATOR` 保存。T1.3 不得使用 “canonical”“article 正文已验证”“原始链接”
或同义标签。

feed title/content 只能支持其明确包含的事实。需要打开 article 才能确认的机制、价格、
能力、安全、availability 或政策 claim 必须由后续模型 abstain，不能靠模型常识补齐。

### 7. Review contract

T1.3 qualification 阶段采用最保守规则：

- 所有可能进入 story 的 assertive claim 都产生 `ReviewRequirement(required=True)`；
  不信任 Generator 自报的 fact class、risk 或“是否影响总体结论”。
- Generator draft 最多 8 stories、每 story 最多 4 assertive claims、总计最多
  24 assertive claims；超限为 `SCHEMA_INVALID`。
- 未审内容只能使用独立 `SourceExcerpt` 类型，并且必须是单一 evidence title/content
  的 normalized 精确 span。
- `SourceExcerpt` 不得出现在 title、Top、ranking、conclusion、recommendation、
  impact 或任何最终事实字段。
- 每个 evidence ref 必须存在于 curation result；任意伪造、遗漏、跨 cluster 引用或
  同一 claim 引用不完整都失败。
- Review input 绑定 source/coverage/evidence/UserContext/curation policy 与 draft
  hashes。

本任务只派生 review requirements，不产生伪造 ReviewDecision 或 accepted story。

## 非目标

- 不调用 Codex，不下载/安装/升级 CLI，不登录或复制认证缓存。
- 不访问互联网，不执行真实 Generator/Reviewer，不做 schema repair。
- 不实现 article 正文抓取、canonical/link verification 或最终日报。
- 不把产物计入 M3 内容质量、用户注意力或原始证据验收。
- 不修改 T1.1/T1.2 seam、旧 `review_policy_v73.py`、renderer、orchestrator、CLI、
  publisher、Task、native、私有仓或 Vault。
- 不新增依赖，不写 runtime，不启用旧 main/UI/fetcher。

## 允许修改的文件

实现代码与测试最多只修改以下 5 个文件：

1. `src/pkm_workflow/curation.py`（新）
2. `src/pkm_workflow/review_contract.py`（新）
3. `tests/test_v8_curation.py`（新）
4. `tests/test_v8_review_contract.py`（新）
5. `tests/fixtures/v8_curation_manifest.json`（新）

全部验证通过后，允许额外只更新 `docs/agentic/tasks.md` 的 T1.3a 状态/证据与本文件
状态；不授权修改其他生产模块、私有仓或 Vault。

## TDD 顺序

1. 真实 T1.1/T1.2 closure 成功；伪造/遗漏/重算任一层失败。
2. Coverage 现场重算不等于 claimed 时失败；Coverage 不足不产生 candidates。
3. UserContext canonicalization 稳定；错字段、错 hash、Vault path 输入失败。
4. source role 未在完整 ordering 中失败；synthetic/live generation 不可混用。
5. event-time precedence、边界、缺失与窗口外 reason。
6. URL/content/title exact clusters；跨 origin 成员全部保留。
7. 72 小时与 Latin/CJK Jaccard 边界；输入重排 hash 不变。
8. 每个 evidence 恰好 cluster/exclusion 二选一；mutation 不能制造 omission。
9. profile topic hit 产生精确 field path；无 hit 无个性化加分。
10. role/freshness/hash tie-break 字节级稳定。
11. 24 clusters 成功；25 返回非瞬态 `POLICY_OVERFLOW` 且不截断。
12. ModelEvidenceItem 不含 URL/Markdown/URI 字段；locator 只在 audit sidecar。
13. 所有 assertive claims 均 review-required；自报低风险不能降级。
14. 8/4/24 边界；超限 schema invalid。
15. exact source excerpt 成功；伪 span、伪 evidence、excerpt 注入结论字段失败。
16. 每个 proof/context/policy/evidence binding 的 mutation 都由生产 seam 杀死。

禁止用 hardcoded final shortlist、test-only branch 或直接复制 expected output 通过测试。

## 验证命令

```powershell
cd D:\Agent_programs\obsidian_workflow_open
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_v8_curation.py tests\test_v8_review_contract.py -q
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_v73_source_import.py tests\test_v73_source_governance.py tests\test_v8_source_proof.py tests\test_v8_fetch_service.py tests\test_v8_evidence.py -q
.\.venv\Scripts\python.exe -m ruff check src\pkm_workflow\curation.py src\pkm_workflow\review_contract.py tests\test_v8_curation.py tests\test_v8_review_contract.py
.\.venv\Scripts\python.exe -m mypy src\pkm_workflow\curation.py src\pkm_workflow\review_contract.py
git diff --check
git diff --name-status --diff-filter=DR
```

目标测试、T1.1/T1.2 回归、Ruff、Mypy 必须通过；新生产模块 line+branch ≥90%，
关键 proof/omission/review-downgrade/overflow 分支 100%；tracked delete/rename 为空。

另做一次只读资格核对，证明精确 UserContext 文件按上述算法得到批准 digest；命令不得
输出 contract 正文或读取同目录其他文件。

## 完成定义

- 完整上游 closure 只能经生产验证器进入 curation，Coverage 不能由调用方伪造。
- 相同有效输入得到字节级稳定 clusters/order/hash，输入重排不改变结果。
- 跨 origin 聚类不丢 evidence；每个 item 都有唯一 cluster 或 exclusion 归宿。
- 个性化加成精确引用批准 contract 字段；一般影响不冒充个人相关性。
- overflow、Coverage 不足和所有 contract 失败均 fail-closed，不产生模型输入。
- 模型 evidence view 不暴露可引用 URL，不夸大 feed 摘要的证据能力。
- 所有 assertive claims 都生成 Reviewer requirement，Generator 不能自报降级。
- synthetic policy 明确不可用于 live qualification。
- 没有 Codex、网络、runtime、Vault、删除、移动、重命名或旧入口副作用。
- 只有全部条件满足才把 T1.3a 改为 `DONE`；T1.3b 仍保持
  `BLOCKED_PREQUALIFICATION`，不得自动认领。

## 完成证据（2026-08-18）

- 23 个目标行为测试通过；T1.1/T1.2 的 182 个回归测试通过。
- 新生产模块 line+branch 合计 93.51%；Ruff、Mypy 通过。
- 精确 UserContext 只读资格 hash 与本文件批准 digest 一致。
- 三轮独立对抗性代码审查后无 P0/P1；所有审查发现均先以失败测试复现，再修复。
- 未调用 Codex/网络，未写 runtime/Vault/私有仓，未删除、移动或重命名文件，未启用生产。

## 必须停止并汇报的情况

- 需要修改 T1.1/T1.2 production seam 才能验证 closure。
- 无法在 5 个实现/测试文件内表达公开 contract。
- 需要第三方依赖、网络、Codex、runtime、私有仓写入或 Vault 访问。
- event clustering 只能通过丢弃 evidence 才能满足 cap。
- 任一 assertive claim 可以绕过 review requirement。
- 测试要求放宽真实 hash/proof/window/overflow 规则才能通过。

遇到以上情况保持测试红或返回明确 blocker，先报告证据，不擅自扩大范围。
