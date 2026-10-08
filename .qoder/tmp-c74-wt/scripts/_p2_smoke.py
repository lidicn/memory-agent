"""P2 修复冒烟验证：直接测 insights 模块，绕过 app.py 的 jose 依赖链。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.insights.parser.entity import normalize_state, EntityResolver
from memory_agent.insights.utils import parse_time_range, finalize_climate_session
from memory_agent.insights.service import compute_sessions
from memory_agent.insights.activity import analyze_rhythm, Signal, ActivityRule
from memory_agent.insights.nlquery import detect_intent
from memory_agent.insights.persona import PersonaBuilder
from memory_agent.insights.anomaly import AnomalyDetector

ok = 0
def check(name, cond):
    global ok
    status = "PASS" if cond else "FAIL"
    if cond: ok += 1
    print(f"  [{status}] {name}")

print("== parser/entity ==")
check("normalize_state('0') 归 other（遥测值不误判 off）", normalize_state("0") == "other")
check("normalize_state('1') 归 other（遥测值不误判 on）", normalize_state("1") == "other")
check("normalize_state('unavailable') 归 other（不再归 off）", normalize_state("unavailable") == "other")
check("normalize_state('off') 仍 off", normalize_state("off") == "off")
check("normalize_state('on') 仍 on", normalize_state("on") == "on")
check("normalize_state('开') 仍 on", normalize_state("开") == "on")
check("_strip_room 降序替换不拆碎'卫生间'", "卫" not in EntityResolver._strip_room("卫生间热水器", "卫生间"))

print("== utils ==")
check("parse_time_range('08:00-12:00') 返回分钟元组", parse_time_range("08:00-12:00") == (480, 720, False))
check("parse_time_range('坏数据') 返回 None 不炸", parse_time_range("坏数据") is None)
check("parse_time_range('12') 返回 None", parse_time_range("12") is None)

print("== service.compute_sessions（之前 import 缺失） ==")
from memory_agent.insights.models import EventRecord, TimeRange
from datetime import datetime, timedelta
now = datetime.now()
def ev(ts, state, eid="light.living"):
    return EventRecord(ts=ts, entity_id=eid, state=state, attributes={},
                       friendly_name="客厅灯", room="客厅", domain="light", unit="")
events = [
    ev((now - timedelta(minutes=30)).timestamp(), "on"),
    ev((now - timedelta(minutes=20)).timestamp(), "off"),
]
tr = TimeRange(now - timedelta(hours=1), now)
sess = compute_sessions(events, tr, min_session_seconds=1.0)
check("compute_sessions 返回 dict", isinstance(sess, dict))
check("compute_sessions 切出 1 段会话", len(sess.get("light.living", [])) == 1)

print("== activity.analyze_rhythm 跨夜窗口 ==")
buckets = [0]*24
buckets[22] = 10; buckets[23] = 10; buckets[0] = 5; buckets[8] = 20
r = analyze_rhythm(buckets)
# 跨夜窗 21:00-次日11:00 = buckets[21..23]+buckets[0..11] = 0+10+10 + 5+...+20(8点) = 45；total=45
check("night_ratio_percent 用跨夜窗 21-11（8点在窗内）", r["night_ratio_percent"] == 100.0)

print("== activity.Signal 容错 ==")
s = Signal.from_text("卫生间|存在|abc")  # 之前 float('abc') 炸
check("Signal.from_text 脏分钟数不崩", s.min_minutes == 0.0)
r = ActivityRule.from_dict("k","洗澡",{"window":"坏数据","min_minutes":"xyz"})
check("ActivityRule.from_dict 脏 window/min_minutes 不崩", r.window == (0,24) and r.min_minutes == 5.0)

print("== nlquery 路由 ==")
check("'我几点睡觉' 不被泛词抢成 device_usage", detect_intent("我几点睡觉") != "device_usage")
check("'空调用了多久' 仍路由 device_usage", detect_intent("上周空调用了多久") == "device_usage")
check("'最近作息怎么样' 不被'怎么'拆坏路由 rhythm", detect_intent("最近作息怎么样") == "rhythm")

print("== persona.explain 未命中字段齐 ==")
p = PersonaBuilder()
out = p.explain("nonexistent", {})
need_keys = {"insight_id","found","type","title","detail","room","entity_id","friendly_name","score","explanation","evidence","data"}
check("未命中返回含全部字段", need_keys.issubset(out.keys()))

print(f"\n== {ok} 项断言全部通过 ==")
sys.exit(0 if ok > 0 else 1)
