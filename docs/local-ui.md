# Local UI Guide

`ui_server.py` + `ui/` 提供了一个**本地单用户控制面板**。  
它不替代 CLI，而是给现有工作流加上：

- quick run
- source editing
- output / vault settings
- live logs
- doctor trigger

## Design Boundaries

- 只监听 `127.0.0.1`
- 不做认证、多租户或公网部署
- 不引入数据库、任务队列或 WebSocket
- 仍然以 `main.py` 作为真实工作流入口

## Start

先安装依赖：

```bash
pip install -r requirements.txt
npm install
npm --prefix ui install
```

然后启动：

```bash
npm run dev:full
```

或者分开启动：

```bash
npm run dev:api
npm run dev:ui
```

## Pages

### Dashboard

- 当前运行状态
- Quick Run
- 最近输出文件
- Feed health 摘要
- 实时日志流

### Sources

- 查看和编辑 RSS 源
- 查看和编辑 YouTube channel
- 启用 / 禁用 source

### Output

- `write_mode`
- `vault_path`
- `obsidian_api_base`
- `obsidian_api_key`
- digest 关键 limits
- `enable_llm_copy`

### Logs

- 历史日志
- SSE 实时日志流

### Settings

- 触发 doctor
- 查看诊断输出

## Runtime Model

控制面板本身不重写工作流逻辑：

1. UI 调 `ui_server.py`
2. `ui_server.py` 调用 `main.py`
3. 日志写入 `fetch.log`
4. UI 通过 `/api/status` 和 `/api/logs/stream` 获取运行态

这意味着：

- CLI 和 UI 结果保持同一条主链
- skill wrapper 不需要重新适配另一套业务逻辑
- 文档、测试和行为边界更容易维持一致

## Recommended Gate

在提交控制面板相关改动前，至少跑这些：

```bash
python -m pytest -q
npm run build
npm run test:ui
python main.py --doctor --doctor-skip-network
python main.py --dry-run
```
