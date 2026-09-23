# HTTP API 错误响应约定

Pulse 对外 HTTP API 的 `HTTPException.detail`（及等价 JSON 字段）按**调用方**区分语言与形态，避免门户与机器客户端混用同一套文案。

## 门户 API（`/api/auth/*`、`/api/v2/*`）

**受众**：管理后台 SPA、人工操作的浏览器客户端。

| 项 | 约定 |
|----|------|
| 语言 | **简体中文**（与 UI 一致） |
| `detail` | 可直接展示给管理员；说明「缺什么权限 / 哪条业务规则不满足」 |
| 状态码 | 常规 REST：`401` 未登录、`403` 无权限、`404` 资源不存在、`400` 参数或业务校验失败 |

示例：`缺少权限: accounts.read`、`仅主使用人或管理员可绑定 API Key`。

新增或修改门户路由时，请保持 `detail` 为中文短句，勿仅返回英文内部代号。

## 内部 Service API（`/api/internal/v1/*`）

**受众**：Go MITM Proxy、Assistant、Channel 镜像等**服务间**调用；配置 `PULSE_INTERNAL_SERVICE_TOKEN` / `PULSE_INTERNAL_TOKEN`。

| 项 | 约定 |
|----|------|
| 语言 | **英文**（日志、跨语言客户端、自动化测试） |
| `detail` | 简短、稳定；优先固定短语（如 `Unauthorized`、`Invocation not found`） |
| 鉴权失败 | `401` + `Unauthorized`；未配置 token 时应用层应 **fail closed**（见 `SECURITY.md`） |

机器客户端不应依赖中文 `detail` 做分支逻辑；若未来需要可解析错误，应引入明确的 `code` 字段（单独 ADR/issue），而非翻译现有中文文案。

## Assistant 镜像 API（`/api/assistant/v1/*`）

经 Pulse 门户代理或直连 Assistant 进程时，错误语言与 Assistant 模块现有习惯一致（多为英文 `detail`）。门户仅做透传或包装时，不要擅自把内部英文改成中文。

## 实现提示

- 门户权限：`pulse/web/app.py` 中 `require_capability` 使用 ``f"缺少权限: {capability}"``。
- 内部路由：见 `pulse/web/internal_*_api.py`；新增端点请沿用英文 `detail`。
- 业务异常转 HTTP：门户层可 `HTTPException(status_code=400, detail=str(exc))` 当 `exc` 已是中文；内部层应对调用方返回英文或结构化错误。

## 测试

- 门户：断言 `response.json()["detail"]` 含预期中文或权限 capability 名。
- 内部：断言 `detail == "Unauthorized"` 等固定英文字符串。
