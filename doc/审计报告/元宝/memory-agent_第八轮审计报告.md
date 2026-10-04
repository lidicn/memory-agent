# memory-agent 第八轮审计报告

> 审计对象：https://github.com/lidicn/memory-agent （main 分支）
> 审计日期：2026-10-04
> 本轮方向：**外部输入边界与数据流**（V3 剩余部分）
> 前置：第一~七轮累计 P1×6、P2×6、P3×1
> 依据：《memory-agent_审计收敛规划.md》第八轮

---

## 一、工作流迭代 V3（完成）：外部输入边界探针

新增 `input_r8.py`，三个维度：

| 探针 | 检测目标 |
|---|---|
| **R8-1** | 外部入口 → 危险汇点（SQL 拼接 / eval / exec / subprocess） |
| **R8-2** | 数值参数（limit/days/top_n…）从入口取值后无 clamp |
| **R8-3** | 外部字符串 → 文件路径拼接，缺少 realpath 约束 |

---

## 二、结论先行

> **本轮确认 1 个新缺陷（P2-7）：MCP 技能读取接口存在路径遍历。**
>
> 最有说服力的不是缺陷本身，而是它的形态：**同一个模块的"写"函数做了严格校验，"读"函数一行校验都没有**。这种不对称通常不是有意设计，而是遗漏。
>
> R8-1（SQL 注入族）**0 命中**——这是好消息，且与前几轮一致：项目的 SQL 参数化做得干净。

---

## 三、P2-7　MCP 技能读取接口路径遍历

**位置**：
- `src/memory_agent/mcp_server.py:3038` `get_skill(name)` — MCP 工具（READ scope）
- `src/memory_agent/mcp_server.py:3068` `res_skill(name)` — MCP 资源 `skill://{name}`

```python
def get_skill(name: str, version: str = "latest") -> dict:
    if not name:
        return {"ok": False, "error": "name 不能为空"}
    rt = get_runtime()
    skill_path = os.path.join(rt.config.skills_dir, name, "SKILL.md")   # ← 直接拼接
    if not os.path.isfile(skill_path):                                   # ← 只查存在性
        return {"ok": False, "error": f"skill 不存在: {name}"}
    with open(skill_path, "r", encoding="utf-8") as f:
        content = f.read()
```

### 铁证：写路径有校验，读路径没有

同文件 `save_skill`（2937 行）：

```python
if not name or not re.match(r"^[A-Za-z0-9_\-]+$", name):
    return {"ok": False, "error": "name 只能含字母、数字、下划线、连字符"}
```

**写操作用正则白名单严格限制，读操作零校验。** 作者显然考虑过 name 的安全性，但只在写路径落地了。

### 实测复现

构造 `skills_dir=/tmp/x/skills`，其同级目录 `/tmp/x/env/` 下放一个 `SKILL.md`：

```
get_skill("diet")     → {'ok': True, 'content': 'real skill'}
get_skill('../env')   → {'ok': True, 'content': 'JWT_SECRET=supersecret123'}  ← 读出目录外
get_skill('../')      → {'ok': False, ...}   （无 SKILL.md，读不到）
```

`os.path.isfile()` 只判断"存在且是文件"，**不判断"是否在 skills_dir 内"**。缺少 `os.path.realpath(path).startswith(realpath(skills_dir))` 这类约束。

### 定级 P2 而非 P1 的理由（如实说明）

**可利用性受限**：路径被拼接了固定后缀 `/SKILL.md`，因此只能读取**目标目录下恰好名为 `SKILL.md` 的文件**，不能任意读文件。攻击者需知道或猜到某个目录外位置存在 `SKILL.md`。

**权限面**：`get_skill` 登记在 READ scope（`mcp_scopes.py:59`），任何持有只读令牌的客户端/LLM 均可调用。这是"低权限读越界"而非"提权"。

**综合**：不是可任意读文件的严重漏洞，但确实跨越了目录边界，且与同模块写函数的校验强度明显不匹配。定 **P2**。

### 修复建议

两处统一加白名单校验，与 `save_skill` 同款：

```python
if not name or not re.match(r"^[A-Za-z0-9_\-]+$", name):
    return {"ok": False, "error": "name 只能含字母、数字、下划线、连字符"}
```

或加 realpath 前缀约束（若将来需要支持子目录名）。

---

## 四、R8-1：SQL 注入族 0 命中（回归哨兵）

外部入口 → `eval` / `exec` / `subprocess` / SQL f-string 拼接：**0 处**。

这与第一轮一致（34 条 SQL 注入告警全部证伪）。项目的 SQL 走参数化占位符 + 列名白名单 + ORDER BY 三元常量，做得扎实。

**保留该探针作为回归哨兵**——它现在产 0，但一旦有人引入字符串拼接 SQL 就会立刻报警。这是"零产出探针"的正当价值：不是每轮都要出 bug，有些探针是用来确认"这里依然干净"。

---

## 五、R8-2 数值边界：6 处，降级为 P3 / 证伪

| 位置 | 参数 | 判定 |
|---|---|---|
| `candidate_promotion.py:359` | count | **证伪**：用了 `_to_int()`（内部 try/except 转 int，失败回默认） |
| `llm_client.py:120` | temperature | **证伪**：用了 `_num()` |
| `insights/activity.py:95` | window | **证伪**：`from_dict` 解析配置，非外部输入 |
| `scripts/verify_*.py` ×2 | count | **证伪**：验证脚本，非服务路径 |
| `insights_legacy.py:3241` | days | **P3**，见下 |

### P3-2　`days` 极值导致降级 / 负值导致窗口反转

`ask_memory(question, days=7)` 的 days 是 MCP 工具参数（`tool_schema.py:435`，integer 类型，无上下界）。

实测：

| days | 行为 |
|---|---|
| `7` | 正常 |
| `3650` | 正常（10 年窗口） |
| `10**6` | **OverflowError: date value out of range** |
| `-5` | **不报错，start(10-09) > end(10-04)，窗口反转** |

**为什么只定 P3**：

- OverflowError 被 `@_degrade` 装饰器捕获（`insights/api.py:67-87`），返回降级结果并带 `error` 字段，**不会 500**
- 负值不崩溃，只是窗口反转 → SQL `BETWEEN` 恒为空 → 静默返回空结果

都是"拿到错误结果"而非"服务崩溃"，且需要主动传极端值才触发。

**建议**：`resolve_nl_window` 对 days 做 clamp（如 `1 <= days <= 3650`）并对负值取绝对值或报错。

---

## 六、R8-3 其余 8 处路径候选：逐个证伪

| 位置 | 判定 |
|---|---|
| `mcp_server.py:3038 get_skill` | **P2-7** |
| `mcp_server.py:3068 res_skill` | **P2-7（同一缺陷的第二入口）** |
| `app.py:495 metrics_ingest_endpoint` | 证伪：写固定路径 `af_metrics.json`，name 不参与 |
| `config.py:357 load` | 证伪：读配置文件，路径非外部输入 |
| `mcp_server.py:655 seed_builtin_skills` | 证伪：内置固定名 |
| `mcp_server.py:3012 list_skills` | 证伪：只 `os.listdir(base)`，不拼接外部名 |
| `patterns.py:66 / template_validate.py:85` | 证伪：固定 state 文件路径 |
| `behavior_routes.py:568/628` | 证伪：输出目录固定 `/data/feedback_packs` |

---

## 七、第八轮产出小结

| 项 | 结果 |
|---|---|
| 新缺陷 | **P2-7 ×1**（2 个入口）、**P3-2 ×1** |
| 工作流改进 | 3 个探针（R8-1/2/3），V3 迭代完成 |
| 证伪 | R8-2 中 5/6、R8-3 中 8/10 |

### 收敛判据

第八轮出现新 P2，**判据 2（连续 2 轮无新 P1/P2）仍未满足**。

连续四轮（五~八）的产出：0 / 0 / P2×1 / P2×1。**新维度确实持续产出，但量级已从 P1 降到 P2** —— 这与"剩余缺陷严重性递减"的预期一致。

---

## 八、规划执行状态

- [x] 第五轮 · 孤儿 triage + 路由接漏 → P3×1
- [x] 第六轮 · 双轨口径比对 → 0（修正第一轮定性）
- [x] 第七轮 · 并发与竞态 → P2×1
- [x] **第八轮 · 外部输入边界 → P2×1 + P3×1**
- [ ] 第九轮 · 未测区域收口（15 个 0% 模块 + 11 个高复杂度 + learning_* + 54 个孤儿 + 16 个委托方法规范化）
- [ ] 第十轮 · 生命周期 / 资源 / 历史修复回归（V4）

工作流迭代：
- [x] V2 · 遗留项可追踪化 + 孤儿 triage 修正
- [x] **V3（完成）· 并发 / 输入边界探针**
- [ ] V3 剩余 · 生命周期探针（第十轮前）
- [ ] V4 · 收敛度量 + 历史审计修复验证器

---

## 九、审计覆盖声明

- 本轮为**第八轮**。三个探针均为静态 AST 分析，**未做运行时模糊测试、未构造真实 HTTP 请求验证**。
- P2-7 的实测是**复刻路径构造逻辑的独立脚本**，非在真实项目运行时复现。
- R8-2 的 6 处中仅 `days` 做了运行时验证，其余 5 处基于代码阅读证伪。
- R8-3 的 10 处逐处核查完毕（8 证伪 + 2 确认）。
- **未审查**：认证/授权边界、请求体大小限制、文件上传、SSRF（HA/MQTT 出站请求）、LLM prompt 注入。
- 环境限制照旧：Python 3.10（低于项目要求的 3.11）、Semgrep 未安装。
