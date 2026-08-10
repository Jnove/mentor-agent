"""知识库正式发布目录边界测试（纯标准库）。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.kb_paths import count_ignored_markdown, iter_published_markdown


def test_only_allowlisted_top_level_directories_are_published():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        expected = []
        for rel in ("通知/z.md", "FAQ/a.md", "政策/nested/b.md"):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rel, encoding="utf-8")
            expected.append(rel)
        for rel in ("staging/bad.md", "zju-welcome/raw.md", "unknown/new.md"):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rel, encoding="utf-8")

        actual = [path.relative_to(root).as_posix()
                  for path in iter_published_markdown(root)]
        assert actual == sorted(expected)
        assert count_ignored_markdown(root) == 3


if __name__ == "__main__":
    test_only_allowlisted_top_level_directories_are_published()
    print("ok  test_only_allowlisted_top_level_directories_are_published")
