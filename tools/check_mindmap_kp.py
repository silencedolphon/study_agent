        c = js[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return js[start:j + 1]
    return ""


def check_frontend(dashboard, kp_raw: dict) -> None:
    """前端：接线是静态检查，渲染函数交给 node 真跑一遍（不是看代码猜）。"""
    log("[6] 前端：点节点弹知识点小窗（接线 + 渲染函数真跑）")
    js = dashboard.INDEX_HTML
    bind = _grab_function(js, "bindMindmapTree")
    open_kp = _grab_function(js, "mmOpenKp")
    render_kp = _grab_function(js, "mmRenderKp")
    no_kp = _grab_function(js, "mmNoKpHtml")
    open_node = _grab_function(js, "mmOpenNode")
    card = _grab_function(js, "mindmapCard")
    for src, nm in ((bind, "bindMindmapTree"), (open_kp, "mmOpenKp"),
                    (render_kp, "mmRenderKp"), (no_kp, "mmNoKpHtml"),
                    (open_node, "mmOpenNode"), (card, "mindmapCard")):
        check(bool(src), f"抠到 {nm} 源码")
    check("doSearch" not in bind,
          "bindMindmapTree 里不再有 doSearch（点节点名不再跳检索页）")
    check("mmOpenNode" in bind, "点节点名改为打开知识点小窗（mmOpenNode）")
    check("evPopMount" in open_kp and "evPopMount" in open_node,
          "知识点弹窗复用既有 evPop 小窗（evPopMount），没有另造组件")
    check("evPopMount" in _grab_function(js, "evPopShow"),
          "聊天的出处小窗也走同一个挂载点（全页只有一套弹窗机制）")
    check("在资料库中检索此主题" in render_kp and "在资料库中检索此主题" in no_kp,
          "有/无知识点的弹窗里都保留「在资料库中检索此主题」入口")
    check("查看该节点的知识点" in card,
          "mindmapCard() 文案已改成描述新行为")
    check("点任一节点名可检索该主题的原文切块" not in js, "旧文案已不存在")
    for kw, desc in (("已并入父级知识点", "四级节点：提示内容已并入父级"),
                     ("mmkp-parent", "四级节点：可跳到父级知识点"),
                     ("非教学页", "E1：提示非教学页不建知识点"),
                     ("分类节点", "纯分类节点：提示展开子节点"),
                     ("展开子节点", "分类/根节点：可展开子节点")):
        check(kw in no_kp, desc)
    check("mmname.has-kp" in js and "has-kp" in _grab_function(js, "mmNodes"),
          "有知识点的节点有可见标记（.has-kp：颜色 + ◆）")
    check("MM_BLOCK_LIMIT" in js and "展开全部" in render_kp,
          "长内容有展开保护（默认前 N 块，按钮展开全部）")
    check(not re.search(r"<script[^>]+src=|<link[^>]+href=|cdn\.|unpkg|jsdelivr",
                        js),
          "没有引入任何外部 CDN / 第三方库（离线优先）")
    # 页面骨架/样式没被改坏（新加的 CSS 若少一个大括号，会吃掉后面所有规则）
    css = ""
    mst = re.search(r"<style>([\s\S]*?)</style>", dashboard.INDEX_HTML)
    if mst:
        css = mst.group(1)
    check(bool(css) and css.count("{") == css.count("}"),
          "内联 CSS 大括号配平（新样式没吃掉后面的规则）",
          f"{{={css.count('{')} }}={css.count('}')}")
    for tag in ("<html", "</html>", "</body>", "<script>", "</script>",
                'id="view"'):
        check(tag in dashboard.INDEX_HTML, f"页面骨架含 {tag}")

    node = shutil.which("node")
    if not node:
        log("  [SKIP] 环境里没有 node，跳过渲染函数的行为验证")
        return
    # esc 是箭头函数（const esc = …），不是 function 声明，单独按语句抓
    m_esc = re.search(r"const esc = [\s\S]*?;\n", js)
    parts = [m_esc.group(0) if m_esc else ""] + [
        _grab_function(js, f)
        for f in ("mmCut", "mmKindLabel", "mmBlocksHtml", "mmQsHtml")]
    mlimit = re.search(r"const\s+MM_BLOCK_LIMIT\s*=\s*\d+;", js)
    if not all(parts) or not mlimit:
        check(False, "抠出前端渲染函数（esc/mmCut/mmKindLabel/mmBlocksHtml/mmQsHtml）",
              f"esc={'ok' if m_esc else 'miss'} "
              f"limit={'ok' if mlimit else 'miss'}")
        return
    kp = kp_raw["knowledge_points"][5]          # KP-06：23 块、有跨页块、17 道题
    blocks = kp["blocks"]
    driver = "\n".join(parts) + "\n" + mlimit.group(0) + f"""
const KP_BLOCKS = {json.dumps(blocks, ensure_ascii=False)};
const KP_QS = {json.dumps(kp['questions'], ensure_ascii=False)};
const EVIL = [{{chunk_id: 1, page: 1, kind: 'body', source_ref: 'p.1',
  shared: false, text: '<script>alert(1)</script> & "引号"'}}];
const html6 = mmBlocksHtml(KP_BLOCKS, MM_BLOCK_LIMIT);
const htmlAll = mmBlocksHtml(KP_BLOCKS, KP_BLOCKS.length);
const evil = mmBlocksHtml(EVIL, 5);
const qs = mmQsHtml(KP_QS);
console.log(JSON.stringify({{
  n6: (html6.match(/class="kpblkh"/g) || []).length,
  nAll: (htmlAll.match(/class="kpblkh"/g) || []).length,
  chunk: html6.includes('chunk ' + KP_BLOCKS[0].chunk_id),
  src: html6.includes(KP_BLOCKS[0].source_ref),
  body: html6.includes('正文块'), fig: html6.includes('识图块'),
  shared: htmlAll.includes('跨页块'),
  escaped: !evil.includes('<script>') && evil.includes('&lt;script&gt;'),
  qsTitle: qs.includes('关联题目 ' + KP_QS.length + ' 道'),
  qsShown: (qs.match(/class="kpq"/g) || []).length,
  cut: mmCut('一二三四五', 3), kind: mmKindLabel('r2')
}}));
"""
    tmp = Path(tempfile.mkdtemp(prefix="dsh_kp_front_")) / "mm_render_check.js"
    tmp.write_text(driver, encoding="utf-8")
    try:
        r = subprocess.run([node, str(tmp)], capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
_STUB_JS = r"""
const LISTENERS = {click: [], keydown: [], scroll: []};
function mkEl(tag){
  const el = {
    tagName: String(tag || 'div').toUpperCase(), className: '', style: {},
    dataset: {}, innerHTML: '', textContent: '',
    offsetWidth: 320, offsetHeight: 200, children: [], _cls: [],
    classList: {
      add: c => { if (el._cls.indexOf(c) < 0) el._cls.push(c); },
      remove: c => { const i = el._cls.indexOf(c); if (i >= 0) el._cls.splice(i, 1); },
      contains: c => el._cls.indexOf(c) >= 0
    },
    addEventListener: () => {}, removeEventListener: () => {},
    appendChild: c => { el.children.push(c); return c; },
    remove: () => {},
    closest: () => null,
    contains: n => n === el || el.children.indexOf(n) >= 0,
    getBoundingClientRect: () => ({left: 10, top: 10, width: 40, height: 12,
                                   right: 50, bottom: 22}),
    querySelector: () => null, querySelectorAll: () => []
  };
  return el;
}
const BODY = mkEl('body');
const SEL_CACHE = {};
global.document = {
  body: BODY,
  createElement: mkEl,
  querySelector: sel => (SEL_CACHE[sel] = SEL_CACHE[sel] || mkEl('div')),
  querySelectorAll: () => [],
  addEventListener: (k, f) => { (LISTENERS[k] = LISTENERS[k] || []).push(f); },
  removeEventListener: (k, f) => {
    const a = LISTENERS[k] || []; const i = a.indexOf(f); if (i >= 0) a.splice(i, 1);
  }
};
global.window = {
  innerWidth: 1200, innerHeight: 800,
  addEventListener: global.document.addEventListener,
  removeEventListener: global.document.removeEventListener
};
function fire(kind, ev){ (LISTENERS[kind] || []).slice().forEach(f => f(ev)); }
const $ = sel => global.document.querySelector(sel);
const sleep = ms => new Promise(r => setTimeout(r, ms));
"""

_TEST_JS = r"""
// 详情接口桩：KP-01 故意慢（用于"连点两个节点不串内容"的竞态验证）
const DELAY = {'KP-01': 60, 'KP-06': 1};
const DETAILS = __DETAILS__;
global.fetch = async (path) => {

def check_popup_runtime(dashboard, mm: dict) -> None:
    """用真实前端函数 + DOM 桩，跑一遍"点节点 → 弹窗 → 关闭 → 连点"。"""
    log("[7] 弹窗控制流真跑（node + DOM 桩）")
    node = shutil.which("node")
    if not node:
        log("  [SKIP] 环境里没有 node，跳过")
        return
    js = dashboard.INDEX_HTML
    picks = {}
    for n, _d, _p in walk_tree(mm["tree"]):
        picks.setdefault(n["name"], n)
    need = ("胰岛素的作用", "糖尿病概述", "胰腺与胰岛细胞", "THANK YOU")
    missing = [x for x in need if x not in picks]
    if missing:
        check(False, "拿到用于验证的树节点", str(missing))
        return
    kp6 = dict(picks["胰岛素的作用"])
    cat = dict(picks["糖尿病概述"])
    tree = [{"name": "胰岛素和口服降糖药", "page": None, "n_pages": 78,
             "children": [kp6, cat, picks["胰腺与胰岛细胞"], picks["THANK YOU"]],
             "kp_node_kind": "root", "kp_subtree": 40}]
    details = {}
    for kid in ("KP-06", "KP-01"):
        d = dashboard.api_mindmap_kp(DOC_ID, kid)
        check(not d.get("error"), f"{kid} 详情可作桩数据")
        details[kid] = d

    consts = [r"const esc = [\s\S]*?;\n",
              r"const MINDMAP = \{[^\n]*\};", r"let EVPOP = null;",
              r"const MM_BLOCK_LIMIT = \d+;",
              r"const MMPOP = \{[^\n]*\};"]
    parts: list[str] = [_STUB_JS]
    for pat in consts:
        m = re.search(pat, js)
        if not m:
            check(False, f"抠出前端片段 {pat}")
            return
        parts.append(m.group(0))
    funcs = ("api", "evPopEl", "evPopOutside", "evPopClose", "evPopEsc",
             "evPopScroll", "evPopPlace", "evPopMount", "mmCut",
             "mmKindLabel", "mmBlocksHtml", "mmQsHtml", "mmOpenKp",
             "mmRenderKp", "mmNoKpHtml", "mmOpenNode", "mmNodes")
    for f in funcs:
        src = _grab_function(js, f)
        if not src:
            check(False, f"抠出前端函数 {f}")
            return
        parts.append(src)
    parts.append("function openChunk(){}\nfunction doSearch(){}\n")
    parts.append(_TEST_JS
                 .replace("__TREE__", json.dumps(tree, ensure_ascii=False))
                 .replace("__DETAILS__", json.dumps(details, ensure_ascii=False)))
    tmp = Path(tempfile.mkdtemp(prefix="dsh_kp_popup_")) / "popup_flow.js"
    tmp.write_text("\n".join(parts), encoding="utf-8")
    try:
        r = subprocess.run([node, str(tmp)], capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        check(r.returncode == 0, "node 跑通点节点→弹窗控制流",
              (r.stderr or "")[:400])
        if r.returncode != 0:
            return
        out = json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)

    check(out["dataK"], "节点名带 data-k（下标路径，供点击回查节点）")
    check(out["hasKpClass"], "有知识点的节点带 has-kp 标记（视觉可区分）")
    kp6 = out["kp06"]
    for kw in ("胰岛素的作用", "R1b 有四级·多页综合", "p.15–p.17", "doc#89",
               "chunk 24655", "正文块", "识图块", "展开全部 23 块",
               "在资料库中检索此主题", "关联题目 17 道"):
        check(kw in kp6, f"点 KP-06 节点后的小窗含「{kw}」")
    check(kp6.count('class="kpblkh"') == 6,
          "长内容只渲染前 6 块（展开保护在真流程里生效）",
          str(kp6.count('class="kpblkh"')))
    check("查看该节点的知识点" not in kp6 and "doSearch" not in kp6,
          "弹窗内容里没有「默认跳检索」的痕迹")
    check("四级节点" in out["lv4"] and "查看父级知识点" in out["lv4"]
          and "胰岛素的作用" in out["lv4"],
          "四级节点：提示已并入父级 + 给出跳父级按钮")
    check("分类节点" in out["cat"] and "展开子节点" in out["cat"],
          "分类节点：提示展开子节点")
    check("非教学页" in out["e1"], "E1：提示非教学页不建知识点")
    check(out["escClosed"] is True, "Esc 能关掉小窗")
    check(out["reopened"] is True, "关掉后还能再点开别的节点")
    check(out["outsideClosed"] is True, "点小窗外部能关掉")
    check(out["pos"]["display"] == "block" and out["pos"]["left"] != "",
          "小窗被真正显示并定位（不是留在左上角）", str(out["pos"]))
    check("胰岛素的作用" in out["race"]
          and "胰腺与胰岛细胞" not in out["race"],
          "连点两个节点：最终显示后点的那个，内容不串")


def emit(head: str) -> None:
    """结论行放第一行并 flush（宿主按它判断成败）。"""
    print(head, flush=True)
    print("\n".join(LOG), flush=True)
    if FAILS:
        print(f"\n失败 {len(FAILS)} 项：", flush=True)
        for f in FAILS:
            print("  - " + f, flush=True)
    else:
        print(f"\n全部 {N_OK} 项断言通过。", flush=True)


if __name__ == "__main__":
    sys.exit(main())