/* 定向洞察页（v0.8 记忆研究员）：四轴定向 + 安全闸 + 定期洞察的任务配置 */

import { api } from '../api.js';
import { fmtTime } from '../util.js';

const DIR_LABEL = {
  rhythm: '作息节律',
  anomaly: '设备异常',
  habit: '习惯固化',
  member_diff: '成员差异',
  linkage: '跨设备联动',
  energy: '能耗用量',
  sequence: '序列模式'
};
const DIR_OPTIONS = Object.keys(DIR_LABEL);
const WINDOW_OPTIONS = [7, 14, 30]; // 默认近7天，硬上限≤30天

const TPL = `
<!-- 头部 -->
<div class="flex items-center gap-3 flex-wrap">
  <div class="w-11 h-11 rounded-2xl grid place-items-center grad-brand text-white shrink-0">
    <svg viewBox="0 0 24 24" class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/><path d="M8 11h6M11 8v6"/></svg>
  </div>
  <div class="flex-1 min-w-0">
    <h2 class="text-lg font-semibold leading-tight">定向洞察任务</h2>
    <p class="text-[12px] text-txt-3 mt-0.5">记忆研究员：LLM 只解释规则算好的信号，按 方向 × 时间 × 区域 × 对象 定期自动挖掘，带安全闸防 token 爆炸。</p>
  </div>
  <button class="btn-primary" @click="newJob()">
    <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
    新建任务
  </button>
  <label class="flex items-center gap-2 text-[12px] cursor-pointer select-none" title="总开关（researcher_enabled）">
    <span class="text-txt-3">研究员总开关</span>
    <input type="checkbox" class="toggle" :checked="gates.enabled" @change="toggleGlobal($event.target.checked)">
    <span x-text="gates.enabled ? '已开启' : '已关闭'" :class="gates.enabled ? 'text-ok' : 'text-txt-3'"></span>
  </label>
</div>

<!-- 任务列表 -->
<div x-show="loading" class="empty"><span class="spinner"></span><span>加载中…</span></div>
<div x-show="!loading && !jobs.length" class="empty">
  <span>还没有定向洞察任务</span>
  <span class="text-[11px]">点「新建任务」，选好方向 / 区域 / 对象 / 时间，研究员就会每天凌晨自动挖掘洞察。</span>
  <button class="btn-soft" @click="newJob()">新建第一个任务</button>
</div>

<div class="grid md:grid-cols-2 xl:grid-cols-3 gap-4" x-show="!loading && jobs.length">
  <template x-for="(j, i) in jobs" :key="j.job_id">
    <div class="card p-4 flex flex-col stagger" :style="'--d:' + (i*35) + 'ms'">
      <div class="flex items-start gap-3">
        <div class="w-9 h-9 rounded-xl grid place-items-center shrink-0 grad-brand text-white">
          <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
        </div>
        <div class="min-w-0 flex-1">
          <h4 class="text-sm font-semibold truncate" x-text="j.name"></h4>
          <p class="text-[11px] text-txt-3 mt-0.5 truncate">
            <span x-text="dirLabel(j.direction)"></span> · 近<span x-text="j.window_days"></span>天
            · <span x-text="j.area || '全屋'"></span> · <span x-text="j.member || '全部成员'"></span>
          </p>
        </div>
        <label class="shrink-0 cursor-pointer" title="启用 / 暂停">
          <input type="checkbox" class="switch" :checked="j.enabled" @change="toggleJob(j, $event.target.checked)">
        </label>
      </div>

      <div class="flex flex-wrap items-center gap-1.5 mt-3">
        <span class="badge badge-brand" x-text="dirLabel(j.direction)"></span>
        <span class="badge badge-mute" x-text="'近' + j.window_days + '天'"></span>
        <span class="badge badge-mute" x-show="j.area" x-text="j.area"></span>
        <span class="badge badge-mute" x-show="j.member" x-text="j.member"></span>
        <span class="badge badge-info" x-text="'预算 ' + (j.budget_tokens||20000) + 'tok'"></span>
      </div>

      <div class="flex items-center justify-between mt-3 pt-3 border-t border-white/5 text-[10px] text-txt-3">
        <span x-text="j.last_run_at ? ('上次运行 ' + fmtTime(j.last_run_at, true)) : '从未运行'"></span>
        <div class="flex gap-1" @click.stop>
          <button class="icon-btn" title="立即运行" @click="runNow(j.job_id)">
            <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 3l14 9-14 9V3z"/></svg>
          </button>
          <button class="icon-btn" title="编辑" @click="editJob(j)">
            <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>
          </button>
          <button class="icon-btn hover:!text-danger" title="删除" @click="remove(j)">
            <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
          </button>
        </div>
      </div>
    </div>
  </template>
</div>

<!-- 运行日志 / 成本 -->
<div class="card p-4 mt-5" x-show="jobs.length">
  <div class="flex items-center gap-2 mb-3">
    <h3 class="font-semibold text-sm">运行日志 / 成本</h3>
    <button class="btn-ghost btn-xs" @click="loadRuns()">刷新</button>
  </div>
  <div class="overflow-x-auto thin-scroll">
    <table class="w-full text-[12px]">
      <thead>
        <tr class="text-txt-3 text-left">
          <th class="py-1.5 pr-3 font-medium">任务</th>
          <th class="py-1.5 pr-3 font-medium">时间</th>
          <th class="py-1.5 pr-3 font-medium">Token</th>
          <th class="py-1.5 pr-3 font-medium">单元</th>
          <th class="py-1.5 pr-3 font-medium">命中</th>
          <th class="py-1.5 font-medium">状态</th>
        </tr>
      </thead>
      <tbody>
        <template x-for="r in runs" :key="r.run_id">
          <tr class="border-t border-white/5">
            <td class="py-1.5 pr-3 font-mono truncate max-w-[140px]" x-text="r.job_id"></td>
            <td class="py-1.5 pr-3 text-txt-2" x-text="fmtTime(r.started_at, true)"></td>
            <td class="py-1.5 pr-3" x-text="r.token_used"></td>
            <td class="py-1.5 pr-3" x-text="(r.units_processed||0) + '/' + (r.units_total||0)"></td>
            <td class="py-1.5 pr-3" x-text="r.hits||0"></td>
            <td class="py-1.5"><span class="badge" :class="r.ok ? 'badge-ok' : 'badge-danger'" x-text="r.ok ? '成功' : '失败'"></span></td>
          </tr>
        </template>
        <tr x-show="!runs.length"><td colspan="6" class="py-3 text-center text-txt-3">暂无运行记录</td></tr>
      </tbody>
    </table>
  </div>
</div>

<!-- 方向洞察质量（v0.8-3 反馈反哺方向/模板权重）-->
<div class="card p-4 mt-4" x-show="jobs.length">
  <div class="flex items-center gap-2 mb-3">
    <h3 class="font-semibold text-sm">方向洞察质量</h3>
    <span class="text-[11px] text-txt-3">由「记忆」页对研究员洞察的 👍👎 汇总而来</span>
  </div>
  <div class="grid grid-cols-2 md:grid-cols-3 gap-2">
    <template x-for="d in directionFeedback" :key="d.direction">
      <div class="card-flat p-3 flex items-center justify-between">
        <div>
          <div class="text-xs font-medium" x-text="dirLabel(d.direction)"></div>
          <div class="text-[10px] text-txt-3" x-text="d.count + ' 条 · 均信任 ' + d.avg_trust"></div>
        </div>
        <div class="text-[11px] flex gap-1.5"><span class="text-ok" x-text="'👍' + d.up"></span><span class="text-danger" x-text="'👎' + d.down"></span></div>
      </div>
    </template>
    <p x-show="!directionFeedback.length" class="text-xs text-txt-3 col-span-full">暂无数据（研究员运行并收到反馈后显示）</p>
  </div>
</div>

<!-- 新建 / 编辑抽屉 -->
<div class="modal-mask" x-show="drawerOpen" x-transition.opacity @click.self="drawerOpen=false" style="display:none">
  <div class="glass rounded-2xl w-full max-w-lg max-h-[90vh] flex flex-col anim-in" x-show="drawerOpen">
    <div class="flex items-center justify-between p-4 border-b border-white/5">
      <h3 class="font-semibold" x-text="editing ? '编辑定向洞察任务' : '新建定向洞察任务'"></h3>
      <button class="icon-btn" @click="drawerOpen=false"><svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
    </div>
    <div class="flex-1 overflow-y-auto thin-scroll p-4 space-y-4">
      <div>
        <label class="lbl">任务名称</label>
        <input class="inp" x-model="form.name" placeholder="如：主卧作息洞察">
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="lbl">方向（有限类别，不开放 LLM 自由探索）</label>
          <select class="inp" x-model="form.direction">
            <template x-for="d in dirOptions" :key="d"><option :value="d" x-text="dirLabel(d)"></option></template>
          </select>
        </div>
        <div>
          <label class="lbl">时间范围（硬上限 ≤30 天）</label>
          <select class="inp" x-model.number="form.window_days">
            <template x-for="w in windowOptions" :key="w"><option :value="w" x-text="'近 ' + w + ' 天'"></option></template>
          </select>
        </div>
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="lbl">区域（逗号分隔，留空=全屋）</label>
          <input class="inp" x-model="form.area" placeholder="如：主卧,客厅">
        </div>
        <div>
          <label class="lbl">对象（逗号分隔成员，留空=全部成员）</label>
          <input class="inp" x-model="form.member" placeholder="如：lidicn">
        </div>
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="lbl">排程（cron，默认每日 03:00）</label>
          <input class="inp font-mono" x-model="form.schedule" placeholder="0 3 * * *">
        </div>
        <div>
          <label class="lbl">单任务 token 预算</label>
          <input class="inp" type="number" min="1000" step="1000" x-model.number="form.budget_tokens">
        </div>
      </div>
      <label class="flex items-center gap-2 cursor-pointer select-none">
        <input type="checkbox" x-model="form.enabled" class="toggle">
        <span class="text-[12px]">启用（参与每日定期洞察）</span>
      </label>

      <div class="card-flat p-3 text-[12px]">
        <div class="flex items-center justify-between">
          <span class="text-txt-3">预计分析单元数</span>
          <span class="font-semibold" :class="previewUnits() > unitCap ? 'text-danger' : 'text-ok'" x-text="previewUnits()"></span>
        </div>
        <p class="text-[10px] text-txt-3 mt-1">= 方向(1) × 区域数(<span x-text="areaCount()"></span>) × 对象数(<span x-text="memberCount()"></span>)。单任务上限 <span x-text="unitCap"></span>，超出会被截断。</p>
      </div>
      <p class="text-[11px] text-amber-400" x-show="previewUnits() > unitCap">⚠ 单元数超限，请缩减区域 / 对象选择，否则多余组合不会运行。</p>
    </div>
    <div class="p-4 border-t border-white/5 flex justify-end gap-2">
      <button class="btn-soft" @click="drawerOpen=false">取消</button>
      <button class="btn-primary" :disabled="saving" @click="save()">
        <span x-show="saving" class="spinner"></span><span x-text="editing ? '保存' : '创建'"></span>
      </button>
    </div>
  </div>
</div>
`;

export function insightJobsPage() {
  return {
    tpl: TPL,
    jobs: [],
    runs: [],
    directionFeedback: [],
    loading: true,
    saving: false,
    drawerOpen: false,
    editing: false,
    gates: { enabled: false, daily_token_budget: 80000, unit_cap: 50 },
    unitCap: 50,
    dirOptions: DIR_OPTIONS,
    windowOptions: WINDOW_OPTIONS,
    form: {},
    fmtTime,
    dirLabel: (d) => DIR_LABEL[d] || d || '未指定',
    areaCount() { return (this.form.area || '').split(',').map((s) => s.trim()).filter(Boolean).length || 1; },
    memberCount() { return (this.form.member || '').split(',').map((s) => s.trim()).filter(Boolean).length || 1; },
    previewUnits() { return this.areaCount() * this.memberCount(); },

    init() {
      this.load();
      this.loadRuns();
      this.loadDirectionFeedback();
    },
    async load() {
      this.loading = true;
      try {
        const d = await api.researcherJobs();
        this.jobs = d.jobs || [];
        if (d.gates) this.gates = Object.assign(this.gates, d.gates);
        if (d.gates && typeof d.gates.unit_cap === 'number') this.unitCap = d.gates.unit_cap;
      } catch (e) {
        this.$store.app.err('任务加载失败：' + e.message);
      } finally {
        this.loading = false;
      }
    },
    async loadRuns() {
      try {
        const d = await api.researcherRuns({ limit: 50 });
        this.runs = d.runs || [];
      } catch (e) { /* 非关键 */ }
    },
    newJob() {
      this.editing = false;
      this.form = {
        name: '', direction: 'rhythm', window_days: 7, area: '', member: '',
        schedule: '0 3 * * *', budget_tokens: 20000, enabled: true
      };
      this.drawerOpen = true;
    },
    editJob(j) {
      this.editing = true;
      this.form = {
        job_id: j.job_id, name: j.name, direction: j.direction || 'rhythm',
        window_days: j.window_days || 7, area: j.area || '', member: j.member || '',
        schedule: j.schedule || '0 3 * * *', budget_tokens: j.budget_tokens || 20000,
        enabled: !!j.enabled
      };
      this.drawerOpen = true;
    },
    async save() {
      if (!(this.form.name || '').trim()) { this.$store.app.warn('请填写任务名称'); return; }
      if (Number(this.form.window_days) > 30) { this.$store.app.warn('时间范围硬上限 30 天'); return; }
      this.saving = true;
      try {
        await api.researcherSaveJob(this.form);
        this.$store.app.ok(this.editing ? '已保存' : '已创建');
        this.drawerOpen = false;
        await this.load();
      } catch (e) {
        this.$store.app.err('保存失败：' + e.message);
      } finally {
        this.saving = false;
      }
    },
    async remove(j) {
      if (!(await this.$store.app.ask('删除任务', '确定删除「' + j.name + '」？'))) return;
      try {
        await api.researcherDeleteJob(j.job_id);
        await this.load();
      } catch (e) { this.$store.app.err('删除失败：' + e.message); }
    },
    async toggleJob(j, enabled) {
      try {
        await api.researcherToggleJob(j.job_id, enabled);
        j.enabled = enabled;
      } catch (e) { this.$store.app.err('切换失败：' + e.message); }
    },
    async toggleGlobal(enabled) {
      try {
        await api.saveConfig({ researcher_enabled: enabled });
        this.gates.enabled = enabled;
        this.$store.app.ok(enabled ? '研究员已开启（每日低峰自动洞察）' : '研究员已关闭');
      } catch (e) {
        this.gates.enabled = enabled;
        this.$store.app.warn('总开关已切换前端状态；服务端持久化需环境变量 RESEARCHER_ENABLED（' + e.message + '）');
      }
    },
    async runNow(jobId) {
      try {
        await api.researcherRunNow(jobId || '');
        this.$store.app.ok('已触发' + (jobId ? '该任务' : '全部任务') + '的洞察运行');
        setTimeout(() => this.loadRuns(), 1500);
      } catch (e) { this.$store.app.err('触发失败：' + e.message); }
    },
    async loadDirectionFeedback() {
      try {
        const d = await api.researcherDirectionFeedback();
        this.directionFeedback = d.directions || [];
      } catch (e) { /* 非关键 */ }
    }
  };
}
