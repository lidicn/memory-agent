/* 应用入口：注册 store、外壳与各页面组件 */

import { api, auth, ApiError } from './api.js';
import { registerStore } from './store.js';
import { dashboardPage } from './pages/dashboard.js';
import { collectPage } from './pages/collect.js';
import { assistantPage } from './pages/assistant.js';
import { insightsPage } from './pages/insights.js';
import { mcpPage } from './pages/mcp.js';
import { acpPage } from './pages/acp.js';
import { agentMemoryPage } from './pages/agent_memory.js';
import { settingsPage } from './pages/settings.js';
import { membersPage } from './pages/members.js';

const ic = (path) =>
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
  'stroke-linecap="round" stroke-linejoin="round" class="w-full h-full">' + path + '</svg>';

const NAV = [
  {
    id: 'dashboard', label: '概览', desc: '家庭记忆中枢运行全貌',
    icon: ic('<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>')
  },
  {
    id: 'collect', label: '数据采集', desc: '从 Home Assistant 抓取并沉淀行为事件',
    icon: ic('<path d="M21 12a9 9 0 11-6.22-8.56"/><path d="M12 7v5l3 2"/><path d="M17 3l4 1-1 4"/>')
  },
  {
    id: 'assistant', label: 'AI 助手', desc: '与内置大模型对话、生成行为洞察',
    icon: ic('<path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/><circle cx="9" cy="10" r="1"/><circle cx="12.5" cy="10" r="1"/><circle cx="16" cy="10" r="1"/>')
  },
  {
    id: 'insights', label: '行为洞察', desc: '沉淀下来的规律与自动化建议',
    icon: ic('<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 00-4 12.7V17h8v-2.3A7 7 0 0012 2z"/>')
  },
  {
    id: 'members', label: '家庭成员', desc: '为每个家人建立生活习惯档案',
    icon: ic('<path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/>')
  },
  {
    id: 'mcp', label: 'MCP 接入', desc: '为 AI Agent 签发访问凭证',
    icon: ic('<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2"/><path d="M2 13h20"/>')
  },
  {
    id: 'acp', label: 'ACP 接入', desc: '与 autoflow 双向互通（拓扑 X）',
    icon: ic('<path d="M12 2a3 3 0 00-3 3v1H6a3 3 0 000 6h3v1a3 3 0 006 0v-1h3a3 3 0 000-6h-3V5a3 3 0 00-3-3z"/><circle cx="12" cy="5" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/>')
  },
  {
    id: 'agent_memory', label: 'Agent 记忆', desc: '参与式写回的向量记忆库',
    icon: ic('<path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z"/>')
  },
  {
    id: 'settings', label: '系统设置', desc: '连接、模型与账号管理',
    icon: ic('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09A1.65 1.65 0 008 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06A1.65 1.65 0 004.6 15a1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06A1.65 1.65 0 009 4.6a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06A1.65 1.65 0 0019.4 9c.14.35.4.64.73.83.3.17.64.26.98.26H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z"/>')
  }
];

// 底部 Tab「更多」入口图标
const MORE_ICON = ic('<circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="19" r="1.4"/>');

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
  Alpine.data('mcpPage', mcpPage);
  Alpine.data('acpPage', acpPage);
  Alpine.data('agentMemoryPage', agentMemoryPage);
  Alpine.data('membersPage', membersPage);
  Alpine.data('settingsPage', settingsPage);
});
