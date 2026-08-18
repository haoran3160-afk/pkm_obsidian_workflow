# Lightweight Architecture

## 设计原则

1. 一个进程能可靠完成的事，不拆成服务。
2. 数据边界靠小而明确的 API 和 create-new 语义，不靠庞大权限系统。
3. 模型只做语义判断，事实、路径、链接、状态和 Markdown 都由程序决定。
4. 先证明内容值得读，再增加低概率威胁的防御。
5. 保留历史代码但缩小生产 closure；不可达比“兼容所有旧入口”更安全。

## 目标组件

```text
src/pkm_workflow/
├── cli.py                # 唯一 CLI；参数解析与真实 exit code
├── orchestrator.py       # due 计算、全局锁、阶段编排
├── source_import.py      # 保留 baseline 原始身份
├── source_governance.py  # registry、generation、Coverage proof
├── fetch_service.py      # 固定来源网络抓取与限制
├── evidence.py           # 无网络解析、规范化与确定性校验
├── codex_runner.py       # 固定 argv、schema、认证/额度分类
├── review_policy.py      # claim/story/digest fail-closed 决策
├── renderer.py           # 纯确定性 Markdown renderer
├── publisher.py          # backing、os.link、receipt、reconcile
├── status.py             # create-new event/projection
└── home.py               # create-once Home/Bases payload
```

这是一张目标地图，不要求一次重命名或重写现有模块。每个任务在现有 seam 上做最小迁移；同一职责最终只能保留一套生产实现。

## 单进程编排

`orchestrator` 是一个短生命周期 Python 进程：获取全局锁、检查容量/资格、计算 due workflows，按顺序调用阶段函数，写结构化事件并退出。它不是 daemon，不开放端口，不接受远程请求。

阶段输出使用不可变 dataclass/JSON schema，并包含输入 hash。阶段只能读取上一步明确传入的文件，不得自行扫描仓库或 Vault。

## 数据流与证明

```text
Registry generation
    └─ canonical source snapshot hash
          └─ observations hash
                └─ coverage result hash
                      └─ evidence registry hash
                            └─ generator output hash
                                  └─ review decisions hash
                                        └─ rendered artifact hash
                                              └─ publish receipt
```

SHA-256 负责发现遗漏、错配和意外修改，不用于抵抗拥有本机写权限的攻击者。manifest 由确定性 serializer 创建；相同输入必须得到相同 hash。

因此 source proof 是 tamper-evident 内部一致性闭包，不是签名或恶意调用者认证。T1.1 验证 proof 相对于给定 baseline/policy 完整；后续 orchestrator 必须从用户批准的固定位置读取并核对预期 `baseline_id`/`source_policy_hash`。本地代码或批准配置已被恶意替换不在首发威胁模型内。

Source proof 使用单一强入口：

```python
result = CoverageEvaluator().evaluate(proof)
```

`proof` 必须包含完整 baseline identity、用户批准的 classification/policy generation、workflow snapshot、精确 endpoint terminal observations 和各层 hash。不得保留 `evaluate(entries, observations, window)` 之类允许调用方自行定义分母的兼容重载。

分类 generation 覆盖全部 baseline rows，但把 registry 完整性与 workflow 活跃集合分开：legacy-disabled rows 始终留在 registry/proof 中，却不能改善健康 numerator；enabled rows 必须由用户明确映射到 AI/Weekly/IELTS。未分类时只允许生成 `REGISTRY_CLASSIFICATION_REQUIRED`，不允许自动推荐结果进入生产 policy。

所有进入 canonical JSON 的权重使用整数 basis points。terminal observation set 必须与 snapshot 的 endpoint set 精确相等；网络或解析失败用显式 outcome 表达，不能靠省略记录表达。

## 网络边界

只有 `fetch_service` 和 Codex CLI 需要网络：

- `fetch_service` 只能访问 registry 枚举的 endpoint，并执行 SSRF、redirect、peer、MIME、大小和超时校验。
- parser、coverage、renderer、publisher 均不联网。
- Codex 只接收本次 run directory 中密封的 evidence/schema，内部工具全部关闭；它的网络只用于套餐服务，不用于网页搜索。
- 无需首发 WFP；若应用层限制在红队中可被真实绕过，才重新评估 OS 隔离。

## Codex 调用策略

先由确定性规则把大量 feed 收敛成少量值得考虑的 candidates，再使用模型：

- Generator：每个 workflow 一次为主，只处理候选，输出 claims/evidence IDs。
- Reviewer：T1.3 qualification 阶段审核所有可能进入 story 的 assertive claims；每份
  最多 8 stories、每 story 最多 4 claims、总计最多 24 assertive claims。未审内容
  只能是隔离 excerpt，不能进入用户结论。
- schema 修复最多一次；相同 evidence/policy 的已成功结果可复用。
- auth、quota、工具事件或 schema 不一致均停止发布。

T1.3a 先冻结并验证 curation/review contract，不调用外部模型；T1.3b 只有在固定原生
Codex CLI、专用套餐登录与 Windows sandbox 真实 preflight 通过后才执行。选择性审核
仍是长期额度优化方向，但不得依赖 Generator 自报的 risk/fact class；降低范围必须新增
decision，并用真实 holdout 证明确定性分类器不能降级受保护 claim。

T1.3b 直接启动官方 release 的原生 `codex.exe`，不经过 npm/PowerShell wrapper。
固定 `read-only`、approval never、ignore user config/rules、ephemeral、isolated non-git
cwd、schema 与 JSONL；Job Object 提供硬超时和进程树终止。preflight 冻结实际允许的
helper 拓扑与读取类别，并用仓库、Vault 和私有配置 canary 证明拒读。没有 ETW 时只
声明这些可观察边界通过，不扩写成“看见所有系统文件读取”。

## 发布边界

Vault publisher 只暴露枚举接口，例如：

```python
publish(workflow, content_date, artifact_kind, run_id, backing_path, expected_hash)
```

它不接受任意 target。内部通过固定映射得到父目录与 leaf，确认 resolved parent 位于精确白名单，确认 runtime/Vault 同一 NTFS volume，再使用 `os.link` create-new。

由于 hard link 共享 file identity，发布后用户编辑 target 也会修改 backing。因此“审计原件”不能与发布 backing 共用同一文件。若需要保留发布时内容，receipt 记录 hash，并另建独立 create-new audit copy；首发不需要 v7.3 A/B 事务协议。reconcile 以 target 当前 hash、receipt 和独立 audit hash 区分“发布中断”与“用户编辑”。

## 调度边界

一个 Task 使用多个 trigger，统一执行 `pkm run --due-only --cause task`。due 计算是 Python 纯函数，Task XML 不携带业务逻辑。`IgnoreNew` 是第一层并发控制，全局文件锁是第二层。

升级只替换固定安装位置中的已验 wheel/config，并保留版本与 hash 记录。首发不需要 PromotionLedger；失败时禁用唯一 Task，修复后由用户重新批准启用。

## 可信范围

首发信任：Windows 用户账户、NTFS、本机 Python 环境、Task Scheduler 和用户批准的配置。远程 feed 与模型输出不可信。

首发不防御：本地管理员、能修改代码/registry/runtime 的恶意进程、磁盘固件谎报 flush、多用户租户攻击。若这些假设改变，必须重新威胁建模，而不是偷偷给当前架构贴上企业级安全标签。

## 历史 native 代码

`native/` 保留为实验和未来候选，不进入当前 wheel、Task action、验收 denominator 或发布路径。不得为了让旧 native 测试通过而扩展它。重新启用需满足 `decisions.md` 的升级条件并获得用户批准。
