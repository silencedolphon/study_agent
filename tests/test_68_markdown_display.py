"""Regression checks for the shared Markdown renderer and window preferences."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class MarkdownDisplayTest(unittest.TestCase):
    def test_markdown_and_window_preferences(self):
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const path = require('path');
const front = path.join(process.cwd(), 'core', 'frontend');
const store = new Map();
const ctx = {console, localStorage:{getItem:k=>store.get(k) ?? null,
  setItem:(k,v)=>store.set(k,v)}, document:{getElementById:()=>null}};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(front, 'vendor_markdown_it.js'), 'utf8'), ctx);
vm.runInContext(fs.readFileSync(path.join(front, '05_display.js'), 'utf8'), ctx);
vm.runInContext(`const esc = s => String(s??'').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));`, ctx);
const assistant = fs.readFileSync(path.join(front, '10_assistant.js'), 'utf8');
vm.runInContext(assistant.slice(0, assistant.indexOf('let CHAT =')), ctx);
const md = ctx.mdToHtml('# 标题\n\n**加粗** 和 `代码`\n\n- 第一条\n- 第二条\n\n| 项目 | 值 |\n| --- | --- |\n| A | B |\n\n> 引用\n\n[安全](https://example.org/)\n\n[危险](javascript:alert(1))\n\n<img src=x onerror=alert(1)>\n\n![外链](https://example.org/a.png)');
for (const tag of ['<h1>', '<strong>', '<code>', '<ul>', '<table>', '<blockquote>'])
  assert(md.includes(tag), 'missing ' + tag);
assert(md.includes('&lt;img'), 'raw HTML must be escaped');
assert(!md.includes('<img'), 'external images must not load automatically');
assert(!md.includes('href="javascript:'), 'dangerous links must not be clickable');
assert(md.includes('rel="noopener noreferrer"'), 'external links need safe rel');
assert.deepStrictEqual(JSON.parse(JSON.stringify(ctx.kpWindowRead())), {width:87.5,height:87.5});
ctx.kpWindowSet(5, 150);
assert.deepStrictEqual(JSON.parse(store.get('study_agent_knowledge_window_v1')), {width:40,height:100});
ctx.kpWindowSet(70, 65);
assert.deepStrictEqual(JSON.parse(JSON.stringify(ctx.kpWindowRead())), {width:70,height:65});
'''
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
