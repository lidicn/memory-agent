/* 信号规则页（学习策略）：独立管理页
 *
 * 两层数据视图：
 *  - 硬排除（signal_exclusions 表，无歧义硬排）：手动添加 / 撤销（墓碑）。
 *  - 软记忆（topic_key=signal_trust 的 agent 记忆，带条件软判）：复用 agent_memory 的
 *    晋升 / 撤销 / 反馈通道。
 * 交互完全复用 agent_memory.js 的设计语言与状态徽章体系：
 *   card / glass / badge* / btn-brand / icon-btn / stagger / modal-mask ...
 */

import { api } from '../api.js';
import { fmtTime, copyText, debounce } from '../util.js';

const SCOPE_LABEL = {
  all: '全部维度',
  wake_anchor: '起床锚定',
  presence: '在房判定',
  working: '工作/电脑',
  watching_tv: '看电视',
};
const TYPE_LABEL = {
  exclude: '排除',
  is_automation: '是自动化信号',
  not_automation: '非自动化信号',
};
const STATE_META = {
  staging: { label: '暂存', cls: 'badge-mute' },
  live: { label: '生效', cls: 'badge-ok' },
  revoked: { label: '撤销', cls: 'badge-danger' },
  pending_review: { label: '待审', cls: 'badge-warn' },
};

const TPL = `
<!-- 概览 -->
<div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
  <div class="card-flat p-3 flex items-center gap-3">
    <span class="badge badge-danger">硬</span>
    <div><div class="text-lg font-semibold leading-none" x-text="counts.hard"></div>
    <div class="text-[10px] text-txt-3 mt-0.5">硬排除规则</div></div>
  </div>
  <div class="card-flat p-3 flex items-center gap-3">
    <span class="badge badge-brand">软</span>
    <div><div class="text-lg font-semibold leading-none" x-text="counts.soft"></div>
    <div class="text-[10px] text-txt-3 mt-0.5">软记忆</div></div>
  </div>
  <div class="card-flat p-3 flex items-center gap-3">
    <span class="badge badge-mute">生效</span>
    <div><div class="text-lg font-semibold leading-none" x-text="hardActiveCount"></div>
    <div class="text-[10px] text-txt-3 mt-0.5">生效中硬排除</div></div>
  </div>
  <div class="card-flat p-3 flex items-center gap-3">
    <span class="badge badge-warn">优先</span>
    <div><div class="text-[11px] font-medium leading-tight">硬排 &gt; 软判</div>
    <div class="text-[10px] text-txt-3 mt-0.5">命中即跳过软记忆</div></div>
  </div>
</div>

<!-- 工具条 -->
<div class="flex flex-wrap items-center gap-3">
  <div class="relative flex-1 min-w-[200px] max-w-[340px]">
    <svg class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-txt-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
    <input class="inp pl-9" x-model="q" @input="search()" placeholder="搜索实体 / 规则正文 / 关键词…">
  </div>

  <div class="flex flex-wrap gap-1.5">
    <template x-for="s in scopes" :key="s">
      <button class="badge cursor-pointer" :class="scopeFilter===s ? 'badge-brand' : 'badge-mute'"
              @click="scopeFilter=s" x-text="SCOPE_LABEL[s] || s"></button>
    </template>
  </div>

  <div class="ml-auto flex items-center gap-2">
    <button class="btn-soft btn-xs" @click="load()" :disabled="loading">刷新</button>
    <button class="btn-brand btn-xs" @click="openTeach()">
      <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
      <span>教一条规则</span>
    </button>
  </div>
</div>

<div x-show="loading" class="empty"><span class="spinner"></span><span>加载中…</span></div>

<!-- 硬排除 -->
<div class="card p-4 mt-4" x-show="!loading">
  <div class="flex items-center justify-between mb-3">
    <div class="flex items-center gap-2">
      <span class="w-7 h-7 rounded-lg grid place-items-center grad-brand text-white">
        <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M12 2l9 4.5v5c0 5-3.4 8.5-9 11-5.6-2.5-9-6-9-11v-5z"/></svg>
      </span>
      <h3 class="font-semibold">硬排除 <span class="text-[11px] font-normal text-txt-3">无歧义硬排</span></h3>
    </div>
    <span class="badge badge-mute" x-text="hardItems.length + ' 条'"></span>
  </div>

  <div x-show="!hardItems.length" class="empty text-[11px]">
    <span x-show="!hard.length">还没有硬排除规则</span>
    <span x-show="hard.length">当前筛选下没有匹配的硬排除</span>
  </div>

  <div class="space-y-3" x-show="hardItems.length">
    <template x-for="(r, i) in hardItems" :key="r.exclusion_id">
      <div class="card-flat p-4 flex flex-col stagger" :style="'--d:' + (i*30) + 'ms'">
        <div class="flex items-start gap-3">
          <span class="badge shrink-0" :class="r.scope==='all' ? 'badge-brand' : 'badge-mute'" x-text="SCOPE_LABEL[r.scope] || r.scope"></span>
          <span class="badge shrink-0" :class="{'is_automation':'badge-warn','not_automation':'badge-ok'}[r.exclusion_type] || 'badge-mute'" x-text="TYPE_LABEL[r.exclusion_type] || r.exclusion_type"></span>
          <span class="badge shrink-0 badge-danger" x-show="r.revoked" x-text="'已撤销'"></span>
          <div class="min-w-0 flex-1">
            <p class="font-mono text-sm text-txt-1 break-all" x-text="r.entity_id"></p>
            <p class="text-[12px] text-txt-2 mt-1 leading-relaxed" x-show="r.reason" x-text="r.reason"></p>
          </div>
        </div>
        <div class="flex items-center justify-between mt-3 pt-3 border-t border-white/5">
          <span class="text-[10px] text-txt-3" x-text="'创建于 ' + fmtTime(r.created_at)"></span>
          <div class="flex gap-1">
            <button class="icon-btn" title="详情" @click="detail = Object.assign({}, r, {_kind:'hard'})">
              <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/></svg>
            </button>
            <button class="icon-btn hover:!text-danger" title="撤销" x-show="!r.revoked" @click="revokeHard(r)">
              <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</div>

<!-- 软记忆 -->
<div class="card p-4 mt-4" x-show="!loading">
  <div class="flex items-center justify-between mb-3">
    <div class="flex items-center gap-2">
      <span class="w-7 h-7 rounded-lg grid place-items-center grad-brand text-white">
        <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8z"/></svg>
      </span>
      <h3 class="font-semibold">软记忆 <span class="text-[11px] font-normal text-txt-3">带条件软判 · topic_key=signal_trust</span></h3>
    </div>
    <span class="badge badge-mute" x-text="softItems.length + ' 条'"></span>
  </div>

  <div x-show="!softItems.length" class="empty text-[11px]">
    <span x-show="!soft.length">还没有软记忆（可用「教一条规则」选软记忆，或在 AI 助手沉淀）</span>
    <span x-show="soft.length">当前筛选下没有匹配的软记忆</span>
  </div>

  <div class="space-y-3" x-show="softItems.length">
    <template x-for="(m, i) in softItems" :key="m.memory_id">
      <div class="card-flat p-4 flex flex-col stagger" :style="'--d:' + (i*30) + 'ms'">
        <div class="flex items-start gap-3">
          <span class="badge shrink-0" :class="(STATE_META[m.state]||{}).cls||'badge-mute'" x-text="(STATE_META[m.state]||{}).label||m.state"></span>
          <div class="min-w-0 flex-1">
            <p class="text-sm leading-relaxed text-txt-1 line-clamp-3" x-text="m.text"></p>
            <div class="flex flex-wrap items-center gap-1.5 mt-2">
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
            <button class="icon-btn hover:!text-danger" title="撤销" x-show="m.state!=='revoked'" @click="revokeSoft(m)">
              <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
            </button>
            <button class="icon-btn" title="详情" @click="detail = Object.assign({}, m, {_kind:'soft'})">
              <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/></svg>
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</div>

<!-- 教一条规则 弹层 -->
<div class="modal-mask" x-show="teachOpen" x-transition.opacity @click.self="teachOpen=false" style="display:none">
  <div class="glass rounded-2xl w-full max-w-2xl max-h-[90vh] flex flex-col anim-in" x-show="teachOpen">
    <div class="flex items-center gap-3 p-5 border-b border-white/5">
      <div class="w-10 h-10 rounded-xl grid place-items-center grad-brand text-white">
        <svg viewBox="0 0 24 24" class="w-5 h-5" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M12 2l9 4.5v5c0 5-3.4 8.5-9 11-5.6-2.5-9-6-9-11v-5z"/></svg>
      </div>
      <div class="min-w-0 flex-1">
        <h3 class="font-semibold">教一条信号规则</h3>
        <p class="text-[11px] text-txt-3 mt-0.5">硬排写入信号表（无歧义）；软记忆落向量库（参与信任闭环）</p>
      </div>
      <button class="icon-btn" @click="teachOpen=false">
        <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
      </button>
    </div>
    <div class="flex-1 overflow-y-auto thin-scroll p-5 space-y-4 text-sm">
      <div>
        <div class="lbl">实体 ID <span class="text-danger">*</span></div>
        <input class="inp w-full mt-1" x-model="form.entity_id" placeholder="如 light.xiaomi_speaker / binary_sensor.study_pc_presence">
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="lbl">类型</div>
          <select class="inp w-full mt-1" x-model="form.kind">
            <option value="hard">硬排（信号表）</option>
            <option value="soft">软记忆（向量库）</option>
          </select>
        </div>
        <div>
          <div class="lbl">检测维度 scope</div>
          <select class="inp w-full mt-1" x-model="form.scope">
            <option value="all">全部维度</option>
            <option value="wake_anchor">起床锚定</option>
            <option value="presence">在房判定</option>
            <option value="working">工作/电脑</option>
            <option value="watching_tv">看电视</option>
          </select>
        </div>
      </div>

      <!-- 硬排专属 -->
      <div x-show="form.kind==='hard'" class="space-y-3">
        <div>
          <div class="lbl">排除类型</div>
          <select class="inp w-full mt-1" x-model="form.exclusion_type">
            <option value="exclude">排除（不采信）</option>
            <option value="is_automation">是自动化信号</option>
            <option value="not_automation">非自动化信号</option>
          </select>
        </div>
        <div>
          <div class="lbl">理由（可选）</div>
          <input class="inp w-full mt-1" x-model="form.reason" placeholder="如：小爱音箱定时模式切换是自动化信号，不是起床">
        </div>
      </div>

      <!-- 软记忆专属 -->
      <div x-show="form.kind==='soft'" class="space-y-3">
        <div>
          <div class="lbl">正文 <span class="text-danger">*</span></div>
          <textarea class="inp w-full mt-1 h-20" x-model="form.text" placeholder="如：书房电脑常年开机，不能作为『在家/工作』的判定依据"></textarea>
        </div>
        <div>
          <div class="lbl">真实溯源 source_refs <span class="text-danger">*</span></div>
          <input class="inp w-full mt-1" x-model="form.source_refs" placeholder="逗号分隔，如 event:xxxx,activity:yyyy">
        </div>
      </div>
    </div>
    <div class="flex items-center justify-between gap-2 p-4 border-t border-white/5">
      <span class="text-[10px] text-txt-3">硬排幂等（同实体+维度复用一条）；软记忆可经反馈/晋升越用越准</span>
      <div class="flex gap-2">
        <button class="btn-ghost btn-xs" @click="teachOpen=false">取消</button>
        <button class="btn-brand btn-xs" @click="saveTeach()" :disabled="saving">
          <span x-show="saving" class="spinner"></span><span x-text="saving ? '保存中' : '保存规则'"></span>
        </button>
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
        <h3 class="font-semibold" x-text="detail && (detail._kind==='hard' ? '硬排除详情' : '软记忆详情')"></h3>
        <p class="text-[11px] text-txt-3 font-mono mt-0.5" x-text="detail && (detail._kind==='hard' ? detail.exclusion_id : detail.memory_id)"></p>
      </div>
      <button class="icon-btn" @click="detail=null">
        <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
      </button>
    </div>
    <div class="flex-1 overflow-y-auto thin-scroll p-5 space-y-4 text-sm" x-show="detail">
      <!-- 硬排除 -->
      <template x-if="detail && detail._kind==='hard'">
        <div class="space-y-3">
          <div class="card-flat p-3"><div class="text-[10px] text-txt-3">实体</div><div class="text-sm mt-1 font-mono break-all" x-text="detail.entity_id"></div></div>
          <div class="grid grid-cols-3 gap-3">
            <div class="card-flat p-3"><div class="text-[10px] text-txt-3">维度</div><div class="text-xs mt-1" x-text="SCOPE_LABEL[detail.scope] || detail.scope"></div></div>
            <div class="card-flat p-3"><div class="text-[10px] text-txt-3">类型</div><div class="text-xs mt-1" x-text="TYPE_LABEL[detail.exclusion_type] || detail.exclusion_type"></div></div>
            <div class="card-flat p-3"><div class="text-[10px] text-txt-3">状态</div><div class="text-xs mt-1" x-text="detail.revoked ? '已撤销' : '生效中'"></div></div>
          </div>
          <div class="card-flat p-3" x-show="detail.reason"><div class="text-[10px] text-txt-3">理由</div><div class="text-sm mt-1 leading-relaxed" x-text="detail.reason"></div></div>
          <div class="card-flat p-3"><div class="text-[10px] text-txt-3">创建</div><div class="text-xs mt-1" x-text="fmtTime(detail.created_at, true)"></div></div>
        </div>
      </template>
      <!-- 软记忆 -->
      <template x-if="detail && detail._kind==='soft'">
        <div class="space-y-3">
          <p class="text-txt-1 leading-relaxed" x-text="detail.text"></p>
          <div class="grid grid-cols-3 gap-3">
            <div class="card-flat p-3"><div class="text-[10px] text-txt-3">状态</div><div class="text-xs mt-1" x-text="(STATE_META[detail.state]||{}).label || detail.state"></div></div>
            <div class="card-flat p-3"><div class="text-[10px] text-txt-3">信任</div><div class="text-xs mt-1" x-text="fmtTrust(detail.trust)"></div></div>
            <div class="card-flat p-3"><div class="text-[10px] text-txt-3">过期</div><div class="text-xs mt-1" x-text="detail.expires_at ? fmtTime(detail.expires_at) : '不过期'"></div></div>
          </div>
          <div x-show="(detail.tags||[]).length">
            <div class="lbl">标签</div>
            <div class="flex flex-wrap gap-1.5"><template x-for="t in (detail.tags) || []" :key="t"><span class="badge badge-mute" x-text="'#' + t"></span></template></div>
          </div>
          <div x-show="(detail.source_refs||[]).length">
            <div class="lbl">溯源引用（source_refs）</div>
            <div class="space-y-1"><template x-for="r in (detail.source_refs) || []" :key="r"><div class="card-flat p-2 font-mono text-[11px] text-txt-2" x-text="r"></div></template></div>
          </div>
        </div>
      </template>
    </div>
    <div class="flex items-center justify-between gap-2 p-4 border-t border-white/5">
      <span class="text-[10px] text-txt-3" x-show="detail" x-text="'创建于 ' + fmtTime((detail&&detail.created_at) || '', true)"></span>
      <div class="flex gap-2">
        <button class="btn-ghost btn-xs" @click="copyJSON()">复制 JSON</button>
        <button class="btn-brand btn-xs" x-show="detail && detail._kind==='soft' && detail.state!=='live' && detail.state!=='revoked'" @click="promote(detail)">晋升</button>
        <button class="btn-danger btn-xs" x-show="detail && detail._kind==='hard' && !detail.revoked" @click="revokeHard(detail)">撤销硬排</button>
        <button class="btn-danger btn-xs" x-show="detail && detail._kind==='soft' && detail.state!=='revoked'" @click="revokeSoft(detail)">撤销记忆</button>
      </div>
    </div>
  </div>
</div>
`;

export function signalRulesPage() {
  return {
    tpl: TPL,
    q: '',
    scopes: ['all', 'wake_anchor', 'presence', 'working', 'watching_tv'],
    scopeFilter: 'all',
    loading: true,
    hard: [],
    soft: [],
    counts: { hard: 0, soft: 0 },
    detail: null,

    teachOpen: false,
    form: { entity_id: '', kind: 'hard', scope: 'all', exclusion_type: 'exclude', reason: '', text: '', source_refs: '' },
    saving: false,

    SCOPE_LABEL,
    TYPE_LABEL,
    STATE_META,
    fmtTime,

    get hardItems() {
      const low = this.q.trim().toLowerCase();
      let rows = this.hard;
      if (this.scopeFilter !== 'all') rows = rows.filter((r) => r.scope === this.scopeFilter);
      if (low) {
        rows = rows.filter(
          (r) =>
            (r.entity_id || '').toLowerCase().includes(low) ||
            (r.reason || '').toLowerCase().includes(low)
        );
      }
      return rows;
    },
    get softItems() {
      const low = this.q.trim().toLowerCase();
      let rows = this.soft;
      if (low) {
        rows = rows.filter(
          (m) =>
            (m.text || '').toLowerCase().includes(low) ||
            (m.topic_key || '').toLowerCase().includes(low)
        );
      }
      return rows;
    },
    get hardActiveCount() {
      return this.hard.filter((r) => !r.revoked).length;
    },

    init() {
      this.load();
      this.search = () => {}; // 列表为计算属性，输入即过滤，无需重新拉取
    },

    async load() {
      this.loading = true;
      try {
        const d = await api.signalRules({});
        this.hard = d.hard || [];
        this.soft = d.soft || [];
        this.counts = d.counts || { hard: this.hard.length, soft: this.soft.length };
      } catch (e) {
        this.$store.app.err('规则加载失败：' + e.message);
      } finally {
        this.loading = false;
      }
    },

    search() {},

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

    // ── 硬排除 ──
    async revokeHard(r) {
      if (!r) return;
      const yes = await this.$store.app.ask(
        '撤销硬排除',
        '确定撤销该硬排除规则？该实体将重新进入对应检测维度（墓碑保留审计）。',
        '撤销'
      );
      if (!yes) return;
      try {
        await api.signalRevoke(r.exclusion_id);
        this.$store.app.ok('已撤销硬排除');
        this.detail = null;
        this.load();
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    // ── 软记忆（复用 agent_memory 通道）──
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
    async promote(m) {
      if (!m) return;
      try {
        const d = await api.agentMemoryPromote({ memory_id: m.memory_id });
        this.$store.app.ok(d.message || '已晋升');
        this.detail = null;
        this.load();
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },
    async revokeSoft(m) {
      if (!m) return;
      const yes = await this.$store.app.ask('撤销记忆', '确定撤销该软记忆？它将退出检索。', '撤销');
      if (!yes) return;
      try {
        await api.agentMemoryRevoke(m.memory_id);
        this.$store.app.ok('已撤销');
        this.detail = null;
        this.load();
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    // ── 教一条规则 ──
    openTeach() {
      this.form = { entity_id: '', kind: 'hard', scope: 'all', exclusion_type: 'exclude', reason: '', text: '', source_refs: '' };
      this.teachOpen = true;
    },
    async saveTeach() {
      const f = this.form;
      if (!f.entity_id.trim()) {
        this.$store.app.err('请填写实体 ID');
        return;
      }
      if (f.kind === 'soft' && !f.text.trim()) {
        this.$store.app.err('软记忆必须填写正文');
        return;
      }
      if (f.kind === 'soft' && !f.source_refs.trim()) {
        this.$store.app.err('软记忆必须填写真实溯源（source_refs）');
        return;
      }
      this.saving = true;
      try {
        const body = {
          entity_id: f.entity_id.trim(),
          scope: f.scope,
          kind: f.kind,
          reason: f.reason.trim(),
          exclusion_type: f.exclusion_type,
          source_refs: f.kind === 'soft' ? f.source_refs.split(',').map((s) => s.trim()).filter(Boolean) : [],
          text: f.text.trim(),
          session_id: 'web-ui',
        };
        const d = await api.signalTeach(body);
        this.$store.app.ok(d.message || '已保存规则');
        this.teachOpen = false;
        this.load();
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.saving = false;
      }
    },

    async copyJSON() {
      if (!this.detail) return;
      const { _kind, ...rest } = this.detail;
      const okFlag = await copyText(JSON.stringify(rest, null, 2));
      okFlag ? this.$store.app.ok('已复制 JSON') : this.$store.app.err('复制失败');
    }
  };
}
