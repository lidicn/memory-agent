# memory-agent 审计报告 · 第三期第八轮

- **审计对象**：`lidicn/memory-agent`
- **轮次**：`round-008`
- **报告日期**：2026-10-09

---

## 一句话结论

**ERRH-05 报的 5 条「网络调用无超时、远端不响应会永久挂起」全部证伪，没有一条是真缺陷。根因是判据只看调用点有没有 `timeout=` 关键字，看不见 httpx/socket 的**对象级默认超时**——由此提炼 W99 并修复，5 条 high 归零，另立 M14（low）记录一个真实但轻微的问题：21 处 HA 请求依赖 httpx 默认 5 秒。**

---

## 一、R3【证伪】5 条「无超时」全是误报

| 位置 | 规则判定 | 实测 |
|---|---|---|
| `ha_client.py:23/64` | 无 timeout | `httpx.Client()` 无参 ⇒ 默认 `Timeout(5.0)`（已实测） |
| `tv_service.py:411/421` | socket recv 无限阻塞 | `socket.create_connection(timeout=3.0)` 会把 timeout 设到 socket 上，`gettimeout()` 返回 3.0，**recv 3.00s 抛 timeout** |
| `vision_service.py:223/411/651` | 无 timeout | 签名含 `timeout` 形参 / 函数体显式设置 |
| `llm_client.py:120/150` | 无 timeout | `httpx.AsyncClient(timeout=httpx.Timeout(self.timeout))` |

**29 处未传 timeout 的 `.get()`，全都有 5 秒保护。**

### ⚠ 我自己在这里犯了一次探针失误

验证 socket 语义时，我在测试脚本里调了 `c.settimeout(5)` 来"防止测试挂死"——
**这一句覆盖掉了被测的 3.0**，于是第一版测出"recv 阻塞 5 秒"，配合我预写的
注释"None 表示无限阻塞"，看起来完美印证了假设。

去掉 `settimeout(5)` 重测，真实结果是 **3.00 秒抛 timeout**。

**这已经是本项目第 N 次"工具/探针干扰被测对象"**，这次是我亲手制造的：
为了防止测试本身卡住而加的保护，恰恰改变了被测行为。

---

## 二、W99【工作流】对象级默认超时识别

### 缺口

ERRH-05 的判据是 `TIMEOUT_HINT.search(函数源码)`，
只看**调用点**有没有 `timeout` 字样。看不见：

- `httpx.Client()` 无参 ⇒ 默认 5s
- `socket.create_connection(..., timeout=X)` ⇒ X 对 recv 也生效
- `sock.settimeout(X)`

**这与 doubao-butler W62（timeout 在被调函数里）是同一族，「超时不在调用点」第三次遇到 ⇒ 该做通用化。**

### 修法（三档，不是一律消除）

| 档 | 判据 | 处置 |
|---|---|---|
| `explicit` | client 构造带 timeout / socket settimeout | **消除**（真没有缺失） |
| `httpx-default` | httpx 系列无参构造 | **降 low 并注明**，不静默 |
| `requests-none` | requests 系列无 timeout | **保留 high**（requests 默认真的无限等待） |

**关键设计：`httpx-default` 不消除而是降级。** 因为默认 5s 未必匹配业务耗时——
`fetch_frame` 的注释就写着"米家关键帧间隔长，实测 4~13s"，依赖 5s 会频繁超时。

### 效果

```
ERRH-05-no-timeout:  high 5 → 0
新增 ERRH-05-httpx-default-timeout: low 2
门禁 ok=True, drift=0
```

> 另需说明：剩余 13 条 medium 是 `ERRH-05-no-cleanup`（超时后未清理资源），
> 是**另一条检查项**，与本次超时判据无关，本轮未处理。

---

## 三、M14【低】21 处 HA 请求依赖 httpx 默认 5 秒

`ha_client.py` 共 37 处 HTTP 调用：
**8 处**显式设了 `timeout`（5 / 10 / 30 / 60 秒，其中 `HISTORY_TIMEOUT = 60`），
**21 处**用默认 5 秒。

作者专门为历史查询设了 60 秒，说明**已知部分 HA 接口很慢**。
其余接口是否都能在 5 秒内返回，**没有实测**。

定 low 的理由：这不是"永久挂起"（原判 high 是误报），只是"默认超时可能偏短"。
需实测 HA 接口耗时才能定性。

---

## 四、数字

| 项 | 值 |
|---|---|
| 本轮全量命中 | 982（high 16 / medium 752 / low 214） |
| 新增确证缺陷 | **1**（M14，low） |
| 证伪 | **1 组 5 条**（R3，ERRH-05 无超时） |
| ERRH-05-no-timeout | high **5 → 0** |
| 门禁 | ok=True / drift=0 |

---

## 五、台账现状

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

## 六、如实说明

- **M14 未实测 HA 接口真实耗时**，只确认"默认 5s"这个事实与作者的显式设置不一致。
- **13 条 `ERRH-05-no-cleanup`（medium）本轮未处理**。
- **依赖 CVE 面仍未扫**（pip-audit 无法解析 pyproject、PyPI/OSV 403）⇒ 风险未知。
- **门禁第一次跑 FAIL**：`external_tool_defects` 0 命中，原因是外部工具跨 bash 调用丢失
  （第十六轮记录过的现象）。在同一次调用内先 `ensure_tools.py` 再跑，恢复 ok=True。
- 全量命中中的 medium/low **仍未逐条分诊**。
- 补丁只在只读副本 `repos/ma-patched`，**原仓库未改动**。

---

## 七、本轮最该记住的一条

**这次最贵的不是 W99，是我验证 socket 时自己制造的那次失误。**

为了防止测试卡死，我在脚本里加了 `settimeout(5)`——
它把被测的 3.0 覆盖掉了，于是测出"阻塞 5 秒"。
而我预写的打印文案又正好写着"None 表示无限阻塞"，
**两个错误互相印证，差点让我把一个假阳性写成真缺陷。**

去掉那行重测，答案是 3.00 秒。

和第五轮那个 GIL 测量假象是同一类：**测量手段本身改变了被测对象**。
区别在于，第五轮是环境（GIL 调度）造成的，这次是我自己写的探针造成的。

推论：**给探针加保护时，必须确认这个保护不会作用于被测路径。**
"防止测试挂死"和"测出真实行为"在这里是直接冲突的——
正确做法是用外层超时（子进程 / 信号），而不是改被测 socket 的属性。
