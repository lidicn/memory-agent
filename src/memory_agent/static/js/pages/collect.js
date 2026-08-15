/* 数据采集页：调度配置 / 手动触发 / 历史回填 / 热力日历 / 任务history / 房间实体 */

import { api } from '../api.js';
import {
  fmtNum, fmtTime, fmtDuration, todayStr, monthStr, shiftMonth,
  monthFirstWeekday, heatLevel
} from '../util.js';

const TPL = `
<!-- 顶部：调度状态 + 手动操作 -->
<div class="grid lg:grid-cols-3 gap-5">

  <!-- 调度配置 -->
  <div class="card p-5 lg:col-span-2">
    <div class="flex items-center justify-between mb-5">
      <div>
        <h3 class="font-semibold flex items-center gap-2">采集调度
          <span class="badge text-[10px]" :class="st.source==='mariadb' ? 'badge-brand' : 'badge-mute'"
                x-text="st.source==='mariadb' ? '数据源·直连DB' : '数据源·REST'"></span>
        </h3>
        <p class="text-[11px] text-txt-3 mt-0.5">
          上次采集：<span x-text="fmtTime(st.last_poll_time)"></span>
          · 下次：<span x-text="st.next_run ? fmtTime(st.next_run) : '—'"></span>
        </p>
      </div>
      <div class="flex items-center gap-3">
        <span class="text-xs" :class="cfg.polling_enabled ? 'text-ok' : 'text-txt-3'"
              x-text="cfg.polling_enabled ? '自动采集已开启' : '自动采集已关闭'"></span>
        <div class="switch" :class="cfg.polling_enabled && 'on'" @click="toggleEnabled()"></div>
      </div>
    </div>

    <div class="grid sm:grid-cols-3 gap-4">
      <div>
        <label class="lbl">触发方式</label>
        <select class="sel" x-model="cfg.polling_mode">
          <option value="interval">固定间隔</option>
          <option value="scheduled">每天定时</option>
          <option value="manual">仅手动</option>
        </select>
      </div>
      <div x-show="cfg.polling_mode === 'interval'">
        <label class="lbl">间隔（分钟，≥15）</label>
        <input type="number" min="15" step="5" class="inp" x-model.number="cfg.interval_minutes">
      </div>
      <div x-show="cfg.polling_mode === 'scheduled'">
        <label class="lbl">每日执行时间</label>
        <input type="time" class="inp" x-model="cfg.polling_time">
      </div>
      <div>
        <label class="lbl">数据保留（天）</label>
        <input type="number" min="0" step="1" class="inp" x-model.number="cfg.data_retention_days"
               placeholder="0 表示永久保留">
      </div>
    </div>

    <div class="flex items-center gap-3 mt-5">
      <button class="btn-primary" @click="saveConfig()" :disabled="saving">
        <span x-show="saving" class="spinner"></span>
        <span x-text="saving ? '保存中…' : '保存调度配置'"></span>
      </button>
      <p class="text-[11px] text-txt-3" x-show="cfg.polling_mode==='manual'">
        仅手动模式下不会自动执行，需要在右侧手动触发。
      </p>
    </div>
  </div>

  <!-- 手动操作 -->
  <div class="card p-5 flex flex-col">
    <h3 class="font-semibold mb-4">手动执行</h3>

    <button class="btn-primary w-full justify-center py-2.5 mb-3"
            @click="trigger()" :disabled="$store.app.collecting">
      <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M13 2L3 14h8l-1 8 10-12h-8l1-8z"/></svg>
      <span x-text="$store.app.collecting ? '采集进行中…' : '立即增量采集'"></span>
    </button>

    <button class="btn-danger w-full justify-center" @click="cancel()"
            x-show="$store.app.collecting">中止当前任务</button>

    <div class="mt-auto pt-4 space-y-2 text-[11px] text-txt-3">
      <div class="flex justify-between"><span>事件总量</span><span class="font-mono text-txt-2" x-text="fmtNum(st.stats && st.stats.total_events)"></span></div>
      <div class="flex justify-between"><span>覆盖天数</span><span class="font-mono text-txt-2" x-text="fmtNum(st.stats && st.stats.days_covered)"></span></div>
      <div class="flex justify-between"><span>启用实体</span><span class="font-mono text-txt-2" x-text="fmtNum(roomsInfo.enabled_entities) + ' / ' + fmtNum(roomsInfo.total_entities)"></span></div>
    </div>
  </div>
</div>

<!-- 实时进度 -->
<div class="card p-5" x-show="$store.app.collecting || prog.phase === 'error'">
  <div class="flex items-center justify-between mb-3">
    <div class="flex items-center gap-2">
      <span class="w-2 h-2 rounded-full bg-ok pulse-dot" x-show="$store.app.collecting"></span>
      <h3 class="font-semibold text-sm" x-text="phaseText(prog.phase)"></h3>
      <span class="badge text-[10px]"
            x-show="prog.source"
            :class="{'badge-brand': prog.source==='mariadb', 'badge-warn': prog.source==='rest-fallback', 'badge-mute': prog.source==='rest'}"
            x-text="({'mariadb':'直连DB','rest-fallback':'DB失败·回退REST','rest':'REST'})[prog.source]"></span>
    </div>
    <span class="text-xs font-mono text-txt-2" x-text="fmtDuration(prog.elapsed) + ' · ' + fmtNum(prog.total_events) + ' 条'"></span>
  </div>
  <div class="pbar mb-3"><i :style="'width:' + percent + '%'"></i></div>
  <div class="grid sm:grid-cols-2 gap-y-1 text-[11px] text-txt-3">
    <div>房间：<span class="text-txt-2" x-text="(prog.current_room || '—') + ' (' + (prog.current_room_index||0) + '/' + (prog.total_rooms||0) + ')'"></span></div>
    <div>实体：<span class="text-txt-2 font-mono" x-text="prog.current_entity || '—'"></span></div>
    <div class="sm:col-span-2" x-show="prog.message">说明：<span class="text-txt-2" x-text="prog.message"></span></div>
  </div>
</div>

<!-- 历史回填 -->
<div class="card p-5">
  <div class="flex items-center justify-between mb-4">
    <div>
      <h3 class="font-semibold">历史回填</h3>
      <p class="text-[11px] text-txt-3 mt-0.5">按天分片抓取，重复执行不会产生重复事件</p>
    </div>
    <div class="flex gap-1.5">
      <template x-for="q in quickRanges" :key="q.days">
        <button class="btn-ghost btn-xs" @click="applyRange(q.days)" x-text="q.label"></button>
      </template>
    </div>
  </div>

  <div class="grid sm:grid-cols-4 gap-4 items-end">
    <div>
      <label class="lbl">开始日期</label>
      <input type="date" class="inp" x-model="bf.start_day">
    </div>
    <div>
      <label class="lbl">结束日期</label>
      <input type="date" class="inp" x-model="bf.end_day">
    </div>
    <div class="sm:col-span-2">
      <label class="lbl">限定房间（不选＝全部启用的房间）</label>
      <div class="flex flex-wrap gap-1.5 max-h-[76px] overflow-y-auto thin-scroll">
        <template x-for="r in roomNames" :key="r">
          <button class="badge cursor-pointer transition"
                  :class="bf.rooms.includes(r) ? 'badge-brand' : 'badge-mute'"
                  @click="toggleBfRoom(r)" x-text="r"></button>
        </template>
        <span x-show="!roomNames.length" class="text-[11px] text-txt-3">暂无房间配置</span>
      </div>
    </div>
  </div>

  <button class="btn-soft mt-4" @click="runBackfill()" :disabled="$store.app.collecting || backfilling">
    <span x-show="backfilling" class="spinner"></span>
    <span x-text="backfilling ? '提交中…' : '开始回填'"></span>
  </button>
</div>

<!-- 日历 + 任务历史 -->
<div class="grid lg:grid-cols-5 gap-5">

  <div class="card p-5 lg:col-span-2">
    <div class="flex items-center justify-between mb-4">
      <h3 class="font-semibold">采集日历</h3>
      <div class="flex items-center gap-1">
        <button class="icon-btn" @click="moveMonth(-1)">
          <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg>
        </button>
        <span class="text-xs font-mono w-[62px] text-center" x-text="cal.month"></span>
        <button class="icon-btn" @click="moveMonth(1)">
          <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 18l6-6-6-6"/></svg>
        </button>
      </div>
    </div>

    <div class="grid grid-cols-7 gap-1 mb-1 text-center text-[10px] text-txt-3">
      <template x-for="w in ['日','一','二','三','四','五','六']" :key="w"><div x-text="w"></div></template>
    </div>
    <div class="grid grid-cols-7 gap-1">
      <template x-for="i in calPad" :key="'p'+i"><div></div></template>
      <template x-for="d in cal.days" :key="d.day">
        <div class="heat" :class="heatClass(d)" :title="d.day + ' · ' + d.events + ' 条'"
             x-text="Number(d.day.slice(8))"></div>
      </template>
    </div>

    <div class="flex items-center justify-between mt-4 text-[11px] text-txt-3">
      <span x-text="'本月 ' + fmtNum(cal.total) + ' 条 / ' + (cal.covered_days||0) + ' 天'"></span>
      <div class="flex items-center gap-1">
        <span>少</span>
        <template x-for="l in [0,1,2,3,4]" :key="l">
          <span class="w-2.5 h-2.5 rounded-sm" :class="'heat-'+l"></span>
        </template>
        <span>多</span>
      </div>
    </div>
  </div>

  <div class="card p-5 lg:col-span-3">
    <div class="flex items-center justify-between mb-4">
      <h3 class="font-semibold">任务历史</h3>
      <button class="btn-ghost btn-xs" @click="loadJobs()">刷新</button>
    </div>
    <div class="overflow-x-auto -mx-5 px-5 max-h-[340px] overflow-y-auto thin-scroll">
      <table class="tbl" x-show="jobs.length">
        <thead><tr><th>类型</th><th>状态</th><th>事件</th><th>耗时</th><th>时间</th></tr></thead>
        <tbody>
          <template x-for="j in jobs" :key="j.id">
            <tr>
              <td><span class="text-xs" x-text="j.type === 'backfill' ? '历史回填' : '增量采集'"></span></td>
              <td><span class="badge" :class="statusBadge(j.status)" x-text="statusText(j.status)"></span></td>
              <td class="font-mono text-xs" x-text="fmtNum((j.result && j.result.events) || (j.progress && j.progress.total_events) || 0)"></td>
              <td class="font-mono text-xs text-txt-2" x-text="fmtDuration((j.progress && j.progress.elapsed) || 0)"></td>
              <td class="text-xs text-txt-3" x-text="fmtTime(j.created_at, true)"></td>
            </tr>
          </template>
        </tbody>
      </table>
      <div x-show="!jobs.length" class="empty">还没有采集记录</div>
    </div>
  </div>
</div>

<!-- 房间与实体 -->
<div class="card p-5">
  <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
    <div>
      <h3 class="font-semibold">房间与实体</h3>
      <p class="text-[11px] text-txt-3 mt-0.5">
        已启用 <span class="text-txt-2 font-mono" x-text="fmtNum(roomsInfo.enabled_entities)"></span>
        / <span class="font-mono" x-text="fmtNum(roomsInfo.total_entities)"></span> 个实体
      </p>
    </div>
    <div class="flex gap-2">
      <button class="btn-ghost" @click="discover()" :disabled="discovering">
        <span x-show="discovering" class="spinner"></span>
        <span x-text="discovering ? '扫描中…' : '重新发现实体'"></span>
      </button>
      <button class="btn-primary" @click="saveRooms()" :disabled="savingRooms">
        <span x-show="savingRooms" class="spinner"></span>
        <span x-text="savingRooms ? '保存中…' : '保存实体配置'"></span>
      </button>
    </div>
  </div>

  <div class="space-y-2">
    <template x-for="name in roomNames" :key="name">
      <div class="card-flat overflow-hidden">
        <div class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-white/[.03] transition"
             @click="expanded = expanded === name ? '' : name">
          <div class="switch scale-90" :class="rooms[name].enabled !== false && 'on'"
               @click.stop="rooms[name].enabled = rooms[name].enabled === false"></div>
          <span class="text-sm font-medium" x-text="name"></span>
          <span class="badge badge-mute" x-text="enabledCount(name) + ' / ' + entityCount(name)"></span>
          <span class="badge badge-warn" x-show="rooms[name].stale" title="本次发现未返回该房间">离线</span>
          <svg class="w-4 h-4 ml-auto text-txt-3 transition-transform"
               :class="expanded === name && 'rotate-90'"
               viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 18l6-6-6-6"/></svg>
        </div>

        <div x-show="expanded === name" x-transition.opacity.duration.150ms
             class="border-t border-white/5 px-4 py-3">
          <div class="flex gap-2 mb-3">
            <button class="btn-ghost btn-xs" @click="setAll(name, true)">全部启用</button>
            <button class="btn-ghost btn-xs" @click="setAll(name, false)">全部停用</button>
          </div>
          <div class="grid sm:grid-cols-2 xl:grid-cols-3 gap-x-4 gap-y-1.5 max-h-[280px] overflow-y-auto thin-scroll">
            <template x-for="(info, eid) in rooms[name].entities" :key="eid">
              <label class="flex items-center gap-2.5 py-1 cursor-pointer group">
                <input type="checkbox" class="accent-brand-500 w-3.5 h-3.5 shrink-0"
                       :checked="info.enabled !== false"
                       @change="info.enabled = $event.target.checked">
                <span class="min-w-0 flex-1">
                  <span class="block text-xs truncate group-hover:text-txt-1 transition" x-text="info.name || eid"></span>
                  <span class="block text-[10px] text-txt-3 font-mono truncate" x-text="eid"></span>
                </span>
              </label>
            </template>
          </div>
        </div>
      </div>
    </template>
    <div x-show="!roomNames.length" class="empty">
      <span>还没有房间配置</span>
      <button class="btn-soft" @click="discover()">从 Home Assistant 发现实体</button>
    </div>
  </div>
</div>
`;

export function collectPage() {
  return {
    tpl: TPL,

    st: { stats: {} },
    cfg: { polling_enabled: false, polling_mode: 'interval', interval_minutes: 30, polling_time: '03:00', data_retention_days: 0 },
    rooms: {},
    roomsInfo: { total_entities: 0, enabled_entities: 0 },
    excluded: [],
    jobs: [],
    cal: { month: monthStr(), days: [], max: 0, total: 0, covered_days: 0 },
    bf: { start_day: todayStr(-7), end_day: todayStr(), rooms: [] },
    expanded: '',

    saving: false,
    backfilling: false,
    discovering: false,
    savingRooms: false,

    quickRanges: [
      { days: 7, label: '近 7 天' },
      { days: 30, label: '近 30 天' },
      { days: 90, label: '近 90 天' }
    ],

    // 供模板使用的格式化函数
    fmtNum, fmtTime, fmtDuration,

    get prog() { return this.$store.app.collectProgress || {}; },

    /** 进度百分比：按「房间进度 + 房间内实体进度」两级估算 */
    get percent() {
      const p = this.prog;
      const tr = Number(p.total_rooms) || 0;
      if (!tr) return this.$store.app.collecting ? 6 : 0;
      const ri = Math.max(0, Number(p.current_room_index) || 0);
      const te = Number(p.total_entities_in_room) || 0;
      const ei = Math.max(0, Number(p.current_entity_index) || 0);
      const inner = te ? Math.min(1, ei / te) : 0;
      return Math.min(99, Math.round(((Math.max(0, ri - 1) + inner) / tr) * 100));
    },

    get roomNames() { return Object.keys(this.rooms || {}); },
    get calPad() { return monthFirstWeekday(this.cal.month); },

    init() {
      this.load();
      this._onFinish = () => { this.loadJobs(); this.loadCalendar(this.cal.month); this.loadStatus(); };
      window.addEventListener('mw:collect-finished', this._onFinish);
    },

    destroy() {
      window.removeEventListener('mw:collect-finished', this._onFinish);
    },

    async load() {
      await Promise.all([
        this.loadStatus(),
        this.loadRooms(),
        this.loadJobs(),
        this.loadCalendar(this.cal.month)
      ]);
    },

    async loadStatus() {
      try {
        const d = await api.collectStatus();
        this.st = d;
        this.cfg = {
          polling_enabled: !!d.enabled,
          polling_mode: d.mode || 'interval',
          interval_minutes: d.interval_minutes || 30,
          polling_time: d.time || '03:00',
          data_retention_days: this.cfg.data_retention_days || 0
        };
      } catch (e) {
        this.$store.app.err('采集状态加载失败：' + e.message);
      }
    },

    async loadRooms() {
      try {
        const d = await api.haRooms();
        this.rooms = d.rooms || {};
        this.excluded = d.excluded_entities || [];
        this.roomsInfo = { total_entities: d.total_entities || 0, enabled_entities: d.enabled_entities || 0 };
      } catch (e) {
        this.$store.app.err('房间配置加载失败：' + e.message);
      }
    },

    async loadJobs() {
      try {
        const d = await api.collectJobs(30);
        this.jobs = d.jobs || [];
      } catch (e) { /* 次要数据，静默 */ }
    },

    async loadCalendar(month) {
      try {
        const d = await api.collectCalendar(month);
        this.cal = d;
      } catch (e) { /* 次要数据，静默 */ }
    },

    moveMonth(delta) { this.loadCalendar(shiftMonth(this.cal.month, delta)); },

    heatClass(d) {
      const base = 'heat-' + heatLevel(d.events, this.cal.max);
      return d.day === todayStr() ? base + ' heat-today' : base;
    },

    // ── 调度 ─────────────────────────────────────────────
    async toggleEnabled() {
      const next = !this.cfg.polling_enabled;
      this.cfg.polling_enabled = next;
      try {
        const d = await api.collectEnable(next);
        this.$store.app.ok(d.message);
      } catch (e) {
        this.cfg.polling_enabled = !next;
        this.$store.app.err(e.message);
      }
    },

    async saveConfig() {
      this.saving = true;
      try {
        const d = await api.collectConfig({
          polling_enabled: this.cfg.polling_enabled,
          polling_mode: this.cfg.polling_mode,
          polling_time: this.cfg.polling_time,
          interval_minutes: this.cfg.interval_minutes,
          data_retention_days: this.cfg.data_retention_days
        });
        this.$store.app.ok(d.message || '已保存');
        this.loadStatus();
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.saving = false;
      }
    },

    // ── 手动执行 ─────────────────────────────────────────
    async trigger() {
      const app = this.$store.app;
      try {
        const d = await api.collectTrigger();
        app.ok(d.message || '采集任务已启动');
        app.collecting = true;
        app.startHeartbeat();
      } catch (e) {
        app.err(e.message);
      }
    },

    async cancel() {
      try {
        const d = await api.collectCancel();
        this.$store.app.warn(d.message || '已请求取消');
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    applyRange(days) {
      this.bf.start_day = todayStr(-(days - 1));
      this.bf.end_day = todayStr();
    },

    toggleBfRoom(name) {
      const i = this.bf.rooms.indexOf(name);
      if (i === -1) this.bf.rooms.push(name); else this.bf.rooms.splice(i, 1);
    },

    async runBackfill() {
      const app = this.$store.app;
      if (!this.bf.start_day || !this.bf.end_day) { app.warn('请选择回填区间'); return; }
      if (this.bf.start_day > this.bf.end_day) { app.warn('开始日期不能晚于结束日期'); return; }
      this.backfilling = true;
      try {
        const d = await api.collectBackfill({
          start_day: this.bf.start_day,
          end_day: this.bf.end_day,
          rooms: this.bf.rooms.length ? this.bf.rooms : undefined
        });
        app.ok(d.message || '回填任务已启动');
        app.collecting = true;
        app.startHeartbeat();
      } catch (e) {
        app.err(e.message);
      } finally {
        this.backfilling = false;
      }
    },

    // ── 房间实体 ─────────────────────────────────────────
    entityCount(name) {
      const r = this.rooms[name] || {};
      return Object.keys(r.entities || {}).length;
    },
    enabledCount(name) {
      const r = this.rooms[name] || {};
      return Object.values(r.entities || {}).filter((e) => e.enabled !== false).length;
    },
    setAll(name, value) {
      const entities = (this.rooms[name] || {}).entities || {};
      Object.keys(entities).forEach((k) => { entities[k].enabled = value; });
    },

    async discover() {
      const app = this.$store.app;
      this.discovering = true;
      try {
        const d = await api.haDiscover();
        this.rooms = d.rooms || {};
        app.ok('发现 ' + (d.total_entities || 0) + ' 个实体，确认后点「保存实体配置」生效');
      } catch (e) {
        app.err('发现实体失败：' + e.message);
      } finally {
        this.discovering = false;
      }
    },

    async saveRooms() {
      this.savingRooms = true;
      try {
        const d = await api.haSaveRooms(JSON.parse(JSON.stringify(this.rooms)), this.excluded);
        this.$store.app.ok(d.message || '已保存');
        this.loadRooms();
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.savingRooms = false;
      }
    },

    // ── 展示映射 ─────────────────────────────────────────
    phaseText(p) {
      return {
        idle: '空闲', polling: '正在采集', writing: '正在写入',
        done: '已完成', error: '执行失败', cancelled: '已取消'
      }[p] || '处理中';
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
