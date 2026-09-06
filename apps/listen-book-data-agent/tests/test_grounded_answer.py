from datetime import date
from decimal import Decimal
import unittest

from app.services.grounded_answer_service import build_grounded_answer


class GroundedAnswerTest(unittest.TestCase):
    def test_single_row_answer_quotes_actual_values_and_time_range(self):
        answer = build_grounded_answer(
            sql="SELECT COUNT(*) AS 播放次数 FROM play_session LIMIT 500",
            rows=[{"播放次数": 123, "播放金额": Decimal("12.50")}],
            metric_infos=[{"name": "play_count"}],
            analysis_plan={
                "time_range": {
                    "label": "最近7天",
                    "start": "2026-07-10",
                    "end": "2026-07-16",
                }
            },
        )

        self.assertIn("play_count", answer.summary)
        self.assertIn("123", answer.summary)
        self.assertIn("12.50", answer.summary)
        self.assertIn("最近7天", answer.summary)
        self.assertEqual(answer.sql, "SELECT COUNT(*) AS 播放次数 FROM play_session LIMIT 500")

    def test_empty_result_does_not_invent_a_value(self):
        answer = build_grounded_answer(
            sql="SELECT id FROM audio_album LIMIT 500",
            rows=[],
            metric_infos=[],
            analysis_plan={"time_range": {}},
        )

        self.assertEqual(answer.row_count, 0)
        self.assertIn("未返回数据", answer.summary)
        self.assertNotIn("0 条", answer.summary)

    def test_multiple_rows_only_summarizes_returned_rows(self):
        rows = [
            {"日期": date(2026, 7, 10), "播放次数": 10},
            {"日期": date(2026, 7, 11), "播放次数": 12},
            {"日期": date(2026, 7, 12), "播放次数": 14},
            {"日期": date(2026, 7, 13), "播放次数": 16},
        ]
        answer = build_grounded_answer(
            sql="SELECT stat_date, play_count FROM ranking_item LIMIT 500",
            rows=rows,
            metric_infos=[{"name": "play_count"}],
            analysis_plan={"time_range": {}},
        )

        self.assertEqual(answer.row_count, 4)
        self.assertIn("共返回 4 行", answer.summary)
        self.assertIn("仅展示前 3 行摘要", answer.summary)
        self.assertNotIn("16", answer.summary)


if __name__ == "__main__":
    unittest.main()
