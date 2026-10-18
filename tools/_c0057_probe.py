# C-0057 临时侦查脚本 2（交付前删除）
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
c = sqlite3.connect("file:G:/study_agent/index/study.db?mode=ro", uri=True)
print("== chunks 规模 by doc (前 25 大) ==")
q = """select d.id, d.title, d.kind, count(*) n,
       sum(c.char_len) chars, sum(coalesce(c.token_est,0)) tok,
       max(c.token_est) maxtok
       from chunks c join documents d on d.id=c.doc_id
       group by d.id order by tok desc limit 25"""
for r in c.execute(q):
    print(r)
print()
print("== figure_desc 汇总 ==")
for r in c.execute("""select doc_id, count(*) n, sum(length(coalesce(description,''))),
                      sum(coalesce(tokens_in,0)), sum(coalesce(tokens_out,0)),
                      sum(coalesce(no_info,0)) from figure_desc group by doc_id
                      order by 3 desc limit 15"""):
    print(r)
print()
print("== chunks 表列 ==")
print([x[1] for x in c.execute("pragma table_info(chunks)")])
print("== 单块样例（doc 71 前 3 块）==")
for r in c.execute("""select id, ord, source_ref, heading_path, topic, char_len, token_est,
                      substr(text,1,120) from chunks where doc_id=71 order by ord limit 3"""):
    print(r)
print()
print("== 五表行数 ==")
for t in ("knowledge_points", "kp_edges", "kp_sources", "reviews", "mistakes"):
    print(t, c.execute("select count(*) from " + t).fetchone()[0])
print("== meta ==")
for r in c.execute("select key, value from meta"):
    print(r)
