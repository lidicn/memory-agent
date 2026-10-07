# memory-agent 第十四轮审计报告：错误处理路径健壮性与未完成桩

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.13「grep TODO/FIXME 优先」+ 错误处理路径自身健壮性**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

本轮起点是一条最朴素的操作：**`grep -rn "TODO\|FIXME"`**。项目自己标出的未完成点，比设计判据可靠得多（lesson 136）。

### 确认缺陷

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-29** | 🔴 High | `rule_engine.py:1035-1105` 五个动作 | 规则引擎的 `alert` / `webhook` / `tts` / `light` / `camera` **全是桩**（只 `logger.info`），却返回 `{"ok": True}` 且**触发历史落库 `dry_run=0`** |
| **MA-30** | 🔴 High | `api/nr_routes.py:52-56` `nr_execute_action` | `POST /api/nr/execute-action` **零实现**，直接返回 `ok({"message": "动作已执行: ..."})` |

### 三个判据的核查结果

| 判据 | 结果 |
|---|---|
| **A** logging 格式串占位符与实参不匹配 | ✅ **0 处**（可静态判定的 137 处 logger 调用全部匹配） |
| **B** except 块内非 logging 函数调用 | 229 处 → 分诊：多为降级/记账，**本次不单报**（与第五轮静态降级族重叠） |
| **C** `CancelledError` 捕获后未 re-raise | 24 处仅 4 处 re-raise，但 SSE 相关 4 处**为有意吞掉**（有注释说明理由） |

---

## 二、方法：v2.13 的迭代点

| 版本 | 做法 |
|---|---|
| v2.11/v2.12（十二、十三轮） | 契约声明验证 |
| **v2.13（本轮）** | **① 先 grep TODO/FIXME 再定维度；② 错误处理路径自身健壮性** |

**lesson 136**：每轮开新维度前先 grep 一遍 `TODO|FIXME|XXX|HACK`。本轮 9 处 TODO 里 5 处直指规则引擎动作桩。

---

## 三、确认缺陷

### 🔴 MA-29　规则引擎五个动作是桩，却谎称成功并留下"已触发"痕迹

**位置**：`rule_engine.py` 的 `_action_alert` / `_action_webhook` / `_action_tts` / `_action_light` / `_action_camera`

代码形状（五处一致）：

```python
def _action_webhook(self, rule, event):
    # TODO: 调用 Webhook（可配置 URL/Header/模板）
    logger.info(f"[规则] webhook TODO 未实现: rule={rule.get('rule_id')} "
                f"url={...} payload={...}")
    return True, {"ok": True, "action": "webhook", "detail": "TODO 未实现", ...}
```

#### 实测（7 种动作类型，非 dry_run）

| 动作 | 是否真执行 | 返回值 | AlertDispatcher 调用 |
|---|---|---|---|
| `log` | ✅ 真记日志 | `ok=True` | — |
| `infer_activity` | ✅ **真落库**（`upsert_detected_activities`） | `ok=True` | — |
| `alert` | ❌ 桩 | `ok=True` | **0 次** |
| `webhook` | ❌ 桩 | `ok=True` | — |
| `tts` | ❌ 桩 | `ok=True` | — |
| `light` | ❌ 桩 | `ok=True` | — |
| `camera` | ❌ 桩 | `ok=True` | — |

⇒ **7 次非 dry_run 调用，全部落库 `rule_trigger_history` 且 `dry_run=0`。**

#### 三处独立证据证明这是"遗漏"而非"有意设计"

**① 依赖已注入却从未调用**（lesson 138，最强证据）

```python
# 引擎构造：AlertDispatcher 已注入
def get_rule_engine(store, alert_dispatcher=None):
    return ActiveRuleEngine(store, alert_dispatcher)
# ActiveRuleEngine.__init__: self.alert_dispatcher = alert_dispatcher
```

而 `_action_alert` 的注释是 `# TODO: 调用 AlertDispatcher 或直接推送`。

⇒ **接口已就位、依赖已注入、只差一行**——这比"缺功能"更能证明是遗漏。

**② 同仓有诚实的桩**（lesson 139）

`intent_action.py:204-232` 是同一批动作类型的另一条实现路径：

- `tts` / `alert` → **真调** `rt.alert_dispatcher.dispatch(...)`
- `light` / `camera` / `webhook` → 写 `result_entry["result"] = f"动作类型 {action_type} 需要外部系统配合，已记录建议"`，并 `skipped += 1`

⇒ **同一个仓里两种桩：一种诚实告知未执行，一种返回 `ok=True`。**

**③ 团队已修过同类 TODO（lesson 29 铺开度反查）**

`rule_engine.py:840` 附近的 TODO（`state` 恒真）**已被修掉**。说明"TODO 桩"这个形状团队识别过并修过一处，但没铺开到其余五个动作。

#### 危害

用户视角的完整链路：
1. 配好规则（如"检测到有人进书房 → webhook 通知"）
2. 触发历史**显示已触发**（`dry_run=0`）
3. 接口返回 `ok: True`
4. **但什么都没发生**

⇒ 与本项目深恶痛绝的"假绿"完全同形（lesson 137）。

#### 附带：字段缺失使调用方无法区分（lesson 140）

```python
# dry_run 分支：
return {"ok": ..., "dry_run": True, "dispatched": False, ...}
# 非 dry_run 分支（5 个桩）：
return {"ok": True, "action": ..., "detail": ..., ...}      # ← 无 dispatched
```

⇒ 即便不修桩，也应**在非 dry_run 分支补 `dispatched` 字段**，让调用方/UI 能区分。

---

### 🔴 MA-30　`POST /api/nr/execute-action` 零实现，直接返回"动作已执行"

**位置**：`api/nr_routes.py:52-56`，路由注册于 `:199`

```python
async def nr_execute_action(request: Request):
    body = await json_body(request)
    action_type = (body.get("action_type") or "").strip()
    target = (body.get("target") or "").strip()
    return ok({"message": f"动作已执行: {action_type} -> {target}"})
```

⇒ **函数体里没有任何执行逻辑**，纯粹把入参回显成一句"已执行"。

#### 实测要点

| 项 | 结论 |
|---|---|
| 路由注册 | ✅ 已注册（`nr_routes.py:199`） |
| 鉴权 | ✅ 不在 `PUBLIC_PREFIXES`，中间件要求 JWT ⇒ **非未授权访问** |
| 前端调用方 | ⚠️ 全仓 grep **无调用方**（`.py`/`.js`/`.html` 均未引用）⇒ 是预留端点 |

⇒ 定 High 的理由不是"可被匿名利用"，而是：**任何登录用户（含非 admin）调用都会拿到"动作已执行"的成功回执，而系统什么都没做。** 一旦前端接上或脚本调用，就是静默假成功。

#### 与 MA-29 的关系

两者是同一形状的两次出现：

| | 位置 | 谎称方式 |
|---|---|---|
| MA-29 | 规则引擎动作 | `{"ok": True}` + 触发历史 `dry_run=0` |
| MA-30 | NR 路由 | `{"message": "动作已执行: ..."}` |

---

## 四、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| **判据 A：logging 格式串** | ✅ 137 处可静态判定的 logger 调用，占位符与实参**全部匹配**（修正正则后 0 处） |
| **判据 C：CancelledError** | ✅ SSE 相关的 `api/deps.py:136`、`api/llm_routes.py:276/349`、`acp_server.py:478` **为有意吞掉并 return**，注释写明"避免重复发 `http.response.start` 导致 RuntimeError、前端空白" ⇒ **正确的工程决策，非缺陷** |
| `house_time.py` 时区接缝 | ✅ 设计严谨：IANA/DST 主路径、`is_active` 门、`status()` 回显、`reset_homesdk_probe()` 可重置 ⇒ 时间语义维度无货 |
| `poller.py:408/641`、`runtime.py:553` 的 CancelledError | ✅ 正确 re-raise |
| `nr_routes.py` 的其他路由 | ⚠️ 未逐一核查（仅报 `nr_execute_action`） |

### 一次自查：判据 A 首版全部误报（lesson 141）

首版正则 `%(?![%])[sdifouxXeEgGcprga]` **匹配不到精度说明符**（`%.2f`、`%.40s`），把 7 条正确日志全判为不匹配；修正后归零。

⇒ **扫出命中后先自问"我的判据有没有漏形态"**——这是 lesson 44 的第 N 次兑现。

---

## 五、修复建议

### MA-29（五处）

**最小修复（不改功能，只改诚实度）**——照抄 `intent_action.py` 的形状：

```python
def _action_webhook(self, rule, event):
    logger.info(f"[规则] webhook 动作未实现: rule={rule.get('rule_id')}")
    return True, {
        "ok": True,
        "action": "webhook",
        "dispatched": False,                      # ← 补上
        "skipped": True,                          # ← 补上
        "detail": "webhook 下发尚未接入，已记录建议（未实际派发）",
    }
```

**完整修复**：`_action_alert` 已有注入的 `self.alert_dispatcher`，一行即可接通：

```python
self.alert_dispatcher.dispatch(title=..., body=..., level=...)
```

### MA-30（一处）

二选一：
- **有实现** → 接上真实执行逻辑
- **无实现** → 至少改为失败/未实现回执，不要说"已执行"：

```python
return error("该动作类型尚未接入执行器（未实际执行）", 501)
```

### 建议加门禁

> `check_stub_claims_success.py`：断言函数体内含 `TODO`/`FIXME` 注释时，
> 其返回/响应中不得出现 `ok=True` 或"已执行/已完成/成功"类措辞。

这条能一次性封住"桩谎称成功"整族，与项目"基线只准减少"的机制契合。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | 触发含 `webhook` 动作的规则（非 dry_run） | 响应含 `dispatched=False` 与明确"未派发"说明 |
| 2 | 同上后查 `rule_trigger_history` | 能区分"真派发"与"桩"（或不再落 `dry_run=0`） |
| 3 | 触发含 `alert` 动作的规则 | `AlertDispatcher.dispatch` **被调用**（或明确标未派发） |
| 4 | `POST /api/nr/execute-action` | 不再返回"动作已执行"（501 或真实执行） |
| 5 | `log` / `infer_activity` 动作 | 仍真执行（不回归） |

---

## 六、横向观察

### 这是"假成功"族的第三次出现，但性质最严重

| 轮 | 缺陷 | 谎称方式 |
|---|---|---|
| 十三 MA-28 | `teach_signal` dry_run | 声明"只校验"但校验不存在 |
| **十四 MA-29** | 规则引擎 5 动作 | `ok=True` + 触发历史 `dry_run=0` |
| **十四 MA-30** | `nr_execute_action` | "动作已执行" |

前两次是"声明的那一步没兑现"，**MA-29/MA-30 是完整功能链路谎称完成**——不仅返回值假，还**留下了"已执行"的持久化痕迹**（`rule_trigger_history`）。这在排查时最具误导性。

### 又一次"同仓两种写法"，且对照极其完整

| 诚实的写法 | 谎称的写法 |
|---|---|
| `intent_action.py` 的 light/camera/webhook（明确"需要外部系统配合，已记录建议"+`skipped`） | `rule_engine.py` 的同名动作（`ok=True`） |
| `_action_infer_activity`（真落库） | `_action_alert` 等（只 log） |
| `rule_engine.py:840` 已修的 state TODO | 其余 5 个动作 TODO |

⇒ **团队完全知道该怎么写**——`intent_action.py` 那份就是标准答案，而且覆盖了相同的动作类型。问题仍是：知识停在写下它的那个文件里。

### 一个值得注意的细节

`get_rule_engine(store, alert_dispatcher)` **把 dispatcher 传进去了**，说明当初设计规则引擎时就打算让 alert 动作真派发。**接口接好了、依赖注入了、注释里写着"TODO: 调用 AlertDispatcher"**——只差一行。

这与第十三轮 MA-28 同形：**功能路径本身没写错，错的是"接上"那一步。**

---

## 七、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ `all_walk` / `rel_path` / `full_unparse` |
| **门 2** | 命中多时手工分诊 | ✅ **决定性**：判据 B 229 处 → 与第五轮静默降级族重叠，不单报；判据 C 24 处 → 逐读 4 处 SSE 均有注释说明理由，判非缺陷 |
| **门 3** | 每条缺陷有实测 + 同仓对照 | ✅ MA-29 七种动作实测（spy dispatcher 调用 0 次）+ 三条独立证据（依赖注入/诚实对照/已修同类）；MA-30 路由注册与鉴权均已核实 |

### 遗留队列（十四轮累积）

| 项 | 状态 |
|---|---|
| **MCP 工具登记表** | ✅ 十三轮闭合 |
| **启动期写工具断言** | ✅ 十三轮闭合（反向变异验证有效） |
| **锁覆盖** | ✅ 十二轮闭合 |
| **logging 格式串** | ✅ 本轮闭合（0 处） |
| **`nr_routes.py` 其余路由** | ⚠️ 只报了 `nr_execute_action` |
| **判据 B 的 229 处** | ⚠️ 与第五轮静默降级族重叠，未逐一重新分诊 |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 34 处未钳制 `LIMIT` 站点 | ⚠️ 抽查 |
| 24 个数值配置键中其余 16 个 | ⚠️ 未逐一找消费点 |
| 29 处传递性阻塞中未单报的 | ⚠️ 归入 MA-24 的 P99 范畴 |
| `fail-closed` 39 处声明 | ⚠️ 已核成员隔离 + 写工具断言两支 |
| 其余 3 处 `dry_run` | ⚠️ 静态确认正确，未逐一实测 |

### 环境

| 项 | 值 |
|---|---|
| 已装 | `bcrypt`、`python-jose`、`httpx`、`tomli` |
| 仍未装 | `chromadb`（体积过大）、`starlette`、`pymysql` |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **136–141**（已并入 `lessons-round2.md`，共 1517 行） |
| 仓库状态 | 探针已还原；源码、量具、门禁、基线均未修改 |
