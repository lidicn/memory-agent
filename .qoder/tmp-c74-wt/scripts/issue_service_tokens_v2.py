"""重新签发 DB 和 AF 的 service_token，记录明文。"""
import sys
sys.path.insert(0, "/app/src")

from memory_agent.service_tokens import (
    ServiceTokenStore, BUTLER_SCOPES, APP_SCOPES,
)

store = ServiceTokenStore()

# 先吊销已存在的 doubao-butler（如果有）
existing = [t["name"] for t in store.list_tokens()]
print(f"现有令牌: {existing}")

if "doubao-butler" in existing:
    print("吊销旧的 doubao-butler...")
    res = store.revoke("doubao-butler")
    print(f"  吊销结果: {res}")

if "autoforge" in existing:
    print("吊销旧的 autoforge...")
    res = store.revoke("autoforge")
    print(f"  吊销结果: {res}")

print()

# DB：butler + app 两个面的 scope 合并，source="butler"
db_scopes = sorted(set(BUTLER_SCOPES + APP_SCOPES))
print(f"=== 签发 DB 令牌 (doubao-butler) ===")
print(f"scope 数量: {len(db_scopes)}")
db_res = store.generate("doubao-butler", db_scopes, source="butler")
if db_res.get("ok"):
    print(f"名称: {db_res['name']}")
    print(f"前缀: {db_res['prefix']}")
    print(f"明文令牌: {db_res['token']}")
    print(f"source: {db_res.get('source')}")
else:
    print(f"签发失败: {db_res.get('error')}")

print()

# AF：butler scope（包含 metrics-ingest），source 为空（AF 不写记忆）
print(f"=== 签发 AF 令牌 (autoforge) ===")
print(f"scope 数量: {len(BUTLER_SCOPES)}")
af_res = store.generate("autoforge", BUTLER_SCOPES, source="")
if af_res.get("ok"):
    print(f"名称: {af_res['name']}")
    print(f"前缀: {af_res['prefix']}")
    print(f"明文令牌: {af_res['token']}")
    print(f"source: {af_res.get('source')}")
else:
    print(f"签发失败: {af_res.get('error')}")

print()
print("=== 当前令牌列表 ===")
for t in store.list_tokens():
    print(f"  {t['name']}: prefix={t['prefix']} scopes={len(t['scopes'])} created={t['created_at']} source={t.get('source','')}")
