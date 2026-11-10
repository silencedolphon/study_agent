# 第三方组件与许可声明（THIRD-PARTY NOTICES）

**软件**：study_agent（学习看板）　**著作权人**：潜声海豚 `<2227663973@qq.com>`
**本文件版本**：v1.0　**生成日期**：2026-10-09
**对应依赖清单**：`requirements.txt`（32 个直接依赖，版本逐一锁定）
**许可原文**：见同目录 `THIRD-PARTY-LICENSES.txt`（约 460 KB，从本机虚拟环境中各包自带的许可文件原样复制）

**前端 Markdown 组件**：`markdown-it` 15.0.3，MIT 许可，来源 <https://github.com/markdown-it/markdown-it>。许可全文附在 `core/frontend/vendor_markdown_it.js` 顶部及同目录 `vendor_markdown_it.LICENSE.txt`。

**前端公式组件**：`KaTeX` 0.16.22（MIT，<https://github.com/KaTeX/KaTeX>）及 `markdown-it-texmath` 1.0.0（MIT，<https://github.com/goessner/markdown-it-texmath>）；许可全文分别见 `core/frontend/vendor_katex.LICENSE.txt` 和 `core/frontend/vendor_texmath.LICENSE.txt`。

---

## 0. 这个文件是干什么的

本软件以**编译后的可执行文件（exe）**形式分发，**不提供自身源代码**（见 `LICENSE`）。
但 exe 内部打包了大量第三方开源组件，而这些组件的许可（MIT / BSD / Apache-2.0 / MPL-2.0 …）
**要求在分发时保留版权声明与许可文本**。

因此：

- 本文件 = 组件清单（名称 / 版本 / 许可类型 / 来源）；
- `THIRD-PARTY-LICENSES.txt` = 各组件的许可**原文**；
- 二者必须与 exe **一同提供**，不得删除。

> 本文件只描述第三方组件，不改变本软件自身的专有许可（`LICENSE`）。
> 第三方组件的许可**优先适用于该组件自身**；若与 `LICENSE` 冲突，就该组件以第三方许可为准。

---

## 1. ⚠ 风险提示（必读）：PyMuPDF 是 AGPL-3.0 / 商业双许可

**结论：已核实。`pymupdf` 不是宽松许可，而是 `AGPL-3.0` 或 Artifex 商业许可（双许可）。**

| 项目 | 内容 |
|---|---|
| 组件 | PyMuPDF（Python 导入名 `fitz`，版本 1.28.2） |
| 许可 | **GNU AGPL-3.0 或 Artifex Software 商业许可**（二选一） |
| 本地证据 | `venv\Lib\site-packages\pymupdf-1.28.2.dist-info\COPYING`，全文一句话：`Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License` |
| 上游证据 | 官方许可页 <https://pymupdf.io/licensing>：明确写「Use in proprietary software: **No**（AGPLv3 路线）／Yes（商业路线）」 |
| 是否真的进了 exe | **是**。打包脚本 `tools/pack_exe.py` 的 `THIRD_PARTY` 清单含 `fitz`；此前构建产物中可见 `_internal\pymupdf\mupdfcpp64.dll`（约 26 MB）与 `_mupdf.pyd` |

### 为什么这是真实风险

AGPL-3.0 是**强 copyleft**，比 GPL 更严：

1. **分发即触发源码义务**：你把 exe 给别人，就等于分发 AGPL 覆盖的代码，需要向接收者提供**整个衍生作品的对应源代码**（AGPL 第 5 条）。而本软件明确选择「不公开源码」——**两者直接冲突**。
2. **网络使用也触发**：即使不分发，只要把基于它的服务通过网络提供给用户，也要向这些用户提供源码（AGPL 第 13 条）。
3. 「exe 是编译产物」**不构成豁免**。目标代码形式的分发同样受 AGPL 约束。

### 可选应对（按推荐顺序）

| 方案 | 做法 | 代价 |
|---|---|---|
| **A. 只自用，不公开分发**（当前最现实） | 自己打包、自己用，或只给少数几位明确知情的同事/朋友并说明该组件许可 | ¥0；但对「分享给别人」这件事构成限制 |
| **B. 换掉 PyMuPDF** | 改用宽松许可的 PDF 解析方案（如 `pypdf` / `pdfminer.six`，均为 BSD/MIT 类） | 需要改代码并回归测试 PDF 解析链路；工作量中等 |
| **C. 购买 Artifex 商业许可** | 向 Artifex 购买商业授权，即可闭源分发 | 需要付费（金额需与其销售方确认）；对个人学习项目通常不划算 |
| **D. 整体开源** | 按 AGPL-3.0 发布本软件源码 | **与人的决定（源码不公开）直接冲突，已排除** |

> **在本问题解决之前，不建议把打包好的 exe 公开发布到任何公开渠道。**
> 私下自用不受影响——AGPL 的义务主要由「分发」和「对外提供网络服务」触发。
>
> 备注：官方页面另有一个「Open Source and Community-Driven」路线，那是给**开源项目**用的；
> 本软件选择闭源，因此不适用。

### 另一个需要留意的 copyleft 组件（风险等级：低）

| 组件 | 许可 | 性质 | 说明 |
|---|---|---|---|
| **GEOS**（Shapely 捆绑的 native 库） | **LGPL-2.1** | 弱 copyleft | 以**独立 DLL** 形式随包分发（`shapely.libs\geos-*.dll`、`geos_c-*.dll`）。LGPL 允许专有软件**动态链接**使用，但要求：① 告知接收者该库为 LGPL；② 提供该库源码的获取途径。**本文件与 `THIRD-PARTY-LICENSES.txt` 已满足①**；②的源码获取途径：<https://libgeos.org/> |
| certifi / tqdm | MPL-2.0 | 文件级弱 copyleft | MPL-2.0 只要求对**被 MPL 覆盖的那些文件**提供源码，不传染整个软件。二进制分发时须告知源码获取途径，见第 4 节。 |

---

## 2. 全部 32 个直接依赖

「证据」列的含义见第 5 节。所有组件均**已确认许可类型**，无「未核实」项（未能核实的资产另见第 3 节）。

| # | 组件 | 版本 | 许可类型 | 来源 | 证据 |
|---:|---|---|---|---|---|
| 1 | anyio | 4.15.1 | MIT | <https://github.com/agronholm/anyio> | A |
| 2 | certifi | 2026.7.22 | MPL-2.0 | <https://github.com/certifi/python-certifi> | A |
| 3 | click | 8.5.0 | BSD-3-Clause | <https://github.com/pallets/click> | A |
| 4 | colorama | 0.4.6 | BSD-3-Clause | <https://github.com/tartley/colorama> | A |
| 5 | filelock | 3.32.6 | MIT | <https://github.com/tox-dev/py-filelock> | A |
| 6 | flatbuffers | 25.12.19 | Apache-2.0 | <https://github.com/google/flatbuffers> | B |
| 7 | fsspec | 2026.7.0 | BSD-3-Clause | <https://github.com/fsspec/filesystem_spec> | A |
| 8 | h11 | 0.16.0 | MIT | <https://github.com/python-hyper/h11> | A |
| 9 | hf-xet | 1.6.0 | Apache-2.0 | <https://github.com/huggingface/xet-core> | A |
| 10 | httpcore | 1.0.9 | BSD-3-Clause | <https://github.com/encode/httpcore> | A |
| 11 | httpx | 0.28.1 | BSD-3-Clause | <https://github.com/encode/httpx> | A |
| 12 | huggingface_hub | 1.31.0 | Apache-2.0 | <https://github.com/huggingface/huggingface_hub> | A |
| 13 | idna | 3.19 | BSD-3-Clause | <https://github.com/kjd/idna> | A |
| 14 | lxml | 6.1.3 | BSD-3-Clause | <https://github.com/lxml/lxml> | A |
| 15 | numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | <https://github.com/numpy/numpy> | A |
| 16 | onnxruntime | 1.30.0 | MIT | <https://github.com/microsoft/onnxruntime> | B |
| 17 | opencv-python | 5.0.0.93 | 打包层 MIT；内含 OpenCV 二进制为 Apache-2.0 | <https://github.com/opencv/opencv-python> | A |
| 18 | packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | <https://github.com/pypa/packaging> | A |
| 19 | pillow | 12.3.0 | MIT-CMU（HPND） | <https://github.com/python-pillow/Pillow> | A |
| 20 | protobuf | 7.36.1 | BSD-3-Clause | <https://github.com/protocolbuffers/protobuf> | A |
| 21 | pyclipper | 1.4.0 | MIT | <https://github.com/fonttools/pyclipper> | A |
| 22 | **pymupdf** | **1.28.2** | **AGPL-3.0 或 Artifex 商业许可（双许可）** ⚠ | <https://pymupdf.io/licensing> | A |
| 23 | python-docx | 1.2.0 | MIT | <https://github.com/python-openxml/python-docx> | A |
| 24 | python-pptx | 1.0.2 | MIT | <https://github.com/scanny/python-pptx> | A |
| 25 | PyYAML | 6.0.3 | MIT | <https://github.com/yaml/pyyaml> | A |
| 26 | rapidocr-onnxruntime | 1.2.3 | Apache-2.0 | <https://github.com/RapidAI/RapidOCR> | B |
| 27 | shapely | 2.1.2 | BSD-3-Clause（另捆绑 GEOS，LGPL-2.1，见第 1 节） | <https://github.com/shapely/shapely> | A |
| 28 | six | 1.17.0 | MIT | <https://github.com/benjaminp/six> | A |
| 29 | tokenizers | 0.23.2 | Apache-2.0 | <https://github.com/huggingface/tokenizers> | B |
| 30 | tqdm | 4.70.1 | MPL-2.0（主体）AND MIT（部分文件） | <https://github.com/tqdm/tqdm> | A |
| 31 | typing_extensions | 4.16.0 | PSF-2.0 | <https://github.com/python/typing_extensions> | A |
| 32 | xlsxwriter | 3.2.9 | BSD-2-Clause | <https://github.com/johnmcnamara/XlsxWriter> | A |

**统计**：宽松许可（MIT / BSD / Apache-2.0 / PSF / Zlib / CC0 / 0BSD）30 个；
弱 copyleft（MPL-2.0，文件级）2 个（certifi、tqdm）；**强 copyleft 1 个（pymupdf，AGPL-3.0）**。

---

## 3. 随包分发的其它第三方资产（不在 `requirements.txt` 内）

exe 除了 Python 包，还会带以下内容。**逐个列出**，并如实标注核实状态。

| 资产 | 来源 | 许可 | 核实状态 |
|---|---|---|---|
| **bge-small-zh-v1.5 嵌入模型**（`models\bge-small-zh-v1.5\`，ONNX 量化版约 23 MB，同样打包进 exe） | 模型原版 BAAI/bge-small-zh-v1.5；本项目默认以 `Xenova/bge-small-zh-v1.5` 这个移植版为仓库名（见 `core/config.py` 的 `EMBED_MODEL_REPO`） | **未核实** | ⚠ **本地 `models\bge-small-zh-v1.5\` 内没有附带 LICENSE / README 文件；本机网络策略下 HuggingFace 不可访问，无法查证。社区惯例通常为 MIT，但本次未取得权威来源，故不写成结论。上线分发前应到模型页确认。** |
| **GEOS**（`shapely.libs\geos-*.dll`、`geos_c-*.dll`） | <https://libgeos.org/> | LGPL-2.1 | 已核实：随包 `shapely-2.1.2.dist-info\licenses\LICENSE_GEOS` 明示 `License: LGPLv2.1`，与文件名 `geos-*.dll` 一致 |
| **OpenBLAS**（`numpy.libs\libscipy_openblas64_*.dll`） | <https://github.com/OpenMathLib/OpenBLAS/> | BSD-3-Clause | 已核实：numpy 许可汇总文件内 `Name: OpenBLAS` 段落附三条款 BSD 全文 |
| **MuPDF**（`pymupdf\mupdfcpp64.dll`、`_mupdf.pyd`） | Artifex | AGPL-3.0（随 PyMuPDF 双许可） | 已核实，见第 1 节 |
| **PP-OCR ONNX 模型**（`rapidocr_onnxruntime\models\*.onnx`） | PaddlePaddle / PaddleOCR | Apache-2.0 | 已核实（随 `rapidocr-onnxruntime` 包分发，适用该包 Apache-2.0） |
| **Microsoft Visual C++ 运行时**（`msvcp140*.dll`） | Microsoft | Microsoft 可再分发条款（Microsoft Distributable Code） | 已核实：随包 `shapely-2.1.2.dist-info\licenses\LICENSE_win32` 明示 `License: MSVC` 并附再分发条款摘要 |
| Python 运行时本体（`python313.dll` 等，由 PyInstaller 打入） | Python Software Foundation | PSF-2.0 | 已核实（PSF 许可允许再分发） |

---

## 4. 分发时你必须一并做到的事（合规清单）

打包给别人时，**exe 所在目录必须同时包含**：

- [ ] `LICENSE`（本软件的专有许可）
- [ ] `NOTICE.txt`（权利归属声明）
- [ ] `THIRD-PARTY-NOTICES.md`（本文件，组件清单）
- [ ] `THIRD-PARTY-LICENSES.txt`（各组件的许可原文）

具体义务来源：

| 许可 | 要求 | 本项目的满足方式 |
|---|---|---|
| MIT / BSD-2 / BSD-3 / PSF / Zlib | 保留版权声明与许可文本 | `THIRD-PARTY-LICENSES.txt` 收录全文 |
| Apache-2.0 | 提供许可副本；保留 NOTICE；标注修改过的文件 | 同上（本项目未修改这些组件的源码） |
| MPL-2.0（certifi、tqdm） | 告知接收者如何取得 MPL 覆盖文件的源码 | 本文件第 2 节给出各组件的来源仓库链接 |
| LGPL-2.1（GEOS） | 告知 + 允许用户替换该库 | 声明见第 1 / 3 节；以独立 DLL 形式分发，用户可自行替换 |

⚠ **当前打包脚本尚未自动带上这些文件**：`tools/pack_exe.py` 的发布清单里目前只有 `README.md`，
没有 `LICENSE` / `NOTICE.txt` / `THIRD-PARTY-NOTICES.md` / `THIRD-PARTY-LICENSES.txt`。
**在公开发布前需要把打包脚本的清单补齐**（该项属于打包卡的范围，不在本文件的改动范围内，此处仅作提示）。

---

## 5. 证据等级与核实方法

| 等级 | 含义 |
|---|---|
| **A** | 本机虚拟环境里该包**自带的许可原文文件**已逐字读取核对（`*.dist-info\licenses\*`、`LICENSE`、`COPYING`、`LICENCE` 等）。许可原文同时被收录进 `THIRD-PARTY-LICENSES.txt`。 |
| **B** | 该包**没有附带许可原文文件**，许可类型取自其**包元数据**（`*.dist-info\METADATA` 的 `License` / `License-Expression` / `Classifier` 字段），并于 2026-10-09 与上游官方来源交叉核实（GitHub 仓库 API 的 `license.spdx_id` 或上游 `LICENSE` 文件）。 |
| **未核实** | 未能取得权威来源，不作猜测。见第 3 节的 bge 模型条目。 |

**核实方法（可复现，全部离线 + 公开页面，未使用任何付费服务）**：

1. 读取 `requirements.txt` 的 32 个锁定版本；
2. 逐个读取 `venv\Lib\site-packages\<包>-<版本>.dist-info\METADATA`；
3. 逐个读取该 dist-info 下的 `LICENSE*` / `COPYING*` / `LICENCE*` / `NOTICE*` 与 `licenses/` 子目录；
4. 对步骤 3 取不到原文的 4 个包（flatbuffers、onnxruntime、rapidocr-onnxruntime、tokenizers），
   查上游官方来源：`https://api.github.com/repos/<owner>/<repo>`（`license.spdx_id`）
   或仓库根目录的 `LICENSE`；
5. PyMuPDF 另查官方许可页 <https://pymupdf.io/licensing>；
6. 捆绑的 native 库（GEOS、OpenBLAS、MSVC 运行时）查其所属包 dist-info 内的专门许可文件。

**已知的表述边界（不夸大、不 hiding）**：

- `numpy` 的许可汇总文件中出现 GPL 相关文字，来源是其捆绑的 **GCC 运行时库**（`GPL-3.0-or-later WITH GCC-exception-3.1`）。该例外条款**明确允许专有程序使用**，不构成本软件的开源义务。
- `pillow` 的许可汇总文件中出现 GPL / LGPL 字样，来源是其源码树中捆绑的若干组件（如 XZ Utils 的命令行脚本、FreeType 的 GPL 备选条款、Autotools 构建文件）。已在本机核对：Pillow 实际安装的二进制扩展为 `_avif / _imaging / _imagingcms / _imagingft / _imagingmath / _imagingmorph / _imagingtk / _webp`，**不含** GPL 许可的 `libimagequant`（RECORD 中无相关条目）。Pillow 主体为 MIT-CMU（HPND），FreeType 部分按 FTL 使用。
- `opencv-python` 的 dist-info 里 `LICENSE.txt` 是**打包脚本作者**的 MIT 许可；其内含的 OpenCV 二进制适用 **Apache-2.0**（见包内 `LICENSE-3RD-PARTY.txt` 开头）。该文件还汇总了 OpenCV 捆绑的众多第三方组件（其中包含 LGPL / GPL 的许可全文）——**这些条目本次未逐项核实**是否全部进入 Windows 版 `cv2` 二进制，如需严格审计请查阅 OpenCV 官方发行说明。
- `onnxruntime` 自身为 MIT，但它捆绑了一批第三方组件（上游仓库另有 `ThirdPartyNotices.txt`）——**未逐项核实**。

---

## 6. 维护要求

1. **依赖版本变化**：`requirements.txt` 中任一依赖的版本变化后，须重新生成本文件与
   `THIRD-PARTY-LICENSES.txt`，并重新核对是否引入新的 copyleft 组件。
2. **新增依赖**：引入新依赖前先确认其许可**不是** AGPL / GPL / SSPL 等强 copyleft（PyMuPDF 的教训）。
3. **本文件不得删除**：删除它会同时违反多个第三方许可（MIT / BSD / Apache-2.0）以及 `LICENSE` 第 5 条。

---

*本文件由卡 C-0075 生成；清单与版本以 `requirements.txt` 为准，许可原文以 `THIRD-PARTY-LICENSES.txt` 为准。*
