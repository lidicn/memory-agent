# vendor/ —— 进仓的构建产物

| 文件 | sha256 | 来源 | 授权 |
|------|--------|------|------|
| `homesdk-0.3.1-py3-none-any.whl` | `b4b5d6bbe424205bb425762223576a3b3409c93f262f79bc8e5a7016ad4b814c` | `E:\NAS\homesdk\dist\`，由 homesdk commit `5e4ba33` 重建（44,126 B，18 个成员） | DCD 裁定 `20261002-AgentOps与MA交付面` §二 Q1 = **A**（vendored wheel 进本仓 + Dockerfile 一行） |

**为什么是 44 行的 sha 而不是一个目录**：MA 原先在生产看不见 homesdk——容器 `PYTHONPATH=/app/src`，
那份库只在 `/tmp/pylibs`（测试/门禁面显式加 PYTHONPATH 才有效）。共享可写目录当信任面，等于
"谁改目录谁影响六仓"；进仓的 wheel 加 sha 登记才是可复现的 provenance（同上裁定 §五 判例 1）。

**换版本必须同批改三处**：本表、Dockerfile 的安装行、`house_time`/`mqtt_bridge` 里对库版本的断言测试。
只换其一 = 镜像与账不一致。

**已作废的 sha（不要再抄它）**：`36fdf77a…`（首次投递产物，不可复现，已在 homesdk `VERSIONS.txt` 写明原因）。
