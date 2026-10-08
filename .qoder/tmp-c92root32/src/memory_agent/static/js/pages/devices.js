/* 设备身份页：逻辑设备清单 + 失效/健康清单（v0.3）
 *
 * 目的：把 HA 实体动荡对用户可见（A3）。
 *   - 逻辑设备：一个物理设备 ↔ 多个可能漂移的 entity_id，模板只认这里的稳定名
 *   - 失效清单：长期不可见的实体，若仍被模板引用（referenced=1）会直接让洞察失真
 */

import { api } from '../api.js';

const STATE_META = {
  active: { label: '在线', cls: 'text-emerald-400 border-emerald-400/40 bg-emerald-400/10' },
  unknown: { label: '短暂失联', cls: 'text-amber-400 border-amber-400/40 bg-amber-400/10' },
  stale: { label: '已失效', cls: 'text-rose-400 border-rose-400/40 bg-rose-400/10' },
  disabled: { label: '已禁用', cls: 'text-txt-3 border-line bg-black/20' },
};

const PROVENANCE_LABEL = {
  discovered: '自动发现',
  'auto-merged': '自动合并',
  'auto-remapped': '已重匹配',
  'user-pinned': '用户钉选',
};

const TPL = `
<div class="space-y-5">
  <div class="flex items-center justify-between gap-3">
    <div>
      <h2 class="text-xl font-semibold">设备身份</h2>
      <p class="text-[12px] text-txt-3 mt-0.5">
        逻辑设备把「物理设备」与易漂移的 HA 实体解耦；集成重登或双集成时会自动改指，模板不再失效
      </p>
    </div>
    <button class="btn-primary btn-sm" @click="loadAll()" :disabled="loading">
      <span x-show="!loading">刷新</span>
      <span x-show="loading">刷新中…</span>
    </button>
  </div>

  <!-- 概览 -->
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
    <div class="card p-4">
      <div class="text-[11px] text-txt-3">逻辑设备</div>
      <div class="text-2xl font-semibold mt-1" x-text="devices.length"></div>
    </div>
    <div class="card p-4">
      <div class="text-[11px] text-txt-3">已合并冗余</div>
      <div class="text-2xl font-semibold mt-1" x-text="mergedCount"></div>
    </div>
    <div class="card p-4">
      <div class="text-[11px] text-txt-3">已重匹配</div>
      <div class="text-2xl font-semibold mt-1" x-text="remappedCount"></div>
    </div>
    <div class="card p-4">
      <div class="text-[11px] text-txt-3">失效实体</div>
      <div class="text-2xl font-semibold mt-1" :class="staleCount ? 'text-rose-400' : ''" x-text="staleCount"></div>
    </div>
  </div>

  <!-- 页签 -->
  <div class="flex gap-1 border-b border-line">
    <button class="px-3 py-2 text-[13px] border-b-2 -mb-px transition"
            :class="tab === 'devices' ? 'border-brand text-txt-1' : 'border-transparent text-txt-3 hover:text-txt-2'"
            @click="tab = 'devices'">逻辑设备</button>
    <button class="px-3 py-2 text-[13px] border-b-2 -mb-px transition"
            :class="tab === 'health' ? 'border-brand text-txt-1' : 'border-transparent text-txt-3 hover:text-txt-2'"
            @click="tab = 'health'">
      失效 / 健康清单
      <span x-show="staleCount" class="ml-1 px-1.5 py-0.5 rounded-full text-[10px] bg-rose-400/15 text-rose-400" x-text="staleCount"></span>
    </button>
    <button class="px-3 py-2 text-[13px] border-b-2 -mb-px transition"
            :class="tab === 'merges' ? 'border-brand text-txt-1' : 'border-transparent text-txt-3 hover:text-txt-2'"
            @click="tab = 'merges'">
      合并审计
      <span x-show="mergedCount" class="ml-1 px-1.5 py-0.5 rounded-full text-[10px] bg-brand/15 text-brand" x-text="mergedCount"></span>
    </button>
  </div>

  <!-- 加载中 -->
  <div x-show="loading" class="card p-8 grid place-items-center text-txt-3">
    <span class="spinner"></span><span class="ml-2 text-xs">加载中…</span>
  </div>

  <!-- 逻辑设备 -->
  <div x-show="!loading && tab === 'devices'">
    <div x-show="!devices.length" class="card p-10 text-center">
      <div class="text-5xl mb-3">🔌</div>
      <p class="font-medium">还没有逻辑设备</p>
      <p class="text-[12px] text-txt-3 mt-1">服务启动后会自动对账 HA 实体注册表并生成，稍后刷新</p>
    </div>

    <div x-show="devices.length" class="space-y-3">
      <template x-for="d in devices" :key="d.stable_id">
        <div class="card p-5">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-medium" x-text="d.display_name || d.stable_id"></span>
                <span class="px-1.5 py-0.5 rounded text-[10px] border border-line text-txt-3" x-text="provenanceLabel(d.provenance)"></span>
                <span x-show="(d.candidates || []).length > 1"
                      class="px-1.5 py-0.5 rounded text-[10px] border border-brand/40 text-brand"
                      x-text="'冗余 ' + (d.candidates || []).length + ' 路'"></span>
              </div>
              <div class="text-[11px] text-txt-3 mt-1 break-all">
                <span x-text="d.stable_id"></span>
                <span class="mx-1">·</span>
                <span x-text="d.device_class"></span>
              </div>
            </div>
            <div class="text-right shrink-0">
              <div class="text-[11px] text-txt-3">主实体</div>
              <div class="text-[12px] break-all max-w-[240px]" x-text="d.primary_entity || '—'"></div>
            </div>
          </div>

          <div x-show="(d.candidates || []).length" class="mt-3 pt-3 border-t border-line space-y-1.5">
            <template x-for="c in (d.candidates || [])" :key="c.entity_id">
              <div class="flex items-center gap-2 text-[11px]">
                <span class="w-2 h-2 rounded-full shrink-0"
                      :class="c.state === 'active' ? 'bg-emerald-400' : 'bg-txt-3/40'"></span>
                <span class="text-txt-2 break-all" x-text="c.entity_id"></span>
                <span x-show="c.room" class="text-txt-3" x-text="c.room"></span>
                <span x-show="c.entity_id === d.primary_entity"
                      class="px-1.5 py-0.5 rounded text-[10px] border border-brand/40 text-brand">主</span>
              </div>
            </template>
          </div>
        </div>
      </template>
    </div>
  </div>

  <!-- 失效 / 健康清单 -->
  <div x-show="!loading && tab === 'health'">
    <div class="flex items-center gap-2 mb-3">
      <template x-for="s in stateFilters" :key="s.v">
        <button class="px-2.5 py-1 rounded text-[12px] border transition"
                :class="stateFilter === s.v ? 'border-brand text-brand' : 'border-line text-txt-3 hover:text-txt-2'"
                @click="stateFilter = s.v; loadHealth()" x-text="s.label"></button>
      </template>
    </div>

    <div x-show="!health.length" class="card p-10 text-center">
      <div class="text-5xl mb-3">✅</div>
      <p class="font-medium" x-text="stateFilter === 'stale' ? '没有失效设备' : '暂无记录'"></p>
      <p class="text-[12px] text-txt-3 mt-1" x-show="stateFilter === 'stale'">模板引用的实体都还在，洞察可信</p>
    </div>

    <div x-show="health.length" class="card divide-y divide-line">
      <template x-for="h in health" :key="h.entity_id">
        <div class="p-4 flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="text-[13px] break-all" x-text="h.entity_id"></span>
              <span class="px-1.5 py-0.5 rounded text-[10px] border"
                    :class="stateMeta(h.state).cls" x-text="stateMeta(h.state).label"></span>
              <span x-show="h.referenced"
                    class="px-1.5 py-0.5 rounded text-[10px] border border-amber-400/40 text-amber-400 bg-amber-400/10">被模板引用</span>
            </div>
            <div x-show="h.note" class="text-[11px] text-txt-3 mt-1" x-text="h.note"></div>
            <div x-show="h.stable_id" class="text-[11px] text-txt-3 mt-0.5 break-all">
              归属逻辑设备：<span x-text="h.stable_id"></span>
            </div>
          </div>
          <div class="text-right shrink-0 text-[11px] text-txt-3">
            <div>最近在线</div>
            <div x-text="fmtTime(h.last_seen)"></div>
          </div>
        </div>
      </template>
    </div>
  </div>

  <!-- 合并审计（v0.6 #1）：A2 自动合并清单 + 拆分回滚 -->
  <div x-show="!loading && tab === 'merges'">
    <p class="hint mb-3">A2 全自动合并产生的逻辑设备在此列出。若合并有误，可「拆分回滚」还原为各自独立的设备（标记为「用户钉选」，对账时不再自动重合并）。</p>
    <div x-show="!merges.length" class="card p-10 text-center">
      <div class="text-5xl mb-3">🧬</div>
      <p class="font-medium">没有自动合并记录</p>
      <p class="text-[12px] text-txt-3 mt-1">实体相似度未跨过阈值，或合并设备已被人工拆分</p>
    </div>
    <div x-show="merges.length" class="space-y-3">
      <template x-for="d in merges" :key="d.stable_id">
        <div class="card p-5">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-medium" x-text="d.display_name || d.stable_id"></span>
                <span class="px-1.5 py-0.5 rounded text-[10px] border border-brand/40 text-brand"
                      x-text="'自动合并 · ' + (d.candidates || []).length + ' 路'"></span>
              </div>
              <div class="text-[11px] text-txt-3 mt-1 break-all" x-text="d.stable_id"></div>
            </div>
            <button class="btn-soft btn-sm shrink-0" @click="splitMerge(d.stable_id)">拆分回滚</button>
          </div>
          <div class="mt-3 pt-3 border-t border-line space-y-1.5">
            <template x-for="c in (d.candidates || [])" :key="c.entity_id">
              <div class="flex items-center gap-2 text-[11px]">
                <span class="w-2 h-2 rounded-full shrink-0" :class="c.state === 'active' ? 'bg-emerald-400' : 'bg-txt-3/40'"></span>
                <span class="text-txt-2 break-all" x-text="c.entity_id"></span>
                <span x-show="c.entity_id === d.primary_entity"
                      class="px-1.5 py-0.5 rounded text-[10px] border border-brand/40 text-brand">主</span>
              </div>
            </template>
          </div>
        </div>
      </template>
    </div>
  </div>
</div>
`;

export const devicesPage = () => ({
  tpl: TPL,
  tab: 'devices',
  loading: false,
  devices: [],
  health: [],
  merges: [],
  stateFilter: 'stale',
  stateFilters: [
    { v: 'stale', label: '已失效' },
    { v: 'unknown', label: '短暂失联' },
    { v: 'active', label: '在线' },
    { v: '', label: '全部' },
  ],

  get mergedCount() {
    return this.devices.filter((d) => d.provenance === 'auto-merged').length;
  },
  get remappedCount() {
    return this.devices.filter((d) => d.provenance === 'auto-remapped').length;
  },
  get staleCount() {
    return this.health.filter((h) => h.state === 'stale').length;
  },

  init() {
    this.loadAll();
  },

  async loadAll() {
    await Promise.all([this.loadDevices(), this.loadHealth(), this.loadMerges()]);
  },

  async loadMerges() {
    try {
      const d = await api.listMerges();
      this.merges = d.merges || [];
    } catch (e) {
      this.$store.app.err(e.message || '加载合并审计失败');
    }
  },

  async splitMerge(stable_id) {
    const yes = await this.$store.app.ask('拆分回滚', '确定将该自动合并拆回各自独立的设备（标记为用户钉选）？', '拆分');
    if (!yes) return;
    try {
      await api.splitMerge(stable_id);
      await this.loadAll();
      this.$store.app.ok('已拆分回滚');
    } catch (e) {
      this.$store.app.err(e.message);
    }
  },

  async loadDevices() {
    this.loading = true;
    try {
      const d = await api.identityDevices();
      this.devices = d.devices || [];
    } catch (e) {
      this.$store.app.err(e.message || '加载逻辑设备失败');
    } finally {
      this.loading = false;
    }
  },

  async loadHealth() {
    try {
      const d = await api.identityHealth(this.stateFilter);
      this.health = d.health || [];
    } catch (e) {
      this.$store.app.err(e.message || '加载健康清单失败');
    }
  },

  provenanceLabel(p) {
    return PROVENANCE_LABEL[p] || p || '自动发现';
  },

  stateMeta(state) {
    return STATE_META[state] || { label: state || '未知', cls: 'text-txt-3 border-line bg-black/20' };
  },

  fmtTime(ts) {
    if (!ts) return '—';
    return String(ts).replace('T', ' ').slice(0, 16);
  },
});
