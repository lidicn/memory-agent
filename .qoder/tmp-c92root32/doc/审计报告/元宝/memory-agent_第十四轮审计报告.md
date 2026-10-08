# memory-agent 第十四轮审计报告

> 审计对象：https://github.com/lidicn/memory-agent （main 分支）
> 审计日期：2026-10-04
> 本轮方向：**69 处静默降级正确性验证**
> 前置：第一~十三轮累计 P1×6、P2×9、P3×6、P4×3
> 本轮为新增 5 轮的第四轮

---

## 一、结论先行

> **本轮确认 1 个 P2（P2-10）、1 个 P3（P3-7）。**
>
> 但本轮真正的价值不在缺陷数，而在于**推翻了自己第一版的判级**：
>
> 初版把 57 处 `except: pass` 一律标为"D1-致命"。人工复核后发现，**绝大多数是正确的容错设计**——真正的判据不是"except 里写了什么"，而是"吞掉异常后控制流是否继续走了本不该走的路径"。

---

## 二、扫描规模：176 处，远超预期的 69 处

第一轮报的"69 处静默降级"是**保守估计**。本轮用 AST 精确扫 `ExceptHandler` 的 body：

```
静默降级点总计：176 处

降级值类型分布：
  pass            57
  return None     39
  return ok:False 34  （其中 37 处带 error/reason 说明）
  return []       14
  return {}       13
  return 0        10
  return ""        7
  空 set / 空信封  2
```

---

## 三、P2-10　`_pinned_entities` 降级导致用户手动拆分被静默撤销

**位置**：`src/memory_agent/identity.py:404`

```python
def _pinned_entities(self) -> set[str]:
    """人工拆分（user-pinned）后固定下来的实体 id。"""
    pinned: set[str] = set()
    try:
        for dev in self.store.list_logical_devices():
            if (dev.get("provenance") or "") != "user-pinned":
                continue
            ...
    except Exception:  # noqa: BLE001
        return set()          # ← 查询失败 → 空集合，无日志
    return pinned
```

### 下游后果链

```python
pinned = self._pinned_entities()          # 失败 → set()
for e in entities:
    if e["entity_id"] in pinned:          # 失败时恒为 False
        clusters.append({"members": [e]})  # 独立成簇，不参与自动合并
        continue
    # 否则进入相似度合并逻辑
```

| 状态 | 行为 |
|---|---|
| 正常 | pinned 实体独立成簇，**不参与自动合并** |
| 降级 | pinned 为空 → 用户手动拆分的实体**重新进入相似度合并** |

**后果**：用户花了力气拆开的实体，因一次查询失败被重新合并回去，且**无任何日志**。

### 定级 P2 的理由

- 后果是**用户显式操作被静默撤销**，数据层面不可逆（合并后需用户重新拆分）
- 触发条件：`list_logical_devices()` 恰好在聚类那一刻失败——**概率低但非不可能**
- 无日志 → 事后无法追溯

### 修复建议

加日志；或降级时**保守处理**（失败即中止聚类，而非继续用空 pinned 合并）。

---

## 四、P3-7　`_degrade` 对非 dict 降级值不补 `error` 字段

**位置**：`src/memory_agent/insights/api.py:67` 装饰器实现

```python
result = factory()
if isinstance(result, dict):       # ← 只有 dict 才补 error
    result["error"] = str(exc)
return result
```

21 个 `@_degrade` 中，20 个的工厂返回 `Page.build([]).to_dict(...)`（dict，会补 error ✅）。

**唯一例外**：

```python
@_degrade(list)
def room_names(self, only_enabled: bool = True) -> List[str]:
```

### 实测对比

```
room_names()      失败 → []                              （无处放 error）
entity_catalog()  失败 → {'items': [], 'total': 0, 'error': '数据库连接失败'}
```

**同一个装饰器，两种截然不同的可观测性**：调用方无法区分"真的没有房间"和"查询失败"。

### 定级 P3 而非 P2

- 缓解因素：装饰器已记 `LOG.exception`，运维可查日志
- 下游影响有限：房间解析失败 → 降级为"无房间约束"，而非返回错误数据
- 已确认**不影响** `match_device`（它从 `name_map` 取 rooms，不走 `room_names()`）

### 建议

给 `_degrade` 加个可选参数，或在 `room_names` 上改用返回 dict 的工厂。

---

## 五、本轮最重要的方法论教训：推翻自己的判级

### 初版分级（错误）

| 级别 | 数量 | 判据 |
|---|---|---|
| D1-致命（吞异常） | 57 | `except: pass` 一律算致命 |
| D2-有害 | 80 | 返回空值/None |
| D3-可接受 | 39 | 带 error 说明 |

### 人工复核后的真相

抽查 8 处 D1，**全部是正确设计**：

| 位置 | 实际性质 |
|---|---|
| `vision_service.py:279/756/765` | 可选信号源探测链——边缘信号失败→试运动信号→都没有则返回 `(False,"")`。**探测链的正常写法** |
| `vision_service.py:495` | `os.remove` 清理可选文件，`except OSError: pass` |
| `debug_routes.py:106/114/118/129/141` | 队列操作容错，且有 `print` 提示 |
| `store.py:764` | `except FileNotFoundError: pass`（删除可能不存在的文件） |
| `store.py:1922` | `close()` 时容错，资源释放不应抛 |

### 修正后的判据

> **真正的判据不是"except 里写了什么"，而是：吞掉异常后，控制流是否继续走了本不该走的路径？**

| 类 | 描述 | 处置 |
|---|---|---|
| **A** | 可选步骤失败 → 跳过，继续下一步 | **正确**（绝大多数 D1 属此类） |
| **B** | 核心查询失败 → 返回空值当正常结果 | 缺陷 |
| **C** | 核心查询失败 → 返回空值且**无日志** | 严重缺陷 |

按此重分，B/C 类剩 **55 处**（而非初版的 137 处）。

**教训**：先按语法模式批量分级，再不加复核地写进报告，会造出 80+ 个假 bug。这与第七轮（async 无 await 不竞态）、第八轮（写有校验读没有）、第十三轮（内部 helper 无需规范化）是同一条原则——**必须理解异常处理在控制流中的角色**。

---

## 六、第十四轮产出小结

| 项 | 结果 |
|---|---|
| 新缺陷 | **P2-10 ×1**（`_pinned_entities` 降级撤销用户操作）、**P3-7 ×1**（`_degrade` 非 dict 不补 error） |
| 工作流改进 | 2 个探针（`r14_probes.py`：R14-1 静默降级、R14-2 `_degrade` 工厂） |
| 证伪 | 57 处 D1 中抽查 8 处全部为正确容错；37 处带 error 说明的 ok:False 为正确设计 |

### 收敛判据

本轮出现新 P2 → **判据 2 中断**，重新计数。

---

## 七、规划执行状态

- [x] 第十一轮 · 孤儿功能族 triage + 工具契约 → P3×1
- [x] 第十二轮 · 高复杂度函数 → P2×1 + P3×1 + P4×1
- [x] 第十三轮 · 委托规范化 + 时区点 → P4×2
- [x] **第十四轮 · 静默降级正确性 → P2×1 + P3×1**
- [ ] **第十五轮 · 缓存/状态一致性 + 收敛总报告** ← 最后一轮

---

## 八、审计覆盖声明

- 本轮为**第十四轮**。
- 176 处静默降级点中，**逐个阅读上下文的约 15 处**（identity 4、vision_service 5、debug_routes 6 抽样、store 2）。其余 161 处基于"降级值类型 + 有无日志 + 有无注释 + 文件归属"做**统计性分类**，**未逐行人工复核**。
- P2-10 的后果分析基于代码阅读与逻辑推演，**未运行时复现**（需构造 `list_logical_devices()` 抛异常的时机）。
- B/C 类 55 处仅列出文件分布，**未逐一验证**。
- `_degrade` 的实测是**复刻装饰器实现的独立脚本**，非项目运行时。
- 本轮未做：并发压测、长跑、真实 HA 环境。
- 环境限制：Python 3.10（低于项目要求的 3.11）、Semgrep/radon 未安装。
