-- 初始化通用 API 工具数据
-- 包含了几个常用的公开 API 工具供测试使用

INSERT INTO sys_api_tools (id, name, description, method, url_template, headers, parameter_schema, is_active, created_at, updated_at)
VALUES
(
    UUID(),
    'get_current_weather',
    '获取指定城市的实时天气信息 (使用 wttr.in 服务)',
    'GET',
    'https://wttr.in/{city}?format=j1',
    '{}',
    '{
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称 (支持拼音或英文，如: Shanghai, Beijing)"
            }
        },
        "required": ["city"]
    }',
    1,
    NOW(),
    NOW()
),
(
    UUID(),
    'get_ip_info',
    '获取当前执行环境的 IP 地址信息',
    'GET',
    'https://httpbin.org/ip',
    '{}',
    '{
        "type": "object",
        "properties": {},
        "required": []
    }',
    1,
    NOW(),
    NOW()
),
(
    UUID(),
    'search_github_repos',
    '搜索 GitHub 上的开源项目',
    'GET',
    'https://api.github.com/search/repositories?q={query}&sort={sort}&order=desc&per_page=5',
    '{"Accept": "application/vnd.github.v3+json"}',
    '{
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词 (e.g. machine learning)"
            },
            "sort": {
                "type": "string",
                "description": "排序方式 (stars, forks, updated)",
                "default": "stars"
            }
        },
        "required": ["query"]
    }',
    1,
    NOW(),
    NOW()
),
(
    UUID(),
    'get_exchange_rate',
    '获取货币汇率 (使用 frankfurter.app)',
    'GET',
    'https://api.frankfurter.app/latest?from={from_currency}&to={to_currency}',
    '{}',
    '{
        "type": "object",
        "properties": {
            "from_currency": {
                "type": "string",
                "description": "源货币代码 (e.g. USD, EUR, CNY)",
                "default": "USD"
            },
            "to_currency": {
                "type": "string",
                "description": "目标货币代码 (e.g. CNY, JPY)",
                "default": "CNY"
            }
        },
        "required": ["from_currency", "to_currency"]
    }',
    1,
    NOW(),
    NOW()
);
