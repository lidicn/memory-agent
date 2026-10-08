"""机械收敛 llm_routes.py 的 `if True:` 死分支（DCD 20261005 §二.2 Q4 授权 MA 自办）。

行尾用 newline='' 原样读写：本机文本模式重写会把 LF 变成 CRLF，diff 会整份翻掉。
"""
P = "src/memory_agent/api/llm_routes.py"
with open(P, "r", encoding="utf-8", newline="") as f:
    lines = f.readlines()

i = 174  # 0-based：第 175 行
assert lines[i].strip().startswith("if True:"), lines[i]
assert lines[i].startswith("    if True:"), repr(lines[i])

j = i + 1
while not (lines[j].startswith("    async def generator") or lines[j].startswith("    async def ")):
    j += 1
end = j  # 不含

body = lines[i + 1:end]
out = []
for ln in body:
    if ln.strip() == "":
        out.append(ln)
        continue
    assert ln.startswith("        "), repr(ln)
    out.append(ln[4:])

# 172~174 是 `if True:` 上方的旧话术（"仅当疑似数据查询时启用"）——条件早就不存在了，
# 一并换成与真实行为一致的两行，避免下一次审计再把这条注释当成缺陷。
assert lines[i - 3].strip().startswith("# 仅当疑似数据查询时启用"), lines[i - 3]
head = [
    "    # 始终携带工具，由模型自行决定是否调用（消除与 MCP 的能力割裂）。\n",
    "    # agent loop 支持多轮工具调用：LLM 可能先查目录，再查用量，最后总结。\n",
    "    final_response: dict | None = None\n",
]
lines[i - 3:end] = head + out
with open(P, "w", encoding="utf-8", newline="") as f:
    f.write("".join(lines))
print("DEDENT_OK removed_if_true=1 lines=%d" % len(lines))
