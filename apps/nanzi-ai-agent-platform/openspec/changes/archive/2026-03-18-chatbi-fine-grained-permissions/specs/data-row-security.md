# 规范：行级数据隔离 (Row-Level Security)

## 1. 核心目标
本规范定义了 ChatBI 在执行 SQL 查询时，如何将预定义的权限过滤条件注入到原始 SQL 的 `WHERE` 子句中，确保用户仅能访问其授权范围内的数据。

## 2. 注入逻辑准则 (Injection Rules)

### 2.1 注入位置
- 改写引擎 **MUST** 遍历 SQL AST 中的所有 `SELECT` 表达式。
- 对于每个 `SELECT` 表达式，引擎 **MUST** 检查其引用的表是否受权限规则约束。
- 注入条件 **MUST** 被追加到 `WHERE` 子句中，并使用 `AND` 操作符连接。

### 2.2 别名处理 (Alias Resolution)
- 引擎 **MUST** 能够识别表别名（Alias）。
- 注入的条件 **MUST** 使用对应的表别名作为前缀（如：`original_table AS t1` -> `t1.region_code = 'SH'`）。

### 2.3 占位符解析 (Placeholder Resolution)
- 注入字符串中包含的占位符（如 `{user.dept_id}`） **MUST** 在注入前被替换为当前用户的实际维度值。
- 如果占位符对应的维度值为空且策略未定义默认值，该查询 **MUST** 被拦截并报错。

### 2.4 SQL 注入防御
- 所有的动态注入值（特别是字符串） **MUST** 经过转义或参数化处理，防止恶意用户利用维度值进行二次 SQL 注入。

## 3. 验收标准 (Acceptance Criteria)

### 场景 A：单表查询
- **GIVEN**: 用户 A (region='SH') 提问 “查看所有服务器”
- **WHEN**: 模型生成 `SELECT * FROM sys_server_assets`
- **THEN**: 最终执行的 SQL **MUST** 包含 `WHERE region_code = 'SH'`

### 场景 B：多表 Join 查询
- **GIVEN**: 用户 B (dept_id=101) 提问 “服务器及其关联的机房”
- **WHEN**: 模型生成 `SELECT * FROM assets JOIN rooms ON assets.room_id = rooms.id`
- **THEN**: 如果 `assets` 表受限，最终 SQL **MUST** 包含 `assets.dept_id = 101`

### 场景 C：配置了 `1=1` (管理员例外)
- **GIVEN**: 用户 C (Admin)
- **WHEN**: 命中的策略规则为 `1=1`
- **THEN**: 最终 SQL **MUST** 保持原样或注入 `AND 1=1`（不影响结果集）。
