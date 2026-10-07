# vendor/ —— 进仓的构建产物

| 文件 | sha256 | 来源 | 授权 |
|------|--------|------|------|
| `homesdk-0.3.2-py3-none-any.whl` | `19bc83a67a96931c4556caec52f29c190aaa03a0a7036e0b41c242a533fb5505` | `E:\NAS\homesdk\dist\`，由 homesdk 源码 `7658e73` 构建，DCD 亲手构建并登记在 `dist/VERSIONS.txt` 0.3.2 段（49,374 B，22 个成员，新增 `homesdk/adm/`） | DCD 裁定 `20261007-MA五件与AF一件` §四 Q2（授权换 `vendor/` + `Dockerfile`；**镜像重烤随下一次既有变更窗**，不单独开窗） |
| ~~`homesdk-0.3.1-py3-none-any.whl`~~ | `b4b5d6bbe424205bb425762223576a3b3409c93f262f79bc8e5a7016ad4b814c` | 同一 `dist/`，由 homesdk commit `5e4ba33` 重建（44,126 B，18 个成员） | 原授权 `20261002-AgentOps与MA交付面` §二 Q1 = **A**（vendored wheel 进本仓 + Dockerfile 一行）；**2026-10-07 下架换 0.3.2**，sha 留档只为对账历史镜像 |

**运行面与仓内目前是两套读数**：仓里已是 0.3.2，NAS 容器里装的仍是烤进镜像的那枚 0.3.1——
`homesdk.adm` 在运行面**尚不存在**，`adm_linkage` 的库路径探测因此为假、走本仓词表。这不是缺陷，
是 §四 Q2 的授权边界（重烤搭变更窗）。窗口内动作 = 装 0.3.2 → 翻 `advertise()`/LWT 为 JSON →
断连记 degraded + `ADM_ERR_BROKER_UNREACHABLE` → 跑探针三组。

**为什么是 44 行的 sha 而不是一个目录**：MA 原先在生产看不见 homesdk——容器 `PYTHONPATH=/app/src`，
那份库只在 `/tmp/pylibs`（测试/门禁面显式加 PYTHONPATH 才有效）。共享可写目录当信任面，等于
"谁改目录谁影响六仓"；进仓的 wheel 加 sha 登记才是可复现的 provenance（同上裁定 §五 判例 1）。

**换版本必须同批改四处**：本表、Dockerfile 的安装行（含它上面那行注释里的版本号）、
`house_time`/`mqtt_bridge` 里对库版本的断言测试、`tests/test_vma_dcd_20261002_payload.py`
的 vendor 三处一致锁。只换其一 = 镜像与账不一致。

**已作废的 sha（不要再抄它）**：`36fdf77a…`（首次投递产物，不可复现，已在 homesdk `VERSIONS.txt` 写明原因）。
