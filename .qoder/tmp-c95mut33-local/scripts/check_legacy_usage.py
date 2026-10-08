"""检查旧令牌使用量。"""
import sys
sys.path.insert(0, "/app/src")

from memory_agent.service_tokens import get_service_token_store

store = get_service_token_store()
usage = store.legacy_usage()
print("=== 旧令牌使用量 ===")
print(f"双轨天数: {usage.get('dual_track_days')}")
print(f"零使用天数要求: {usage.get('legacy_zero_usage_days')}")
print()
for name, info in usage.get("records", {}).items():
    print(f"  {name}:")
    print(f"    use_count: {info.get('use_count')}")
    print(f"    last_used_at: {info.get('last_used_at')}")
    print(f"    can_revoke: {info.get('can_revoke')}")
    print(f"    zero_usage_days: {info.get('zero_usage_days')}")
    print()

print("=== 新令牌列表 ===")
for t in store.list_tokens():
    print(f"  {t['name']}: prefix={t['prefix']} use_count={t.get('use_count')} last_used={t.get('last_used_at')}")
