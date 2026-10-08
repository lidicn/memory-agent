"""只读探针 2：判定 NEW-P2-2 在本居的严重度——被 sensor 域排除掉的占用证据，
在不过滤的域里有没有替代来源。不写库、不建对象。"""
import sqlite3

DB = "/data/memory_agent.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

BEHAVIOR_WORDS = ("有人", "无人", "进入", "离开", "on", "off", "播放中", "暂停", "home", "away")

print("[1] 被排除域里、状态是行为文本的实体（全量，带房间）")
rows = conn.execute(
    "SELECT entity_id, domain, room, COUNT(*) c, MIN(ts) a, MAX(ts) b, "
    "GROUP_CONCAT(DISTINCT new_state) vals FROM events WHERE domain='sensor' "
    "GROUP BY entity_id, room ORDER BY c DESC"
).fetchall()
hits = [r for r in rows if any(w in (r["vals"] or "") for w in BEHAVIOR_WORDS)]
tot = 0
for r in hits:
    print(f"  {r['entity_id']:<50} room={str(r['room']):<8} rows={r['c']:<8} "
          f"span={r['a'][:10]}→{r['b'][:10]} states={(r['vals'] or '')[:48]}")
    tot += r["c"]
print(f"  命中 {len(hits)} 个（实体×房间），合计 {tot} 行")

print("\n[2] 这些占用状态在**未被排除**的域里有没有替代来源")
for w in ("有人", "无人", "进入", "离开"):
    r2 = conn.execute(
        "SELECT domain, COUNT(*) c, COUNT(DISTINCT entity_id) e FROM events "
        "WHERE new_state=? AND domain<>'sensor' GROUP BY domain ORDER BY c DESC LIMIT 6",
        (w,),
    ).fetchall()
    print(f"  state={w:<4} 非 sensor 域: " + (", ".join(f"{x['domain']}={x['c']}行/{x['e']}实体" for x in r2) or "0 行"))

print("\n[3] binary_sensor / event 域在做什么（有没有占用类替代证据）")
for dom in ("binary_sensor", "event", "switch", "light", "climate", "cover"):
    r3 = conn.execute(
        "SELECT COUNT(*) c, COUNT(DISTINCT entity_id) e, MAX(ts) mx FROM events WHERE domain=?",
        (dom,),
    ).fetchone()
    vals = conn.execute(
        "SELECT GROUP_CONCAT(DISTINCT v) g FROM (SELECT new_state v FROM events WHERE domain=? LIMIT 400)",
        (dom,),
    ).fetchone()["g"]
    print(f"  {dom:<14} rows={r3['c']:<9} entities={r3['e']:<5} last={str(r3['mx'])[:16]} states={(vals or '')[:70]}")

print("\n[4] 同名物理设备的多域重复（lumi/ainice 系列按 entity_id 前缀聚）")
r4 = conn.execute(
    "SELECT substr(entity_id, 1, instr(substr(entity_id, 8), '_') + 7) pre, domain, COUNT(*) c "
    "FROM events WHERE entity_id LIKE 'sensor.ainice%' OR entity_id LIKE 'binary_sensor%' "
    "GROUP BY pre, domain ORDER BY c DESC LIMIT 12"
).fetchall()
for x in r4:
    print(f"  {x['pre']:<24} {x['domain']:<14} rows={x['c']}")

print("\nintegrity_check =", conn.execute("PRAGMA integrity_check").fetchone()[0])
conn.close()
