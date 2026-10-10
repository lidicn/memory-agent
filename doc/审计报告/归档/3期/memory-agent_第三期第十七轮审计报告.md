# memory-agent 审计报告 · 第三期第十七轮（覆盖盲区）

- **审计对象**：`lidicn/memory-agent`
- **轮次**：`round-017`
- **模式**：**覆盖盲区**（`coverage.json` COV-01 ~ COV-05）
- **报告日期**：2026-10-10

---

## 一句话结论

**COV-02（状态损坏自动 PoC，漏 16 轮）本轮打通：新建专用适配器 `scripts/ma_state_poc.py`，实测原仓库 `data_lost`、补丁副本 `guarded`。M6 从"手工确证"升级为"每轮可回归"。COV-01（依赖 CVE）实测确认网络不可达——PyPI 与 OSV 均 HTTP 403，做了降级替代但**仍是风险未知**。COV-03 需你确认，COV-04 仍未做。**

---

## 一、COV-02：16 年的债，本轮还了

### 起点

`state PoC` 在 memory-agent 上一直是 `targets=2 / unavailable=2`，
报错 `ModuleNotFoundError: No module named 'bcrypt'`。

### 第一步：补环境

装了 `bcrypt 5.0.0` + `python-jose 3.5.0`（真实安装，非 stub）。
装完 import 不再报错，但变成 `seed-not-observable`——**能导入，测不到**。

### 第二步：通用探针补 W123 系列

| 编号 | 改动 | 解决什么 |
|---|---|---|
| W123 | `_CfgDuck`：config 类形参的鸭子替身，路径属性返回占位符 + 实例化后重定向 | `_Duck` 让 `os.path.dirname(_Duck)` TypeError |
| W123b | config 类形参**优先** `_CfgDuck`，不用 `_build_for_annotation` 造真对象 | 真 Config 的 `users_file` 落在探针 ROOT 之外 |
| W123c | ROOT 下建 `__pocroot__` 软链指向自身 | 被测代码原样使用占位符时，两条路径指向同一文件 |
| W123d | 播种优先**创建类**方法 | 实测先命中 `change_password`，空库上 no-op ⇒ count 0 |

**结果：unavailable → no_write。** 能实例化、能观测文件了，但仍判不出 `data_lost`。

### 第三步：专用适配器（W124）

通用探针要在十几个项目间共用，判据必须保守；专用适配器可以精确知道
`AuthManager` 的入口是 `register(username, password)`。

新建 `scripts/ma_state_poc.py`，四步协议：建库 → 播种 → 损坏 → 触发写盘。

### 对照实测

| | 播种 | 损坏后 | 盘上 | 丢失 | 判定 |
|---|---|---|---|---|---|
| **原仓库** | 3 条 | 写入成功 | 1 条 | alice/bob/carol | **data_lost** |
| **补丁副本** | 3 条 | 拒绝写入 | — | — | **guarded** |

补丁版日志：`状态文件不可读，已置位并拒绝后续覆盖写入`。

**M6 从此可以每轮自动回归，不再依赖我手工复现。**

---

## 二、COV-01：依赖 CVE 面——实测确认不可达

| 数据源 | 结果 |
|---|---|
| `pypi.org` | **HTTP 403** |
| `api.osv.dev` | **HTTP 403** |

**降级替代做了什么**：依赖声明静态审查。`pyproject.toml` 只声明下界
（`pydantic>=2.0`、`uvicorn>=0.30`、`httpx>=0.27`、`mcp>=2.0`、`pymysql>=1.1` 等），
**没有 pin 文件** ⇒ 不存在"锁定在含 CVE 旧版"的问题。

**但这不能替代 CVE 扫描**：无法证明当前实际安装的版本无 CVE。
**风险未知，不是无风险。**

---

## 三、剩余盲区

| ID | 面 | 状态 |
|---|---|---|
| COV-01 | 依赖 CVE | **网络不可达**，需联网环境或本地 CVE 库 |
| COV-03 | M3 部署路径 | **需你确认** `./data` 是否在 NAS 网络挂载上 |
| COV-04 | 268 条 medium 分诊 | 仍未做 |
| COV-02 | 状态损坏 PoC | ✅ **本轮打通** |
| COV-05 | 写入热路径/LLM/MCP-ACP | ✅ 014/015/016 已覆盖 |

---

## 四、本轮最该记住的一条

**"能导入"和"能测"之间隔着一层，我只解决了一半就以为解决了。**

装完 bcrypt 后 import 成功，我以为适配器通了——结果 `seed-not-observable`。
再补 W123 系列，`no_write`——还是没到 `data_lost`。
最后靠**按项目写死的专用适配器**才打通。

三层障碍，每一层都伪装成上一层已解决：
- import 失败 → 装依赖 → 看着像好了
- 测不到 → W123 → 看着像好了
- 判不出 → W124 → 真好了

**推论：通用探针的能力边界是"尽量覆盖"，不是"确证已知目标"。**
两者要分开建。我一直想用通用探针解决所有项目，这次才认——
它有它的职责，确证得靠专用适配器。这也解释了为什么 COV-02 能漏 16 轮：
每次我都是"改到 import 不报错"就收工了。

---

## 五、如实说明

- **COV-01 仍未扫**，网络不可达是实测确认的，不是没试。
- **W123 系列让通用探针从 unavailable 前进到 no_write，但 memory-agent 上
  仍未自动判出 data_lost** ——判据靠专用适配器。通用探针的 `no_write` 状态
  我没有冒充成"已验证安全"。
- **COV-03 需你确认**，16 轮未决。
- **COV-04（268 条 medium）仍未做**，且门禁里降噪欠账 447 条只核验 10 条。
- 补丁只在只读副本 `repos/ma-patched`，**原仓库未改动**。
