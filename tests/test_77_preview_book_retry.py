"""补齐教材总结不得因预习提示词更新而重跑预习。"""
import shutil
import subprocess
import unittest
from pathlib import Path


class PreviewBookRetry(unittest.TestCase):
    def test_stale_preview_never_invokes_preview_pipeline(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node 未安装')
        js = Path(__file__).resolve().parents[1] / 'core' / 'frontend' / '70_preview.js'
        source = js.read_text(encoding='utf-8')
        func = source[source.index('async function pvBookRetry('):source.index('async function pvBookWatch(')]
        harness = r'''
const calls=[];
const PV={docId:4, subject:'book', busy:{}};
const SUBJ={cur:'book'};
let message='';
function pvRender(){}
function pvBookWatch(){}
function toast(s){ message=s; }
async function confirmBox(t, s){ if(!s.includes('不重新预习')) throw Error('missing disclosure'); return true; }
async function api(url, opts){
  calls.push(url);
  // 假设预习提示词已变化：任何访问预习接口都是错误。
  if(url.includes('/api/preview/')) throw Error('stale preview was rerun');
  if(url.startsWith('/api/textbook-kp/estimate')) return {ok:true,signature:'sig',calls:2,estimated_input_tokens:200,estimated_output_cap:0};
  if(url==='/api/textbook-kp/start') {const p=JSON.parse(opts.body); if(p.force || !p.confirmed || p.max_calls!==2) throw Error('bad request'); return {started:true};}
  throw Error('unexpected endpoint '+url);
}
''' + func + r'''
(async()=>{ await pvBookRetry(3); if(calls.length!==2 || !PV.bookRetry || PV.bookRetry.chapter!==3) throw Error(message || 'not started'); })().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run([node, '-e', harness], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
