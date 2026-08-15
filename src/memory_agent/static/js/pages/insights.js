/* 行为洞察页：模板列表 / 检索 / 详情 / 删除 / 导出 */

import { api } from '../api.js';
import { fmtTime, downloadJSON, copyText, debounce } from '../util.js';

const CAT = {
  sleep: { label: '作息', cls: 'grad-violet' },
  meal: { label: '饮食', cls: 'grad-warn' },
  hygiene: { label: '清洁', cls: 'grad-aqua' },
  appliance: { label: '电器', cls: 'grad-brand' },
  security: { label: '安防', cls: 'grad-ok' },
  energy: { label: '能耗', cls: 'grad-warn' },
  comfort: { label: '舒适', cls: 'grad-aqua' },
  other: { label: '其他', cls: 'grad-brand' }
};

const TPL = `
<!-- 工具条 -->
<div class="flex flex-wrap items-center gap-3">
  <div class="relative flex-1 min-w-[220px] max-w-[380px]">
    <svg class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-txt-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
    <input class="inp pl-9" x-model="q" @input="search()" placeholder="搜索洞察名称、描述或实体…">
  </div>

  <div class="flex flex-wrap gap-1.5">
    <button class="badge cursor-pointer" :class="category==='all' ? 'badge-brand' : 'badge-mute'"
            @click="category='all'; load()">全部</button>
    <template x-for="c in categories" :key="c">
      <button class="badge cursor-pointer" :class="category===c ? 'badge-brand' : 'badge-mute'"
              @click="category=c; load()" x-text="catLabel(c)"></button>
    </template>
  </div>

  <div class="ml-auto flex items-center gap-2">
    <span class="text-[11px] text-txt-3" x-text="total + ' 条洞察'"></span>
    <button class="btn-ghost btn-xs" @click="exportAll()" :disabled="!items.length">导出全部</button>
  </div>
</div>

<!-- 列表 -->
<div x-show="loading" class="empty"><span class="spinner"></span><span>加载中…</span></div>

<div x-show="!loading && !items.length" class="empty">
  <span>还没有行为洞察</span>
  <span class="text-[11px]">去「AI 助手 → 行为分析助手」跑一次分析，把结果存为洞察模板</span>
  <button class="btn-soft" @click="$store.app.go('assistant')">去生成</button>
</div>

<div class="grid md:grid-cols-2 xl:grid-cols-3 gap-4" x-show="!loading && items.length">
  <template x-for="(t, i) in items" :key="t.id">
    <div class="card p-4 flex flex-col stagger cursor-pointer" :style="'--d:' + (i*35) + 'ms'"
         @click="detail = t">
      <div class="flex items-start gap-3">
        <div class="w-9 h-9 rounded-xl grid place-items-center shrink-0" :class="catClass(t.category)">
          <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M9 18h6M10 22h4"/><path d="M12 2a7 7 0 00-4 12.7V17h8v-2.3A7 7 0 0012 2z"/></svg>
        </div>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-1.5">
            <h4 class="text-sm font-semibold truncate" x-text="t.name"></h4>
            <span class="badge badge-mute shrink-0" x-show="t.builtin">内置</span>
          </div>
          <p class="text-[11px] text-txt-3 mt-0.5 line-clamp-2 leading-relaxed" x-text="t.description || '无描述'"></p>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-1.5 mt-3">
        <span class="badge badge-brand" x-text="catLabel(t.category)"></span>
        <span class="badge badge-mute" x-text="(t.entities||[]).length + ' 实体'"></span>
        <span class="badge" :class="confBadge(t.confidence)" x-text="'置信 ' + Math.round((t.confidence||0)*100) + '%'"></span>
        <span class="badge badge-mute" x-show="t.sample_days" x-text="t.sample_days + ' 天样本'"></span>
      </div>

      <div class="flex items-center justify-between mt-3 pt-3 border-t border-white/5">
        <span class="text-[10px] text-txt-3" x-text="fmtTime(t.updated_at || t.created_at)"></span>
        <div class="flex gap-1" @click.stop>
          <button class="icon-btn" title="导出" @click="exportOne(t)">
            <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><path d="M7 10l5 5 5-5M12 15V3"/></svg>
          </button>
          <button class="icon-btn hover:!text-danger" title="删除" x-show="!t.builtin" @click="remove(t)">
            <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
          </button>
        </div>
      </div>
    </div>
  </template>
</div>

<!-- 详情弹层 -->
<div class="modal-mask" x-show="detail" x-transition.opacity @click.self="detail=null" style="display:none">
  <div class="glass rounded-2xl w-full max-w-2xl max-h-[86vh] flex flex-col anim-in" x-show="detail">
    <div class="flex items-start gap-3 p-5 border-b border-white/5">
      <div class="w-10 h-10 rounded-xl grid place-items-center shrink-0" :class="detail && catClass(detail.category)">
        <svg viewBox="0 0 24 24" class="w-5 h-5" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M9 18h6M10 22h4"/><path d="M12 2a7 7 0 00-4 12.7V17h8v-2.3A7 7 0 0012 2z"/></svg>
      </div>
      <div class="min-w-0 flex-1">
        <h3 class="font-semibold" x-text="detail && detail.name"></h3>
        <p class="text-[11px] text-txt-3 font-mono mt-0.5" x-text="detail && detail.id"></p>
      </div>
      <button class="icon-btn" @click="detail=null">
        <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
      </button>
    </div>

    <div class="flex-1 overflow-y-auto thin-scroll p-5 space-y-4 text-sm" x-show="detail">
      <p class="text-txt-2 leading-relaxed" x-text="detail && detail.description"></p>

      <div class="grid grid-cols-3 gap-3">
        <div class="card-flat p-3"><div class="text-[10px] text-txt-3">分类</div><div class="text-xs mt-1" x-text="detail && catLabel(detail.category)"></div></div>
        <div class="card-flat p-3"><div class="text-[10px] text-txt-3">置信度</div><div class="text-xs mt-1" x-text="detail && (Math.round((detail.confidence||0)*100) + '%')"></div></div>
        <div class="card-flat p-3"><div class="text-[10px] text-txt-3">样本天数</div><div class="text-xs mt-1" x-text="detail && (detail.sample_days || 0)"></div></div>
      </div>

      <div x-show="detail && detail.pattern">
        <div class="lbl">规律描述</div>
        <div class="card-flat p-3 text-xs leading-relaxed text-txt-2" x-text="detail && detail.pattern"></div>
      </div>

      <div x-show="detail && (detail.entities||[]).length">
        <div class="lbl">关联实体</div>
        <div class="space-y-1.5">
          <template x-for="e in (detail && detail.entities) || []" :key="e.entity_id + (e.attribute||'')">
            <div class="card-flat p-2.5 flex items-center gap-2 text-[11px]">
              <span class="font-mono text-brand-400 truncate flex-1" x-text="e.entity_id"></span>
              <span class="badge badge-mute" x-text="e.attribute || 'state'"></span>
              <span class="badge badge-mute" x-text="(e.pattern||'equals') + ' ' + (e.value||'')"></span>
              <span class="badge badge-info" x-show="e.time_range" x-text="e.time_range"></span>
            </div>
          </template>
        </div>
      </div>

      <div x-show="detail && (detail.nr_condition || detail.nr_action)">
        <div class="lbl">Node-RED 建议</div>
        <div class="snippet thin-scroll" x-text="detail && ('条件：' + (detail.nr_condition || '—') + '\\n动作：' + (detail.nr_action || '—'))"></div>
      </div>
    </div>

    <div class="flex items-center justify-between gap-2 p-4 border-t border-white/5">
      <span class="text-[10px] text-txt-3" x-text="detail && ('更新于 ' + fmtTime(detail.updated_at || detail.created_at, true))"></span>
      <div class="flex gap-2">
        <button class="btn-ghost btn-xs" @click="copyJSON()">复制 JSON</button>
        <button class="btn-ghost btn-xs" @click="exportOne(detail)">导出</button>
        <button class="btn-danger btn-xs" x-show="detail && !detail.builtin" @click="remove(detail)">删除</button>
      </div>
    </div>
  </div>
</div>
`;

export function insightsPage() {
  return {
    tpl: TPL,
    items: [],
    categories: [],
    total: 0,
    q: '',
    category: 'all',
    loading: true,
    detail: null,

    fmtTime,

    init() {
      this.load();
      this.search = debounce(() => this.load(), 320);
    },

    async load() {
      this.loading = true;
      try {
        const d = await api.insights({ category: this.category, q: this.q.trim() });
        this.items = d.templates || [];
        this.total = d.total || this.items.length;
        if (d.categories && d.categories.length) this.categories = d.categories;
      } catch (e) {
        this.$store.app.err('洞察加载失败：' + e.message);
      } finally {
        this.loading = false;
      }
    },

    search() { /* init 中被 debounce 版本覆盖 */ },

    catLabel(c) { return (CAT[c] && CAT[c].label) || c || '其他'; },
    catClass(c) { return (CAT[c] && CAT[c].cls) || 'grad-brand'; },
    confBadge(v) {
      const n = Number(v) || 0;
      if (n >= 0.8) return 'badge-ok';
      if (n >= 0.5) return 'badge-warn';
      return 'badge-mute';
    },

    async remove(t) {
      if (!t) return;
      const yes = await this.$store.app.ask('删除洞察', '确定删除「' + t.name + '」？该操作不可撤销。', '删除');
      if (!yes) return;
      try {
        await api.deleteTemplate(t.id);
        this.$store.app.ok('已删除');
        this.detail = null;
        this.load();
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    exportOne(t) {
      if (!t) return;
      downloadJSON('insight-' + t.id + '.json', t);
    },

    exportAll() {
      downloadJSON('memory-agent-insights.json', this.items);
    },

    async copyJSON() {
      const okFlag = await copyText(JSON.stringify(this.detail, null, 2));
      okFlag ? this.$store.app.ok('已复制 JSON') : this.$store.app.err('复制失败');
    }
  };
}
