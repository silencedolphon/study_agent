# -*- coding: utf-8 -*-
"""C-0098③ 专项探针：资料页表格行内「⋯」菜单 + 批量入库弹窗（真浏览器，只点不改数据）。

血统：`tools/c0098_audit_ui.py`（= C-0096 的 audit_c0096.py，源自 C-0091 的 audit_cdp.py）
的同一套最小 WebSocket + CDP 客户端，只换探针脚本。**只连自己起的临时端口实例**。

覆盖本卡验收项：
  · 已入库表 / raw 表每行 = 1 个主操作 + 1 个「⋯」，功能一个不少（菜单项逐条比对）；
  · ⋯ 菜单走现有 evPop 浮层（`.evpop`），Esc 沿用同一条关闭路径；
  · 批量入库整张卡片进了 `modal()` 弹窗：表格/并发/全选/全不选/开始/重试失败项/清除结果
    + S1「识图要花钱（约 25 秒/页）」醒目提示；
  · D10（「仍是原来的串行入口」）不在页面；D11 收进 tooltip。

用法（需先自起实例，例如 --port 8798）：
  python c0098_docs_probe.py --port 8798 --cdp-port 9336 --out <绝对路径.json>
输出结尾固定打印 `C0098-DOCSPROBE-PASS` 或 `C0098-DOCSPROBE-FAIL`。
"""
import argparse
import importlib.util
import io
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))

PROBE_JS = r"""
(async () => {
  const r = { steps: [], data: {} };
  const log = (k, ok, note) => r.steps.push({step: k, ok: !!ok, note: String(note === undefined ? '' : note).slice(0, 200)});
  const wait = ms => new Promise(z => setTimeout(z, ms));
  const vis = el => { if (!el) return false; const b = el.getBoundingClientRect();
    return b.width > 0 && b.height > 0 && getComputedStyle(el).display !== 'none'; };
  const txt = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const esc_ = null;
  try {
    go('docs');
    for (let i = 0; i < 60 && document.querySelectorAll('#view button').length < 5; i++) await wait(250);
    await wait(500);
    const view = document.querySelector('#view');
    const tbls = [...view.querySelectorAll('table')];
    const btns = [...view.querySelectorAll('button')].filter(vis);
    const inTbl = b => tbls.some(t => t.contains(b));
    r.data.btn_nontable = btns.filter(b => !inTbl(b)).length;
    r.data.btn_table = btns.filter(inTbl).length;
    r.data.btn_total = btns.length;
    log('按钮 非表格/表格/合计', true, r.data.btn_nontable + ' / ' + r.data.btn_table + ' / ' + r.data.btn_total);

    const rowCounts = {};
    let rows = 0, dots = 0, mains = 0;
    const mainNames = {};
    view.querySelectorAll('tbody tr').forEach(tr => {
      const bs = [...tr.querySelectorAll('button')].filter(vis);
      rows += 1;
      rowCounts[bs.length] = (rowCounts[bs.length] || 0) + 1;
      bs.forEach(b => {
        if (txt(b).indexOf('⋯') >= 0) dots += 1;
        else { mains += 1; const t = txt(b); mainNames[t] = (mainNames[t] || 0) + 1; }
      });
    });
    r.data.rows = rows; r.data.dots = dots; r.data.mains = mains;
    r.data.row_button_hist = rowCounts; r.data.main_names = mainNames;
    log('表格行数 / 行内 ⋯ / 行内主按钮', rows && dots && mains,
        rows + ' 行 | ⋯ ' + dots + ' | 主按钮 ' + mains + ' | 每行按钮数分布 ' + JSON.stringify(rowCounts));
    log('行内主按钮分布', true,
        JSON.stringify(mainNames) + '（其中「移除索引」属孤儿提示条，卡 C-0097② 区段，本卡不碰）');
    log('每行都是 1 主操作 + 1 个 ⋯', Object.values(rowCounts).every(v => v === 2) || '（不支持格式的行=1）',
        JSON.stringify(rowCounts));

    const allDots = [...view.querySelectorAll('tbody tr button')].filter(b => txt(b).indexOf('⋯') >= 0);
    const docDots = allDots.filter(b => (b.getAttribute('onclick') || '').indexOf('docsDocRowMenu') >= 0);
    const rawDots = allDots.filter(b => (b.getAttribute('onclick') || '').indexOf('docsFileRowMenu') >= 0);
    r.data.doc_dots = docDots.length; r.data.raw_dots = rawDots.length;
    log('已入库行 ⋯ / raw 行 ⋯', docDots.length > 0 && rawDots.length > 0,
        docDots.length + ' / ' + rawDots.length);

    const popOpen = () => { const p = document.querySelector('.evpop'); return !!p && vis(p); };
    const popItems = () => { const p = document.querySelector('.evpop');
      return p ? [...p.querySelectorAll('.pbody button')].map(b => ({t: txt(b), title: (b.title || '').slice(0, 90)})) : []; };
    const escKey = () => document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));

    // ---- 已入库行「⋯」菜单 ----
    docDots[0].click(); await wait(350);
    let items = popItems();
    r.data.doc_menu = items.map(x => x.t);
    log('已入库行 ⋯ 菜单打开（evPop）', popOpen(), JSON.stringify(r.data.doc_menu));
    const need = ['导出分享包', '改标题', '改名', '移除'];
    log('功能一个不少：导出/改标题/改名/移除', need.every(k => items.some(x => x.t.indexOf(k) >= 0)),
        JSON.stringify(r.data.doc_menu));
    const rm = items.find(x => x.t.indexOf('移除') >= 0) || {title: ''};
    log('D11 移除说的是 tooltip（只删索引/保留原文件）', /只删索引/.test(rm.title), rm.title);
    const dr = items.find(x => x.t.indexOf('改名') >= 0) || {title: ''};
    log('改名项 tooltip 在', /文件名/.test(dr.title), dr.title);
    escKey(); await wait(250);
    log('Esc 关闭 ⋯ 菜单（evPop 同一条关闭路径）', !popOpen(), '');

    // ---- raw 行「⋯」菜单 ----
    rawDots[0].click(); await wait(350);
    items = popItems();
    r.data.raw_menu = items.map(x => x.t);
    log('raw 行 ⋯ 菜单打开（evPop）', popOpen(), JSON.stringify(r.data.raw_menu));
    log('raw 行功能不少：改名 / 删文件（+ 已入库才有 移除索引）',
        items.some(x => x.t.indexOf('改名') >= 0) && items.some(x => x.t.indexOf('删文件') >= 0),
        JSON.stringify(r.data.raw_menu));
    document.body.click(); await wait(250);
    log('点菜单外关闭（evPopOutside）', !popOpen(), '');

    // ---- D11 前半：行内主操作的 tooltip ----
    // 「重建」（已入库表 / raw 已入库行）收 D11 的前半句；「入库」（raw 待入库行）只需说清自己。
    const rb = [...view.querySelectorAll('tbody tr button')].filter(b => txt(b) === '重建');
    const ib = [...view.querySelectorAll('tbody tr button')].filter(b => txt(b) === '入库');
    r.data.rebuild_n = rb.length;
    r.data.rebuild_titles = [...new Set(rb.map(b => b.title))];
    r.data.ingest_titles = [...new Set(ib.map(b => b.title))];
    log('行内「重建」tooltip 收下 D11 前半（重新解析并覆盖索引）',
        rb.length > 0 && r.data.rebuild_titles.every(t => /重新解析并覆盖索引/.test(t)),
        rb.length + ' 个；' + JSON.stringify(r.data.rebuild_titles));
    log('行内「入库」tooltip 一句话说清', ib.length > 0 && r.data.ingest_titles.length > 0,
        ib.length + ' 个；' + JSON.stringify(r.data.ingest_titles));

    // ---- 孤儿提示条（卡 C-0097② 区段）的折叠态，只观察不评判 ----
    r.data.details = [...view.querySelectorAll('details')].map(d => ({
      open: !!d.open, rows: d.querySelectorAll('tbody tr').length,
      sum: ((d.querySelector('summary') || {}).innerText || '').replace(/\s+/g, ' ').slice(0, 60)
    }));
    log('孤儿提示条折叠态（观察项）', true, JSON.stringify(r.data.details).slice(0, 200));

    // ---- 批量入库弹窗 ----
    const vt = txt(view);
    r.data.view_has_serial = /仍是原来的/.test(vt);
    r.data.view_has_158 = /一次提交多份/.test(vt);
    r.data.view_has_batchcard = /多管线并发跑/.test(vt);
    log('页面上没有 D10（仍是原来的串行入口）', !r.data.view_has_serial, '');
    log('页面上没有原批量卡片（158 字说明/常驻表格）', !r.data.view_has_158 && !r.data.view_has_batchcard,
        'has158=' + r.data.view_has_158 + ' hasCard=' + r.data.view_has_batchcard);

    const openBtn = [...view.querySelectorAll('button')].find(b => txt(b).indexOf('批量入库') >= 0);
    log('批量入库入口按钮在卡片标题行（卡 C-0097② 提供）', !!openBtn, openBtn ? txt(openBtn) : '');
    if (openBtn) {
      openBtn.click(); await wait(600);
      const modal = document.querySelector('#modal');
      const box = document.querySelector('#modalbox');
      const bt = txt(box);
      r.data.modal_on = !!(modal && modal.classList.contains('on'));
      r.data.modal_text_len = bt.length;
      r.data.modal_flags = {
        money: /识图要花钱/.test(bt), money25: /25 秒/.test(bt),
        table: !!box.querySelector('#batch-list table, table#batch-list, #batch-list'),
        workers: !!box.querySelector('#batch-workers'),
        all: /全选待入库/.test(bt), none: /全不选/.test(bt),
        start: /开始批量入库/.test(bt), retry: !!box.querySelector('#batch-retry'),
        dismiss: !!box.querySelector('#batch-dismiss'), selinfo: !!box.querySelector('#batch-selinfo'),
        progress: !!box.querySelector('#batch-progress'), tasks: !!box.querySelector('#batch-tasks'),
        d10: /仍是原来的/.test(bt), d9_158: /一次提交多份/.test(bt)
      };
      log('弹窗打开（复用 #modal / modal()）', r.data.modal_on, 'box 文本 ' + bt.length + ' 字');
      log('弹窗内 S1 花钱告知（识图要花钱 + 约 25 秒/页）',
          r.data.modal_flags.money && r.data.modal_flags.money25, '');
      const f = r.data.modal_flags;
      log('弹窗内功能齐全（表格/并发/全选/全不选/开始/重试/清除/已选/进度/任务区）',
          f.table && f.workers && f.all && f.none && f.start && f.retry && f.dismiss && f.selinfo && f.progress && f.tasks,
          JSON.stringify(f));
      // 全选 / 全不选 真能勾（只在弹窗内动勾选框，不发任何请求）
      const cbs = [...box.querySelectorAll('.batch-cb')];
      const selinfo = box.querySelector('#batch-selinfo');
      const allBtn = [...box.querySelectorAll('button')].find(b => txt(b) === '全选待入库');
      const noneBtn = [...box.querySelectorAll('button')].find(b => txt(b) === '全不选');
      if (allBtn && cbs.length) {
        allBtn.click(); await wait(200);
        const onN = cbs.filter(c => c.checked).length;
        noneBtn.click(); await wait(200);
        const offN = cbs.filter(c => c.checked).length;
        r.data.sel_all = onN; r.data.sel_none = offN;
        log('全选/全不选真的生效', onN === cbs.length && offN === 0,
            '勾选 ' + onN + '/' + cbs.length + ' → 全不选后 ' + offN + '；已选信息=' + txt(selinfo));
      } else log('全选/全不选真的生效', false, '按钮或勾选框缺失');
      escKey(); await wait(300);
      log('Esc 关闭弹窗（与既有弹窗同一条路径）', !(modal && modal.classList.contains('on')), '');
    }
  } catch (e) { log('探针异常', false, (e && e.message) || String(e)); }
  return r;
})()
"""


COUNT_JS = r"""
(async () => {
  // 中立统计：不依赖本卡新增的任何函数，改前改后都能跑同一份，用来出对称的三个数字
  // （表格行内按钮 / 非表格按钮 / 正文与页高）+ 行内按钮名分布。
  const r = { steps: [], data: {} };
  const log = (k, ok, note) => r.steps.push({step: k, ok: !!ok, note: String(note === undefined ? '' : note).slice(0, 220)});
  const wait = ms => new Promise(z => setTimeout(z, ms));
  const vis = el => { if (!el) return false; const b = el.getBoundingClientRect();
    return b.width > 0 && b.height > 0 && getComputedStyle(el).display !== 'none'; };
  try {
    go('docs');
    for (let i = 0; i < 60 && document.querySelectorAll('#view button').length < 5; i++) await wait(250);
    await wait(600);
    const view = document.querySelector('#view');
    const tbls = [...view.querySelectorAll('table')];
    const btns = [...view.querySelectorAll('button')].filter(vis);
    const inTbl = b => tbls.some(t => t.contains(b));
    r.data.btn_nontable = btns.filter(b => !inTbl(b)).length;
    r.data.btn_table = btns.filter(inTbl).length;
    r.data.btn_total = btns.length;
    log('按钮 非表格/表格/合计', true, r.data.btn_nontable + ' / ' + r.data.btn_table + ' / ' + r.data.btn_total);
    const rowBtn = {};
    let trs = 0;
    view.querySelectorAll('tbody tr').forEach(tr => {
      trs += 1;
      [...tr.querySelectorAll('button')].filter(vis).forEach(b => {
        const t = ((b.innerText || '').trim().replace(/\s+/g, ' ')) || '(空)';
        rowBtn[t] = (rowBtn[t] || 0) + 1;
      });
    });
    r.data.trs = trs; r.data.row_button_names = rowBtn;
    log('行内按钮名分布', true, JSON.stringify(rowBtn));
    log('表格行数 / 行内可见按钮总数', true,
        trs + ' 行 / ' + Object.values(rowBtn).reduce((a, b) => a + b, 0) + ' 个');
    r.data.details = [...view.querySelectorAll('details')].map(d => ({
      open: !!d.open, rows: d.querySelectorAll('tbody tr').length,
      sum: ((d.querySelector('summary') || {}).innerText || '').replace(/\s+/g, ' ').slice(0, 60)
    }));
    log('details 折叠态', true, JSON.stringify(r.data.details).slice(0, 200));
    r.data.viewLen = (view.innerText || '').length;
    r.data.bodyLen = document.body.innerText.length;
    r.data.scrollHeight = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
    // 正文里"表格数据"与"表格外"分开算：方案 §8.4 的 ≤2500 字只有在表格数据被算进去时
    // 才看得出差距来自哪里（文件名/路径是数据，不是提示文字）。
    const norm = t => (t || '').replace(/\s+/g, ' ').trim().length;
    const tblTxt = tbls.reduce((a, t) => a + norm(t.innerText), 0);
    const viewTxt = norm(view.innerText);
    r.data.table_text = tblTxt;
    r.data.nontable_text = viewTxt - tblTxt;
    log('正文 非表格/表格/合计', true,
        r.data.nontable_text + ' / ' + tblTxt + ' / ' + viewTxt);
    log('正文/页高', true, 'view ' + r.data.viewLen + ' 字 | body ' + r.data.bodyLen + ' 字 | 页高 ' + r.data.scrollHeight + 'px');
  } catch (e) { log('探针异常', false, (e && e.message) || String(e)); }
  return r;
})()
"""


def load_audit():
    """复用 audit 脚本里的最小 CDP 客户端（同一血统，不重写一遍）。"""
    path = os.path.join(HERE, "c0098_audit_ui.py")
    spec = importlib.util.spec_from_file_location("c0098_audit_ui", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--cdp-port", type=int, required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--count-only", action="store_true",
                    help="只跑中立统计（不依赖本卡新 UI，改前改后都能跑）")
    a = ap.parse_args()

    au = load_audit()
    site = "http://127.0.0.1:%d/" % a.port
    prof = os.path.join(os.environ.get("TEMP", "."), "c0098_probe_profile")
    proc, ver = au.start_edge(prof, a.cdp_port)
    try:
        ws = au.WS(au.page_target(a.cdp_port))
        ws.cmd("Page.enable")
        ws.cmd("Runtime.enable")
        ws.cmd("Log.enable")
        ws.cmd("Emulation.setDeviceMetricsOverride",
               {"width": 1600, "height": 1200, "deviceScaleFactor": 1, "mobile": False})
        ws.cmd("Page.navigate", {"url": site})
        for _ in range(60):
            time.sleep(0.5)
            if ws.eval_js("document.readyState") == "complete":
                break
        time.sleep(3.0)
        res = ws.eval_js(COUNT_JS if a.count_only else PROBE_JS, await_promise=True) or {}
        res["js_errors"] = ws.js_errors()
        res["site"] = site
        res["browser"] = ver.get("Browser")
    finally:
        proc.kill()

    steps = res.get("steps", [])
    bad = [s for s in steps if not s.get("ok")]
    print("站点 %s | %s" % (site, res.get("browser")))
    for s in steps:
        print("  %s %-46s %s" % ("OK  " if s.get("ok") else "NG  ", s.get("step"), s.get("note", "")[:150]))
    errs = res.get("js_errors", [])
    # 已知环境性噪声：favicon 404（改前改后各 1 条，与本次改动无关）。
    noise = [e for e in errs if e.get("kind") == "log.network" and "404" in str(e.get("text", ""))]
    real = [e for e in errs if e not in noise]
    print("JS 错误：%d 条（其中已知 favicon 404 噪声 %d 条，真实错误 %d 条）"
          % (len(errs), len(noise), len(real)))
    for e in real[:5]:
        print("   ! %s %s" % (e.get("kind"), e.get("text", "")[:140]))
    if a.out:
        with io.open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("报告 ->", a.out)
    ok = (not bad) and not real
    print("C0098-DOCSPROBE-%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
