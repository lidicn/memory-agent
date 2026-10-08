"""为 DB 和 AF 签发 service_token。"""
import sys
sys.path.insert(0, "/app/src")

from memory_agent.service_tokens import (
    ServiceTokenStore, BUTLER_SCOPES, APP_SCOPES,
)

store = ServiceTokenStore()

# DB：butler + app 两个面的 scope 合并
db_scopes = sorted(set(BUTLER_SCOPES + APP_SCOPES))
print(f"=== 签发 DB 令牌 ===")
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

# AF：butler scope（包含 metrics-ingest）
print(f"=== 签发 AF 令牌 ===")
print(f"scope 数量: {len(BUTLER_SCOPES)}")
af_res = store.generate("autoforge", BUTLER_SCOPES, source="autoforge")
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
    print(f"  {t['name']}: prefix={t['prefix']} scopes={len(t['scopes'])} created={t['created_at']}")
