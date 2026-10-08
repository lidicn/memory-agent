# 交接单：list_members 响应体过大导致截断

## 问题描述

deepseek++ 扩展通过 MCP 调用 `list_members` 时，响应体超过客户端上限被截断（`truncated: true`），导致只能看到第一个成员，后续成员数据完全丢失。

**实测情况**：
- 连续 4 次调用，截断位置完全相同
- 可见成员数始终只有 1 个（lidicn）
- 已尝试在客户端把 maxResultBytes 从 64KB 改成 1MB，无效
- 已尝试在服务端去掉 avatar_url 字段，仍然截断

**结论**：问题不只是 avatar_url，响应体里还有其他大字段（可能是 profile_json、face_feature、tags 聚合等）。需要从根本上解决大字段传输问题。

## 代码位置

**文件**：`src/memory_agent/store.py`
**函数**：`list_members()`（第 2744 行）

```python
def list_members(self) -> list[dict]:
    conn = self.connect()
    with self._lock:
        rows = conn.execute("SELECT * FROM members ORDER BY created_at").fetchall()
        # ... 批量拉 rooms/devices/tags ...
        for r in rows:
            m = dict(r)  # ← SELECT * + dict(r) 把所有大字段都带出来了
            m["profile"] = self._parse_json_field(m.get("profile_json"))
            m["rooms"] = rooms_map.get(m["id"], [])
            m["devices"] = devices_map.get(m["id"], [])
            m["tags"] = tags_map.get(m["id"], [])
            members.append(m)
    return members
```

members 表的大字段：
- `avatar_url`：TEXT，存 base64 data URI（单张头像几十 KB）
- `profile_json`：TEXT，存 JSON（可能包含大量行为分析文本）
- `face_feature`：BLOB/TEXT，存人脸向量（之前审计报告 P1-8 已提过）
- `appearance_json`：TEXT，存外貌描述

## 建议修改方案（二选一，由 MA 决定）

---

### 方案 A：文件传递（推荐）

**思路**：MCP 响应只返回轻量摘要，完整数据写到文件，响应里给文件路径。

**具体做法**：

1. `list_members` 返回精简摘要：
```json
{
  "members": [
    {"id": "xxx", "name": "lidicn", "rooms": ["主卧室", "书房"], "deviceCount": 26, "tagCount": 3},
    ...
  ],
  "fullDataPath": "/tmp/members_full_1234567890.json"
}
```

2. 把完整数据（含 avatar_url、profile_json、face_feature 等大字段）写到一个临时文件
3. 响应里加 `fullDataPath` 字段，指向那个文件
4. 客户端需要完整数据时，再读文件

**优点**：
- MCP 响应永远小（< 10KB），不会截断
- 一次调用就能拿到摘要，需要完整数据时再读文件
- 不需要 N+1 次调用

**缺点**：
- 需要文件系统读写
- 需要清理临时文件

---

### 方案 B：精简字段 + 单独工具

**思路**：`list_members` 只返回最精简字段，大字段需要时单独调工具获取。

**具体做法**：

1. `list_members` 只返回：
```json
{
  "members": [
    {"id": "xxx", "name": "lidicn", "rooms": ["主卧室", "书房"], "devices": [...], "tags": [...]},
    ...
  ]
}
```

去掉所有大字段：`avatar_url`、`profile_json`、`face_feature`、`appearance_json`

2. 需要看某个成员完整详情时，调 `get_member_persona(member_id)`

**优点**：
- 最简单，不用文件传递
- list_members 响应永远小

**缺点**：
- 要拿全量成员信息得 N+1 次调用
- AI 要自己判断要不要调详情

---

## 验收标准

改完后调 `list_members`：
1. 响应体大小 < 10KB（或确认不再被截断）
2. 能看到所有成员（不止第一个）
3. 不需要二次调用就能拿到成员的基本信息（id、name、rooms、devices、tags）

## 其他说明

- deepseek++ 扩展的 `maxResultBytes` 默认值已从 64KB 改成 1MB，但服务端不发大字段才是根本解决
- 同表的 `face_feature` 字段之前审计报告（P1-8）已经提过类似问题，建议一起处理
- 这个问题已影响实际使用：AI 无法获取完整成员列表，影响家庭成员识别和行为归因
