from app.services.ai.runtime.agentscope.stream_reconcile import move_quick_suggestions_to_end


def test_move_quick_suggestions_to_end_after_chart():
    content = """### 💡 建议
---
- 关注回款波动

### 💬 您可能还想了解
---
- [🙋 查看趋势图](quick:查看2026年各月回款率趋势图)
- [🙋 对比季度](quick:对比2025与2026年各季度回款率)

```chart
{"title": {"text": "对比图"}, "series": []}
```

（* 数据来源：财务数据集）"""
    fixed = move_quick_suggestions_to_end(content)
    assert fixed.index("```chart") < fixed.index("您可能还想了解")
    assert fixed.strip().endswith("对比2025与2026年各季度回款率)")


def test_move_quick_suggestions_keeps_already_last():
    content = """### 📊 数据概览
---
| 指标 | 数值 |
| --- | ---: |
| 回款 | 100 |

```chart
{"series": []}
```

### 💬 您可能还想了解
---
- [🙋 继续分析](quick:继续分析回款结构)"""
    assert move_quick_suggestions_to_end(content) == content
