/* Agent 记忆页：参与式写回向量库的可视化管理
 *
 * 展示 staging/live/revoked/pending_review 各状态记忆，支持手动晋升/撤销/
 * 反馈/检索，并触发自动晋升 sweep。所有写回都经过服务层护栏（溯源校验、
 * 矛盾检测、信任回路），与 MCP 工具共用 AgentMemoryService。
 */

import { api } from '../api.js';
import { fmtTime, downloadJSON, copyText, debounce } from '../util.js';

const STATE_META = {
  staging: { label: '暂存', cls: 'badge-mute' },
  live: { label: '生效', cls: 'badge-ok' },
  revoked: { label: '撤销', cls: 'badge-danger' },
  pending_review: { label: '待审', cls: 'badge-warn' },
};

const TPL = `
<!-- 健康概览 -->
<div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
  <template x-for="c in stateChips" :key="c.key">
    <div class="card-flat p-3 flex items-center gap-3">
      <span class="badge" :class="c.cls" x-text="c.label"></span>
      <div><div class="text-lg font-semibold leading-none" x-text="c.value"></div>
      <div class="text-[10px] text-txt-3 mt-0.5" x-text="c.sub"></div></div>
    </div>
  </template>
</div>

<div x-show="healthLoading" class="empty"><span class="spinner"></span><span>健康检查中…</span></div>

<!-- 工具条 -->
<div class="flex flex-wrap items-center gap-3">
  <div class="relative flex-1 min-w-[200px] max-w-[340px]">
    <svg class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-txt-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
    <input class="inp pl-9" x-model="q" @input="search()" placeholder="搜索记忆内容或 topic…">
  </div>

  <div class="flex flex-wrap gap-1.5">
    <template x-for="s in states" :key="s">
      <button class="badge cursor-pointer" :class="state===s ? (STATE_META[s]||{}).cls||'badge-brand' : 'badge-mute'"
              @click="state=s; load()" x-text="(STATE_META[s]||{}).label||s"></button>
    </template>
  </div>

  <div class="ml-auto flex items-center gap-2">
    <button class="btn-soft btn-xs" @click="openRetrieve()">检索</button>
    <button class="btn-soft btn-xs" @click="refreshHealth()" :disabled="busy">刷新</button>
    <button class="btn-brand btn-xs" @click="sweep()" :disabled="busy">
      <span x-show="busy" class="spinner"></span><span x-text="busy ? '执行中' : '触发晋升扫描'"></span>
    </button>
  </div>
</div>

<!-- 列表 -->
<div x-show="loading" class="empty"><span class="spinner"></span><span>加载中…</span></div>

<div x-show="!loading && !items.length" class="empty">
  <span>暂无 Agent 记忆</span>
  <span class="text-[11px]">Agent 通过 MCP 工具 add_semantic_memory 写入后，会落在这里；或去「AI 助手」让模型沉淀记忆</span>
</div>

<div class="space-y-3 mt-4" x-show="!loading && items.length">
  <template x-for="(m, i) in items" :key="m.memory_id">
    <div class="card p-4 flex flex-col stagger" :style="'--d:' + (i*30) + 'ms'">
      <div class="flex items-start gap-3">
        <span class="badge shrink-0" :class="(STATE_META[m.state]||{}).cls||'badge-mute'" x-text="(STATE_META[m.state]||{}).label||m.state"></span>
        <div class="min-w-0 flex-1">
          <p class="text-sm leading-relaxed text-txt-1 line-clamp-3" x-text="m.text"></p>
          <div class="flex flex-wrap items-center gap-1.5 mt-2">
            <span class="badge badge-mute" x-show="m.topic_key" x-text="'#' + m.topic_key"></span>
            <template x-for="t in (m.tags||[])" :key="t"><span class="badge badge-mute" x-text="'#' + t"></span></template>
            <span class="badge" :class="trustBadge(m.trust)" x-text="'信任 ' + fmtTrust(m.trust)"></span>
            <span class="badge badge-mute" x-text="(m.source_refs||[]).length + ' 溯源'"></span>
            <span class="badge" :class="m.mirror_dirty ? 'badge-warn' : 'badge-mute'" x-text="m.mirror_dirty ? '镜像脏' : '镜像同步'"></span>
          </div>
        </div>
      </div>

      <div class="flex items-center justify-between mt-3 pt-3 border-t border-white/5">
        <span class="text-[10px] text-txt-3" x-text="'创建于 ' + fmtTime(m.created_at)"></span>
        <div class="flex gap-1">
          <button class="icon-btn" title="有用" @click="fb(m, true)">
            <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 10v11M2 12a2 2 0 012-2h3v11H4a2 2 0 01-2-2zM7 10l4-7a2 2 0 013 2l-1 5h5a2 2 0 012 2.5l-2 7a2 2 0 01-2 1.5H7"/></svg>
          </button>
          <button class="icon-btn" title="无用" @click="fb(m, false)">
            <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17 14V3M22 12a2 2 0 00-2 2h-3V3h3a2 2 0 012 2zM17 14l-4 7a2 2 0 01-3-2l1-5H6a2 2 0 01-2-2.5l2-7a2 2 0 012-1.5h11"/></svg>
          </button>
          <button class="icon-btn text-ok" title="晋升 live" x-show="m.state!=='live' && m.state!=='revoked'" @click="promote(m)">
            <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
          </button>
          <button class="icon-btn hover:!text-danger" title="撤销" x-show="m.state!=='revoked'" @click="revoke(m)">
            <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
          </button>
          <button class="icon-btn" title="详情" @click="detail = m">
            <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/></svg>
          </button>
        </div>
      </div>
    </div>
  </template>
</div>

<!-- 检索浮层 -->
<div class="modal-mask" x-show="showRetrieve" x-transition.opacity @click.self="showRetrieve=false" style="display:none">
  <div class="glass rounded-2xl w-full max-w-2xl max-h-[86vh] flex flex-col anim-in" x-show="showRetrieve">
    <div class="flex items-center gap-3 p-5 border-b border-white/5">
      <div class="w-10 h-10 rounded-xl grid place-items-center grad-brand text-white">
        <svg viewBox="0 0 24 24" class="w-5 h-5" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
      </div>
      <div class="min-w-0 flex-1">
        <h3 class="font-semibold">检索已生效记忆</h3>
        <p class="text-[11px] text-txt-3 mt-0.5">按相似度 + 信任度重排，仅检索 live 记忆</p>
      </div>
      <button class="icon-btn" @click="showRetrieve=false">
        <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
      </button>
    </div>
    <div class="p-5 space-y-3">
      <div class="flex gap-2">
        <input class="inp flex-1" x-model="rq" placeholder="输入自然语言问题，如「用户几点睡」" @keydown.enter="doRetrieve()">
        <button class="btn-brand btn-sm" @click="doRetrieve()" :disabled="retrieving">
          <span x-show="retrieving" class="spinner"></span><span>检索</span>
        </button>
      </div>
      <div x-show="!retrieving && !retrieveHits.length" class="empty text-[11px]">输入问题后点击检索</div>
      <div class="space-y-2" x-show="retrieveHits.length">
        <template x-for="(h, i) in retrieveHits" :key="h.memory_id">
          <div class="card-flat p-3">
            <p class="text-xs leading-relaxed" x-text="h.text"></p>
            <div class="flex flex-wrap items-center gap-1.5 mt-2">
              <span class="badge badge-ok" x-text="'score ' + h.final_score"></span>
              <span class="badge badge-mute" x-text="'sim ' + h.similarity"></span>
              <span class="badge badge-mute" x-text="'trust ' + h.trust"></span>
              <span class="badge badge-mute" x-show="h.topic_key" x-text="'#' + h.topic_key"></span>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</div>

<!-- 详情弹层 -->
<div class="modal-mask" x-show="detail" x-transition.opacity @click.self="detail=null" style="display:none">
  <div class="glass rounded-2xl w-full max-w-2xl max-h-[86vh] flex flex-col anim-in" x-show="detail">
    <div class="flex items-start gap-3 p-5 border-b border-white/5">
      <div class="w-10 h-10 rounded-xl grid place-items-center grad-brand text-white">
        <svg viewBox="0 0 24 24" class="w-5 h-5" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M12 2a7 7 0 00-4 12.7V17h8v-2.3A7 7 0 0012 2z"/></svg>
      </div>
      <div class="min-w-0 flex-1">
        <h3 class="font-semibold">记忆详情</h3>
        <p class="text-[11px] text-txt-3 font-mono mt-0.5" x-text="detail && detail.memory_id"></p>
      </div>
      <button class="icon-btn" @click="detail=null">
        <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
      </button>
    </div>
    <div class="flex-1 overflow-y-auto thin-scroll p-5 space-y-4 text-sm" x-show="detail">
      <p class="text-txt-1 leading-relaxed" x-text="detail && detail.text"></p>
      <div class="grid grid-cols-3 gap-3">
        <div class="card-flat p-3"><div class="text-[10px] text-txt-3">状态</div><div class="text-xs mt-1" x-text="detail && (STATE_META[detail.state]||{}).label || detail.state"></div></div>
        <div class="card-flat p-3"><div class="text-[10px] text-txt-3">信任</div><div class="text-xs mt-1" x-text="detail && fmtTrust(detail.trust)"></div></div>
        <div class="card-flat p-3"><div class="text-[10px] text-txt-3">过期</div><div class="text-xs mt-1" x-text="detail && (detail.expires_at ? fmtTime(detail.expires_at) : '不过期')"></div></div>
      </div>
      <div x-show="detail && (detail.tags||[]).length">
        <div class="lbl">标签</div>
        <div class="flex flex-wrap gap-1.5">
          <template x-for="t in (detail && detail.tags) || []" :key="t"><span class="badge badge-mute" x-text="'#' + t"></span></template>
        </div>
      </div>
      <div x-show="detail && (detail.source_refs||[]).length">
        <div class="lbl">溯源引用（source_refs）</div>
        <div class="space-y-1">
          <template x-for="r in (detail && detail.source_refs) || []" :key="r">
            <div class="card-flat p-2 font-mono text-[11px] text-txt-2" x-text="r"></div>
          </template>
        </div>
      </div>
    </div>
    <div class="flex items-center justify-between gap-2 p-4 border-t border-white/5">
      <span class="text-[10px] text-txt-3" x-text="detail && ('创建于 ' + fmtTime(detail.created_at, true))"></span>
      <div class="flex gap-2">
        <button class="btn-ghost btn-xs" @click="copyJSON()">复制 JSON</button>
        <button class="btn-brand btn-xs" x-show="detail && detail.state!=='live' && detail.state!=='revoked'" @click="promote(detail)">晋升</button>
        <button class="btn-danger btn-xs" x-show="detail && detail.state!=='revoked'" @click="revoke(detail)">撤销</button>
      </div>
    </div>
  </div>
</div>
`;

export function agentMemoryPage() {
  return {
    tpl: TPL,
    items: [],
    states: ['all', 'staging', 'live', 'revoked', 'pending_review'],
    state: 'all',
    q: '',
    loading: true,
    busy: false,
    detail: null,
    health: null,
    healthLoading: true,

    showRetrieve: false,
    rq: '',
    retrieveHits: [],
    retrieving: false,

    fmtTime,

    get stateChips() {
      const h = this.health || {};
      const s = h.states || {};
      return [
        { key: 'staging', label: '暂存', cls: 'badge-mute', value: s.staging || 0, sub: '待晋升' },
        { key: 'live', label: '生效', cls: 'badge-ok', value: s.live || 0, sub: '可检索' },
        { key: 'pending_review', label: '待审', cls: 'badge-warn', value: s.pending_review || 0, sub: '冲突挂起' },
        { key: 'revoked', label: '撤销', cls: 'badge-danger', value: s.revoked || 0, sub: '已失效' },
      ];
    },

    init() {
      this.load();
      this.refreshHealth();
      this.search = debounce(() => this.load(), 320);
    },

    async load() {
      this.loading = true;
      try {
        const q = this.q.trim();
        const d = await api.agentMemories({
          state: this.state,
          topic_key: '',
        });
        let rows = d.memories || [];
        if (q) {
          const low = q.toLowerCase();
          rows = rows.filter(
            (m) =>
              (m.text || '').toLowerCase().includes(low) ||
              (m.topic_key || '').toLowerCase().includes(low)
          );
        }
        this.items = rows;
      } catch (e) {
        this.$store.app.err('记忆加载失败：' + e.message);
      } finally {
        this.loading = false;
      }
    },

    search() { /* init 中被 debounce 版本覆盖 */ },

    async refreshHealth() {
      this.healthLoading = true;
      try {
        this.health = await api.agentMemoryHealth();
      } catch (e) {
        /* 健康信息非阻断 */
      } finally {
        this.healthLoading = false;
      }
    },

    trustBadge(v) {
      const n = Number(v) || 0;
      if (n >= 0.3) return 'badge-ok';
      if (n <= -0.3) return 'badge-danger';
      return 'badge-mute';
    },
    fmtTrust(v) {
      const n = Number(v) || 0;
      return (n > 0 ? '+' : '') + n.toFixed(2);
    },

    async promote(m) {
      if (!m) return;
      try {
        const d = await api.agentMemoryPromote({ memory_id: m.memory_id });
        this.$store.app.ok(d.message || '已晋升');
        this.detail = null;
        this.load();
        this.refreshHealth();
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    async revoke(m) {
      if (!m) return;
      const yes = await this.$store.app.ask('撤销记忆', '确定撤销该记忆？它将退出检索且不可再被晋升（除非回滚）。', '撤销');
      if (!yes) return;
      try {
        await api.agentMemoryRevoke(m.memory_id);
        this.$store.app.ok('已撤销');
        this.detail = null;
        this.load();
        this.refreshHealth();
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    async fb(m, useful) {
      if (!m) return;
      try {
        await api.agentMemoryFeedback(m.memory_id, useful);
        this.$store.app.ok(useful ? '已标记有用' : '已标记无用');
        this.load();
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    async sweep() {
      this.busy = true;
      try {
        const d = await api.agentMemorySweep();
        const sw = (d.sweep || {});
        this.$store.app.ok('晋升扫描完成：扫描 ' + (sw.scanned || 0) + '，晋升 ' + (sw.promoted || 0));
        this.load();
        this.refreshHealth();
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.busy = false;
      }
    },

    async doRetrieve() {
      const q = this.rq.trim();
      if (!q) return;
      this.retrieving = true;
      this.retrieveHits = [];
      try {
        const d = await api.agentMemoryRetrieve({ question: q, top_k: 5 });
        this.retrieveHits = d.memories || [];
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.retrieving = false;
      }
    },

    openRetrieve() {
      this.showRetrieve = true;
      this.rq = '';
      this.retrieveHits = [];
    },

    async copyJSON() {
      const okFlag = await copyText(JSON.stringify(this.detail, null, 2));
      okFlag ? this.$store.app.ok('已复制 JSON') : this.$store.app.err('复制失败');
    }
  };
}
