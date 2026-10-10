# memory-agent 审计报告 · 第三期第九轮

- **审计对象**：`lidicn/memory-agent`
- **轮次**：`round-009`
- **报告日期**：2026-10-09

---

## 一句话结论

**第八轮遗留的 11 条 `ERRH-05-no-cleanup`（medium）全部证伪，没有一条是真泄漏。根因有三个叠在一起的判据缺陷，逐个修掉（W100 / W100b / W101）后：11 条 medium 降为 low 并注明「非流式不泄漏」，流式单独出一条 medium 规则（本项目 0 命中）。门禁 ok=True / drift=0。本轮新增确证缺陷 0 项。**

---

## 一、R4【证伪】11 条「超时后未清理」全是误报

判据是"有网络调用 + 有 timeout，**但没有 finally 也没有 with**"。
三个缺陷叠在一起：

### W100：「提到」≠「调用」

`NET_CALL` 是正则，匹配文本。`llm_client.LLMProvider.__init__`
被判"含网络调用"——实际它只有 `self._client: Optional[httpx.AsyncClient]`
这类**赋值与类型注解**，一次请求都没发。

修法：加 AST 判定 `_has_real_net_call()`，先排除注解节点（AnnAssign / arg.annotation），
再要求调用动词是 `get/post/request/send/...`。

### W100b：`.get()` 会命中 `dict.get`

**这条是我修 W100 时自己引入的。** 用 AST 后，
`backend.get("name", "")` —— 一个纯字典操作 —— 又被判成网络调用。

讽刺的是，规则文件第 205 行的注释早就写着：

> `.get(`/`.post(` 会命中 dict.get 与 FastAPI 装饰器 → 623 条全误报

我写 AST 版时把它又带回来了。修法是再要求**接收者**带网络特征词
（`client/session/httpx/conn/sock/http/api/...`）。

**这与 W61（函数内 import 同名）同源：正则看得见文本，看不见语义角色；
第二次遇到 ⇒ 该走 AST 而不是正则。**

### W101：非流式响应根本不需要 finally

`httpx.Response.close` 的文档写着：

> Automatically called if the response body is read to completion.

非流式 `client.get/post` 会把响应体完整读入内存，**没有需要显式释放的资源**。
只有 `client.stream(...)` / 裸 socket / 文件句柄才真需要 `with/finally`。

**全项目唯一的流式调用 `llm_client.py:340`，反而写对了：**

```python
async with httpx.AsyncClient(timeout=timeout_cfg) as client:
    async with client.stream("POST", ...) as resp:
```

⇒ **11 条全部误报，一条真泄漏都没有。**

---

## 二、W100 / W100b / W101 修复效果

| 规则 | 修前 | 修后 |
|---|---|---|
| `ERRH-05-no-timeout` | high 5 | **0**（第八轮已修） |
| `ERRH-05-no-cleanup` | medium 11 | **low 11**（注明"确认非流式后可忽略"） |
| `ERRH-05-stream-no-cleanup`（新增） | — | medium **0**（流式规则，本项目无命中） |
| `ERRH-05-httpx-default-timeout` | — | low 1（M14） |

**关键设计：`low` 而不是消除。** 非流式不泄漏是 httpx/requests 的语义，
换成别的库未必成立；降权并注明理由，比静默删掉诚实。

### 负向测试（全部通过）

```
✓ 流式 + with        → streaming=True
✓ 流式 + 无 with     → streaming=True
✓ 非流式 get         → streaming=False
✓ dict.get           → streaming=False
✓ 注解提到 AsyncClient → 不算调用
✓ 真的 client.get    → 算调用
```

门禁：**ok=True / failures=[] / drift=0**。

---

## 三、数字

| 项 | 值 |
|---|---|
| 新增确证缺陷 | **0** |
| 证伪 | **1 组 11 条**（R4） |
| no-cleanup | medium 11 → **low 11** |
| 门禁 | ok=True / drift=0 |

---

## 四、台账现状（无变化）

| ID | 严重度 | 状态 |
|---|---|---|
| M1 / M2 | high | 已修复验证 |
| M5 | high | 已修·待确认磁盘开销 |
| M6 / M7 / M9 / M13 | medium | 已修复验证 |
| M3 | medium | 待你确认部署路径 |
| M12 / M14 | low | still_open |
| M8 / M10 | low | still_open |
| M4 | info | 已核实历史修复 |

---

## 五、如实说明

- **本轮新增确证缺陷为 0**，全部产出是判据收敛与证伪。
- **11 条降为 low 不等于"我已逐条确认非流式"** —— 判据依据是
  `client.get/post` 未传 `stream=True`，属**结构性判断**，不是运行时确认。
- **唯一流式调用已人工确认写法正确**（`async with client.stream(...)`）。
- 依赖 CVE 面仍未扫（pip-audit 无法解析 pyproject、PyPI/OSV 403）⇒ 风险未知。
- 全量命中中的 medium/low 仍未逐条分诊。
- 补丁只在只读副本 `repos/ma-patched`，**原仓库未改动**。

---

## 六、本轮最该记住的一条

**我在修 W100 的时候，把 W100b 这个坑又踩了一遍。**

规则文件第 205 行白纸黑字写着"`.get(` 会命中 dict.get → 623 条全误报"。
我从正则改成 AST，本意是修掉"注解被当成调用"，
结果新写的 AST 判据**把 dict.get 又判成了网络调用**。

教训有两层：

1. **改判据实现方式（正则→AST）不等于修掉语义缺陷。**
   语义缺陷是"分不清这个 `.get` 是字典还是 HTTP"，换个实现方式它照样在。

2. **注释里记录的历史坑，改代码时要回头读。**
   那条注释是别人（前几轮的我）用 623 条误报换来的；
   我没读它，于是用另一种方式把同一个坑又踩了一次。

推论：**一个判据缺口如果反复出现（这里是第三次：W61 import 同名、
W79f 包名映射、本轮 dict.get），该做的不是每次补一条排除规则，
而是把"语义角色判定"抽成公共能力。**
W100 + W100b 合起来才勉强够用，但它们仍然是两个补丁，不是一个机制。
