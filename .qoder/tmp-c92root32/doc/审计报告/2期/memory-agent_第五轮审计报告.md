# memory-agent 第五轮审计报告：门禁盲区里的静默降级

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.4「找到自带门禁 → 用变异测试找它的盲区 → 盲区即新维度」**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

v2.3 说"先找项目自带门禁，别重复造轮子"。**v2.4 补上另一半：门禁覆盖不到的形状，就是下一轮该审的维度。**

项目自带门禁套件（`vendor/homesdk/gates`，五条规则 + `swallow-and-claim-ok`）。运行结果：

```
新增/未获批 0 条（error 0 / warn 0），基线内存量 208 条
计数：except-pass-broad=28 | fake-ok-const=173 | swallow-and-claim-ok=7
```

**0 新增 ≠ 没问题。** 我做了 **7 例变异测试**，确认 3 个盲区：

| 探针 | 门禁结果 |
|---|---|
| A 空体 `pass` + `ok=True` | ✅ 判红 |
| D `return None` + 外层 `ok=True` | ✅ 判红 |
| F 吞异常后 `return {"ok": True}` | ✅ 判红 |
| G 留痕后 `return None` | ✅ 正确放行（非缺陷） |
| **B `except: return None`** | **❌ 漏检** |
| **C `except: return []`** | **❌ 漏检** |
| **E `except: return default`** | **❌ 漏检** |

⇒ 盲区形状：**except 体非空但不留痕，返回降级值（静默降级）**。门禁只认"空体"，不认"降级值"。

据此全仓扫描得 47 处，按"**降级值在调用点被读成什么**"分诊（lesson 92），**确证 3 条**：

| 编号 | 级别 | 位置 | 降级值 | 被读成 |
|---|---|---|---|---|
| **MA-14** | 🔴 High | `activity_inference.py:115` `_in_time_window` | `True` | **命中时间窗** ⇒ 约束失效 |
| **MA-15** | 🟠 Medium | `insights_legacy.py:2214` `_check_activity_rule_coverage` | `None` | **覆盖良好** ⇒ 预检失败不可见 |
| **MA-16** | 🟡 Low | `template_validate.py:248` `_within_window` | `True` | **校验 ok** ⇒ 坏时间戳也说实体没问题 |

---

## 二、方法：v2.4 的迭代点

| 版本 | 做法 |
|---|---|
| v2.2（三轮） | 注释里的"改前后果"当 checklist |
| v2.3（四轮） | 先跑遗留队列；读工具自带"已知局限"当新维度 |
| **v2.4（本轮）** | **找到自带门禁 → 变异测试找盲区 → 盲区即新维度** |

**lesson 90**：门禁跑完"0 新增"后，注入已知违规看它认不认；它不认的那几条，就是本轮判据。
**lesson 91**：基线吸收 ≠ 已确认无害，要把基线里最严重的一类单独列出。

### 门禁运行环境说明（lesson 94）

沙箱 Python 3.10 无 `tomllib`（3.11+ 才有）。用 `sys.modules['tomllib'] = tomli` 注入后门禁正常运行——**门禁跑不起来时先确认是依赖问题还是真的失败**。

---

## 三、确认缺陷

### 🔴 MA-14　`_in_time_window`：坏时间窗 → 放行，规则约束失效

**位置**：`activity_inference.py:100-116`，调用点 `activity_inference.py:231`

```python
def _in_time_window(ts: str, window: str) -> bool:
    if not window:
        return True            # 空窗 = 不限，显式放行
    try:
        ...
    except Exception:
        return True            # ← 坏窗也放行
```

**语义**：返回 `True` = 命中窗口 = **该规则的这一节成立**。调用点据此把事件序列记为一次命中（`hits.append`）。

**实测**（`ts = 2026-01-01T03:00:00`，凌晨 3 点）：

| `time_window` | 结果 | |
|---|---|---|
| `08:00-22:00` | `False` 拦截 | ✅ 正确 |
| **`bad`** | **`True` 放行** | ❌ 约束失效 |
| **`abc-def`** | **`True` 放行** | ❌ 约束失效 |
| **`08:00-`** | **`True` 放行** | ❌ 约束失效 |
| `25:00-26:00` | `False` 拦截 | （数值解析成功，只是越界） |
| `""` | `True` | ✅ 显式"不限" |

**后果**：一条声明"只在 08:00–22:00 成立"的活动规则，只要时间窗字符串写坏，就变成**全天任何时刻都成立**。用户在界面上看到规则"命中率变高"，实际是约束没了。

**这是第一轮 MA-04 的同形第二处（lesson 93）**：

```
第一轮已报：intent_inference.py:134 _match_time_window
本轮发现：  activity_inference.py:115  _in_time_window
```

同仓实测对照（同一组坏输入，两个函数行为一致）：

```
intent_inference:  time_window='bad'      → True
activity_inference: window='bad'          → True
```

⇒ **同一形状在两个文件各一处。第一轮只报了一处，等于没修完。**

---

### 🟠 MA-15　`_check_activity_rule_coverage`：预检失败 ≡ 覆盖良好

**位置**：`insights_legacy.py:2209`；调用点 `insights_legacy.py:2183-2187`

```python
except Exception:
    return None  # 预检失败不阻塞注册，只跳过告警
```

**调用点语义**：

```python
warn = self._check_activity_rule_coverage(room, tags or [])
if warn:
    out["coverage_warning"] = warn["message"]      # 只有非 None 才告警
    out["message"] += " ⚠️ " + warn["message"]
```

⇒ **`None` 被读成"覆盖良好"**。

**实测**（桩对象令 `_iter_all_events` 抛异常，模拟 DB 故障/表损坏）：

```
_iter_all_events 抛 RuntimeError
 → 预检返回: None
 → 调用点 `if warn:` → 不加告警

对照·真的覆盖良好 → None   （同为 None）
```

**两种情形逐字节相同**：预检跑不起来，和预检通过，用户看到的返回完全一样（都是 `ok: True` + 无告警）。

**为什么值得报**：这个预检的**存在理由**写在同一函数的 docstring 里——

> 说明规则要求的标签……没有任何对应实体产生过事件——即『缺实体类型』，规则将无法命中。
> ……会显式给出『缺实体类型』的明确结论与代理建议，**避免运行期含糊的『未命中』**。

它致力于消除"含糊的未命中"，但自己失败时**制造了同样含糊的沉默**。注释写的是"只跳过告警"——**跳过告警这件事本身没有痕迹**。

**定 Medium**：方向是 fail-open 但后果限于"少一条诊断提示"，不阻塞注册（这是有意设计）。

---

### 🟡 MA-16　`_within_window`：坏时间戳 → 判 `ok`

**位置**：`template_validate.py:242-248`；调用点 `template_validate.py:319`

```python
except Exception:
    return True
```

**实测**：

| `last_ts` | `_within_window` | 调用点 `state` |
|---|---|---|
| `2026-10-01T10:00:00` | `True` | `ok` ✅ |
| **`not-a-time`** | **`True`** | **`ok`** ❌ |
| **`2026-13-45T99:99:99`** | **`True`** | **`ok`** ❌ |
| `""` | `True` | （短路，走别的分支） |

⇒ 模板校验报告会说这个实体"一切正常"，而实际上它的最后数据时间戳是坏的。本该是 `no_data`。

**定 Low**：只影响校验报告（诊断面），不影响数据写入或安全判定。

---

## 四、观察项（不升级为缺陷，但应记录）

### 观察 1：基线里躺着 5 条 `swallow-and-claim-ok`

`.gates-baseline.txt` 吸收 208 条存量违规，其中 **5 条是 `swallow-and-claim-ok`**——正是这套门禁自称"存在的理由"的形状：

| 位置 | 说明 |
|---|---|
| `agent_memory.py` `AgentMemoryService.promote_memory` | |
| `insights_legacy.py` `InsightService.device_health` | |
| `insights_legacy.py` `InsightService.plan_question` | |
| `mcp_server.py` `_build_server.retrieve_agent_memories` | 吞掉的是**审计日志写入失败**（`except Exception: pass`），注释声明"旁路审计不得影响主链路" |
| `signal_learning.py` `SignalLearningService.suggest_rules_from_negative_feedback` | |

抽查 `retrieve_agent_memories` 确认：**被吞的是审计旁路**，有 `# noqa: BLE001 —— 旁路审计不得影响主链路` 注释，属**有意设计**。

⇒ 但这条对第二轮 MA-05 有个加重作用：那个 admin 通道"查全量"的出证日志（`AUDIT: cross-member recall`）**写入失败是静默的**，所以"证据说谎"之外还可能是"证据根本没写"。

**lesson 91：基线是"已知且暂不修"的登记册，不是"已确认无害"。**

### 观察 2：`_already_promoted` 与 `_idem_store` 的 fail-open 均有文档

```
candidate_promotion.py:1394  docstring: 存储层不支持该查询时静默返回 False，退化为不跳过
mcp_server.py:820           docstring: 占位失败一律按「放行执行」处理：幂等是防重复的优化
```

两者都**写明了意图**，且 `_idem_reserve` 有 debug 日志 ⇒ 不升级为缺陷。
但注意 `_already_promoted` 的 docstring 说的是"存储层**不支持该查询**"，
实际 `except Exception` 覆盖的是**所有错误**（含瞬时 DB 故障）⇒ 语义比文档宽。列为观察。

### 观察 3：`identity._health_allows` 失败即放行

```python
except Exception:
    return True
```
docstring 明确"无健康记录（尚未对账过）时放行，保证身份层未就绪时不阻断既有行为" ⇒ **有意 fail-open**，且身份解析不是安全闸。不升级。

---

## 五、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| **门禁套件本身** | ✅ 7 例变异中 4 例正确判红/放行；3 例漏检已记为盲区（本报告主题） |
| 47 处静默降级中的 **44 处** | ✅ 绝大多数是 **fail-closed**（返回 `None/False/0/""` = 否定）或纯解析助手（`_parse_ts`、`_as_mapping`），不构成缺陷 |
| `_fts_index_healthy` 失败返回 `False` | ✅ 退化为纯向量检索，方向安全 |
| `_check_ha_token` / `_authenticate` / `json_body` 返回 falsy | ✅ 均为 fail-closed |
| 全仓 `timedelta(days=)` | ✅ 第四轮已确认 `scan_day_bounds.py` 门禁覆盖，12 例变异无盲区 |
| `store.py:4813` `LIMIT ?` | ✅ 闭合：外部调用点均未传 limit（MCP 侧经 `agent_memory` 固定 500） |

---

## 六、分诊方法（本轮可复用的判据）

47 处命中只留下 3 条，**命中率 6%。关键是这条分诊顺序**：

```
1. 降级值在调用点被读成什么？
     ├─ 否定（None/False/0/""）→ 大概率 fail-closed，不报
     └─ 肯定（True / "ok" / None=良好 / False=未做过）→ 继续 2
2. 有没有文档声明这是有意的？
     ├─ 有（docstring 写明 + 有留痕）→ 观察项
     └─ 无 → 继续 3
3. 实测复现 + 与"真实正常值"对照是否逐字节相同
     └─ 是 → 确认缺陷
```

**lesson 92：先问"这个返回值在调用点被读成什么"，再问"有没有留痕"。**

---

## 七、修复建议

### 三处各一行（把"跳过"变成"可区分"）

```python
# 1) activity_inference.py:115 —— 坏窗应当被看见（与 MA-04 一并修）
except Exception:
    logger.warning("规则 time_window 解析失败，按不限处理: %r", window)
    return True          # 行为不变，但留痕；或改为 return False（fail-closed）

# 2) insights_legacy.py:2209 —— 引入三态
except Exception as exc:
    return {"ok": False, "precheck_failed": True,
            "message": f"实体覆盖预检未能执行（{type(exc).__name__}），未确认覆盖情况"}

# 3) template_validate.py:248
except Exception:
    return False         # 坏时间戳 → no_data，而非 ok
```

### 建议给门禁加一条规则（治本）

现有规则只认"空体 except"。建议在 `homesdk/gates/scan.py` 增加：

> **`silent-degrade-no-trace`**：`except Exception` 体非空、无 `log/raise/counter/errlist-append`、
> 且返回降级值（`None/[]/{}/""/0/False`）→ warn；若降级值在调用点被读成"放行/良好"→ error。

判定"被读成放行"可简化为：**函数名或返回语义命中允许集**（`_allows` / `_healthy` / `_within_window` / `_in_time_window` / `_coverage`），先 warn 再人工确认——与本项目现有"基线只准减少"的机制天然契合。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | `_in_time_window(ts03, "bad")` | 有 warning 日志；或改为 `False` |
| 2 | `_match_time_window({"time_window":"bad"}, 03:00)` | 同上（与 #1 同口径） |
| 3 | 预检中 `_iter_all_events` 抛异常 | 返回**非 None**，调用点给出"预检未执行"提示 |
| 4 | `template_validate` 实体 `last_ts="not-a-time"` | `state = "no_data"`，非 `"ok"` |
| 5 | 新增门禁规则后全仓扫描 | 现有 47 处全部进基线，此后只准减少 |

---

## 八、横向观察

### 五轮下来，"同一形状在多处各一份"第三次出现

| 轮 | 形状 | 实例数 |
|---|---|---|
| 三 | `LIMIT ?` 未钳制 | 4 处（API 2 / MCP 2） |
| 四 | `timedelta` 同族无上界 | 3 处（HTTP 1 / MCP 2） |
| **五** | **坏时间窗 → `return True` 放行** | **2 处（两个文件各一）** |

**每一轮都在重复同一件事：一个形状被发现一次，但它其实有多份。**

本轮这条特别值得注意，因为**第一轮的报告里已经写了 MA-04**，而同形第二处直到第五轮才被扫出来——中间隔了整整三轮。原因是第一轮用的是"已修范式反查"，只沿着 `patterns._json_object` 那条线走，没有对**已报缺陷本身**做同形状横向扫描。

⇒ **建议补一条工作流规则：每报一条缺陷，立即 grep 该形状的全部实例，一并报出。**

（这与 AutoForge 侧"已修范式未铺开"是同一条的两个方向：那边是**修法没铺开**，这边是**缺陷没扫完**。）

### 门禁质量总体评价

门禁套件本身质量很高：五条规则定位精准，`swallow-and-claim-ok` 抓的正是"失败被咽下 + 宣称成功"这一族；`.gates-baseline.txt` 的"只准减少"机制比多数项目都严格。

**唯一的结构性缺口是"体非空"这个判定条件**——`except: return None` 与 `except: pass` 在语义上同族（都是不留痕地吞掉失败），但只有后者被判红。

---

## 九、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 新脚本用 `auditlib` 原语 | ✅ `ma_scan_r7.py` 用 `own_walk` / `has_trace` / `full_unparse` |
| **门 2** | 命中多时手工分诊 | ✅ **决定性**——47 处按"降级值语义方向"降到 3 条 |
| **门 3** | 每条缺陷有实测 PoC + 同仓对照 | ✅ 3 条全部实测；MA-14 有同文件正常窗对照 + 跨文件同形对照；MA-15 有"真覆盖良好"对照 |

### 遗留队列（五轮累积）

| 项 | 状态 |
|---|---|
| `auth.py:92 _save_users` 原子写声明 | ⚠️ 未验证（`bcrypt` 未装） |
| `api/behavior_routes.py:398 behaviors_home_profile` | ⚠️ 未核查 |
| 同族 14 处中剩余 11 处 | ⚠️ 未逐一核查 |
| MA-03 端到端（一轮） | ⚠️ 未复现（`chromadb` 未装） |
| 47 处静默降级中"否定方向"的 44 处 | ⚠️ 仅抽查，未逐一确认调用点语义 |

### 环境

| 项 | 值 |
|---|---|
| 未装依赖 | `chromadb`、`bcrypt`、`starlette` |
| 门禁依赖 | `tomllib` 用 `sys.modules['tomllib'] = tomli` 注入 |
| 门禁运行 | `PYTHONPATH=/tmp/hsdk python3 /tmp/run_gates.py . --no-smoke` → rc=0 |
| 扫描器 | `ma_scan_r7.py`（判据 G 静默降级） |
| 明细 | `reports/ma-r7.json` |
| 新增 lessons | **90–94**（已并入 `lessons-round2.md`，共 1097 行） |
| 仓库状态 | 探针已还原；门禁与基线文件未被修改 |
