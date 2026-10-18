# -*- coding: utf-8 -*-
"""C-0100 真浏览器探针：预习面板（入口 → 弹窗 → 章列表 → 预估 → 拒绝分支 → 进度）。

血统同 `tools/c0098_shots.py`（importlib 复用 `tools/c0098_audit_ui.py` 的最小 WebSocket
+ CDP，无第三方依赖）。**只连自己起的临时端口实例**（`tools\\c0100_tmp_launch.py`，
数据根/配置文件全隔离），真库一个字节都不碰。

★ 不花钱：全程**不会**真的启动一次预习——「开始预习」那一步点下去时开关是关的，
  后端按设计**拒绝**并回可操作提示（0 次模型调用）；进度条与完成态用**假 payload**
  直接喂给面板函数（只验前端分支）。

用法：
    python tools\\c0100_probe_ui.py --port 8791 --cdp-port 9336 --outdir <卡目录>\\screenshots
    python tools\\c0100_probe_ui.py --port 8791 --cdp-port 9336 --outdir <...> --entry-only
输出结尾固定打印 `C0100-UI-PASS` 或 `C0100-UI-FAIL`。
"""
import argparse
import base64
import importlib.util
import io
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
DOC_ID = 91          # 临时库里那份带 42 章、且第 1 章已有预习产物的教材
DONE_CH = 1          # 产物 meta.chapter_no

# 入口按钮：找得到、可见、带 title，并记录它是不是和卡④的「?」同在一行工具栏里
# （知识点非空 → 工具栏；为空 → 整页是空态分支，按钮由 kpPreviewRow() 单独占一行）
ENTRY_JS = """(async () => {
  const wait = ms => new Promise(z => setTimeout(z, ms));
  for (let i = 0; i < 80; i++){
    const b = [...document.querySelectorAll('#view button')]
      .find(x => (x.getAttribute('onclick')||'').includes('openKpsPreview'));
    if (b) {
      const par = b.parentElement;
      return {found: true, text: (b.innerText||'').trim(),
              title: b.getAttribute('title')||'', cls: b.className,
              visible: !!(b.offsetWidth && b.offsetHeight),
              row: !!(par && par.querySelector('[onclick*="openKpsHelp"]')),
              empty: !document.querySelector('#view table')};
    }
    await wait(250);
  }
  return {found: false};
})()"""


def J(s):
    """把中文安全地塞进 JS 表达式（ensure_ascii 转义，避开所有编码坑）。"""
    return json.dumps(s, ensure_ascii=True)


def load_audit():
    path = os.path.join(HERE, "c0098_audit_ui.py")
    spec = importlib.util.spec_from_file_location("c0098_audit_ui", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--cdp-port", type=int, required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--entry-only", action="store_true",
                    help="只验入口按钮（知识点非空/为空两种页面状态的对照用）")
    a = ap.parse_args()

    au = load_audit()
    site = "http://127.0.0.1:%d/" % a.port
    outdir = os.path.abspath(a.outdir)
    os.makedirs(outdir, exist_ok=True)
    prof = os.path.join(os.environ.get("TEMP", "."), "c0100_probe_profile")
    steps = []

    def step(name, ok, note=""):
        steps.append((name, bool(ok), str(note)[:200]))
        print(("ok  " if ok else "NG  ") + name + ((" | " + str(note)) if note else ""),
              flush=True)

    proc, ver = au.start_edge(prof, a.cdp_port)
    try:
        ws = au.WS(au.page_target(a.cdp_port))
        ws.cmd("Page.enable")
        ws.cmd("Runtime.enable")
        ws.cmd("Emulation.setDeviceMetricsOverride",
               {"width": 1600, "height": 1200, "deviceScaleFactor": 1, "mobile": False})
        ws.cmd("Page.navigate", {"url": site})
        for _ in range(60):
            time.sleep(0.5)
            if ws.eval_js("document.readyState") == "complete":
                break
        time.sleep(3.0)

        def js(expr, wait=False):
            return ws.eval_js(expr, await_promise=wait)

        def shot(name, full=False):
            r = ws.cmd("Page.captureScreenshot",
                       {"format": "png", "captureBeyondViewport": bool(full)})
            p = os.path.join(outdir, name)
            with io.open(p, "wb") as f:
                f.write(base64.b64decode(r["data"]))
            print("    截图 %s（%d KB）" % (name, os.path.getsize(p) // 1024), flush=True)

        js("go('kps')", wait=True)
        entry = js(ENTRY_JS, wait=True) or {}
        base = ("入口「%s」可见、带 title" % (entry.get("text") or ""),
                bool(entry.get("found")) and bool(entry.get("visible"))
                and "预习" in (entry.get("text") or "") and bool(entry.get("title")))
        step("1 知识点页出现「预习」入口（可见、带 title）", base[1],
             "%s | class=%s | 空态=%s | 与「?」同行=%s | title=%s"
             % (entry.get("text"), entry.get("cls"), entry.get("empty"),
                entry.get("row"), (entry.get("title") or "")[:60]))
        shot("01b-kps-entry-nonempty.png" if a.entry_only else "01-kps-entry.png", full=True)

        if a.entry_only:
            step("1b 入口与卡④的「?」同在一行工具栏里（知识点非空时才成立）",
                 bool(entry.get("row")) and not entry.get("empty"),
                 "row=%s empty=%s" % (entry.get("row"), entry.get("empty")))
            errs = ws.js_errors()
            step("JS 错误（favicon 404 噪声不算）",
                 not [e for e in errs if "favicon" not in str(e).lower()], str(errs)[:200])
        else:
            # ---------- 2 点击入口 → 弹窗打开 ----------
            js("""(() => {
              const b = [...document.querySelectorAll('#view button')]
                .find(x => (x.getAttribute('onclick')||'').includes('openKpsPreview'));
              if (b) b.click();
              return !!b;
            })()""")
            time.sleep(1.5)
            opened = js("(document.querySelector('#modal')||{}).className || ''")
            step("2 点击入口打开预习弹窗", "on" in str(opened), "class=%s" % opened)

            # ---------- 3 未开启分支：hint + 去设置中心（不是失败） ----------
            nb = js("""(async () => {
              const wait = ms => new Promise(z => setTimeout(z, ms));
              for (let i = 0; i < 80; i++){
                const b = document.querySelector('#pv-body');
                if (b && (b.innerText||'').trim().length > 40) {
                  const t = b.innerText.replace(/\\s+/g, ' ');
                  const btn = [...b.querySelectorAll('button')]
                    .find(x => (x.innerText||'').includes('去设置中心'));
                  return {text: t.slice(0, 400), hasBtn: !!btn};
                }
                await wait(200);
              }
              return {text: ''};
            })()""", wait=True) or {}
            txt3 = nb.get("text") or ""
            step("3 未开启：弹窗内直接展示可操作 hint（不当失败）",
                 ("未开启" in txt3) and ("设置中心" in txt3) and ("PREVIEW_ENABLED" in txt3)
                 and nb.get("hasBtn"),
                 "去设置中心按钮=%s | %s" % (nb.get("hasBtn"), txt3[:150]))
            shot("02-not-enabled-hint.png")

            # ---------- 4 选资料 → 章列表（章号/章名/页范围/节数/产物标记） ----------
            js("""(() => {
              const s = document.querySelector('#pv-doc');
              s.value = %s;
              s.dispatchEvent(new Event('change'));
              return s.value;
            })()""" % J(str(DOC_ID)))
            chs = js("""(async () => {
              const wait = ms => new Promise(z => setTimeout(z, ms));
              for (let i = 0; i < 100; i++){
                const rows = [...document.querySelectorAll('#pv-chwrap tbody tr')];
                if (rows.length > 1) {
                  const r0 = rows[0].innerText.replace(/\\s+/g, ' ').trim();
                  const done = rows.filter(r => (r.innerText||'').includes('已有预习'));
                  return {n: rows.length, first: r0,
                          doneRows: done.map(r => (r.innerText||'').replace(/\\s+/g,' ').trim().slice(0,90)),
                          hasSel: rows.some(r => r.querySelector('button')),
                          note: (document.querySelector('#pv-body').innerText||'').includes('只保留最近一章')};
                }
                await wait(200);
              }
              return {n: 0};
            })()""", wait=True) or {}
            step("4 章列表渲染（章号/章名/页范围/节数/已有产物标记）",
                 (chs.get("n") or 0) > 1 and "第 1 章" in (chs.get("first") or "")
                 and "p.15" in (chs.get("first") or "") and "节" in (chs.get("first") or "")
                 and chs.get("hasSel") and bool(chs.get("doneRows")),
                 "行数=%s | 首行=%s | 已有产物行=%s"
                 % (chs.get("n"), (chs.get("first") or "")[:110], chs.get("doneRows")))
            step("4b 弹窗里有「同一份资料只保留最近一章」的告知",
                 bool(chs.get("note")), "artifact_note 已渲染")
            shot("03-chapters.png", full=True)

            # ---------- 5 选一章 → 预估（调用次数 + 成本上界） ----------
            js("""(() => {
              const rows = [...document.querySelectorAll('#pv-chwrap tbody tr')];
              const b = rows[1].querySelector('button');   // 第 2 章
              if (b) b.click();
              return !!b;
            })()""")
            est = js("""(async () => {
              const wait = ms => new Promise(z => setTimeout(z, ms));
              for (let i = 0; i < 100; i++){
                const t = (document.querySelector('#pv-est')||{}).innerText || '';
                if (t.includes('成本上界')) {
                  return {text: t.replace(/\\s+/g,' ').trim().slice(0, 400),
                          cost: PVP.est ? PVP.est.est_cost_cny : null,
                          calls: PVP.est ? PVP.est.est_calls : null};
                }
                await wait(200);
              }
              return {text: (document.querySelector('#pv-est')||{}).innerText || ''};
            })()""", wait=True) or {}
            t5 = est.get("text") or ""
            step("5 选中章给出预估（调用次数恒 1 + 成本上界）",
                 ("调用次数：1 次" in t5) and ("成本上界：CNY" in t5) and (est.get("calls") == 1)
                 and (est.get("cost") or 0) > 0,
                 "calls=%s cost=%s | %s" % (est.get("calls"), est.get("cost"), t5[:130]))
            shot("04-estimate.png", full=True)

            # ---------- 6 真点「开始预习」→ 后端按开关拒绝（0 花费） ----------
            js("(() => { const b = document.querySelector('#pv-go'); if (b) b.click(); return !!b; })()")
            rej = js("""(async () => {
              const wait = ms => new Promise(z => setTimeout(z, ms));
              for (let i = 0; i < 80; i++){
                const m = document.querySelector('#pv-msg');
                const t = (m && m.innerText || '').replace(/\\s+/g,' ').trim();
                if (t) {
                  return {text: t.slice(0, 400), hasBtn: !!(m.querySelector('button')),
                          btnText: (document.querySelector('#pv-go')||{}).innerText || '',
                          disabled: !!(document.querySelector('#pv-go')||{}).disabled};
                }
                await wait(200);
              }
              return {text: ''};
            })()""", wait=True) or {}
            t6 = rej.get("text") or ""
            step("6 真点开始：开关关着 → 后端拒绝并如实展示 hint（不当作失败）",
                 ("未开启" in t6) and ("设置中心" in t6) and rej.get("hasBtn")
                 and not rej.get("disabled"),
                 "%s | 按钮=%s disabled=%s" % (t6[:120], rej.get("btnText"), rej.get("disabled")))
            shot("05-start-rejected.png", full=True)

            # ---------- 7 「去设置中心」直达预习分区 ----------
            js("""(() => { const b = [...document.querySelectorAll('#pv-msg button')]
                     .find(x => (x.innerText||'').includes('去设置中心')); if (b) b.click(); return !!b; })()""")
            time.sleep(2.5)
            setd = js("""(() => {
              const box = document.querySelector('#setbox');
              const on = (document.querySelector('#setmodal')||{}).className || '';
              const cur = document.querySelector('#setbox button.on');
              const sw = document.querySelector('#setbox [data-k="PREVIEW_ENABLED"]');
              return {on: on, text: ((box||{}).innerText||'').replace(/\\s+/g,' ').slice(0, 1200),
                      cur: (cur||{}).innerText || '', hasSwitch: !!sw,
                      switchOn: sw ? !!sw.checked : null,
                      modalOff: !((document.querySelector('#modal')||{}).className||'').includes('on')};
            })()""") or {}
            t7 = setd.get("text") or ""
            step("7 「去设置中心」直达「AI 预习总结」分区，并能看到「开启预习功能」开关",
                 "on" in str(setd.get("on")) and ("预习总结" in (setd.get("cur") or ""))
                 and bool(setd.get("hasSwitch")) and setd.get("switchOn") is False
                 and setd.get("modalOff"),
                 "分区=%s | 开关存在=%s 当前=%s | %s"
                 % (setd.get("cur"), setd.get("hasSwitch"), setd.get("switchOn"), t7[:110]))
            shot("06-settings-preview-tab.png", full=True)

            # ---------- 8 进度条（假 payload，只验前端分支；不真跑预习、不花钱） ----------
            js("closeSettings();")
            time.sleep(0.4)
            js("openKpsPreview();")
            js("""(async () => {
              const wait = ms => new Promise(z => setTimeout(z, ms));
              for (let i = 0; i < 60 && !document.querySelector('#pv-progress'); i++) await wait(200);
              return !!document.querySelector('#pv-progress');
            })()""", wait=True)
            prog = js("""(() => {
              const p = {kind:'preview', active:true, status:'running',
                label:'预习总结：doc#91 第2章', stage:'预习总结', stage_index:2,
                stages:['读取章节','渲染材料','预习总结','写入产物'], note:'整章 1 次调用',
                percent:50, doc_id:91, chapter:'第2章', calls:1, cost_cny:0.0123,
                elapsed:37.5, out_path:'', error:'', model:'deepseek-flash',
                thinking:true, reasoning_effort:'high'};
              pvPaintProgress(p);
              const box = document.querySelector('#pv-progress');
              const bar = box.querySelector('.pbar i');
              const b = document.querySelector('#pv-go');
              return {html: (box.innerText||'').replace(/\\s+/g,' ').trim(),
                      width: bar ? bar.style.width : '',
                      steps: box.querySelectorAll('.pstep').length,
                      cost: (document.querySelector('#pv-cost')||{}).innerText || '',
                      btn: (b||{}).innerText || '', disabled: !!(b||{}).disabled};
            })()""") or {}
            step("8 进度区渲染（stage/percent/耗时/花费，同一套进度条）",
                 "%" in (prog.get("html") or "") and prog.get("width") == "50%"
                 and (prog.get("steps") or 0) == 4
                 and "已调用 1 次" in (prog.get("cost") or "")
                 and "CNY0.0123" in (prog.get("cost") or "")
                 and prog.get("disabled") and "进行中" in (prog.get("btn") or ""),
                 "width=%s steps=%s | %s | 按钮=%s"
                 % (prog.get("width"), prog.get("steps"), prog.get("cost"), prog.get("btn")))
            shot("07-progress.png", full=True)

            # ---------- 9 与「批量整理」互斥的拒绝文案（假 payload） ----------
            busy = js("""(() => {
              pvShowFail({error:'有一个「批量整理」任务在跑；等它结束（或取消）后再做预习'});
              const m = document.querySelector('#pv-msg');
              const b = document.querySelector('#pv-go');
              return {text: (m.innerText||'').replace(/\\s+/g,' ').trim(),
                      disabled: !!(b||{}).disabled, btn: (b||{}).innerText || ''};
            })()""") or {}
            step("9 与「批量整理」互斥的拒绝文案如实展示（可再次点击）",
                 "批量整理" in (busy.get("text") or "") and "等它结束" in (busy.get("text") or "")
                 and not busy.get("disabled"),
                 "%s | 按钮=%s" % ((busy.get("text") or "")[:120], busy.get("btn")))
            shot("08-busy-reject.png", full=True)

            # ---------- 10 完成后的产物提示（假 payload；真实 out_path 口径） ----------
            done = js("""(async () => {
              const out = 'G:\\\\study_agent\\\\logs\\\\knowledge\\\\91-预习总结.json';
              await pvFinish({kind:'preview', active:false, status:'done',
                label:'预习总结：doc#91 第1章', stage:'写入产物',
                stages:['读取章节','渲染材料','预习总结','写入产物'],
                percent:100, doc_id:91, chapter:'第1章', calls:1, cost_cny:0.0231,
                elapsed:88.2, out_path:out, error:''});
              const b = document.querySelector('#pv-out');
              return {text: (b.innerText||'').replace(/\\s+/g,' ').trim().slice(0, 400),
                      hasBak: (b.innerText||'').includes('.bak-'),
                      hasPath: (b.innerText||'').includes('91-预习总结.json'),
                      cost: (document.querySelector('#pv-cost')||{}).innerText || ''};
            })()""", wait=True) or {}
            t10 = done.get("text") or ""
            step("10 完成后给出产物路径 + 「只保留最近一章、换章会备份」告知",
                 ("预习完成" in t10) and done.get("hasPath") and done.get("hasBak")
                 and ("只保留最近一章" in t10),
                 "%s | cost=%s" % (t10[:150], done.get("cost")))
            shot("09-done-artifact.png", full=True)

            # ---------- 11 「已有任务在跑」时不能重复启动（假 payload：running 态面板） ----------
            runst = js("""(() => {
              PVP.opts = Object.assign({}, PVP.opts, {running: true,
                progress: {kind:'preview', active:true, status:'running',
                  label:'预习总结：doc#91 第2章', stage:'渲染材料', stage_index:1,
                  stages:['读取章节','渲染材料','预习总结','写入产物'], note:'',
                  percent:25, doc_id:91, chapter:'第2章', calls:0, cost_cny:0.0,
                  elapsed:12.0, out_path:'', error:''}});
              pvRender();
              const b = document.querySelector('#pv-go');
              const p = document.querySelector('#pv-progress');
              const bar = p.querySelector('.pbar i');
              return {btn: (b||{}).innerText || '', disabled: !!(b||{}).disabled,
                      width: bar ? bar.style.width : '',
                      cost: (document.querySelector('#pv-cost')||{}).innerText || ''};
            })()""") or {}
            step("11 已有预习在跑时：面板直接禁用「开始」并显示当前进度",
                 runst.get("disabled") and "进行中" in (runst.get("btn") or "")
                 and runst.get("width") == "25%" and "已调用 0 次" in (runst.get("cost") or ""),
                 "按钮=%s disabled=%s width=%s | %s"
                 % (runst.get("btn"), runst.get("disabled"), runst.get("width"),
                    runst.get("cost")))
            shot("10-running-locked.png", full=True)

            errs = ws.js_errors()
            step("12 全程无 JS 错误（favicon 404 噪声不算）",
                 not [e for e in errs if "favicon" not in str(e).lower()],
                 "%d 条：%s" % (len(errs), str(errs)[:200]))
    finally:
        proc.kill()

    print("\n站点 %s | %s | 截图目录 %s" % (site, ver.get("Browser"), outdir))
    bad = [s for s in steps if not s[1]]
    print("C0100-UI-%s（%d/%d 通过）" % ("PASS" if not bad else "FAIL",
                                        len(steps) - len(bad), len(steps)))
    for name, ok, note in bad:
        print("  未过：%s | %s" % (name, note))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
