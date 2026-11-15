import json
from pathlib import Path
import re


def test_all_editor_block_types_have_local_help_and_valid_links():
    editor = Path(__file__).resolve().parents[1] / "src" / "macro_studio" / "editor"
    definitions = (editor / "editor.js").read_text(encoding="utf-8")
    expected = {
        f"ms_{name}" for name in re.findall(r'\b(?:hat|statement|expression)\("([a-z_]+)"', definitions)
    }
    catalog = json.loads((editor / "block_help.json").read_text(encoding="utf-8"))

    assert expected and set(catalog) == expected
    for block, article in catalog.items():
        assert article["title"].strip() and article["summary"].strip(), block
        assert set(article.get("related", ())) <= expected - {block}, block
