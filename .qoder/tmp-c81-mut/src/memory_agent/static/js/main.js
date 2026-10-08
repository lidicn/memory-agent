/* 应用入口：注册 store、外壳与各页面组件 */

import { api, auth, ApiError } from './api.js';
import { registerStore } from './store.js';
import { dashboardPage } from './pages/dashboard.js';
import { collectPage } from './pages/collect.js';
import { assistantPage } from './pages/assistant.js';
import { insightsPage } from './pages/insights.js';
import { insightJobsPage } from './pages/insight_jobs.js';
import { mcpPage } from './pages/mcp.js';
import { acpPage } from './pages/acp.js';
import { agentMemoryPage } from './pages/agent_memory.js';
import { signalRulesPage } from './pages/signal_rules.js';
import { visionPage } from './pages/vision.js';
import { settingsPage } from './pages/settings.js';
import { membersPage } from './pages/members.js';
import { devicesPage } from './pages/devices.js';
import { userManualPage } from './pages/user_manual.js';
// 导航与路由白名单的单一真源（store.js 也 import 同一份，避免漏加导致页面回退概览）
import { NAV, MORE_ICON } from './nav.js';


function shell() {
  return {
    navItems: NAV,
    moreIcon: MORE_ICON,

    // 移动端底部 Tab：主区展示前 5 个入口，「更多」弹出其余入口
    mobileMoreOpen: false,
    get mobileTabs() { return NAV.slice(0, 5); },
    get mobileMore() { return NAV.slice(5); },
    get isMoreRoute() {
      return NAV.slice(5).some((n) => n.id === this.$store.app.route);
    },

    authMode: 'login',
    authForm: { username: '', password: '' },
    authError: '',
    authLoading: false,
    healthLoading: false,

    get currentNav() {
      return NAV.find((n) => n.id === this.$store.app.route) || NAV[0];
    },

    /** 顶栏状态灯：只展示真正会影响使用的四个依赖 */
    get healthChips() {
      const h = this.$store.app.health || {};
      return [
        { key: 'ha', label: 'HA', on: !!(h.ha && h.ha.connected), title: 'Home Assistant：' + (h.ha && h.ha.connected ? '已连接' : ((h.ha && h.ha.error) || '未连接')) },
        { key: 'llm', label: 'LLM', on: !!(h.llm && h.llm.connected), title: '大模型：' + (h.llm && h.llm.connected ? ((h.llm.model || '') + ' 可用') : ((h.llm && h.llm.error) || '未配置')) },
        { key: 'nr', label: 'NR', on: !!(h.nodered && h.nodered.connected), title: 'Node-RED：' + (h.nodered && h.nodered.connected ? '已连接' : ((h.nodered && h.nodered.error) || '未连接')) },
        { key: 'db', label: 'DB', on: !!(h.store && Number(h.store.total_events) >= 0), title: '事件库：' + ((h.store && h.store.total_events) || 0) + ' 条' }
      ];
    },

    init() {
      const app = this.$store.app;

      app.syncRouteFromHash();
      window.addEventListener('hashchange', () => app.syncRouteFromHash());

      // 401 由 api 层统一广播，这里集中做「踢下线」处理
      window.addEventListener('mw:unauthorized', () => {
        if (!app.authed) return;
        app.signOut(true);
        app.warn('登录状态已失效，请重新登录');
      });

      this.bootstrap();
      registerServiceWorker();
    },

    async bootstrap() {
      const app = this.$store.app;

      // 先探系统是否已初始化，决定登录页默认展示登录还是注册
      try {
        const s = await api.authStatus();
        app.initialized = s.initialized !== false;
        if (!app.initialized) this.authMode = 'register';
      } catch (e) { /* 探测失败不阻断，按已初始化处理 */ }

      if (!auth.token) return;
      try {
        const me = await api.me();
        app.setSession('', { username: me.username, is_admin: me.is_admin });
        this.refreshHealth();
      } catch (e) {
        auth.clear();
      }
    },

    async submitAuth() {
      const app = this.$store.app;
      const { username, password } = this.authForm;
      if (!username.trim() || !password) {
        this.authError = '用户名和密码不能为空';
        return;
      }
      this.authLoading = true;
      this.authError = '';
      try {
        const fn = this.authMode === 'login' ? api.login : api.register;
        const d = await fn(username.trim(), password);
        app.setSession(d.token, { username: d.username || username.trim(), is_admin: d.is_admin });
        app.initialized = true;
        this.authForm.password = '';
        app.ok('欢迎回来，' + (d.username || username.trim()));
        this.refreshHealth();
      } catch (e) {
        this.authError = e instanceof ApiError ? e.message : '登录失败，请重试';
      } finally {
        this.authLoading = false;
      }
    },

    async logout() {
      const app = this.$store.app;
      const yes = await app.ask('退出登录', '确定要退出当前账号吗？', '退出');
      if (!yes) return;
      try { await api.logout(); } catch (e) { /* 服务端无状态，失败也照常本地登出 */ }
      app.signOut();
    },

    async refreshHealth() {
      const app = this.$store.app;
      if (!app.authed) return;
      this.healthLoading = true;
      try {
        const h = await api.health();
        app.health = h;
        app.collecting = !!h.collecting;
      } catch (e) {
        // 顶栏刷新是背景动作，失败不打断用户
      } finally {
        this.healthLoading = false;
      }
    }
  };
}

function registerServiceWorker() {
  // PWA 离线壳只在安全上下文（HTTPS / localhost）下注册，
  // 纯 HTTP 局域网访问时静默跳过，App 仍按原方式工作。
  if (!('serviceWorker' in navigator)) return;
  if (location.protocol !== 'https:' && location.hostname !== 'localhost') return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((e) => {
      console.warn('[SW] 注册失败:', e);
    });
  });
}

document.addEventListener('alpine:init', () => {
  const Alpine = window.Alpine;
  registerStore(Alpine);
  Alpine.data('shell', shell);
  Alpine.data('dashboardPage', dashboardPage);
  Alpine.data('collectPage', collectPage);
  Alpine.data('assistantPage', assistantPage);
  Alpine.data('insightsPage', insightsPage);
  Alpine.data('researcherPage', insightJobsPage);
  Alpine.data('mcpPage', mcpPage);
  Alpine.data('acpPage', acpPage);
  Alpine.data('agentMemoryPage', agentMemoryPage);
  Alpine.data('signalRulesPage', signalRulesPage);
  Alpine.data('visionPage', visionPage);
  Alpine.data('membersPage', membersPage);
  Alpine.data('devicesPage', devicesPage);
  Alpine.data('userManualPage', userManualPage);
  Alpine.data('settingsPage', settingsPage);
});
