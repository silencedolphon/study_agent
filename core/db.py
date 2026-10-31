# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。     
# File-ID: SA-05-7c109cf7   (project study_agent; keep this line)
"""SQLite 存储层：文档注册表 + 切块 + 向量 + 全文索引 + 学习数据。

设计要点：
- 向量以 float32 BLOB 存在 embeddings 表，与 chunks 分表：扫元数据时不拖大字段。
- 保留 FTS5 全文索引（可用则建），与向量结果做 RRF 融合，避免纯向量的不可解释性。
- 每个 chunk 都带 source_ref 与 heading_path，检索结果天然可回链。
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path

from . import config

SCHEMA_VERSION = 1

# 写锁等待上限（毫秒）。**所有**连接的默认等待时长，见 `connect()`。
#
# 为什么要有这个常量、而不是只靠 `sqlite3.connect(timeout=...)`：
# `timeout` 参数在 CPython 里就是 `sqlite3_busy_timeout()`，两者等价，
# 但它是**隐式**的——代码里读不出"撞锁会等多久"。显式 PRAGMA 之后，
# 任何一条连接都能用 `PRAGMA busy_timeout` 当场核对（卡 C-0040 实测：
# 两条路径都返回 30000）。
BUSY_TIMEOUT_MS = 30000

# 真实资料库路径的快照，在模块加载时固定。
# 测试脚本会临时改写 config.DB_PATH 指向自己的临时库，
# 护栏必须与"最初的真实库"比对，否则会误判（见 connect()）。
_REAL_DB_PATH = Path(config.DB_PATH).resolve()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 文档注册表：以内容 hash 判重，源文件路径只增不改
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT NOT NULL UNIQUE,      -- 相对 KB_ROOT 的路径
    abs_path      TEXT NOT NULL,
    kind          TEXT NOT NULL,             -- pdf / docx / pptx / text / image
    title         TEXT,
    content_hash  TEXT NOT NULL,             -- 源文件内容 sha256
    size_bytes    INTEGER,
    mtime         REAL,
    n_chunks      INTEGER DEFAULT 0,
    n_chars       INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'ok',         -- ok / partial / failed
    role          TEXT DEFAULT 'courseware', -- textbook / courseware / reference
    corpus        TEXT DEFAULT 'main',       -- main / aux；由路径判定，见 classify_corpus
    warnings      TEXT,                      -- JSON 数组
    extra         TEXT,                      -- JSON 元数据
    indexed_at    REAL,
    updated_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
-- 注意：corpus 的索引**不写在这里**。`CREATE TABLE IF NOT EXISTS` 对旧库是空操作，
-- 旧库的 documents 表此刻还没有 corpus 列，在这里建索引会直接报
-- "no such column: corpus"。索引改由 `_migrate()` 在 ALTER TABLE 之后建。

-- 切块：出处信息一等公民
CREATE TABLE IF NOT EXISTS chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id        INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord           INTEGER NOT NULL,          -- 文档内顺序
    text          TEXT NOT NULL,
    text_norm     TEXT,                      -- 去掉全部空白后的副本，供 LIKE 检索
    heading_path  TEXT,                      -- 标题面包屑
    topic         TEXT,                      -- 所属"节"路径（AI 全局分析给出）
    source_ref    TEXT,                      -- p.12 / slide 5 / para 3
    char_len      INTEGER,
    token_est     INTEGER,
    content_hash  TEXT,
    created_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, ord);

-- 向量单独存，blob 不参与元数据扫描
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id  INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    vec       BLOB NOT NULL
);

-- ---------------- 学习层 ----------------
-- 知识点：掌握度与依赖关系挂在这里
CREATE TABLE IF NOT EXISTS knowledge_points (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    summary       TEXT,
    subject       TEXT,
    mastery       REAL DEFAULT 0.0,          -- 0..1
    ease          REAL DEFAULT 2.5,          -- SM-2 ease factor
    interval_days REAL DEFAULT 0.0,
    reps          INTEGER DEFAULT 0,
    lapses        INTEGER DEFAULT 0,
    due_at        REAL,                      -- 下次复习时间戳
    last_review   REAL,
    had_sources   INTEGER DEFAULT 0,         -- 是否曾经关联过原文出处
    created_at    REAL,
    updated_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_kp_due ON knowledge_points(due_at);

-- 知识点与来源切块的关联：结论必须可追溯
CREATE TABLE IF NOT EXISTS kp_sources (
    kp_id     INTEGER NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    chunk_id  INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    note      TEXT,
    PRIMARY KEY (kp_id, chunk_id)
);

-- 知识点依赖图
CREATE TABLE IF NOT EXISTS kp_edges (
    src_id  INTEGER NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    dst_id  INTEGER NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    kind    TEXT DEFAULT 'prereq',           -- prereq / related
    PRIMARY KEY (src_id, dst_id, kind)
);

-- 复习记录：掌握度的证据链
CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kp_id      INTEGER NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    grade      INTEGER NOT NULL,             -- 0..5 (SM-2)
    correct    INTEGER,                      -- 1/0
    question   TEXT,
    answer     TEXT,
    user_note  TEXT,
    elapsed_ms INTEGER,
    ts         REAL
);
CREATE INDEX IF NOT EXISTS idx_reviews_kp ON reviews(kp_id, ts);

-- 错题本：错因与缺失前置点回写到知识点图
CREATE TABLE IF NOT EXISTS mistakes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kp_id        INTEGER REFERENCES knowledge_points(id) ON DELETE SET NULL,
    doc_id       INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    chunk_id     INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
    question     TEXT,
    my_answer    TEXT,
    right_answer TEXT,
    reason       TEXT,                       -- 概念混淆 / 计算失误 / 前置缺失 / 审题
    missing_prereq TEXT,
    resolved     INTEGER DEFAULT 0,
    created_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_mistakes_open ON mistakes(resolved, created_at);

-- 摄入批次日志
CREATE TABLE IF NOT EXISTS ingest_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL,
    ended_at   REAL,
    added      INTEGER DEFAULT 0,
    updated    INTEGER DEFAULT 0,
    skipped    INTEGER DEFAULT 0,
    failed     INTEGER DEFAULT 0,
    detail     TEXT
);
"""

# ---------------- 课件级遗忘曲线（卡 C-0061） ----------------
# 粒度 = 课件（`documents.id`），与**知识点级**的 `reviews` 表是两套账、互不写入。
#
# 为什么另起两张表而不扩 `reviews`：
#   ① `reviews.kp_id` 是 `NOT NULL` 外键，而课件级没有 kp_id 可填；
#   ② `core/study.py::dashboard()` 用 `reviews` 统计"今日已复习**知识点**数"，
#      混入课件级记录会让总览页的数字凭空变大。
#
# 为什么 `due_day` 存 TEXT `'YYYY-MM-DD'` 而不是时间戳 REAL：
#   月历是**日历日**语义（"10 月 3 日该复习哪几份"），用时刻会引入"几点算今天"的坑；
#   而 `due_day >= '2026-10-01' AND due_day < '2026-11-01'` 是纯字符串范围扫描，
#   既吃得上 `idx_deck_reviews_due`，人肉看库时也直接可读。
#   （`knowledge_points.due_at` 是 REAL 时刻，那是"到期队列按时刻比较"的语义，别照抄。）
#
# 为什么 `intervals` 存 JSON 快照：月历上已经标出去的日期不能因为以后改默认参数
# 就整体漂移——"这份计划按录入当时那套间隔走"。
#
# 抽成独立常量是为了给**写路径**复用（`dashboard.py` 的录入接口会就地补建一次，
# 因为只读连接不跑 `init_db()`，见那边的 `_conn(readonly=True)` 说明）。
DECK_SCHEMA = """
CREATE TABLE IF NOT EXISTS deck_plans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    title       TEXT,                       -- 课件标题快照（documents 删了也还能显示）
    subject     TEXT,                       -- 学科名快照，**仅供展示**；隔离靠库文件本身
    enrolled_at REAL NOT NULL,              -- 录入时刻（unix 秒）= 第 1 轮的起算点
    intervals   TEXT NOT NULL,              -- JSON 数组快照，如 "[1,3,7,16,35]"
    n_rounds    INTEGER NOT NULL,           -- 轮次数 = len(intervals)
    offset_days INTEGER DEFAULT 0,          -- 录入软分摊的起始偏移（天）
    status      TEXT DEFAULT 'active',      -- active / finished / archived
    created_at  REAL,
    updated_at  REAL,
    UNIQUE(doc_id)                          -- 幂等的落点：一份课件只有一条计划
);
CREATE INDEX IF NOT EXISTS idx_deck_plans_status ON deck_plans(status, doc_id);

CREATE TABLE IF NOT EXISTS deck_reviews (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id  INTEGER NOT NULL REFERENCES deck_plans(id) ON DELETE CASCADE,
    round    INTEGER NOT NULL,              -- 第几轮，1 起
    due_day  TEXT NOT NULL,                 -- 'YYYY-MM-DD'（本地日历日）
    state    TEXT DEFAULT 'pending',        -- pending / done / skipped
    done_at  REAL,                          -- 完成时刻（unix 秒）
    grade    INTEGER,                       -- 预留：完成质量 0-5；本期默认 NULL
    note     TEXT,
    UNIQUE(plan_id, round)                  -- 一轮一行，重复标记是 UPDATE 不是 INSERT
);
CREATE INDEX IF NOT EXISTS idx_deck_reviews_due ON deck_reviews(state, due_day);
"""

# 新表拼进 SCHEMA：`init_db()` 在哪跑，两张表就在哪建（新库、旧库、新建学科库都一样）。
# **不写进 `_migrate()`**——那只用于给旧表补列（`CREATE TABLE IF NOT EXISTS` 对旧表是空操作，
# 但新表本来就是"不存在就建"，重复执行幂等）。
SCHEMA = SCHEMA + DECK_SCHEMA

# ---------------- 识图表（卡 C-0077） ----------------
# 为什么这张表要挪进 `init_db()` 的建表清单：
#
# 它原来**只由** `core/figures.py::ensure_schema()` 建，而那个函数只在
# **识图入库**这一条路上被调用。于是"刚建完库、还没做过识图入库"的空库里
# 根本没有它，而两条**只读**路径都会 `SELECT ... FROM figure_desc`：
#   · `core/kp_build.py`（看板「AI 知识点总结」）—— 空库第一次点就是 500：
#     `OperationalError: no such table: figure_desc`
#     （确切落点：`dashboard.py::api_kp_build_start` → `kp_build.estimate()` →
#      `read_chunks_v2()` 的 `FROM figure_desc`；源码模式与 exe 都复现）
#   · `core/chat.py::_figure_blocks`（整份课件问答）
# 这是**产品级**缺陷：交付物就是 exe，别人拿到手第一次点总结就报错。
#
# 处置与 `deck_plans` / `deck_reviews` 同一个范式：**建表搬到 init_db()**，
# 于是新库、旧库（下次启动看板时）、新建学科库、打包后的 exe 首启动全都有表。
#
# DDL **逐字照抄** `core/figures.py::ensure_schema()`（列、类型、默认值、UNIQUE、
# 两个索引一个字符都没改），并且从此**只有这一份**——`ensure_figure_schema()`
# 是唯一真源，`figures.ensure_schema()` 改为调用它，两处 DDL 不可能再漂。
FIGURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS figure_desc (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page         INTEGER NOT NULL,
    page_title   TEXT,
    image_hash   TEXT NOT NULL,           -- 整页图的 sha256，用于跳过重复
    model        TEXT,
    thinking     INTEGER DEFAULT 0,
    tokens_in    INTEGER DEFAULT 0,
    tokens_out   INTEGER DEFAULT 0,
    cost_cny     REAL DEFAULT 0,
    n_chunks     INTEGER DEFAULT 0,
    created_at   REAL,
    UNIQUE(doc_id, page)
);
CREATE INDEX IF NOT EXISTS idx_figdoc ON figure_desc(doc_id, page);
CREATE INDEX IF NOT EXISTS idx_fighash ON figure_desc(image_hash);
"""

SCHEMA = SCHEMA + FIGURE_SCHEMA

# FTS5 是可选能力，缺失时降级为 LIKE 检索。
#
# 分词器必须用 trigram，不能用默认的 unicode61：
# unicode61 会把整段连续中文当成**一个** token，实测 MATCH '"梯度消失"' 与
# MATCH '梯度' 都返回 0 行，中文关键词检索完全失效。
# trigram 按三字符滑窗建索引，中文子串匹配正常（"梯度消失"、"失与梯" 均可命中）。
# 代价：trigram 要求查询词至少 3 字符，短词（CNN、AI）由 LIKE 降级路径兜住。
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    heading_path,
    content='chunks',
    content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text, heading_path)
    VALUES (new.id, new.text, COALESCE(new.heading_path, ''));
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, heading_path)
    VALUES ('delete', old.id, old.text, COALESCE(old.heading_path, ''));
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, heading_path)
    VALUES ('delete', old.id, old.text, COALESCE(old.heading_path, ''));
    INSERT INTO chunks_fts(rowid, text, heading_path)
    VALUES (new.id, new.text, COALESCE(new.heading_path, ''));
END;
"""


def connect(db_path: Path | str | None = None, *,
            immediate: bool = False) -> sqlite3.Connection:
    """连库。

    `immediate=True` 用于**写连接**：让隐式事务以 `BEGIN IMMEDIATE` 开始
    （提前拿写锁）。默认 False——只读连接读了就要拿写锁，会让看板里那些
    长跑只读查询互相排队，所以这个开关必须由写路径显式打开
    （批量入库走 `core/pipeline_pool.py::task_connect`）。

    为什么写连接需要它：WAL 下的 **deferred** 事务是"先读后写"，
    读完之后若别的连接提交过写，升级写锁会直接返回 `SQLITE_BUSY` 的
    `SQLITE_BUSY_SNAPSHOT` 变体，**busy_timeout 对它不生效、重试也没用**。
    提前拿写锁从根上避开这个坑。
    """
    path = Path(db_path or config.DB_PATH)
    # 护栏：测试/基准脚本会设置 KB_TESTING=1。
    # 这类脚本常常要清空文档表来构造干净场景，一旦误连真实库就会
    # 抹掉用户的全部资料。本项目已经因此踩坑两次（smoke_test、bench_embed），
    # 所以在这里硬拦一道，而不是指望每次都记得改对路径。
    #
    # 基准必须是"模块加载时的真实库路径"，不能用 config.DB_PATH：
    # 测试脚本会临时改写该配置来指向自己的临时库，
    # 若拿改写后的值比对，就会把自己的测试库误判成真实库而拒绝（已踩过）。
    if os.environ.get("KB_TESTING") == "1":
        real = Path(_REAL_DB_PATH)
        if path.resolve() == real:
            raise RuntimeError(
                f"拒绝连接真实资料库（KB_TESTING=1）: {real}\n"
                f"测试脚本必须使用独立的数据库文件，"
                f"否则会清空用户数据。"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    # timeout 与显式 PRAGMA 是同一件事（CPython 的 timeout 就是 busy_timeout），
    # 两处都写是为了让"等锁 30 秒"在代码里可见、可核对。
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    if immediate:
        conn.isolation_level = "IMMEDIATE"
    return conn


def has_fts5(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS __fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS __fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def has_trigram(conn: sqlite3.Connection) -> bool:
    """trigram 分词器需要 SQLite >= 3.34。缺失时退回 LIKE 检索。"""
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS __tri_probe "
            "USING fts5(x, tokenize='trigram')"
        )
        conn.execute("DROP TABLE IF EXISTS __tri_probe")
        return True
    except sqlite3.OperationalError:
        return False


def _fts_tokenizer(conn: sqlite3.Connection) -> str:
    """读出已存在 FTS 表实际使用的分词器，用于检测需要迁移的旧表。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
    ).fetchone()
    if not row or not row["sql"]:
        return ""
    sql = row["sql"]
    if "trigram" in sql:
        return "trigram"
    if "unicode61" in sql:
        return "unicode61"
    return "unknown"


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    """删表重建并回填，用于分词器升级后的迁移。"""
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS chunks_ai;
        DROP TRIGGER IF EXISTS chunks_ad;
        DROP TRIGGER IF EXISTS chunks_au;
        DROP TABLE IF EXISTS chunks_fts;
        """
    )
    conn.executescript(FTS_SCHEMA)
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()


def _ensure_figure_columns(conn: sqlite3.Connection) -> list[str]:
    """给**旧库**的 `figure_desc` 补后加的两列（幂等；新库由 SCHEMA 建完就没得补）。

    为什么这两列不写进 `FIGURE_SCHEMA` 的 CREATE TABLE：
    它们本来就是"后加的列"（见 `figures.py` 里那段说明——描述正文必须落库，
    否则"图片没变就复用旧描述、不重复计费"会退化成重跑）。写进 CREATE 只会让
    **新建表**与**老路径 ALTER 出来的表**列序、默认值都对齐的现状变复杂；
    而 `CREATE TABLE IF NOT EXISTS` 对已存在的旧表是空操作，补列只能靠 ALTER。
    于是统一走这一个函数——`init_db()`（新库/旧库）与识图路径共用它。
    """
    done: list[str] = []
    cols: set = set()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='figure_desc'").fetchone():
        cols = {r[1] for r in conn.execute("PRAGMA table_info(figure_desc)")}
    else:
        return done          # 表都没有（没跑过 SCHEMA），无需补列
    if "description" not in cols:
        conn.execute("ALTER TABLE figure_desc ADD COLUMN description TEXT")
        done.append("figure_desc.description 已补齐")
    if "no_info" not in cols:
        conn.execute("ALTER TABLE figure_desc ADD COLUMN no_info INTEGER DEFAULT 0")
        done.append("figure_desc.no_info 已补齐")
    return done


def ensure_figure_schema(conn: sqlite3.Connection) -> None:
    """建识图表与索引（幂等），并补齐后加的列。

    这是 `figure_desc` 结构定义的**唯一真源**（卡 C-0077）：
      · `init_db()` 经 `SCHEMA` 建表 + 经 `_migrate()` 补列；
      · `core/figures.py::ensure_schema()` 直接调它——识图路径可能压根没跑过
        `init_db()`（如 `tools/ingest_figures.py`），那条路上也得有表。
    两处都走同一条 DDL，不会再出现"一边改了、另一边没改"的漂移。
    """
    conn.executescript(FIGURE_SCHEMA)
    _ensure_figure_columns(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """针对旧库做最小增量迁移（CREATE TABLE IF NOT EXISTS 不会补新列）。"""
    done: list[str] = []
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(chunks)")}
    if "text_norm" not in cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN text_norm TEXT")
        rows = conn.execute("SELECT id, text FROM chunks").fetchall()
        conn.executemany(
            "UPDATE chunks SET text_norm=? WHERE id=?",
            [(normalize_for_match(r["text"]), int(r["id"])) for r in rows],
        )
        done.append(f"chunks.text_norm 已补齐（{len(rows)} 行）")

    # 所属"节"路径：新入库管线（AI 全局分析）用它判断"哪些页属于同一节"。
    #
    # 为什么必须单独存一列，而不是从 heading_path 反推：
    # 新管线的面包屑是「节路径 > 本页主题」，**每页的最后一级都不同**，
    # 于是老管线那套"面包屑去掉〔续〕后相同即同主题"的判据全部失效，
    # 跨页扩展一块也补不出来（实测检索层覆盖 0/3）。
    # 老管线的块此列为 NULL，检索时自动退回老判据，行为不变。
    if "topic" not in cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN topic TEXT")
        done.append("chunks.topic 已补齐")

    kp_cols = {r["name"] for r in conn.execute("PRAGMA table_info(knowledge_points)")}
    if "had_sources" not in kp_cols:
        conn.execute(
            "ALTER TABLE knowledge_points ADD COLUMN had_sources INTEGER DEFAULT 0")
        # 回填：当前确实挂着出处的置 1，其余保持 0。
        # 这样"曾经有出处、后来因删资料失去"与"从未关联过出处"能区分开，
        # 孤儿清理只清前者，不会误删手工创建的知识点。
        n = conn.execute(
            """UPDATE knowledge_points SET had_sources=1
               WHERE id IN (SELECT DISTINCT kp_id FROM kp_sources)"""
        ).rowcount
        done.append(f"knowledge_points.had_sources 已补齐（回填 {n} 行）")

    doc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
    if "role" not in doc_cols:
        conn.execute(
            "ALTER TABLE documents ADD COLUMN role TEXT DEFAULT 'courseware'")
        # 回填用与 ingest 相同的判定规则，避免新旧数据口径不一致。
        # 从 ingest 导入会形成循环依赖，所以规则放在本模块底部。
        rows = conn.execute("SELECT id, path, title FROM documents").fetchall()
        cnt: dict[str, int] = {}
        for r in rows:
            role = classify_role(r["title"] or "", r["path"] or "")
            conn.execute("UPDATE documents SET role=? WHERE id=?", (role, r["id"]))
            cnt[role] = cnt.get(role, 0) + 1
        summary = " / ".join(f"{k} {v}" for k, v in sorted(cnt.items()))
        done.append(f"documents.role 已补齐（{summary}）")

    # 库归属（阶段 1 / 卡 C-0018）：只**登记**归属，本阶段不让它参与任何检索。
    #
    # 为什么回填也必须走 classify_corpus、而不是直接 UPDATE ... = 'main'：
    # 与 role 列同一个范式（见上面那段注释）——判定规则只写一处，
    # `_migrate` 回填与 `_upsert_doc` 写入调用同一个函数，新旧数据口径才不会分叉。
    if "corpus" not in doc_cols:
        conn.execute("ALTER TABLE documents ADD COLUMN corpus TEXT DEFAULT 'main'")
        rows = conn.execute("SELECT id, path FROM documents").fetchall()
        c_cnt: dict[str, int] = {}
        for r in rows:
            corpus = classify_corpus(r["path"] or "")
            conn.execute("UPDATE documents SET corpus=? WHERE id=?",
                         (corpus, r["id"]))
            c_cnt[corpus] = c_cnt.get(corpus, 0) + 1
        c_summary = " / ".join(f"{k} {v}" for k, v in sorted(c_cnt.items()))
        done.append(f"documents.corpus 已补齐（{c_summary}）")
    # 旧库补索引（新库由 SCHEMA 建）。IF NOT EXISTS，重复执行无副作用。
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_corpus ON documents(corpus)")

    # 识图表的后加列（卡 C-0077）：表本身现在由 `SCHEMA` 建（新库与旧库都有），
    # 但**旧库**里那张表可能是识图路径早年建的，缺 `description` / `no_info`。
    done += _ensure_figure_columns(conn)

    if done:
        conn.commit()
    return done


# 课件 vs 教材的判定规则。
#
# 用户的实际资料构成：**只有《药理学 第6版》是教材，其余 35 份全是课件**
# ——包括那些讲课用的 PDF 导出件（如 `1-卢-抗高血压药 2025.pdf`，
# 它其实是 ppt 转的 pdf）。所以**不能按文件扩展名判**：
# 扩展名区分的是"文件格式"，而这里要区分的是"资料在教学中的角色"。
#
# 判据是标题/文件名里的**教材特征**：版次、教材/教科书字样。
# 课件常见的是章节号、教师姓名、年份、"课件"字样，都不含教材特征。
_TEXTBOOK_PAT = re.compile(
    r"第\s*[0-9一二三四五六七八九十]+\s*版"      # 第6版 —— 版次是教材的强特征
    r"|教材|教科书"
)
_COURSEWARE_PAT = re.compile(
    r"课件|讲义|幻灯|ppt|讲课|课堂|周次"
)


def classify_role(title: str, path: str = "") -> str:
    """判定一份资料在教学中的角色：textbook / courseware / reference。

    先看课件特征：有明确课件标记的一律算课件（"XX第2版课件"也是课件）。
    再看教材特征：有版次/教材字样的算教材。
    都不命中时归 courseware（这套系统里课件是常态）。
    """
    text = f"{title} {path}".strip()
    low = text.lower()
    if _COURSEWARE_PAT.search(low):
        return "courseware"
    if _TEXTBOOK_PAT.search(text):
        return "textbook"
    return "courseware"


# 资料库归属（corpus）的判定规则。
#
# 为什么用**目录**判、而不是像 role 那样用标题/文件名判：
#   role 判的是"教学角色"，而角色写在标题里（版次、教材字样）；
#   corpus 判的是"这份资料是题/笔记，还是课"——它是**用户放置文件的位置**
#   决定的：把文件丢进 `raw\药理题库\` 这个动作本身就是声明。
#   文件名判不可靠：`题库A.docx` 能认出，但明天丢进来一个
#   `2024年真题精选.docx` 就得改正则；而**分类错误会让试题漏进 AI 问答**，
#   这是需求的红线。路径还可自助修正——把文件拖到别的目录即可。
#
# 判定输入就是 `documents.path` 的既有值（相对 KB_ROOT，见 ingest._rel），
# 所以零额外输入、零新参数；库归属与幂等键是同一个值，不可能相互矛盾。
#
# 目录改名时要同步改这里（将来可提为 config 项 KB_AUX_DIRS 让 .env 覆盖）。
AUX_DIRS = ("raw/药理题库", "raw/学习笔记", "notes")


def classify_corpus(rel_path: str) -> str:
    """判定一份资料属于哪个库：'main'（教材+课件）/ 'aux'（试题+资料笔记）。

    只在**目录前缀**上判定，因此对绝对路径同样安全：测试夹具不在
    `config.ROOT` 下，`ingest._rel()` 对它们走 except 分支返回绝对路径
    （如 `C:/.../fixtures/demo/a.md`），不含任何 AUX_DIRS 前缀 → main，
    与现状一致（既不崩，也不会被误判进 aux）。

    前缀带斜杠，所以 `raw/药理题库备份/` 这类同前缀的兄弟目录不会被误判。
    """
    p = (rel_path or "").replace("\\", "/").lstrip("./")
    for d in AUX_DIRS:
        if p == d or p.startswith(d + "/"):
            return "aux"
    return "main"


def init_db(conn: sqlite3.Connection) -> dict:
    conn.executescript(SCHEMA)
    info = {"fts5": False, "tokenizer": "none"}
    info["migrations"] = _migrate(conn)
    if has_fts5(conn) and has_trigram(conn):
        existing = _fts_tokenizer(conn)
        # 旧库可能建的是 unicode61（中文检索失效），检测到就地迁移
        if existing and existing != "trigram":
            _rebuild_fts(conn)
            info["migrated_from"] = existing
        try:
            conn.executescript(FTS_SCHEMA)
            info["fts5"] = True
            info["tokenizer"] = "trigram"
        except sqlite3.OperationalError:
            info["fts5"] = False
    elif has_fts5(conn):
        # 没有 trigram 只能退回 unicode61，中文检索会打折扣，靠 LIKE 兜底
        try:
            conn.executescript(FTS_SCHEMA.replace("tokenize='trigram'", "tokenize='unicode61'"))
            info["fts5"] = True
            info["tokenizer"] = "unicode61"
        except sqlite3.OperationalError:
            info["fts5"] = False
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('fts5', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("1" if info["fts5"] else "0",),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('fts_tokenizer', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (info["tokenizer"],),
    )
    conn.commit()
    return info


def sha256_file(path: Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_WS = re.compile(r"\s+")


def normalize_for_match(text: str) -> str:
    """检索用归一化：去掉全部空白。

    文档里的空白是不可靠的——PDF 文本层会在版心处硬折行，
    可能拼出 "n0 =n2 + 1" 这类形状，而用户查询写的是 "n0 = n2"。
    把两边都去掉空白后再比较，这类差异就完全消失了。
    """
    return _WS.sub("", text or "").lower()


def now() -> float:
    return time.time()


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )

# ---------------- 应用数据版本（P1-A / S08） ----------------
DATA_REVISION_KEY = "data_revision"


def data_revision(conn: sqlite3.Connection) -> int:
    """读应用数据版本；缺失/损坏返回 0（调用方据此禁用缓存，安全回退）。"""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?",
                           (DATA_REVISION_KEY,)).fetchone()
    except sqlite3.Error:
        return 0
    if not row or row[0] in (None, ""):
        return 0
    try:
        return max(0, int(row[0]))
    except (TypeError, ValueError):
        return 0


def bump_data_revision(conn: sqlite3.Connection) -> int:
    """原子 +1（同一条 UPSERT）；**不 commit**，必须由调用方放在同一写事务里。"""
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, '1') "
        "ON CONFLICT(key) DO UPDATE SET value="
        "CAST(COALESCE(meta.value, '0') AS INTEGER) + 1",
        (DATA_REVISION_KEY,),
    )
    return data_revision(conn)
