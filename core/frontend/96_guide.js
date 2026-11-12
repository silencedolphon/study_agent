// ---------------- 新手指引 ----------------
// 只展示操作说明，不会启动入库、总结或其他会产生模型调用的任务。
const GUIDE = {
  active: false,
  index: 0,
  returnView: '',
  returnScrollX: 0,
  returnScrollY: 0,
  moreWasOpen: false,
  target: null,
  steps: [
    {title:'欢迎使用学习看板', text:'常用流程是：先添加教材或课件，再整理知识点，最后到复习页自测和回顾。这个指引只介绍入口，不会替你启动任何处理任务。', center:true},
    {title:'页面导航', target:'#nav', text:'顶部按钮用来切换问答、知识点、复习和资料；“更多”里还有总览、预习、错题和笔记。'},
    {title:'搜索资料', target:'#q', text:'在这里搜索已经入库的教材和课件内容。点击搜索结果可以查看相关资料与出处。'},
    {title:'切换学科', target:'#subj-btn', text:'不同学科的资料、知识点、错题和笔记彼此独立。点这里切换或新建学科知识库。'},
    {title:'总览', view:'overview', target:'#view .grid', text:'总览集中显示资料、知识点、待复习项目和错题数量，也会列出当前较薄弱的知识点。'},
    {title:'添加资料', view:'docs', target:'#im-go', text:'在“资料”页粘贴本机文件路径并点“导入”；也可以拖放文件。教材、课件和分享包的导入入口都在这页。'},
    {title:'选择资料库', view:'docs', target:'#docs-tabs', text:'这里切换“教材与课件”和“题库与笔记”两个资料库。文件会按所属资料库分别显示。'},
    {title:'批量入库', view:'docs', target:'#view .docs-batch > summary', text:'展开“批量选择文件入库”，可以一次勾选多份待处理文件。开始前会显示任务数量和费用确认。'},
    {title:'整理课件知识点', view:'kps', target:'#kp-build-open', text:'点“选择课件”后选择要整理的课件。批量任务开始前会显示预计调用和费用；已整理的结果会显示在本页。'},
    {title:'查看课件导图', view:'kps', target:'#mm-box', text:'课件思维导图按层级展示知识点。点具体知识点可打开详情；教材总结和名词解释也在知识点页中。'},
    {title:'开始复习', target:'#nav [data-v="review"]', text:'进入复习页后，选择课件开始自测，再按知识点、笔记和重点名词逐步回顾。复习安排也可以在页面日历中查看。'},
    {title:'教材预习', view:'preview', target:'#view .pv-top', text:'在“更多 → 预习”中选择教材和章节，查看预习重点与自测题；批量预习入口也在页面上方。'},
    {title:'更多页面', target:'#nav-more .nav-more-pop', openMore:true, text:'展开菜单后可进入总览、预习、错题和笔记。错题页用于回看答错的题目，笔记页集中查看学习过程中保存的笔记。'},
    {title:'设置与帮助', target:'#set-btn', text:'设置里可以调整模型、密钥、各管线开关和显示选项。下次需要帮助时，再从“设置 → 新手指引”打开本说明。'}
  ]
};

function guideNode(id){ return document.getElementById(id); }

async function startUserGuide(){
  if (GUIDE.active) return;
  const dirty = (typeof setCollect === 'function' && SET && $('#setmodal')?.classList.contains('on'))
    ? Object.keys(setCollect()).length : 0;
  if (dirty){
    const ok = await confirmBox('有未保存的设置改动',
      `当前有 ${dirty} 项设置尚未保存。打开新手指引会关闭设置窗口，这些改动会丢失。仍要继续吗？`);
    if (!ok) return;
  }
  GUIDE.returnView = CURRENT || 'overview';
  GUIDE.returnScrollX = window.scrollX;
  GUIDE.returnScrollY = window.scrollY;
  GUIDE.moreWasOpen = !!document.querySelector('#nav-more')?.open;
  GUIDE.index = 0;
  GUIDE.active = true;
  closeSettings();
  const layer = guideNode('guide-layer');
  if (!layer) { GUIDE.active = false; return; }
  layer.classList.add('on');
  layer.setAttribute('aria-hidden', 'false');
  document.addEventListener('keydown', guideKey);
  window.addEventListener('resize', guidePosition);
  document.addEventListener('scroll', guidePosition, true);
  await guideShow(0);
}

async function guideShow(index){
  if (!GUIDE.active) return;
  const previous = GUIDE.steps[GUIDE.index];
  if (previous && previous.openMore){
    const more = guideNode('nav-more');
    if (more) more.open = false;
  }
  GUIDE.index = Math.max(0, Math.min(index, GUIDE.steps.length - 1));
  const step = GUIDE.steps[GUIDE.index];
  if (step.view && CURRENT !== step.view) await go(step.view);
  if (!GUIDE.active) return;
  if (step.openMore){
    const more = guideNode('nav-more');
    if (more) more.open = true;
  }
  const card = guideNode('guide-card');
  const layer = guideNode('guide-layer');
  if (!card || !layer) return;
  const target = step.target ? document.querySelector(step.target) : null;
  GUIDE.target = target && target.getClientRects().length ? target : null;
  if (GUIDE.target){
    try { GUIDE.target.scrollIntoView({behavior:'smooth', block:'nearest', inline:'nearest'}); }
    catch(e) { GUIDE.target.scrollIntoView(); }
    await new Promise(resolve => setTimeout(resolve, 220));
  }
  const n = GUIDE.index + 1, total = GUIDE.steps.length;
  const missing = step.target && !GUIDE.target
    ? '<p class="guide-missing">当前页面没有显示这个区域，可以继续浏览其他步骤。</p>' : '';
  card.className = 'guide-card' + (step.center || !GUIDE.target ? ' center' : '');
  card.innerHTML = `<div class="guide-count">${n} / ${total}</div>
    <h2 id="guide-title">${esc(step.title)}</h2><p>${esc(step.text)}</p>${missing}
    <div class="guide-actions">
      <button class="text-btn" id="guide-skip">跳过指引</button><span class="guide-spacer"></span>
      ${GUIDE.index ? '<button class="ghost" id="guide-prev">上一步</button>' : ''}
      <button class="act" id="guide-next">${GUIDE.index === total - 1 ? '完成' : '下一步'}</button>
    </div>`;
  const prev = guideNode('guide-prev'), next = guideNode('guide-next'), skip = guideNode('guide-skip');
  if (prev) prev.onclick = () => guideShow(GUIDE.index - 1);
  if (next) next.onclick = () => GUIDE.index === total - 1 ? guideExit() : guideShow(GUIDE.index + 1);
  if (skip) skip.onclick = guideExit;
  guidePosition();
}

function guidePosition(){
  if (!GUIDE.active) return;
  const layer = guideNode('guide-layer');
  const hole = guideNode('guide-hole');
  const top = guideNode('guide-shade-top'), left = guideNode('guide-shade-left');
  const right = guideNode('guide-shade-right'), bottom = guideNode('guide-shade-bottom');
  const card = guideNode('guide-card');
  if (!layer || !hole || !card) return;
  const w = window.innerWidth, h = window.innerHeight;
  const t = GUIDE.target;
  if (!t || !t.getClientRects().length){
    [top,left,right,bottom].forEach(el => { el.style.cssText = ''; });
    top.style.cssText = `left:0;top:0;width:${w}px;height:${h}px`;
    hole.style.display = 'none';
    card.classList.add('center');
    return;
  }
  const r = t.getBoundingClientRect(), pad = 7;
  const x = Math.max(0, r.left - pad), y = Math.max(0, r.top - pad);
  const x2 = Math.min(w, r.right + pad), y2 = Math.min(h, r.bottom + pad);
  hole.style.display = 'block';
  hole.style.cssText = `display:block;left:${x}px;top:${y}px;width:${Math.max(0,x2-x)}px;height:${Math.max(0,y2-y)}px`;
  top.style.cssText = `left:0;top:0;width:${w}px;height:${y}px`;
  left.style.cssText = `left:0;top:${y}px;width:${x}px;height:${Math.max(0,y2-y)}px`;
  right.style.cssText = `left:${x2}px;top:${y}px;width:${Math.max(0,w-x2)}px;height:${Math.max(0,y2-y)}px`;
  bottom.style.cssText = `left:0;top:${y2}px;width:${w}px;height:${Math.max(0,h-y2)}px`;
  card.classList.remove('center');
  card.classList.toggle('top', r.top > h * .54);
}

async function guideExit(){
  if (!GUIDE.active) return;
  GUIDE.active = false;
  document.removeEventListener('keydown', guideKey);
  window.removeEventListener('resize', guidePosition);
  document.removeEventListener('scroll', guidePosition, true);
  const layer = guideNode('guide-layer');
  if (layer){ layer.classList.remove('on'); layer.setAttribute('aria-hidden', 'true'); }
  const more = guideNode('nav-more');
  if (more) more.open = false;
  GUIDE.target = null;
  if (GUIDE.returnView && CURRENT !== GUIDE.returnView) await go(GUIDE.returnView);
  window.scrollTo(GUIDE.returnScrollX, GUIDE.returnScrollY);
  const restoredMore = guideNode('nav-more');
  if (restoredMore) restoredMore.open = GUIDE.moreWasOpen;
}

function guideKey(e){
  if (!GUIDE.active) return;
  if (e.key === 'Escape') { e.preventDefault(); guideExit(); }
  else if (e.key === 'ArrowRight' && GUIDE.index < GUIDE.steps.length - 1){ e.preventDefault(); guideShow(GUIDE.index + 1); }
  else if (e.key === 'ArrowLeft' && GUIDE.index > 0){ e.preventDefault(); guideShow(GUIDE.index - 1); }
}

window.startUserGuide = startUserGuide;
