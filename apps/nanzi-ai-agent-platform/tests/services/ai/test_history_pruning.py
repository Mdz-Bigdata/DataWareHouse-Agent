import pytest
from app.services.ai.executors.common import (
    _clean_assistant_text,
    _compress_markdown_tables,
    convert_history_to_messages,
)
from app.services.ai.runtime.agentscope.compat import AIMessage, HumanMessage

def test_clean_assistant_text_basic():
    # 1. 验证剥离 function_calls
    raw = "这里是最终总结。\n<function_calls>\n<invoke name=\"execute_sql_query\"></invoke>\n</function_calls>"
    cleaned = _clean_assistant_text(raw, strip_thought=False)
    assert cleaned == "这里是最终总结。"

    # 2. 验证剥离未闭合的 function_calls (兜底)
    raw_unclosed = "最终总结。\n<function_calls>\n<invoke..."
    cleaned_unclosed = _clean_assistant_text(raw_unclosed, strip_thought=False)
    assert cleaned_unclosed == "最终总结。"

def test_clean_assistant_text_with_thought():
    # 验证不剥离 thought (strip_thought=False)
    raw = "一些前置回复。\n<thought>我想生成SQL计划...</thought>\n<function_calls>...</function_calls>\n最终回答。"
    cleaned_keep = _clean_assistant_text(raw, strip_thought=False)
    assert "<thought>我想生成SQL计划...</thought>" in cleaned_keep
    assert "<function_calls>" not in cleaned_keep

    # 验证剥离 thought (strip_thought=True)
    cleaned_strip = _clean_assistant_text(raw, strip_thought=True)
    assert "<thought>" not in cleaned_strip
    assert "最终回答。" in cleaned_strip
    assert "一些前置回复。" in cleaned_strip

    # 验证 think 标签剥离
    raw_think = "正常话语。<think>思考链内容</think>最终回答。"
    cleaned_think = _clean_assistant_text(raw_think, strip_thought=True)
    assert "<think>" not in cleaned_think
    assert "思考链内容" not in cleaned_think
    assert "正常话语。" in cleaned_think
    assert "最终回答。" in cleaned_think

def test_clean_assistant_text_chart_block():
    # 验证剥离 chart code block
    raw = "我们把这个数据可视化如下：\n```chart\n{\n  \"type\": \"bar\",\n  \"data\": []\n}\n```\n这是分析报告。"
    cleaned = _clean_assistant_text(raw, strip_thought=False)
    assert "```chart" not in cleaned
    assert " bar " not in cleaned
    assert "我们把这个数据可视化如下：" in cleaned
    assert "这是分析报告。" in cleaned

def test_compress_markdown_tables():
    # 1. 验证小于等于 5 行的表格不被压缩
    short_table = (
        "| ID | 姓名 | 年龄 |\n"
        "| --- | --- | --- |\n"
        "| 1 | 张三 | 18 |\n"
        "| 2 | 李四 | 20 |"
    )
    res_short = _compress_markdown_tables(short_table)
    assert res_short == short_table

    # 2. 验证长表格被截断（保留前3行数据，后续拼接省略标注）
    long_table = (
        "这里是表格数据：\n"
        "| 省份 | 销售额 | 订单数 |\n"
        "| --- | --- | --- |\n"
        "| 上海 | 100 | 10 |\n"
        "| 北京 | 90 | 9 |\n"
        "| 广东 | 80 | 8 |\n"
        "| 浙江 | 70 | 7 |\n"
        "| 江苏 | 60 | 6 |\n"
        "这里是尾部文字。"
    )
    res_long = _compress_markdown_tables(long_table)
    lines = res_long.splitlines()
    
    # 检查是否包含表头、分隔线及前 3 行数据
    assert "| 上海 | 100 | 10 |" in lines
    assert "| 北京 | 90 | 9 |" in lines
    assert "| 广东 | 80 | 8 |"
    # 检查第 4、5 行是否被过滤并替换为占位符
    assert "| 浙江 | 70 | 7 |" not in lines
    assert "| 江苏 | 60 | 6 |" not in lines
    
    # 检查占位符是否生成
    placeholder_line = next((l for l in lines if "[此处省略历史表格明细" in l), None)
    assert placeholder_line is not None
    assert placeholder_line.startswith("|")
    assert placeholder_line.endswith("|")
    assert "省略历史表格明细 2 行" in placeholder_line

def test_convert_history_to_messages_integration():
    # 构造历史对话
    history = [
        {
            "role": "user",
            "content": "查一下空调销售额 ---\n文件绝对路径: /data/1.csv",
        },
        {
            "role": "assistant",
            "content": (
                "<thought>第一步生成SQL</thought>\n"
                "<function_calls>\n<invoke name=\"exec_sql\"></invoke>\n</function_calls>\n"
                "| 城市 | 销售 |\n"
                "| --- | --- |\n"
                "| 上海 | 10 |\n"
                "| 北京 | 20 |\n"
                "这是最终总结。"
            )
        },
        {
            "role": "user",
            "content": "那冰箱呢",
        }
    ]
    
    # 测试 strip_thought=True 时的转换结果
    messages = convert_history_to_messages(history, strip_thought=True)
    assert len(messages) == 3
    
    # 1. 验证历史 User 消息已精简 (剥离 \n\n---\n\n)
    assert messages[0].content == "查一下空调销售额"
    
    # 2. 验证历史 Assistant 消息已精简
    assistant_content = messages[1].content
    assert "<thought>" not in assistant_content
    assert "<function_calls>" not in assistant_content
    assert "| 上海 | 10 |" in assistant_content
    assert "这是最终总结。" in assistant_content
    
    # 3. 验证最后一轮 User 保持原样或 XML 隔离 (未触发历史裁剪)
    assert "那冰箱呢" in messages[2].content
