import unittest
from datetime import date

from app.agent.analysis_plan import build_analysis_plan


class AnalysisPlanTest(unittest.TestCase):
    def test_ranking_plan_resolves_relative_period_and_top_n(self):
        plan = build_analysis_plan(
            "最近30天播放量最高的前10个专辑", reference_date=date(2026, 7, 16)
        )

        self.assertEqual(plan.intent, "ranking")
        self.assertEqual(plan.top_n, 10)
        self.assertEqual(plan.sort_direction, "desc")
        self.assertEqual(plan.time_range.start, "2026-06-17")
        self.assertEqual(plan.time_range.end, "2026-07-16")
        self.assertIn("播放量", plan.metric_hints)
        self.assertIn("专辑", plan.dimensions)

    def test_comparison_plan_preserves_month_grain(self):
        plan = build_analysis_plan("按月看订单金额同比", reference_date=date(2026, 7, 16))

        self.assertEqual(plan.intent, "compare")
        self.assertEqual(plan.comparison, "year_over_year")
        self.assertEqual(plan.time_grain, "month")

    def test_complex_playback_question_preserves_metrics_and_filters(self):
        plan = build_analysis_plan(
            "北京地区男性黄金会员的播放总次数且玄幻和言情类有声书的平均播放时长差多少"
        )

        self.assertEqual(plan.intent, "compare")
        self.assertEqual(plan.comparison, "difference")
        self.assertIn("播放次数", plan.metric_hints)
        self.assertIn("平均播放时长", plan.metric_hints)
        labels = [item["label"] for item in plan.filter_requirements]
        self.assertIn("地区包含北京", labels)
        self.assertIn("性别为男性", labels)
        self.assertIn("会员等级为黄金会员（vip）", labels)
        required_columns = {
            column for item in plan.filter_requirements for column in item["columns"]
        }
        self.assertIn("user_profile.province", required_columns)
        self.assertIn("user_profile.gender", required_columns)
        self.assertIn("member_account.member_level", required_columns)
        self.assertIn("dim_audio_category.category_name", required_columns)

    def test_detail_business_statuses_become_required_filters(self):
        completed = build_analysis_plan("查看最近5条完播记录明细")
        published = build_analysis_plan("查看最近发布的5个专辑明细")

        self.assertIn(
            "play_session.play_status",
            completed.filter_requirements[0]["columns"],
        )
        self.assertEqual(completed.filter_requirements[0]["values"], ["completed"])
        self.assertIn(
            "audio_album.album_status",
            published.filter_requirements[0]["columns"],
        )
        self.assertEqual(published.filter_requirements[0]["values"], ["published"])


if __name__ == "__main__":
    unittest.main()
