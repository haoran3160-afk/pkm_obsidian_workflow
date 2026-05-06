# Local API

`ui_server.py` 暴露的是**本地控制面板 API**，不是公网服务接口。  
默认监听：`http://127.0.0.1:8000`

## Endpoints

## `GET /api/status`

返回运行状态、配置摘要、最近输出和最近一次 feed health。

示例响应：

```json
{
  "run": {
    "active": false,
    "mode": "",
    "started_at": "",
    "finished_at": "",
    "return_code": null,
    "last_error": "",
    "event_count": 0
  },
  "config_summary": {
    "rss_count": 15,
    "youtube_count": 9,
    "write_mode": "disk",
    "vault_path": "D:/personal/Obsidian"
  }
}
```

## `POST /api/run`

触发工作流运行。

请求体：

```json
{
  "mode": "dry-run"
}
```

支持的 `mode`：

- `digest`
- `raw`
- `dry-run`
- `test`

## `POST /api/doctor`

触发 doctor。

请求体：

```json
{
  "skip_network": true
}
```

## `GET /api/logs/history`

返回：

- `fetch.log` 的最近历史
- 当前内存态运行事件

## `GET /api/logs/stream`

SSE 实时事件流。

消费方式示例：

```js
const stream = new EventSource("/api/logs/stream");
stream.onmessage = (event) => {
  console.log(JSON.parse(event.data));
};
```

## `GET /api/config/sources`

读取当前 sources 配置。

## `PUT /api/config/sources`

更新 RSS / YouTube sources。  
请求会先走 Pydantic 校验，再原子写回 `pkm_config.json`。

## `GET /api/config/output`

读取输出相关配置，包括：

- `write_mode`
- `vault_path`
- `obsidian_api_base`
- `obsidian_api_key`
- digest 相关 limits
- LLM copy 开关与模型配置

## `PUT /api/config/output`

更新输出相关配置。  
该接口会同时更新：

- `pkm_config.json`
- `.env`

## `POST /api/validate/vault`

检查 Vault 路径：

```json
{
  "vault_path": "D:/personal/Obsidian"
}
```

响应：

```json
{
  "exists": true,
  "is_dir": true,
  "writable": true
}
```

## Notes

- 这是本地开发 / 本地运维接口，不是稳定外部 SaaS API
- 当前没有认证层，默认只应在本机回环地址下使用
- 行为基于当前 CLI 工作流，主逻辑仍在 `main.py`
