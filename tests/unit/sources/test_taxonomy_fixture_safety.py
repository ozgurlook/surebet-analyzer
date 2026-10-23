import json
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "relative_path",
    [
        "source_a/public_1x2_response.json",
        "wamp_feed/source_c_football_1x2.json",
        "wamp_feed/source_b_football_1x2.json",
    ],
)
def test_taxonomy_fixtures_contain_no_session_material(relative_path: str) -> None:
    path = Path("tests/fixtures/sources") / relative_path
    text = path.read_text(encoding="utf-8").lower()
    forbidden = ("authorization", "cookie", "set-cookie", "token", "sessionid", "jwt")
    assert all(name not in text for name in forbidden)
    assert len(json.loads(text)) > 0
