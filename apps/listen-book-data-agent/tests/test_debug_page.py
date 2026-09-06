import asyncio
import unittest

from app.api.routers.debug_router import debug_page


class DebugPageTest(unittest.TestCase):
    def test_debug_page_contains_query_controls_and_event_renderers(self):
        response = asyncio.run(debug_page())
        page = response.body.decode("utf-8")

        self.assertIn('id="query-input"', page)
        self.assertIn('id="timeline"', page)
        self.assertIn('id="sql-output"', page)
        self.assertIn('id="result-output"', page)
        self.assertIn('id="answer-output"', page)
        self.assertIn('type === "progress"', page)
        self.assertIn('type === "sql"', page)
        self.assertIn('type === "result"', page)
        self.assertIn('type === "answer"', page)
        self.assertIn('type === "error"', page)
        self.assertIn('type === "done"', page)
        self.assertNotIn("—", page)


if __name__ == "__main__":
    unittest.main()
