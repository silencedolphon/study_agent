const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const root = path.join(__dirname, '..', 'core', 'frontend');
const ctx = {console}; ctx.window = ctx; vm.createContext(ctx);
for (const name of ['vendor_markdown_it.js', 'vendor_katex.js', 'vendor_texmath.js'])
  vm.runInContext(fs.readFileSync(path.join(root, name), 'utf8'), ctx);
vm.runInContext(`function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}`, ctx);
vm.runInContext(fs.readFileSync(path.join(root, '10_assistant.js'), 'utf8').split('// 把 [1]')[0], ctx);
function render(src){ ctx.input = src; return vm.runInContext('mdToHtml(input)', ctx); }
for (const src of [String.raw`行内 $E=mc^2$ 文本`, String.raw`\(\frac{a}{b}\)`,
                   String.raw`$$\frac{a}{b}$$`, String.raw`\[x^2\]`]) {
  assert(render(src).includes('class="katex"'), 'math should render: ' + src);
}
assert(!render('`$x$`').includes('class="katex"'), 'code is not math');
for (const raw of ['Fsys = Fabs * Fg * FH', 'F = fa × fg × fh × fl',
  'pKa−pH=log(Cu/Ci)', 'dC/dt = DS/h·(Cs−C) = kS(Cs−C)', 't1/2 超过 50 h'])
  assert(render(raw).includes('class="katex"'), 'legacy expression should render: ' + raw);
assert(!render('羰基 C=O；价格 $5 和 $6').includes('class="katex"'));
assert(!render('`Fsys = Fabs * Fg * FH`').includes('class="katex"'));
for (const raw of ['F=1−e^(−2An)', 'pH=log(Cu/Ci)', 'dC/dt=kSCs'])
  assert(render(raw).includes('class="katex"'), 'unmarked equation should render: ' + raw);
for (const raw of ['r=(1−e^(−nkτ))/(1−e^(−kτ))', '(Xn)min=(Xn)max·e^(−kτ)',
  'fss(n)=1−e^(−nkτ)', '−dX/dt=kX^n', 'fss=Cn/Css=1−e^(−nkτ)',
  'n=0', 'pH=7.4', 't=τ', 'k=0.693/30', 'V = X/C = X0/C0']) {
  const html = render(raw);
  assert(html.includes('class="katex"'), 'recent legacy equation should render: ' + raw);
  assert(!html.includes('katex-error'), 'rendered expression must be valid LaTeX: ' + raw);
}
assert(render('V = X/C = X0/C0').includes('V=X/C = X0/C0'),
  'chained equality should stay in one formula');
assert(/<msup><mi>e<\/mi><mrow>.*?<mn>2<\/mn>.*?<\/mrow><\/msup>/.test(render('F=1−e^(−2An)')),
  'a parenthesized exponent must remain grouped in MathML');
for (const raw of ['C=O', '2026-11-10', '$5 = 1-2', 'https://example.com/?x=1-2',
                   '[x=1-2](https://example.com)', '`pH=log(Cu/Ci)`'])
  assert(!render(raw).includes('class="katex"'), 'non-math must remain unchanged: ' + raw);
assert(render('**重要**：普通文本').includes('<strong>重要</strong>'));
assert(!render(String.raw`$\href{javascript:alert(1)}{click}$`).includes('href="javascript:'));
assert(!render('<img src=x onerror=alert(1)>').includes('<img'));
console.log('Markdown + LaTeX rendering tests passed');
