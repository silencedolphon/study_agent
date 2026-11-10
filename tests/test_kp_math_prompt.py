import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            self.assertNotEqual(result['effective_sha256'], result['sha256'])
            self.assertFalse(kp_build._knowledge_prompt_compatible({}, result))
            self.assertTrue(kp_build._knowledge_prompt_compatible(
                {'prompts': {'knowledge': {'effective_sha256': result['effective_sha256']}}}, result))
            with patch.object(kp_build, 'MATH_FORMAT_INSTRUCTION', 'changed rule'):
                changed = kp_build.load_kp_prompts_v2(path)
            self.assertEqual(result['sha256'], changed['sha256'])
            self.assertNotEqual(result['effective_sha256'], changed['effective_sha256'])

    def test_old_result_is_not_overwritten_or_recomputed_on_resume(self):
        old = {'meta': {'prompts': {'knowledge': {'sha256': 'old'}}},
               'knowledge_points': [{'id': 'p1', 'summary': 'old result'}]}
        before = json.dumps(old, ensure_ascii=False, sort_keys=True)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'existing.json'
            output.write_text(before, encoding='utf-8')
            est = {'ok': True, 'pages_all': [1], 'title': 'deck', 'n_blocks': 1}
            current = {'sha256': 'old', 'effective_sha256': 'new', 'sys': '', 'user': '', 'chars': 0}
            with patch.object(kp_build, 'estimate_v2', return_value=est), \
                 patch.object(kp_build, 'load_kp_prompts_v2', return_value=current), \
                 patch.object(kp_build, 'load_struct_prompts', return_value={}), \
                 patch.object(kp_build, 'read_text_pages', return_value={'n_blocks': 1}), \
                 patch.object(kp_build.runtime_context, 'runtime_enabled', return_value=False), \
                 patch.object(kp_build, 'gen_structure_v2', side_effect=AssertionError('must not call model')):
                result = kp_build.build_v2(1, out_path=output, log=lambda *a: None)
            self.assertFalse(result['ok'])
            self.assertTrue(result['requires_force'])
            self.assertEqual(output.read_text(encoding='utf-8'), before)

    def test_textbook_prompt_uses_json_safe_math_and_preserves_plain_text(self):
        self.assertIn(kp_build.MATH_FORMAT_INSTRUCTION, textbook_kp._SYSTEM)
        self.assertIn('普通文字、金额、代码和网址保持原样', textbook_kp._SYSTEM)
        example = json.loads(r'{"summary":"$\\frac{a}{b}$"}')
        self.assertEqual(example['summary'], r'$\frac{a}{b}$')


if __name__ == '__main__':
    unittest.main()
