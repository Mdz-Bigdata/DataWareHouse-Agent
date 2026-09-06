# Design: 通用 API 工具架构设计

## 1. 数据库设计 (Database Design)

我们需要一张表来存储工具的定义。

### Table: `sys_api_tools`

| Column | Type | Nullable | Comment |
| :--- | :--- | :--- | :--- |
| `id` | INT | PK | 自增主键 |
| `name` | VARCHAR(100) | NO | 工具唯一标识 (e.g., `search_weather`) |
| `display_name` | VARCHAR(100) | NO | 显示名称 (e.g., `天气查询`) |
| `description` | TEXT | NO | 给 LLM 看的工具描述 |
| `method` | VARCHAR(10) | NO | HTTP Method (GET, POST, PUT, DELETE) |
| `url_template` | VARCHAR(500) | NO | URL 模板 (支持 `{param}` 占位符) |
| `headers` | JSON | YES | 预定义 Headers (e.g., `{"Auth": "${API_KEY}"}`) |
| `parameter_schema` | JSON | NO | 参数定义的 JSON Schema (用于 LLM 结构化输出) |
| `is_active` | BOOLEAN | NO | 是否启用 (Default: 1) |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

**Example `parameter_schema`:**
```json
{
  "type": "object",
  "properties": {
    "city": {
      "type": "string",
      "description": "City name"
    }
  },
  "required": ["city"]
}
```

## 2. 后端架构 (Backend Architecture)

### 2.1 动态工具生成 (Dynamic Tool Generation)

我们将实现一个工厂类 `GenericApiToolFactory`。

**流程**：
1.  **Load**: 从 DB 读取所有 `is_active=1` 的记录。
2.  **Construct**: 对每条记录，创建一个 `LangChain.StructuredTool` 实例。
    *   `name`: `tool.name`
    *   `description`: `tool.description`
    *   `args_schema`: 动态生成的 Pydantic 模型 (基于 `parameter_schema`)。
    *   `func`: 绑定到一个通用的 `_execute_request` 函数，利用 `partial` 注入配置。

### 2.2 执行逻辑 (`_execute_request`)

这是一个通用的 Async 函数，处理 HTTP 请求：

```python
async def _execute_request(config: dict, **kwargs):
    # 1. URL 替换
    url = config['url_template'].format(**kwargs)
    
    # 2. Header 处理 (支持环境变量注入)
    headers = resolve_headers(config.get('headers'))
    
    # 3. Body/Query 处理
    # GET: kwargs -> Query Params
    # POST: kwargs -> JSON Body
    
    # 4. 发送请求 (httpx)
    response = await client.request(config['method'], url, headers=headers, ...)
    
    # 5. 结果截断/格式化 (防止 Token 爆炸)
    return format_response(response)
```

## 3. 前端设计 (Frontend Design)

### 3.1 界面布局
- **位置**：设置页 -> Tab 栏 -> [通用设置 | 模型管理 | **工具管理**]
- **列表页**：展示工具名称、描述、Method、状态开关。
- **编辑/新增模态框**：
    - **基本信息**：Name, Display Name, Description.
    - **请求配置**：Method (Dropdown), URL (Input).
    - **Headers**：Key-Value 动态增减列表。
    - **参数定义**：
        - 一个简易的表格编辑器：参数名 | 类型 | 必填 | 描述 | 操作。
        - 也可以提供一个 "Advanced Mode" 直接编辑 JSON Schema。

## 4. 安全性考量 (Security Considerations)
- **SSRF 防护**：通用 HTTP 工具容易成为 SSRF 攻击的跳板。建议限制 `url_template` 只能访问白名单域名，或禁止访问内网 IP (10.x, 192.168.x, 127.x)。
- **敏感信息**：Headers 中的 Token 不应在前端明文返回，或者在 API 响应中脱敏处理。
