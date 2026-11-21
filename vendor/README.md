# vendor/ —— 进仓的构建产物

| 文件 | sha256 | 来源 | 授权 |
|------|--------|------|------|
| `homesdk-0.3.5-py3-none-any.whl` | `0915e23df7ca9eb10c7498ee7dda40f0f7aa44c66730c1cccb8e9031a08d1c33` | `E:\NAS\homesdk\dist\`，DCD 2026-11-18 构建并登记在 `dist/VERSIONS.txt` 0.3.5 段（56,855 B，19 个 `.py` 成员，新增 `homesdk/events.py`） | DCD `20261120-MA-挂门四项启动条件与契约v1事件族埋点口径-裁定.md` Q3 甲（MA 直接 vendor 0.3.5，⛔ 本地兜底双写）＋ `20261117` §2.7 H1；wheel 由 DCD 复制进本目录，MA 改 Dockerfile pin |
| ~~`homesdk-0.3.2-py3-none-any.whl`~~ | `19bc83a67a96931c4556caec52f29c190aaa03a0a7036e0b41c242a533fb5505` | 同一 `dist/`，由 homesdk 源码 `7658e73` 构建，DCD 登记在 `dist/VERSIONS.txt` 0.3.2 段（49,374 B，22 个成员，新增 `homesdk/adm/`） | DCD 裁定 `20261007-MA五件与AF一件` §四 Q2；**2026-11-20 下架换 0.3.5**，sha 留档供对账历史镜像 |
| ~~`homesdk-0.3.1-py3-none-any.whl`~~ | `b4b5d6bbe424205bb425762223576a3b3409c93f262f79bc8e5a7016ad4b814c` | 同一 `dist/`，由 homesdk commit `5e4ba33` 重建（44,126 B，18 个成员） | 原授权 `20261002-AgentOps与MA交付面` §二 Q1 = **A**（vendored wheel 进本仓 + Dockerfile 一行）；**2026-10-07 下架换 0.3.2**，sha 留档只为对账历史镜像 |

**运行面与仓内目前仍是两套读数**：仓里已是 0.3.5，NAS 容器里装的仍是烤进镜像的那枚 **0.3.1**（0.3.2 也从未进过运行面）——
`homesdk.adm`／`homesdk.events` 在运行面**尚不存在**，`adm_linkage` 的库路径探测因此为假、走本仓词表；
事件族发布（`ma/behavior-state`／`ma/prediction`）在库缺席时显式降级并写出理由（不是缺陷）。
这不是漏做，是「镜像重烤随下一次既有变更窗」的授权边界（DCD 20261007 §四 Q2 ＋ 20261120 Q3）。
窗口内动作 = 重烤镜像 → 容器内 `import homesdk` 报 0.3.5 → 事件族真发。

**为什么是 44 行的 sha 而不是一个目录**：MA 原先在生产看不见 homesdk——容器 `PYTHONPATH=/app/src`，
那份库只在 `/tmp/pylibs`（测试/门禁面显式加 PYTHONPATH 才有效）。共享可写目录当信任面，等于
"谁改目录谁影响六仓"；进仓的 wheel 加 sha 登记才是可复现的 provenance（同上裁定 §五 判例 1）。

**换版本必须同批改四处**：本表、Dockerfile 的安装行（含它上面那行注释里的版本号）、
`house_time`/`mqtt_bridge` 里对库版本的断言测试、`tests/test_vma_dcd_20261002_payload.py`
的 vendor 三处一致锁。只换其一 = 镜像与账不一致。

**已作废的 sha（不要再抄它）**：`36fdf77a…`（首次投递产物，不可复现，已在 homesdk `VERSIONS.txt` 写明原因）。
