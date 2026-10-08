# iOS / iPad PWA 指南（Memory Agent）

本项目已支持作为 **PWA（渐进式 Web 应用）** 安装到 iPhone / iPad，使用 Safari「添加到主屏幕」后以独立 App 形式全屏运行，并具备离线壳（断网也能打开 App）。

> 前提：**iOS PWA 的 Service Worker 仅能在 HTTPS（或 localhost）下注册**。纯 HTTP 局域网访问只能当作「全屏书签」，无法离线、无安装横幅。下面先解决 HTTPS。

---

## 一、准备 HTTPS（一次性的事）

> ⚠️ **本节 Caddy 反代方案为「可选 / 已过时」步骤**：当前实际部署已改用 **`tailscale serve` 直接把 HTTPS 转发到应用端口**（如 `:8086`），由 Tailscale 终止 TLS，无需再起 Caddy。只需满足「访问地址是 HTTPS 或 localhost」即可，Service Worker 自然注册、PWA 能力完整。下面的 Caddy 步骤保留仅作参考，新部署不必执行。

外网通过 **Tailscale** 访问家里 NAS，因此最省事的方案是 **Tailscale 节点证书**（一套证书同时覆盖「在家 / 在外」）：

1. 在 NAS 上执行（需已加入 Tailscale）：
   ```bash
   tailscale cert <你的主机名>.ts.net          # 例如 mynas.foo.ts.net
   ```
   得到 `_cert.pem` 与 `_key.pem`，改名覆盖 `certs/cert.pem`、`certs/key.pem`。

> 不想用 Tailscale 证书，也可用自签：`scripts/gen-certs.sh -h <主机名>.ts.net -i 192.168.2.200`（详见脚本输出）。

2. **（可选 / 已过时，当前部署可跳过）** 启动反向代理：
   ```bash
   docker compose up -d caddy
   ```
   Caddy 监听 80/443，将 HTTPS 终止后转发到 `memory-agent:8000`。当前走 `tailscale serve` 直转时无需此步。

3. 在 iPhone / iPad 上**信任一次根 CA**（仅自签证书需要；Tailscale 证书信任 Tailscale 根）：
   - 把 `ca.pem`（自签）或 Tailscale 根证书发送到设备并安装描述文件；
   - `设置 → 通用 → 关于本机 → 证书信任设置`，开启对应 CA 的**完全信任**。

访问地址：当前为 `https://<你的主机名>.ts.net:8086`（Tailscale serve 直转），或去掉端口的 `https://<你的主机名>.ts.net`（经 Caddy / 443 转发，可选）。在家 / 在外 DNS 均由 Tailscale 解析。

---

## 二、添加到主屏幕（每台设备一次）

1. iPhone / iPad 用 Safari 打开 `https://<你的主机名>.ts.net`，登录。
2. 点击底部工具栏「分享」→ **添加到主屏幕** → 名称可用默认「Memory Agent」→ 添加。
3. 回到桌面，点击图标即可全屏独立运行（无浏览器地址栏）。

---

## 三、移动端 UI

- **iPhone（窄屏）**：自动隐藏左侧侧边栏，改为底部 **Tab 栏**（概览 / 采集 / 助手 / 洞察 / 成员 + 「更多」）。「更多」弹出面板含 设置 / MCP接入 / Agent记忆 / 退出登录。已适配 iPhone 底部安全区。
- **iPad（≥768px）**：保留原有左侧侧边栏，体验不变。

---

## 四、离线壳说明

Service Worker 会预缓存首页与核心静态资源（`/static/...`）。联网使用过一次之后，即使**断网**也能打开 App、看到已加载的界面（登录态靠 `localStorage` 的 Bearer Token 保留，不会丢失）。实时数据接口（API / SSE 流）始终走网络，离线时这部分内容为空，恢复联网后即刷新。

---

## 五、已知限制

- **Web Push 不可用**：iOS 对 PWA 推送限制极严（国内基本不可用），因此「采集中」等提醒**无法推送到锁屏**。
- **iOS 16.4 以下**不读取 manifest 的 `display`，但会读取 `apple-mobile-web-app-*` meta 标签兜底，已同时配置。
- 证书有效期约 825 天（自签）或 Tailscale 证书约 90 天，到期需重新生成 / `tailscale cert` 续期（仅旧 Caddy 反代方案需重启 caddy；当前 `tailscale serve` 直转无需重启）。
- 纯 HTTP 局域网访问才没有 PWA 离线/安装能力；当前 `:8086` 是 **Tailscale serve 的 HTTPS 转发**，已是真 PWA（无地址栏、可离线壳），无需 Caddy 即具备全部能力。

---

## 六、验证清单

- [ ] `https://<主机名>.ts.net/health` 返回 `ok`
- [ ] Safari 开发者工具（连接 Mac）Console 无 `[SW] 注册失败`
- [ ] 添加到主屏幕后独立窗口打开，登录态保留
- [ ] 飞行模式 / 断网后仍能打开 App（离线壳生效）
- [ ] Tailscale 外网环境下 HTTPS 访问正常
