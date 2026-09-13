import ast
from pathlib import Path
import unittest

from integrations.data_engine.runtime import TOOL_NAMES


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "apps" / "data-agent-engine"


class ImportedDataEngineCompletenessTests(unittest.TestCase):
    def test_http_adapter_matches_every_native_mcp_tool(self) -> None:
        tree = ast.parse(
            (SOURCE / "backend" / "mcp_servers" / "server.py").read_text(encoding="utf-8")
        )
        native = set()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                function = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(function, ast.Attribute) and function.attr == "tool":
                    native.add(node.name)

        self.assertEqual(native, set(TOOL_NAMES))
        self.assertEqual(len(native), 20)

    def test_all_eight_dsh_skills_and_preset_are_present(self) -> None:
        preset = SOURCE / "dsh-side" / "agent-presets" / "data-agent"
        skills = {path.name for path in (preset / "skills").iterdir() if path.is_dir()}

        self.assertEqual(skills, {
            "explore-fallback",
            "modeling-etl",
            "modeling-workflow",
            "mql-authoring",
            "oag-retrieval",
            "plan-routing",
            "query-metric",
            "warehouse-standards",
        })
        self.assertTrue((preset / "preset.yml").is_file())
        self.assertTrue((preset / "agent.cordis.yml").is_file())


if __name__ == "__main__":
    unittest.main()
