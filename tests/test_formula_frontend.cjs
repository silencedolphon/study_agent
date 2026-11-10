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
function tex(src){const m=render(src).match(/<annotation encoding="application\/x-tex">([^<]*)<\/annotation>/); return m ? m[1] : '';}
for (const src of [String.raw`行内 $E=mc^2$ 文本`, String.raw`\(\frac{a}{b}\)`,
                   String.raw`$$\frac{a}{b}$$`, String.raw`\[x^2\]`]) {
  assert(render(src).includes('class="katex"'), 'math should render: ' + src);
}
assert(tex(String.raw`公式为：\[\frac{dX}{dt}=kX\]，其余正文`) === '\\frac{dX}{dt}=kX',
  'multiline bracket-delimited math embedded in a prose paragraph should render');
assert(tex(String.raw`裸 TeX：\frac{a+b}{c}`) === '\\frac{a+b}{c}',
  'bare LaTeX fractions in older outputs should render');
assert(tex(String.raw`裸 TeX：\sqrt[3]{x}`) === '\\sqrt[3]{x}',
  'bare LaTeX roots in older outputs should render');
assert(!render(String.raw`代码：` + '`' + String.raw`\frac{a}{b}` + '`').includes('class="katex"'),
  'bare LaTeX inside inline code is not math');
const unicodeDerivative = '𝑑𝑋𝐶\n𝑑𝑡=𝑘21𝑋𝑝−𝑘12𝑋𝐶−𝑘10𝑋𝐶';
assert(tex(unicodeDerivative).startsWith('\\frac{dXC}{dt}=k_{21}Xp'),
  'Unicode OCR math and stacked derivative layout should be normalized into LaTeX');
assert(tex('通式为 C=C0e^(-kt)+[k0/(kV)](1-e^(-kt))，随后下降') ===
  'C=C_{0}e^{-kt}+[\\frac{k_{0}}{kV}](1-e^{-kt})',
  'a long equation containing square-bracket groups must be captured before Markdown turns it into a link');
assert(tex('lg(Css-C)=−kt/2.303+lg Css').includes('\\frac{kt}{2.303}'),
  'log-linear equations with nested variables and divisions should render completely');
assert(tex('C_T=k0/(kV)') === 'C_{T}=\\frac{k_{0}}{kV}',
  'subscript equations from plain text should be recognized');
assert(tex('Cl_bile=(dX_bile/dt)/C=(C_bile Q_bile)/C').includes('\\frac{\\frac{dX_{bile}}{dt}}{C}'),
  'equations with named subscripts and nested fractions should render');
assert(tex('t′=4h') === 't^{\\prime}=4h', 'prime variables with units should render');
assert(tex('dC/dt') === '\\frac{dC}{dt}', 'standalone derivative fractions should render');
assert(tex('RT/(N·6πηr)').includes('\\frac{RT}{N\\cdot 6\\pi \\eta r}'),
  'standalone Greek-symbol expressions should be emitted as LaTeX');
assert(tex('3D/r²') === '\\frac{3D}{r^{2}}', 'Unicode superscripts in fractions should render');
assert(tex('初始浓度C₀与稳态浓度Css') === 'C_{0}',
  'standalone subscript variables in prose should render as LaTeX');
assert(tex('Cmax') === 'C_{\\max}', 'common pharmacokinetic symbols should keep their subscripts');
assert(!render('Kcal/min、g/d、μg/ml、cm/sec').includes('class="katex"'),
  'units should not be converted into oversized formulas');
assert(!render('oil/water 分配系数').includes('class="katex"'),
  'ordinary slash prose should remain plain text');
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
  'n=0', 'pH=7.4', 't=τ', 'k=0.693/30', 'V = X/C = X0/C0',
  'R=1/[1−e^(−kτ)]', 'X0*＝X0/(1−e^(−kτ))', 'k＝0.693/t1/2']) {
  const html = render(raw);
  assert(html.includes('class="katex"'), 'recent legacy equation should render: ' + raw);
  assert(!html.includes('katex-error'), 'rendered expression must be valid LaTeX: ' + raw);
}
assert(tex('V = X/C = X0/C0') === 'V=\\frac{X}{C} = \\frac{X_{0}}{C_{0}}',
  'all divisions in chained equalities should use fractions');
assert(tex('X0*=X0/(1-e^(-kτ))') === 'X_{0}^{\\star}=\\frac{X_{0}}{1-e^{-k\\tau }}',
  'loading-dose formula should render with a proper fraction and indices');
assert(tex('k=0.693/t1/2') === 'k=\\frac{0.693}{t_{1/2}}',
  'half-life denominator should remain a subscript inside the fraction');
assert(tex('X0*＝X0/(1−e^(−kτ))') === 'X_{0}^{\\star}=\\frac{X_{0}}{1-e^{-k\\tau }}',
  'full-width equals in legacy Render data must still render as LaTeX fractions');
assert(tex('k＝0.693/t1/2') === 'k=\\frac{0.693}{t_{1/2}}',
  'full-width equals must not bypass formula parsing');
assert(tex('R=1/[1−e^(−kτ)]') === 'R=\\frac{1}{[1-e^{-k\\tau }]}',
  'square-bracketed accumulation-factor equation should remain complete as a fraction');
assert(/<msup><mi>e<\/mi><mrow>.*?<mn>2<\/mn>.*?<\/mrow><\/msup>/.test(render('F=1−e^(−2An)')),
  'a parenthesized exponent must remain grouped in MathML');
for (const raw of ['C=O', '2026-11-10', '$5 = 1-2', 'https://example.com/?x=1-2',
                   '[x=1-2](https://example.com)', '`pH=log(Cu/Ci)`'])
  assert(!render(raw).includes('class="katex"'), 'non-math must remain unchanged: ' + raw);
assert(render('**重要**：普通文本').includes('<strong>重要</strong>'));
assert(/\.katex\{font-size:2em!important\}/.test(fs.readFileSync(path.join(root, 'style.css'), 'utf8')),
  'formula font size should be twice the surrounding text size');
assert(!render(String.raw`$\href{javascript:alert(1)}{click}$`).includes('href="javascript:'));
assert(!render('<img src=x onerror=alert(1)>').includes('<img'));
console.log('Markdown + LaTeX rendering tests passed');
