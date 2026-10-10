# memory-agent 审计报告 · 第三期第十三轮

- **审计对象**：`lidicn/memory-agent`
- **轮次**：`round-013`
- **报告日期**：2026-10-09

---

## 一句话结论

**本轮新增确证缺陷 0 项，全部产出是判据收敛：medium 344 → 268。攻掉的三批噪声各有独立根因——ERRH-02 按返回值语义分层（W109）、AFS-02 识别候选枚举（W111）、MA-TXN 的 `'updated_at'` 误判与 f-string 拆分（W112/W113）。另复核确认：DO-01 那 2 条 high 是 M6 已修但静态规则看不见运行时护栏，已加抑制并留证据。**

---

## 一、medium 收敛曲线

| 阶段 | medium | 来源 |
|---|---|---|
| 第十二轮末 | 344 | — |
| W109/W109b（ERRH-02 分层） | 300 | ERRH-02 77 → 33 |
| W110/W111（可观测性 + 候选枚举） | 277 | ERRH-02 33→26、AFS-02 23→7 |
| W112/W113（MA-TXN 修正） | **268** | MA-TXN 9 条假阳性消除 |

---

## 二、三批噪声与根因

### W109：ERRH-02「伪装成功」不分层

原判据：`except` 里 `return` 一个常量就报 medium。
但它没区分三种语义完全不同的返回值：

| 返回值 | 语义 | 分级 |
|---|---|---|
| `None` / `''` | **显式空**，调用方能判空 ⇒ 失败可观测 | **low** |
| `True` / `{"ok": True}` | **真正伪装成功**，调用方无从分辨 | **high** |
| `{}` / `[]` / `0` | 取决于调用方怎么用 | medium（有日志降 low） |

修后：high 从 16 → **1**（就是 M15 的 `_health_allows`），
其余 51 条 `None` 降 low。**这 51 条本来就把"失败可观测"报成了"伪装成功"。**

**W109b**：第一版 `v.startswith("{")` 把 `{}` 误判成 `{"ok": True}` ——
负向测试 7 例中第 7 例失败才抓出来。

### W111：AFS-02「批量静默失败」没识别候选枚举

`analysis.py:207 parse_insight`：

```python
for candidate in candidates:
    try: json.loads(candidate)
    except ValueError: continue
    return {"ok": True, ...}      # 成功即返回
```

这是**逐个候选尝试、成功即返回**的回退模式，
不是"批量处理中失败被吞掉"。⇒ 加 `_is_candidate_enumeration()`：
循环体非 try 语句内有 `return`/`break` 即判候选枚举，跳过不报。

修后 AFS-02 medium 23 → **7**，且真批量（`bind_stale_templates`）**保留**。

### W112/W113：MA-TXN 两个相邻缺陷叠加

**W112** —— `startswith("UPDATE")` 命中了**列名常量** `'updated_at'`
（`"UPDATED_AT".startswith("UPDATE")` 为 True）⇒ 2 条假阳性。
改正则 `^UPDATE\s+\w`，并排除 `ON CONFLICT` 单行 upsert。

**W113** —— f-string SQL 在 AST 里是 `JoinedStr`，被拆成多个 `Constant`：

```
f"UPDATE members SET {cols} WHERE id = ?"
   → Constant "UPDATE members SET "   ← 判据只看这个
   → Constant " WHERE id = ?"          ← WHERE 在这，看不见
```

`_sql_strings()` 只收集 `Constant` ⇒ **看不见 WHERE ⇒ 误报"全表更新"**。

实测 9 条 MA-TXN-HEAVY-UPDATE：**全部带 WHERE 或为 ON CONFLICT upsert，无一为真**。

⇒ **W112 错在字符串前缀，W113 错在字符串拼接，是相邻的两个缺陷。**

---

## 三、M6 复核：DO-01 静态规则看不见运行时护栏

补丁副本上 DO-01 仍报 2 条 high。**不是没修好，是规则看不见。**

补丁形态：`_load_users` 读失败 → `mark()` 置位；`_save_users` 写前 → `guard()`。
静态规则只看到"读失败返回 `{}`" + "写侧全量覆盖"，看不到 `guard()` 这道运行时闸门。

手工复刻对照（bcrypt 未装，无法直接 import `AuthManager`）：

| | 结果 |
|---|---|
| 无护栏 | 盘上只剩 `['dave']` ⇒ **原 alice/bob/carol 被覆盖** |
| 有护栏 | 写侧抛 `StateUnreadable` ⇒ **拒绝写入，损坏文件保留** |

⇒ 已加抑制并写明"实测复刻"的证据。**静态规则判不了运行时护栏，这是已知边界。**

---

## 四、数字

| 项 | 第十三轮 |
|---|---|
| 全量命中 | 569 → **553** |
| medium | 344 → **268** |
| high | **4**（M15 已修 ×2 条规则、M6 已修 ×2） |
| 新增确证缺陷 | **0** |
| 门禁 | ok=True / drift=0 |

**medium 剩余**：MIXED-RETURN 116、ERRH-02 26、OUTB-01 22、RSC-01 17、
AFS-07 16、AFS-06 13、AFS-01 10、AFS-02 7。

---

## 五、台账

| ID | 严重度 | 状态 |
|---|---|---|
| M1 / M2 | high | 已修复验证 |
| M5 | high | 已修·待确认磁盘开销 |
| M6 / M7 / M9 / M13 / M15 / M16 | medium | 已修复验证 |
| M3 | medium | 待你确认部署路径 |
| M12 / M14 | low | still_open |
| M8 / M10 | low | still_open |
| M4 | info | 已核实历史修复 |

---

## 六、如实说明

- **本轮新增确证缺陷为 0**。产出全部是判据收敛与复核。
- **M6 复核用的是"复刻 mark/guard 调用形态"，不是直接 import AuthManager**
  （bcrypt 未装）⇒ 强度低于端到端实测，已如实标注。
- **state PoC 在 memory-agent 上 targets=2 / unavailable=2**（模块导入不了，
  归因"环境缺口"）⇒ 该项目**尚未建立自动状态 PoC**，M6/M7 靠手工确证。
- 268 条 medium 仍未逐条分诊（MIXED-RETURN 116 占 43%）。
- 依赖 CVE 面仍未扫（pip-audit 无法解析 pyproject、PyPI/OSV 403）⇒ 风险未知。
- 补丁只在只读副本 `repos/ma-patched`，**原仓库未改动**。

---

## 七、本轮最该记住的一条

**W112 和 W113 是同一条 SQL 判据上相邻的两个缺陷，而且它们的性质不同。**

W112 是**我知道该怎么判却写错了**——用 `startswith` 判 SQL 关键字，
忘了 `'updated_at'` 这种字段名也能匹配上。这是编码疏忽。

W113 是**我根本没想到**——f-string 在 AST 里不是一整个字符串，
而是被拆成若干 `Constant` 片段。我一直假设"SQL 文本就是一个 Constant"，
这个假设在 f-string 普遍使用的项目里是错的。

**两者都能被负向测试抓到，但只有 W112 被我预先想到的用例覆盖了。**
W113 是跑完 W112 后发现"还剩 4 条"才追出来的。

推论：**修完假阳性后如果还有剩余，不要急着归为"剩下的都是真的"。**
本轮 W112 修完剩 4 条，如果我接受"4 条真缺陷"这个结论，
就会把 4 个带 `WHERE id = ?` 的标准单行更新报成"全表更新"。
**剩余量本身就是判据未覆盖完的信号。**

另一条：**静态规则看不见运行时护栏（M6 复核）。**
这不是规则写得不好，是静态分析的固有边界。
应对方式不是继续改规则，而是**让每条"已修复"都有实测对照**，
并在抑制项里写明"用什么方法验证过"，下次才不用重新怀疑一遍。
