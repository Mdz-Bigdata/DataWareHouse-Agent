import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parents[1]
DDL_PATH = PROJECT_ROOT / "tools" / "audio_data" / "sql" / "audio.sql"


def test_audio_ddl_contains_all_54_tables():
    ddl = DDL_PATH.read_text(encoding="utf-8")
    assert len(re.findall(r"(?im)^\s*CREATE\s+TABLE\s+", ddl)) == 54


@pytest.mark.integration
@pytest.mark.parametrize("profile", ["smoke", "full"])
def test_audio_data_profiles_acceptance(profile):
    if os.getenv("RUN_AUDIO_DATA_ACCEPTANCE") != "1":
        pytest.skip("set RUN_AUDIO_DATA_ACCEPTANCE=1 to run local MySQL acceptance")
    schema = f"audio_pytest_{profile}"
    command = [
        "uv",
        "run",
        "--group",
        "data",
        "python",
        "-m",
        "tools.audio_data.bootstrap",
        "--profile",
        profile,
        "--schema",
        schema,
        "--reset",
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
