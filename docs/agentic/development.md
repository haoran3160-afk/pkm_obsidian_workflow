# Development Workflow

## 工作单位

一次开发会话只完成 [tasks.md](tasks.md) 中一个任务。任务开始时写清：目标行为、明确非目标、最多 5 个主要文件、失败测试和验证命令。任务完成时提供代码、测试、证据和未解决风险；不能只报告“测试通过”。

## 当前安全命令

当前实现尚未打通 shadow engine，以下命令预期返回非零 `BLOCKED`：

```powershell
.\.venv\Scripts\python.exe scripts\run_workflow.py --doctor --json
.\.venv\Scripts\python.exe scripts\run_workflow.py --dry-run --workflow ai --json
```

输出中的 native/AppContainer/WFP blocker 是 v7.3 遗留诊断，不是当前架构要求。T1.5 只能在对应轻量资格有真实证据时替换它们，不得把旧 blocker 直接硬编码为绿色。

不要运行根目录 `main.py`、`ui_server.py`、旧 Task installer 或 `.agent/workflows` 的历史命令。

## 开发循环

1. 阅读本任务涉及的 canonical 文档和现有 seam。
2. 用行为测试复现缺口；测试必须在修复前失败。
3. 实现最小生产路径，不增加 test-only seam。
4. 运行最小相关测试，再运行静态检查。
5. 检查路径、来源集合、hash 和退出码的负向用例。
6. 更新 task evidence；若规格变化，先更新 spec/decision。

## Python 风格

边界对象使用 frozen dataclass、显式 Enum 和确定性 serializer；验证失败抛具体 `ValueError` 或领域错误，不用 truthy fallback 吞掉缺失字段。例如：

```python
@dataclass(frozen=True)
class ProofBinding:
    source_snapshot_hash: str
    observation_set_hash: str

    def __post_init__(self) -> None:
        if not all(HASH_PATTERN.fullmatch(value) for value in asdict(self).values()):
            raise ValueError("proof bindings require lowercase SHA-256 values")
```

canonical 数据不使用 float；集合在序列化前按稳定 identity 排序。public API 不保留会绕过新强契约的 legacy overload。

常用验证：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src/pkm_workflow
git diff --name-status --diff-filter=DR
```

不能把当前 26 个违反新边界的旧测试强行修成全绿。先把它们标为 legacy/retired，写出理由；新生产 closure 的行为测试必须全部通过。

## 测试策略

### 单元契约

- registry 完整性、stable ID、append-only generation。
- 精确时间窗口、Coverage denominator/proof。
- URL/redirect/SSRF/大小/MIME/超时。
- evidence mapping、数字/日期/版本/地区校验。
- review 的 claim/story/digest fail-closed。
- 路径枚举、create-new、冲突、hash 与 status latest。

### 集成

- 使用真实生产函数和本地 evidence fixtures 完成 fetch 后阶段。
- Codex qualification 使用真实固定 CLI/套餐会话，不把 synthetic binary 当生产证据。
- publisher 在 disposable 同盘 NTFS 目录测试 `os.link`、并发和每个关键点强杀。
- Task 在 disposable 名称空间安装、触发、补跑、禁用；不触碰真实 Vault。

### 内容评估

- 50–100 条历史回放，hidden labels 不随实现修改。
- 每个 accepted critical claim 人工核对原始证据。
- 评估 Top 注意力价值、重复、陈旧、无行动建议和来源独立性。
- 红队输入必须包含 prompt injection、伪 canonical、数字偷换、公司自述和重复 origin。

### 覆盖和 mutation

- 新/改生产模块 line+branch 目标 ≥90%，关键安全分支 100%。
- mutation 只覆盖 publisher、path policy、coverage proof 和 evidence binding；delete、overwrite、path escape、source omission、evidence mismatch mutants 必须被杀死。
- 不为了全仓 mutation 分数改私有函数或硬编码结果。

## 数据保护验证

- 所有故障注入都用 disposable Vault/VHD；真实 Vault 只做最终的受控资格与 shadow 发布。
- 实施前后对两个仓库与允许的 Vault 范围做 inventory；报告必须准确说明覆盖范围，不能把子集证明扩写成“全 Vault 零变化”。
- 当前工作树很脏，所有既有修改都视为用户资产；不 reset、stash、checkout 或覆盖不相关改动。
- 不删除测试产物、历史脚本或 native 实验；通过不可达、警告和 release closure 管理。

## Review 门

代码审查优先级：

1. 数据丢失、覆盖、越界读取和旧入口重新可达。
2. 来源遗漏、Coverage 伪造、证据错配和失败伪成功。
3. 并发、重试、跨日状态和用户编辑后的恢复。
4. 内容质量、重复、时效和个性化可追溯。
5. 可维护性、性能和格式。

每个 finding 必须指向具体文件/行和可复现行为。P0/P1 未闭合时不得进入下一 rollout 阶段。

## 文档规则

- 产品目标变化：改 `product.md`。
- 可观察行为变化：改 `spec.md`。
- 跨模块/依赖/信任边界变化：先写 `decisions.md`，再改 `architecture.md`。
- 实施进度：只改 `tasks.md`，不要复制一份新进度表。
- 历史资料不重写成当前事实；在顶部标为 legacy 并链接这里。
