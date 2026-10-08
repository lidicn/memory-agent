import io

P = ".qoder/tmp-c37-remote13.sh"
text = io.open(P, encoding="utf-8", newline="").read()

old = r"""echo SVC_ANOM_BODY_EID=$(awk '/def _anomaly_report/,/_behavior_insights/' $S | grep -c entity_id)"""
old = old.replace("$(", "\\$(").replace("$S", "\\$S")
new = (r"""echo SVC_ANOM_BODY_EID=$(awk '/def _anomaly_report/,/def behavior_insights/' $S """
       r"""| grep -c entity_id) && echo SVC_ANOM_FILTERS_EID=$(awk '/def _anomaly_report,/"""
       r"""def behavior_insights/' $S | grep -c '_filters(room, category, entity_id)')""")
new = new.replace("$(", "\\$(").replace("$S", "\\$S")

hits = text.count(old)
print("ANCHOR_HITS=%d" % hits)
if hits != 1:
    raise SystemExit(1)
data = text.replace(old, new, 1).encode("utf-8")
with open(P, "wb") as fh:
    fh.write(data)
print("BYTES=%d CR=%d" % (len(data), data.count(b"\r")))
