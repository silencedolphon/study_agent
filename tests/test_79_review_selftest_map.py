"""自测导图复用现有节点渲染，点击打开当前课件，不改变作答。"""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ReviewSelftestMap(unittest.TestCase):
    def test_float_progress_updates_after_mark_and_replacement(self):
        script = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const floating={classList:{remove(){}},querySelector:()=>null,innerHTML:''};
const view={innerHTML:''}, button={disabled:false,textContent:''};
const ctx={REV:{active:true,stage:'selftest',title:'课件',docId:7,st:{items:[{kp_id:'a'},{kp_id:'b'}]},stResults:{}},
 document:{getElementById:()=>floating}, $:s=>s==='#view'?view:button,
 esc:s=>String(s??''), revFloatActions:()=>[], reviewPathHtml:()=>'',reviewSummaryHtml:()=>'',
 bindReviewSelftest:()=>{},revMountSelftestMap:()=>{}};
vm.createContext(ctx);
const src=fs.readFileSync('core/frontend/30_review.js','utf8');
vm.runInContext(src.slice(src.indexOf('function revFloatUpdate()'),src.indexOf('async function revFloatNext()')),ctx);
vm.runInContext(src.slice(src.indexOf('function renderReviewSelftest()'),src.indexOf('function bindReviewSelftest()')),ctx);
ctx.renderReviewSelftest(); assert(floating.innerHTML.includes('已标记 0/2'));
ctx.REV.stResults.a=true; ctx.reviewSyncSelftestButtons();
assert(floating.innerHTML.includes('已标记 1/2')); assert(button.textContent.includes('1/2'));
ctx.REV.st.items=[{kp_id:'a'},{kp_id:'replacement'}]; ctx.renderReviewSelftest();
assert(floating.innerHTML.includes('已标记 1/2'));
ctx.REV.st.items=[{kp_id:'replacement'}]; ctx.renderReviewSelftest();
assert(floating.innerHTML.includes('已标记 0/1'));
'''
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tree_click_uses_selftest_doc_and_preserves_answers(self):
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const ctx = {console, calls:[], SUBJ:{cur:'course-a'},
 REV:{docId:7,stage:'selftest',st:{session_id:'s1'},stResults:{'kp1':true}},
 MINDMAP:{docId:999,nodes:{old:'unchanged'},open:{},downshift:false},
 esc:s=>String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
 api:async()=>({has_structure:true,tree:[{name:'节点',lkp_ready:true,lkp_id:'kp1',lkp_name:'总结',n_pages:1,children:[]}]}),
 evPopEl:()=>({dataset:{}}), mmOpenKp:(...a)=>ctx.calls.push(a),
 mmOpenKpList:()=>{}, mmOpenPending:()=>{}};
const node = {dataset:{k:'0'}, closest:()=>null};
const box = {innerHTML:'',querySelectorAll:s=>s==='details.mmd'?[]:(s==='.mmname'?[node]:[])};
ctx.$ = s => s==='#rv-selftest-map' ? box : null;
vm.createContext(ctx);
const maps = fs.readFileSync('core/frontend/50_knowledge.js','utf8');
vm.runInContext(maps.slice(maps.indexOf('function mmNodes('),maps.indexOf('function bindMindmapTree(')),ctx);
const review = fs.readFileSync('core/frontend/30_review.js','utf8');
vm.runInContext(review.slice(review.indexOf('const REV_MAP ='),review.indexOf('function renderReviewSelftest()')),ctx);
(async()=>{ await ctx.revMountSelftestMap();
assert(box.innerHTML.includes('节点')); assert.strictEqual(typeof node.onclick,'function');
node.onclick({preventDefault(){},stopPropagation(){}});
assert.strictEqual(ctx.calls[0][0],'kp1'); assert.strictEqual(ctx.calls[0][4],7);
assert.strictEqual(ctx.REV.stResults.kp1,true); assert.strictEqual(ctx.MINDMAP.docId,999);
assert.strictEqual(ctx.MINDMAP.nodes.old,'unchanged'); })().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
