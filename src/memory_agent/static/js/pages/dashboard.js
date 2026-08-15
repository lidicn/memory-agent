/* 概览页：关键指标 + 30 天趋势 + 最近任务 + 快捷入口
 * 该页的 DOM 直接写在 index.html 里，这里只提供数据与方法。
 */

import { api } from '../api.js';
import { fmtCompact, fmtNum } from '../util.js';

const icon = (path, stroke) =>
  '<svg viewBox="0 0 24 24" class="w-[18px] h-[18px]" fill="none" stroke="' + (stroke || 'white') +
  '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + path + '</svg>';

const I = {
  db: icon('<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>'),
  cal: icon('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>'),
  home: icon('<path d="M3 12l9-9 9 9"/><path d="M5 10v10h14V10"/>'),
  chip: icon('<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M4 10h3M4 14h3M17 10h3M17 14h3M10 4v3M14 4v3M10 17v3M14 17v3"/>'),
  bolt: icon('<path d="M13 2L3 14h8l-1 8 10-12h-8l1-8z"/>'),
  chat: icon('<path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>'),
  key: icon('<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2M17 6l3 3M14 9l3 3"/>')
};

export function dashboardPage() {
  return {
    loading: true,
    stats: {},
    trend: [],
    jobs: [],

    get trendMax() {
      return this.trend.reduce((m, d) => Math.max(m, d.events || 0), 0);
    },
    get trendTotal() {
      return this.trend.reduce((s, d) => s + (d.events || 0), 0);
    },

    get metrics() {
      const s = this.stats || {};
      const h = this.$store.app.health || {};
      const span = s.first_day && s.last_day ? s.first_day.slice(5) + ' ~ ' + s.last_day.slice(5) : '尚未采集';
      return [
        {
          key: 'events', label: '行为事件总量', value: fmtCompact(s.total_events),
          hint: fmtNum(s.total_events) + ' 条 · ' + span, icon: I.db, bg: 'grad-brand'
        },
        {
          key: 'days', label: '覆盖天数', value: fmtNum(s.days_covered),
          hint: '有数据的自然日', icon: I.cal, bg: 'grad-violet'
        },
        {
          key: 'rooms', label: '活跃房间', value: fmtNum(s.rooms),
          hint: fmtNum(s.entities) + ' 个实体在上报', icon: I.home, bg: 'grad-aqua'
        },
        {
          key: 'tokens', label: 'MCP 凭证', value: fmtNum(h.tokens || 0),
          hint: (h.llm && h.llm.connected) ? '大模型在线' : '大模型未就绪',
          icon: I.chip, bg: (h.llm && h.llm.connected) ? 'grad-ok' : 'grad-warn'
        }
      ];
    },

    get quickActions() {
      const app = this.$store.app;
      return [
        {
          label: '立即采集一次', desc: '拉取上次采集之后的新事件',
          icon: I.bolt, bg: 'grad-brand',
          run: () => this.triggerCollect()
        },
        {
          label: '问问 AI 助手', desc: '基于真实行为数据分析家庭习惯',
          icon: I.chat, bg: 'grad-violet',
          run: () => app.go('assistant')
        },
        {
          label: '签发 MCP Token', desc: '让外部 Agent 安全接入记忆中枢',
          icon: I.key, bg: 'grad-aqua',
          run: () => app.go('mcp')
        }
      ];
    },

    init() {
      this.load();
      // 采集结束后自动刷新，省得用户手动点
      this._onFinish = () => this.load();
      window.addEventListener('mw:collect-finished', this._onFinish);
      this.$watch('$store.app.route', (r) => {
        if (r !== 'dashboard') window.removeEventListener('mw:collect-finished', this._onFinish);
      });
    },

    destroy() {
      if (this._onFinish) window.removeEventListener('mw:collect-finished', this._onFinish);
    },

    async load() {
      this.loading = true;
      try {
        const [s, j] = await Promise.all([api.collectStats(), api.collectJobs(8)]);
        this.stats = s.stats || {};
        this.trend = s.trend || [];
        this.jobs = j.jobs || [];
      } catch (e) {
        this.$store.app.err('概览加载失败：' + e.message);
      } finally {
        this.loading = false;
      }
    },

    async triggerCollect() {
      const app = this.$store.app;
      if (app.collecting) { app.warn('已有采集任务在运行'); return; }
      try {
        const d = await api.collectTrigger();
        app.ok(d.message || '采集任务已启动');
        app.collecting = true;
        app.startHeartbeat();
      } catch (e) {
        app.err(e.message);
      }
    },

    // ── 任务状态映射 ───────────────────────────────────────
    statusDot(s) {
      return {
        success: 'bg-ok', running: 'bg-brand-400 pulse-dot',
        error: 'bg-danger', cancelled: 'bg-warn', pending: 'bg-txt-3'
      }[s] || 'bg-txt-3';
    },
    statusBadge(s) {
      return {
        success: 'badge-ok', running: 'badge-brand',
        error: 'badge-danger', cancelled: 'badge-warn', pending: 'badge-mute'
      }[s] || 'badge-mute';
    },
    statusText(s) {
      return {
        success: '成功', running: '运行中', error: '失败',
        cancelled: '已取消', pending: '等待中'
      }[s] || s || '未知';
    }
  };
}
