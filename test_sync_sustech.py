import tempfile
from pathlib import Path
import unittest

from sync_sustech import Publisher


class TransformTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.source = root / "SUSTech"
        self.source.mkdir()
        (self.source / "课程").mkdir()
        (self.source / "课程" / "图 1#x.png").write_bytes(b"png")
        cfg = root / "sync-config.toml"
        cfg.write_text(f'''[sync]\nsource={str(self.source).replace(chr(92), '/')!r}\nremote_root="Public/SUSTech"\nsite_url="https://example.invalid"\ndav_path="/dav"\ncredential_file="cred"\nstate_file="state"\ncache_dir="cache"\nreport_file="report"\nlog_file="log"\nmarker_name=".marker"\nmarker_value="v1"\nold_roots=[]\nimage_extensions=[".png"]\n[exclude]\npatterns=[]\n''', encoding="utf-8")
        self.pub = Publisher(cfg)
        self.md = self.source / "课程" / "笔记.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_standard_markdown_and_url_encoding(self):
        out, images = self.pub.transform_markdown(self.md, "![](图 1#x.png)")
        self.assertEqual(out, "![](%E5%9B%BE%201%23x.png)")
        self.assertEqual(len(images), 1)

    def test_html_backslash(self):
        out, images = self.pub.transform_markdown(self.md, '<img src=".\\图 1#x.png">')
        self.assertIn('src="%E5%9B%BE%201%23x.png"', out)
        self.assertEqual(len(images), 1)

    def test_mdx_void_elements_are_self_closing(self):
        out, _ = self.pub.transform_markdown(
            self.md,
            '<img src="https://example.com/a.png"><br><br />',
        )
        self.assertEqual(
            out,
            '<img src="https://example.com/a.png" /><br /><br />',
        )

    def test_blank_quote_lines_become_hard_breaks(self):
        source = (
            "> question\n"
            "> \n"
            "> (A) first\n"
            ">\n"
            "> (B) second\n"
        )
        out, _ = self.pub.transform_markdown(self.md, source)
        self.assertEqual(out, "> question  \n> (A) first  \n> (B) second\n")

    def test_obsidian(self):
        out, images = self.pub.transform_markdown(self.md, "![[图 1#x.png|说明]]")
        self.assertEqual(out, "![说明](%E5%9B%BE%201%23x.png)")
        self.assertEqual(len(images), 1)

    def test_missing_warns_and_continues(self):
        out, images = self.pub.transform_markdown(self.md, "![](missing.png)")
        self.assertEqual(out, "![](missing.png)")
        self.assertFalse(images)
        self.assertEqual(self.pub.warnings[0]["reason"], "not found")

    def test_external_unchanged(self):
        out, images = self.pub.transform_markdown(self.md, "![](https://example.com/a.png)")
        self.assertEqual(out, "![](https://example.com/a.png)")
        self.assertFalse(images)


if __name__ == "__main__":
    unittest.main()
