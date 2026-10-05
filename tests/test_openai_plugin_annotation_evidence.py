from __future__ import annotations

import re
import unittest
from pathlib import Path


class OpenAiPluginAnnotationEvidenceTests(unittest.TestCase):
    def test_every_public_tool_is_explicitly_classified_and_documented(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / 'control-plane' / 'cloudflare' / 'src' / 'mcp_http.ts').read_text(encoding='utf-8')
        evidence = (root / 'docs' / 'OPENAI_PLUGIN_ANNOTATION_EVIDENCE.md').read_text(encoding='utf-8')

        tools_start = source.index('const TOOLS: ToolSpec[] = [')
        tools_block = source[tools_start:source.index('];', tools_start)]
        tool_names = re.findall(r'name:\s*"([^"]+)"', tools_block)
        self.assertGreater(len(tool_names), 0)
        self.assertEqual(len(tool_names), len(set(tool_names)))

        tick = chr(96)
        for name in tool_names:
            with self.subTest(tool=name):
                self.assertIn('| ' + tick + name + tick + ' |', evidence)

        rows = re.findall(
            r'^\| `([^`]+)` \| (true|false) \| (true|false) \| (true|false) \|',
            evidence,
            re.MULTILINE,
        )
        self.assertEqual(set(tool_names), {row[0] for row in rows})
        self.assertIn('effectClassCount !== 1', source)
        self.assertIn('NON_DESTRUCTIVE_WRITE_TOOLS', source)


if __name__ == '__main__':
    unittest.main()
