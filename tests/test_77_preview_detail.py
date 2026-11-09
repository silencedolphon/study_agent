"""预习难点、考点使用具体细化标准，旧的字符串结构继续可读。"""
import unittest
from pathlib import Path

from core import config, preview


class TestPreviewDetail(unittest.TestCase):
    def test_prompt_and_summary_preserve_detailed_points(self):
        path = Path(config.ROOT) / 'logs' / 'knowledge' / preview.PREVIEW_PROMPT_NAME
        prompt = preview.load_preview_prompts(path)['sys']
        self.assertIn('5～10 条', prompt)
        self.assertIn('关键判别依据', prompt)
        self.assertIn('不声称真题或必考', prompt)
        raw = {'difficulties': [f'概念 {i}：容易混淆，依据条件判断' for i in range(7)],
               'exam_focus': [f'考查 {i}：解释机制并说明判断依据' for i in range(6)]}
        summary = preview._build_summary_v2(raw, '教材内容')
        self.assertEqual(summary['difficulties'], raw['difficulties'])
        self.assertEqual(summary['exam_focus'], raw['exam_focus'])
        self.assertEqual(preview._build_summary_v2({'difficulties': ['旧格式']}, '')['difficulties'], ['旧格式'])
