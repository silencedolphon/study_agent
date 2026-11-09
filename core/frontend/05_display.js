// 知识点窗口的显示偏好只保存在当前浏览器，不改动资料或模型设置。
const KPW_KEY = 'study_agent_knowledge_window_v1';
const KPW_DEFAULT = {width:87.5, height:87.5};
function kpWindowClamp(v){
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(100, Math.max(40, n)) : 87.5;
}
function kpWindowRead(){
  try {
    const saved = JSON.parse(localStorage.getItem(KPW_KEY) || 'null');
    if (saved && saved.width != null && saved.height != null)
      return {width:kpWindowClamp(saved.width), height:kpWindowClamp(saved.height)};
  } catch(e){}
  return {...KPW_DEFAULT};
}
let KPW = kpWindowRead();
function kpWindowSet(width, height){
  KPW = {width:kpWindowClamp(width), height:kpWindowClamp(height)};
  try { localStorage.setItem(KPW_KEY, JSON.stringify(KPW)); } catch(e){}
  kpDisplayPaint();
  if (typeof EVPOP !== 'undefined' && EVPOP && EVPOP.dataset.kpPop === '1' && EVPOP.style.display !== 'none')
    evPopPlace(null);
}
function kpDisplayPaneHtml(){
  return `<h3>显示设置</h3><div class="pdesc">调整教材和课件知识点的阅读窗口。</div>
    <div class="settings-card kp-window-settings">
      <h4>知识点窗口大小</h4>
      <div class="small muted">比例按当前网页可视区域计算，拖动窗口顶部可移动位置。</div>
      <div class="kp-window-preview" aria-label="窗口大小预览">
        <div id="kp-size-sample" class="kp-window-sample"><span>知识点窗口</span></div>
      </div>
      <label class="kp-size-row srow" for="kp-size-width" data-search="知识点 窗口 大小 宽度">
        <span>宽度</span><input type="range" id="kp-size-width" min="40" max="100" step="0.5" value="${KPW.width}">
        <output id="kp-size-width-value">${KPW.width}%</output>
      </label>
      <label class="kp-size-row srow" for="kp-size-height" data-search="知识点 窗口 大小 高度">
        <span>高度</span><input type="range" id="kp-size-height" min="40" max="100" step="0.5" value="${KPW.height}">
        <output id="kp-size-height-value">${KPW.height}%</output>
      </label>
      <button class="ghost" id="kp-size-reset" type="button">恢复默认（87.5%）</button>
      <div class="small muted" style="margin-top:9px">调整后自动保存在当前浏览器。</div>
    </div>`;
}
function kpDisplayPaint(){
  const width = document.getElementById('kp-size-width');
  const height = document.getElementById('kp-size-height');
  const wv = document.getElementById('kp-size-width-value');
  const hv = document.getElementById('kp-size-height-value');
  const sample = document.getElementById('kp-size-sample');
  if (width) width.value = KPW.width;
  if (height) height.value = KPW.height;
  if (wv) wv.textContent = KPW.width + '%';
  if (hv) hv.textContent = KPW.height + '%';
  if (sample){ sample.style.width = KPW.width + '%'; sample.style.height = KPW.height + '%'; }
}
function kpDisplayBind(){
  const width = document.getElementById('kp-size-width');
  const height = document.getElementById('kp-size-height');
  const reset = document.getElementById('kp-size-reset');
  if (width) width.oninput = () => kpWindowSet(width.value, KPW.height);
  if (height) height.oninput = () => kpWindowSet(KPW.width, height.value);
  if (reset) reset.onclick = () => kpWindowSet(KPW_DEFAULT.width, KPW_DEFAULT.height);
  kpDisplayPaint();
}
