"""临时探针：HST 分数分布 + 两种阈值口径下的异常数对比。用完即删。"""
import sys

sys.path.insert(0, "/app/src")
from memory_agent import algo_kernel as ak  # noqa: E402
from memory_agent.activity_inference import ActivityInferenceService  # noqa: E402
from memory_agent.config import get_config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.store import Store  # noqa: E402

conf = get_config()
store = Store(db_path=conf.db_path, tz_offset_hours=conf.tz_offset_hours)
store.init_schema()
ins = InsightService(conf, store)
svc = ActivityInferenceService(type("RT", (), {"config": conf, "store": store,
                                               "insights": ins})())
events = svc._iter_events("2026-08-18T00:00:00", "2026-09-17T23:59:59", max_rows=200000)
series = ak.extract_observation_series(events, svc._tags_of, bucket_sec=3600)
print("POINTS", len(series))

# 复现内部分数序列（一次遍历，不打异常标记）
det = ak.OnlineAnomalyDetector(window_size=250)
scores = []
for item in series:
    f = det._features(item)
    s = float(det._hst.score_one(f))
    det._hst.learn_one(f)
    scores.append(s)

import statistics  # noqa: E402

print("mean=%.4f p50=%.4f p90=%.4f p95=%.4f p99=%.4f max=%.4f" % (
    statistics.mean(scores), ak._quantile(scores, 0.5), ak._quantile(scores, 0.9),
    ak._quantile(scores, 0.95), ak._quantile(scores, 0.99), max(scores)))

for floor in (0.0, 0.7, 0.9, 0.95, 0.99):
    det2 = ak.OnlineAnomalyDetector(window_size=250)
    r = det2.score_stream(series, min_score=floor)
    print("floor=%.2f -> anomalies=%d" % (floor, r["anomaly_count"]))

# 旧口径（纯相对分位）
det3 = ak.OnlineAnomalyDetector(window_size=250)
old = det3.score_stream(series, min_score=0.0)
print("old-style(quantile only) anomalies=%d" % old["anomaly_count"])
