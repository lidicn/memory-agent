/* MCP 接入页：服务信息 / Token 生成与撤销 / 客户端配置片段 / 握手自检 */

import { api } from '../api.js';
import { fmtTime, copyText } from '../util.js';

/* 兜底描述。正常情况下走后端 describe() 返回的 catalog，
   保证「页面上写的」与「MCP 里注册的」永远是同一份元数据。 */
const TOOL_DESC = {
  help: '工具索引与用法示例',
  get_entity_catalog: '设备目录：友好名 / 房间 / 类别 / 最后在线',
  get_behavior_insights: '服务端直出行为洞察报告',
  get_device_usage: '设备开关时长与次数统计',
  search_events: '按房间与设备类别语义搜索事件',
  save_analysis_template: '保存行为洞察模板',
  list_analysis_templates: '列出全部洞察模板',
  export_insight: '导出单个洞察详情',
  delete_analysis_template: '删除自定义模板',
  get_behavior_summary: '获取指定区间的行为画像',
  get_person_history: '查询某成员的历史轨迹',
  query_events: '按房间/实体/时间检索事件',
  list_rooms_entities: '列出房间与实体清单',
  get_collect_status: '查询采集运行状态',
  trigger_collection: '触发一次增量采集',
  export_history: '导出原始事件历史'
};

const GROUP_BADGE = {
  入口: 'badge-brand', 洞察: 'badge-ok', 精确: 'badge-mute',
  沉淀: 'badge-warn', 运维: 'badge-mute'
};

const CLIENTS = [
  { key: 'claude', label: 'Claude Desktop' },
  { key: 'cursor', label: 'Cursor' },
  { key: 'opencode', label: 'OpenCode' }
];

const TPL = `
<!-- 服务信息 -->
<div class="grid lg:grid-cols-3 gap-5">
  <div class="card p-5 lg:col-span-2">
    <div class="flex items-start justify-between mb-4">
      <div>
        <h3 class="font-semibold">MCP 服务</h3>
        <p class="text-[11px] text-txt-3 mt-0.5">让外部 AI Agent 以标准协议读写这个家的行为记忆</p>
      </div>
      <span class="badge" :class="info.available ? 'badge-ok' : 'badge-danger'"
            x-text="info.available ? '运行中' : '不可用'"></span>
    </div>

    <div x-show="!info.available && info.error" class="card-flat p-3 mb-4 text-[11px] text-danger" x-text="info.error"></div>

    <div class="space-y-3">
      <div>
        <label class="lbl">服务端点</label>
        <div class="flex gap-2">
          <input class="inp inp-mono" readonly :value="info.url || ''">
          <button class="btn-ghost shrink-0" @click="copy(info.url)">复制</button>
        </div>
        <p class="hint">传输方式：<span class="font-mono" x-text="info.transport || '—'"></span>，鉴权头 <span class="font-mono">Authorization: Bearer &lt;Token&gt;</span></p>
      </div>

      <div class="grid grid-cols-3 gap-3">
        <div class="card-flat p-3"><div class="text-[10px] text-txt-3">可用工具</div><div class="text-lg font-semibold mt-0.5" x-text="(info.tools||[]).length"></div></div>
        <div class="card-flat p-3"><div class="text-[10px] text-txt-3">有效 Token</div><div class="text-lg font-semibold mt-0.5" x-text="info.token_count || 0"></div></div>
        <div class="card-flat p-3">
          <div class="text-[10px] text-txt-3">握手自检</div>
          <button class="text-xs mt-1 text-brand-400 hover:text-brand-300" @click="selftest()" :disabled="testing">
            <span x-show="testing" class="spinner"></span><span x-text="testing ? '检测中' : '立即检测'"></span>
          </button>
        </div>
      </div>

      <div x-show="testResult" class="card-flat p-3 text-[11px]"
           :class="testOk ? 'text-ok' : 'text-danger'" x-text="testResult"></div>
    </div>
  </div>

  <div class="card p-5">
    <h3 class="font-semibold mb-3">工具清单</h3>
    <div class="space-y-1.5 max-h-[260px] overflow-y-auto thin-scroll pr-1">
      <template x-for="t in info.tools || []" :key="t">
        <div class="card-flat px-3 py-2">
          <div class="flex items-center gap-1.5">
            <span class="text-[11px] font-mono text-brand-400 truncate" x-text="t"></span>
            <span class="badge shrink-0" :class="groupBadge(t)" x-show="toolGroup(t)" x-text="toolGroup(t)"></span>
          </div>
          <div class="text-[10px] text-txt-3 mt-0.5" x-text="toolDesc(t)"></div>
          <div class="text-[10px] text-txt-4 mt-0.5 font-mono truncate" x-show="toolExample(t)" x-text="toolExample(t)"></div>
        </div>
      </template>
      <div x-show="!(info.tools||[]).length" class="empty py-6">暂无工具</div>
    </div>
  </div>
</div>

<!-- Token 管理 -->
<div class="card p-5">
  <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
    <div>
      <h3 class="font-semibold">访问凭证</h3>
      <p class="text-[11px] text-txt-3 mt-0.5">服务端只保存哈希，明文仅在生成时展示一次</p>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <select class="inp w-[150px]" x-model="newKind">
        <option value="mcp">MCP</option>
        <option value="acp">ACP（拓扑 X 对等）</option>
        <option value="arena">竞技场（AutoFlow）</option>
      </select>
      <select class="inp w-[168px]" x-model="newScopes" :disabled="newKind !== 'mcp'"
              title="新令牌默认只读；写工具（建成员/写记忆/改模板/触发采集等）需带 write">
        <option value="read">只读（read）</option>
        <option value="read,write">读写（read + write）</option>
      </select>
      <input class="inp w-[200px]" x-model="newName" placeholder="凭证名称，如 autoflow-arena"
             @keydown.enter.prevent="create()">
      <button class="btn-primary shrink-0" @click="create()" :disabled="creating || !newName.trim()">
        <span x-show="creating" class="spinner"></span><span x-text="creating ? '生成中…' : '生成 Token'"></span>
      </button>
    </div>
    <p class="hint mt-2">选 <span class="font-mono">竞技场（AutoFlow）</span> 生成 <span class="font-mono">arena</span> 类型令牌：仅能调用 3 个竞技场工具 + 快照接口，与生产 / 管家令牌隔离。把生成的令牌交给 AutoFlow 作为 <span class="font-mono">MEMORY_AGENT_ACP_TOKEN</span> 即可。</p>
    <p class="hint mt-1">🔒 <b>新令牌默认只读</b>：写类工具（创建成员 / 写回标签与记忆 / 保存模板 / 触发采集等 17 个）需要 <span class="font-mono">write</span> 权限，否则服务端返回 <span class="font-mono">DENIED</span>。存量令牌沿用读写全权，可随时收紧。</p>
  </div>

  <div class="overflow-x-auto -mx-5 px-5">
    <table class="tbl" x-show="tokens.length">
      <thead><tr><th>名称</th><th>前缀</th><th>权限</th><th>创建时间</th><th>最近调用</th><th>调用次数</th><th class="text-right">操作</th></tr></thead>
      <tbody>
        <template x-for="t in tokens" :key="t.name">
          <tr>
            <td>
              <span class="text-sm" x-text="t.name"></span>
              <span class="badge badge-warn ml-1.5" x-show="t.migrated" title="由旧版明文配置迁移而来，建议重新签发">已迁移</span>
            </td>
            <td class="font-mono text-xs text-txt-2">
              <span class="badge mr-1" :class="(t.kind||'mcp')==='arena' ? 'badge-warn' : (t.kind||'mcp')==='acp' ? 'badge-brand' : 'badge-mute'" x-text="t.kind || 'mcp'"></span>
              <span x-text="(t.prefix || '—') + '…'"></span>
            </td>
            <td>
              <button class="badge" :class="hasWrite(t) ? 'badge-brand' : 'badge-mute'"
                      :title="(t.scopes_inherited ? '存量令牌：默认沿用读写全权。' : '') + '点击切换 read / read+write'"
                      @click="toggleScopes(t)" x-text="hasWrite(t) ? '读写' : '只读'"></button>
              <span class="badge badge-warn ml-1" x-show="t.scopes_inherited" title="未显式设置过权限，沿用读写全权">继承</span>
            </td>
            <td class="text-xs text-txt-3" x-text="fmtTime(t.created_at, true)"></td>
            <td class="text-xs" :class="t.last_used_at ? 'text-txt-2' : 'text-txt-4'">
              <span x-text="t.last_used_at ? fmtTime(t.last_used_at, true) : '从未调用'"></span>
              <span class="text-[10px] text-txt-4 ml-1" x-show="t.last_used_at" x-text="'（' + relTime(t.last_used_at) + '）'"></span>
            </td>
            <td class="text-xs text-txt-3 font-mono" x-text="t.use_count || 0"></td>
            <td class="text-right"><button class="btn-danger btn-xs" @click="revoke(t)">撤销</button></td>
          </tr>
        </template>
      </tbody>
    </table>
    <div x-show="!tokens.length" class="empty">
      <span>还没有签发任何 MCP Token</span>
      <span class="text-[11px]">先取个名字，点右上角「生成 Token」</span>
    </div>
  </div>
</div>

<!-- 调用审计（v0.7.5） -->
<div class="card p-5">
  <div class="flex flex-wrap items-center justify-between gap-3 mb-3">
    <div>
      <h3 class="font-semibold">调用审计</h3>
      <p class="text-[11px] text-txt-3 mt-0.5">谁 / 何时 / 调了哪个工具 / 耗时 / 成败（落库保留 30 天）</p>
    </div>
    <div class="flex items-center gap-2">
      <label class="flex items-center gap-1.5 text-xs cursor-pointer">
        <div class="switch scale-75" :class="auditFailedOnly && 'on'" @click="auditFailedOnly = !auditFailedOnly; loadAudit()"></div>
        <span>只看失败</span>
      </label>
      <button class="btn-ghost btn-xs" @click="loadAudit()">刷新</button>
    </div>
  </div>
  <div class="overflow-x-auto -mx-5 px-5 max-h-[360px] overflow-y-auto">
    <table class="tbl" x-show="audit.length">
      <thead><tr><th>时间</th><th>令牌</th><th>工具</th><th>权限</th><th>耗时</th><th>结果</th></tr></thead>
      <tbody>
        <template x-for="(a, i) in audit" :key="a.id || i">
          <tr>
            <td class="text-xs text-txt-3 whitespace-nowrap" x-text="fmtTime(a.ts, true)"></td>
            <td class="text-xs" x-text="a.token_name || '—'"></td>
            <td class="font-mono text-xs text-txt-2" x-text="a.tool"></td>
            <td><span class="badge" :class="a.scope === 'write' ? 'badge-warn' : 'badge-mute'" x-text="a.scope || 'read'"></span></td>
            <td class="text-xs font-mono text-txt-3" x-text="Math.round(a.duration_ms) + 'ms'"></td>
            <td>
              <span class="badge" :class="a.ok ? 'badge-ok' : 'badge-danger'" x-text="a.ok ? '成功' : '失败'"></span>
              <span class="text-[10px] text-txt-3 ml-1" x-show="a.error" x-text="(a.error || '').slice(0, 60)"></span>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <div x-show="!audit.length" class="empty">
      <span>暂无调用记录</span>
      <span class="text-[11px]">Agent 调用任一工具后即可在这里追溯</span>
    </div>
  </div>
</div>

<!-- 客户端配置 -->
<div class="card p-5">
  <div class="flex items-center justify-between mb-4">
    <div>
      <h3 class="font-semibold">客户端接入</h3>
      <p class="text-[11px] text-txt-3 mt-0.5">把下面的片段贴进对应客户端的 MCP 配置，替换其中的 Token</p>
    </div>
    <div class="tabs">
      <template x-for="c in clients" :key="c.key">
        <button class="tab" :class="client===c.key && 'tab-active'" @click="client=c.key" x-text="c.label"></button>
      </template>
    </div>
  </div>
  <div class="relative">
    <button class="btn-ghost btn-xs absolute right-2 top-2 z-10" @click="copy(snippet)">复制</button>
    <div class="snippet thin-scroll" x-text="snippet || '// MCP 服务未就绪'"></div>
  </div>
</div>

<!-- 新建 Token 弹层 -->
<div class="modal-mask" x-show="created" x-transition.opacity @click.self="closeCreated()" style="display:none">
  <div class="glass rounded-2xl w-full max-w-xl anim-in" x-show="created">
    <div class="flex items-center gap-3 p-5 border-b border-white/5">
      <div class="w-10 h-10 rounded-xl grad-ok grid place-items-center shrink-0">
        <svg viewBox="0 0 24 24" class="w-5 h-5" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2M17 6l3 3M14 9l3 3"/></svg>
      </div>
      <div>
        <h3 class="font-semibold">Token 已生成</h3>
        <p class="text-[11px] text-warn mt-0.5">明文只显示这一次，关闭后无法找回，请立即保存</p>
      </div>
    </div>

    <div class="p-5 space-y-4">
      <div>
        <label class="lbl">名称</label>
        <div class="text-sm" x-text="created && created.name"></div>
      </div>
      <div>
        <label class="lbl">Token</label>
        <div class="flex gap-2">
          <input class="inp inp-mono" readonly :value="created && created.token" @focus="$event.target.select()">
          <button class="btn-primary shrink-0" @click="copy(created && created.token)">复制</button>
        </div>
      </div>
      <div>
        <label class="lbl">客户端配置片段</label>
        <div class="tabs mb-2">
          <template x-for="c in clients" :key="c.key">
            <button class="tab" :class="createdClient===c.key && 'tab-active'" @click="createdClient=c.key" x-text="c.label"></button>
          </template>
        </div>
        <div class="relative">
          <button class="btn-ghost btn-xs absolute right-2 top-2 z-10" @click="copy(createdSnippet)">复制</button>
          <div class="snippet thin-scroll max-h-[220px]" x-text="createdSnippet"></div>
        </div>
      </div>
    </div>

    <div class="flex justify-end gap-2 p-4 border-t border-white/5">
      <button class="btn-primary" @click="closeCreated()">我已保存</button>
    </div>
  </div>
</div>
`;

export function mcpPage() {
  return {
    tpl: TPL,
    info: { tools: [], snippets: {} },
    tokens: [],
    clients: CLIENTS,
    client: 'claude',
    createdClient: 'claude',
    newName: '',
    newKind: 'mcp',
    newScopes: 'read',
    creating: false,
    testing: false,
    testResult: '',
    testOk: false,
    created: null,
    audit: [],
    auditFailedOnly: false,

    fmtTime,

    get snippet() {
      return (this.info.snippets || {})[this.client] || '';
    },
    get createdSnippet() {
      return this.created ? ((this.created.snippets || {})[this.createdClient] || '') : '';
    },

    init() { this.load(); this.loadAudit(); },

    async load() {
      try {
        const [i, t] = await Promise.all([api.mcpInfo(), api.mcpTokens()]);
        this.info = i;
        this.tokens = t.tokens || [];
      } catch (e) {
        this.$store.app.err('MCP 信息加载失败：' + e.message);
      }
    },

    async loadAudit() {
      try {
        const d = await api.mcpAudit({ limit: 100, failed: this.auditFailedOnly ? 1 : 0 });
        this.audit = d.audit || [];
      } catch (e) { this.audit = []; }
    },

    hasWrite(t) {
      return (t.scopes || []).includes('write');
    },

    async toggleScopes(t) {
      const next = this.hasWrite(t) ? ['read'] : ['read', 'write'];
      try {
        await api.mcpUpdateScopes(t.name, next);
        this.$store.app.ok(`已把「${t.name}」权限设为 ${next.includes('write') ? '读写' : '只读'}`);
        await this.load();
      } catch (e) {
        this.$store.app.err(e.message || '权限更新失败');
      }
    },

    _cat(t) { return (this.info.catalog || []).find((x) => x.name === t) || null; },
    toolDesc(t) { return (this._cat(t) || {}).summary || TOOL_DESC[t] || '扩展工具'; },
    toolGroup(t) { return (this._cat(t) || {}).group || ''; },
    toolExample(t) { return (this._cat(t) || {}).example || ''; },
    groupBadge(t) { return GROUP_BADGE[this.toolGroup(t)] || 'badge-muted'; },

    /** 相对时间。「3 分钟前」比一串裸时间戳更能说明 MCP 是不是真在被调用 */
    relTime(iso) {
      if (!iso) return '';
      const t = Date.parse(String(iso).replace(' ', 'T'));
      if (!isFinite(t)) return '';
      const diff = Math.round((Date.now() - t) / 1000);
      if (diff < 0) return '刚刚';
      if (diff < 60) return diff + ' 秒前';
      if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
      if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
      return Math.floor(diff / 86400) + ' 天前';
    },

    async copy(text) {
      if (!text) return;
      const okFlag = await copyText(text);
      okFlag ? this.$store.app.ok('已复制') : this.$store.app.err('复制失败，请手动选择');
    },

    async create() {
      const name = this.newName.trim();
      if (!name) return;
      this.creating = true;
      try {
        const scopes = (this.newKind === 'mcp' ? this.newScopes || 'read' : 'read,write').split(',');
        const d = await api.mcpCreateToken(name, this.newKind, undefined, scopes);
        this.created = d;
        this.createdClient = this.client;
        this.newName = '';
        this.$store.app.ok(d.message || 'Token 已生成');
        this.load();
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.creating = false;
      }
    },

    closeCreated() { this.created = null; },

    async revoke(t) {
      const yes = await this.$store.app.ask(
        '撤销 Token',
        '撤销后使用「' + t.name + '」的客户端将立即失去访问权限，确定继续？',
        '撤销'
      );
      if (!yes) return;
      try {
        const d = await api.mcpRevokeToken(t.name);
        this.$store.app.ok(d.message || '已撤销');
        this.load();
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    async selftest() {
      this.testing = true;
      this.testResult = '';
      try {
        const d = await api.mcpSelftest();
        this.testOk = true;
        this.testResult = (d.message || '握手成功') +
          '，协议版本 ' + (d.protocol_version || '未知') +
          '，注册工具 ' + (d.tools_count || 0) + ' 个';
        this.$store.app.ok('MCP 握手正常');
      } catch (e) {
        this.testOk = false;
        this.testResult = e.message;
        this.$store.app.err('MCP 自检失败');
      } finally {
        this.testing = false;
      }
    }
  };
}
