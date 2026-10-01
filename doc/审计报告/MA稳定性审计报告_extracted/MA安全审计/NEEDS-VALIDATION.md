# NEEDS-VALIDATION 与未覆盖缺口

## 本轮 `needs_validation` 记录：**0 条**

按技能定义，`needs_validation` 表示"一个有源码依据的边界假设被阻塞，缺少确切事实"。本轮 7 条候选的事实均在源码内闭合（区分在于它们是**稳定性缺陷**而非安全边界违反，故记 `rejected` 而非 `needs_validation`）。

---

## 需在真实部署确认的事项（非 needs_validation，属事实缺口）

| # | 事项 | 为什么需要 owner 确认 |
|---|---|---|
| 1 | **`ha_db` 并发调用点** | 沙箱源码审查只见 `poller.py` 附近单点 `asyncio.to_thread`。若真实部署（或未来版本）存在按实体批并发回填，缺陷 3 的可触发性与严重度需上调 |
| 2 | **`_CONV` 的真实增长速率** | 沙箱用合成 messages（8KB/条）。真实 LLM 上下文含 tool 结果，单条可能数十 KB——需 owner 按实际调用量估算 OOM 时间窗 |
| 3 | **Python 版本差异** | 沙箱为 **3.10.12**，项目要求 **≥3.11**。测试基线 6 failed 中是否有版本差异导致者，需在 3.11 环境复跑确认 |
| 4 | **debug 接口的暴露面** | `debug_routes` 无条件挂载（`api/__init__.py:55,58`），仅 `require_user`。若生产对外暴露，缺陷 2 的放大倍率需重估 |

---

## 本轮未覆盖（scoped run 声明）

21 个覆盖单元中 **12 个 `out_of_scope` 未审**，**绝不等同于"无问题"**：

| 面 | 关注点 |
|---|---|
| `auth.py` / `api/deps.py` | JWT 校验、`require_admin` 分级 |
| `mcp_server.py` | stdio 工具与 scope 门（已有报告覆盖工具登记问题，本轮不重复） |
| `acp_server.py` | ACP 协议入口与属主校验 |
| `ha_client.py` | 出向 HTTP 主机校验 |
| `config.py` | 密钥加载与回退链 |
| `backup.py` | 备份恢复（已确认 `VACUUM INTO` 有 `finally: conn.close()`，健康） |
| `vision_service.py` | 图像处理资源占用 |
| `mqtt_bridge.py` | broker 连接与 ACL（已确认有退避重连与 `_closed` 标志，健康） |
| `store.py` SQL 构造 | 注入面（已确认 FTS5 问题由既有报告覆盖） |
| `pyproject.toml` | 依赖与供应链（`algo` 可选依赖含 AGPLv3 的 pm4py，商用许可需关注） |

---

## 已比对并**不重复**报告的既有发现

仓库内已有 4 份审计报告。本轮已逐条比对，以下为既有发现，**不在本轮重复**：

- `InsightService` 参数顺序错位 —— **已修复**（`runtime.py:69` 现为 `(self.store, self.config)`）
- `announcer.py` `time.monotonic()` 配 `0.0` 哨兵 —— **已修复**（现用 `None` 哨兵）
- MCP 工具登记未实现 / 重复注册 / 权限判定矛盾
- FTS5 external-content 索引重建后不 `rebuild`
- `test_openshs_bench` 等算法输出为空（34 项）
