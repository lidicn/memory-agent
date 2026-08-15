/* 全局状态：登录态、路由、提示、确认框、采集心跳 */

import { api, auth } from './api.js';

const NAV_KEY = 'mw.navCollapsed';
// 必须与 main.js 中的 NAV 数组 id 保持一致；漏加会导致对应页面被 fallback 到 dashboard
const ROUTES = ['dashboard', 'collect', 'assistant', 'insights', 'mcp', 'acp', 'agent_memory', 'members', 'settings'];

export function registerStore(Alpine) {
  Alpine.store('app', {
    // ── 登录态 ───────────────────────────────────────────
    authed: false,
    initialized: true,     // 系统是否已有账号；false 时前端引导注册管理员
    user: { username: '', is_admin: false },

    // ── 路由 ─────────────────────────────────────────────
    route: 'dashboard',
    navCollapsed: (() => {
      try { return localStorage.getItem(NAV_KEY) === '1'; } catch (e) { return false; }
    })(),

    // ── 运行时 ───────────────────────────────────────────
    health: {},
    collecting: false,
    collectProgress: {},

    // ── 提示 ─────────────────────────────────────────────
    toasts: [],
    _tid: 0,

    // ── 确认框 ───────────────────────────────────────────
    confirm: { open: false, title: '', message: '', okText: '确认' },
    _confirmResolve: null,

    // 采集心跳的定时器句柄，避免重复启动
    _pollTimer: null,

    init() {
      // 侧栏折叠状态持久化
      Alpine.effect(() => {
        const v = this.navCollapsed;
        try { localStorage.setItem(NAV_KEY, v ? '1' : '0'); } catch (e) { /* ignore */ }
      });
    },

    // ── 路由 ─────────────────────────────────────────────
    go(route) {
      if (!ROUTES.includes(route)) route = 'dashboard';
      if (location.hash !== '#/' + route) location.hash = '#/' + route;
      else this.route = route;
    },

    syncRouteFromHash() {
      const raw = (location.hash || '').replace(/^#\/?/, '').split('?')[0];
      this.route = ROUTES.includes(raw) ? raw : 'dashboard';
    },

    // ── 提示 ─────────────────────────────────────────────
    toast(message, type = 'info', ttl = 3600) {
      const id = ++this._tid;
      this.toasts.push({ id, message: String(message || ''), type });
      if (this.toasts.length > 4) this.toasts.shift();
      setTimeout(() => this.dismiss(id), ttl);
      return id;
    },
    ok(msg) { return this.toast(msg, 'success'); },
    err(msg) { return this.toast(msg, 'error', 5200); },
    warn(msg) { return this.toast(msg, 'warn', 4600); },
    info(msg) { return this.toast(msg, 'info'); },

    dismiss(id) {
      const i = this.toasts.findIndex((t) => t.id === id);
      if (i !== -1) this.toasts.splice(i, 1);
    },

    // ── 确认框（Promise 化，替代 window.confirm）─────────
    ask(title, message, okText = '确认') {
      this.confirm = { open: true, title, message, okText };
      return new Promise((resolve) => { this._confirmResolve = resolve; });
    },
    resolveConfirm(value) {
      this.confirm.open = false;
      const r = this._confirmResolve;
      this._confirmResolve = null;
      if (r) r(!!value);
    },

    // ── 会话 ─────────────────────────────────────────────
    setSession(token, user) {
      if (token) auth.set(token);
      this.user = {
        username: (user && user.username) || '',
        is_admin: !!(user && user.is_admin)
      };
      this.authed = true;
      this.startHeartbeat();
    },

    signOut(silent) {
      auth.clear();
      this.authed = false;
      this.user = { username: '', is_admin: false };
      this.collecting = false;
      this.collectProgress = {};
      this.stopHeartbeat();
      if (!silent) this.info('已退出登录');
    },

    // ── 采集心跳 ─────────────────────────────────────────
    // 采集是长任务，用户可能停在任意页面，所以心跳放在全局 store，
    // 而不是采集页组件里 —— 页面切走也不会丢进度。
    startHeartbeat() {
      if (this._pollTimer) return;
      const tick = async () => {
        if (!this.authed) return;
        try {
          const d = await api.collectProgress();
          const p = d.progress || {};
          const wasRunning = this.collecting;
          // running 是 get_progress() 塞进 progress 里的派生字段
          this.collecting = !!p.running;
          this.collectProgress = p;
          if (wasRunning && !this.collecting) {
            window.dispatchEvent(new CustomEvent('mw:collect-finished', { detail: p }));
            if (p.phase === 'error') this.err('采集失败：' + (p.message || '未知错误'));
            else if (p.phase === 'cancelled') this.warn('采集已取消');
            else this.ok('采集完成，新增 ' + (p.total_events || 0) + ' 条事件');
          }
        } catch (e) { /* 心跳失败静默，避免刷屏 */ }
      };
      tick();
      // 采集中 2s、空闲 8s：既要进度跟手，又不能白白压服务
      const loop = () => {
        this._pollTimer = setTimeout(async () => {
          await tick();
          if (this.authed) loop();
        }, this.collecting ? 2000 : 8000);
      };
      loop();
    },

    stopHeartbeat() {
      if (this._pollTimer) { clearTimeout(this._pollTimer); this._pollTimer = null; }
    }
  });
}
