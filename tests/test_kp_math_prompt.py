import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core import kp_build, textbook_kp


class MathPromptTest(unittest.TestCase):
    def test_deck_prompt_applies_math_instruction_without_modifying_file(self):
        content = ('<!-- PROMPT-SYS-BEGIN -->\n```text\nSYSTEM\n```\n<!-- PROMPT-SYS-END -->\n'
                   '<!-- PROMPT-USER-BEGIN -->\n```text\nUSER\n```\n<!-- PROMPT-USER-END -->\n')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'prompt.md'
            path.write_bytes(content.encode('utf-8'))
            result = kp_build.load_kp_prompts_v2(path)
            self.assertIn(kp_build.MATH_FORMAT_INSTRUCTION, result['sys'])
            self.assertEqual(path.read_text(encoding='utf-8'), content)
            self.assertEqual(result['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(result['chars'], len(result['sys']) + len(result['user']))

    def test_textbook_prompt_uses_json_safe_math_and_preserves_plain_text(self):
        self.assertIn(kp_build.MATH_FORMAT_INSTRUCTION, textbook_kp._SYSTEM)
        self.assertIn('普通文字、金额、代码和网址保持原样', textbook_kp._SYSTEM)
        example = json.loads(r'{"summary":"$\\frac{a}{b}$"}')
        self.assertEqual(example['summary'], r'$\frac{a}{b}$')


if __name__ == '__main__':
    unittest.main()
