# memory-agent 审计报告 · 第三期第十五轮（FOCUS-2 定向专题）

- **审计对象**：`lidicn/memory-agent`
- **轮次**：`round-015`
- **模式**：定向专题（`focus.json` FOCUS-2：MCP / ACP 外部输入面）
- **报告日期**：2026-10-09

---

## 一句话结论

**确证两项 high 并修复：M18（`vision_test_llm` 的 `vlm_base_url`）与 M19（`face_node_register` 的设备端 `url`）。两者都会把**家庭摄像头画面**POST 到攻击者指定的地址——M18 还会连**已配置的 VLM API Key** 一起送出。这是继第十二轮 M16 之后同族漏洞的第二次、第三次出现，且危害递增。**

---

## 一、为什么是这个专题

`focus.json` 里 FOCUS-2 排第二，理由是：**M16（SSRF）就出在这个面，且是 13 轮里唯一一次定向深挖的收获**。外部可控输入是 high 密度最高的区域。

本轮先枚举了全部接受外部可控 URL / 路径 / 文件名的端点，命中 10 个函数，逐个追去向。结果：2 个有缺陷，4 个确认防护到位。

---

## 二、M18【high·已修复验证】

### 缺陷

`vision_routes.py:111 vision_test_llm` 接受请求体的 `vlm_base_url` / `vlm_endpoint_path` / `vlm_api_key`。

关键在 `vision_service.py:424 vlm_analyze`：

```python
base = (base_url or self.config.vlm_base_url or "").rstrip("/")
...
httpx.post(f"{base}{path}", json=payload, headers={"Authorization": f"Bearer {key}"})
```

**`payload` 含 base64 编码的摄像头帧。**

而 `_unmask_or_none` 的规则是：body 传的是掩码 `********` 时返回 `None`，
**回退使用已保存配置里的密钥**——所以攻击者不填 `vlm_api_key` 也能拿到真实密钥。

### 实测确证

靶子收到：

```
Authorization: Bearer sk-CONFIGURED-VLM-SECRET-KEY-0001
body 1144B，含 data:image/jpeg;base64,...
```

### 危害

- 已认证用户可让服务端把**家庭监控画面**发到任意地址
- 同时外泄**已配置的 VLM API Key**（该密钥可能复用在其他服务）
- 可 SSRF 到 `169.254.169.254` 云元数据

### 修复对照

| | 云元数据 169.254.169.254 | 正常业务 192.168.1.50 |
|---|---|---|
| 原版 | **请求已发出** | 通行 |
| 补丁版 | 拒绝：不允许访问链路本地地址（含云元数据服务） | ✅ 通行 |

---

## 三、M19【high·已修复验证】

### 缺陷

`face_routes.py:57 face_node_register` 经 `_require_device`（**设备令牌**，比 `require_user` 弱）鉴权后，把 body 的 `url` 无校验存入节点。

识别时 `vision_service.py:672`：

```python
httpx.post(f"{url}/api/recognize", json={"image": "data:image/jpeg;base64,..."})
```

### 实测确证

靶子收到 **454B 含 `data:image/jpeg;base64,` 的 POST**。
对 `169.254.169.254` **无任何校验**。

### 与 M18 的差异

M19 的鉴权面更大——**设备令牌泄露即可触发**，不需要用户账号。

### 修复对照

| | 云元数据 | 局域网正常节点 192.168.1.90 |
|---|---|---|
| 原版 | 注册通过 → 识别时把**摄像头帧** POST 过去 | 通行 |
| 补丁版 | 拒绝：不允许访问链路本地地址 | ✅ 通行（业务不受影响） |

---

## 四、三者的关系：同族，但危害递增

| | 入口 | 泄露内容 | 状态 |
|---|---|---|---|
| M16 | `acp_test_outbound` | initialize 握手 + 回显 tools 列表（**指纹**） | 已修（第十二轮） |
| M18 | `vision_test_llm` | **摄像头帧 + 已配置 VLM API Key** | 已修（本轮） |
| M19 | `face_node_register` | **摄像头帧**（设备令牌即可触发） | 已修（本轮） |

**M16 修的时候只修了那一个入口，没把校验抽成公共能力 ⇒ M18/M19 原样复现。**

本轮 W119 补上了：新建 `outbound_guard.py`，提供 `validate_outbound_url` / `guard_outbound_url`，M18/M19 复用。

---

## 五、⚠ 一个必须让你决定的取舍

沿用 M16 的口径：**放行私有网段，只拒绝环回与链路本地**。

理由是 HA / go2rtc / Arcface 节点都在 `192.168.x.x`，一律拒私有会打断主业务（我在 M16 时就差点犯这个错——照搬 doubao-butler 的"拒私有"会把 HA 挡在门外）。

**但这留下了两个未收口的风险，我不会假装它不存在：**

1. **公网外泄未封堵** —— 攻击者用域名（不是 IP）就能让画面发到公网。补丁只挡了环回/链路本地。要封死必须加**显式白名单**，那是行为变更，需你确认。
2. **`private_only=True` 模式对域名无效** —— `face` 场景我原本想用 `private_only=True`（节点本应在局域网），但沙箱无 DNS，域名无法判定是否私有。用它收益极小、还有打断 `.local` 主机名的风险，**所以最终没用，并在补丁注释里写明了理由**。

---

## 六、确认防护到位的四处（不虚报）

| 入口 | 结论 |
|---|---|
| `behaviors_feedback_pack` 的 `snapshot_path` | 已有 `/data/` 白名单（`realpath`）+ label 白名单 + fail-closed ✓ |
| `config test_connection` | ha/nr/llm 分支**均用已保存配置**，不接受 body URL ✓ |
| `query_events` / `search_events` | `limit` clamp `[1,2000]`、`offset` clamp `≥0`，在服务层 ✓ |
| MCP 87 个工具 | wrapper 层"校验 0"是**假信号**——校验在下游服务层；已核验 `add_semantic_memory` 有 `if not text or not text.strip()` ✓ |

**MCP 那一条值得一提**：静态统计显示 87 个工具里 wrapper 层 8 个"校验 0"，
按第十二轮的教训我本来会当噪声放过，但这轮顺着查了下游，确认校验在服务层。
**规则报的"没有校验"经常是"校验在别处"**——这是 W62/W100 之后的又一次。

---

## 七、数字与台账

| 项 | 第十五轮 |
|---|---|
| 模式 | 定向专题（FOCUS-2） |
| 新增确证缺陷 | **2**（M18、M19，**均 high**） |
| 确认防护到位 | 4 处 |
| 门禁 | ok=False（降噪欠账，非本轮新增） |

**high 累计：M1/M2/M5（第 1 轮）、M18/M19（本轮）。**
定向专题两轮出了 1 个 medium + 2 个 high，对比泛扫 12 轮最高 medium——**方向修正有效。**

---

## 八、本轮差点犯的错

写 M19 补丁时，我在注释里写"故用 `private_only=True` 更贴合语义"，
**但代码里没传这个参数**。

这正是我自己反复批评过的**"注释声称做了、代码没做"**——
在 AutoForge 第十轮、本项目第十三轮的样本里都出现过。

发现的方式是回头读了一遍自己写的注释与代码。
已改为如实说明"为什么没用 `private_only`"。

推论：**写完补丁注释后，必须逐句对照代码确认每一条声称都真实存在。**
注释比代码更容易被信任，所以它说谎时危害更大。

---

## 九、如实说明

- **公网外泄未封堵**（见第五节），需你决定是否加白名单。
- **M18/M19 的"摄像头帧外泄"用的是构造靶子实测**，未在生产环境验证真实画面内容。
- `private_only=True` 对域名无效是**已知局限**，已在 `outbound_guard.py` 注释中标注，未冒充已解决。
- **降噪欠账仍未还**：447 条只核验 6 条，门禁 FAIL 持续中。
- 依赖 CVE 面仍未扫，M3 仍等你确认部署路径。
- 补丁只在只读副本 `repos/ma-patched`，**原仓库未改动**。

---

## 十、下一轮

按 `focus.json`：FOCUS-3（LLM 调用链与降级）。
同时**必须继续还降噪欠账**——连续两轮都只还了 6 条，红灯不会自己转绿。

另外建议：`outbound_guard` 已抽成公共模块，**应回头把 M16 那处内联实现也改成复用**，
否则三处校验逻辑会各自漂移。本轮未做，留给下一轮。
