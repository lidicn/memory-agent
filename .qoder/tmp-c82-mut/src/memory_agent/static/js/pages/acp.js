/* ACP 接入页（拓扑 X peer-to-peer）

   与 MCP 页同构，但在「入站」之外额外管理「出站」：
   * 入站：本端作为 ACP server 被 autoflow 调用，地址 /acp，鉴权用 acp_ 令牌。
   * 出站：本端作为 ACP client 主动委派任务给对端 autoflow，
     对端地址 / 令牌来自 config.autoflow_acp_url / autoflow_acp_token（WebUI 可改）。
*/

import { api } from '../api.js';
import { fmtTime, copyText } from '../util.js';

const MASK = '********';

// 入站工具兜底描述（正常以后端 ACP_TOOLS 为准）
const TOOL_DESC = {
  delegate_to_autoflow: '（出站）把一条自然语言任务委派给对端 autoflow 执行'
};

const TPL = `
<!-- 入站：服务信息 -->
<div class="grid lg:grid-cols-3 gap-5">
  <div class="card p-5 lg:col-span-2">
    <div class="flex items-start justify-between mb-4">
      <div>
        <h3 class="font-semibold">ACP 接入服务（入站）</h3>
        <p class="text-[11px] text-txt-3 mt-0.5">让 autoflow 以 Agent Client Protocol 调用本端能力</p>
      </div>
      <span class="badge badge-ok">/acp</span>
    </div>

    <div class="space-y-3">
      <div>
        <label class="lbl">服务端点</label>
        <div class="flex gap-2">
          <input class="inp inp-mono" readonly :value="endpointUrl">
          <button class="btn-ghost shrink-0" @click="copy(endpointUrl)">复制</button>
        </div>
        <p class="hint">传输方式：<span class="font-mono" x-text="info.transport || '—'"></span>，鉴权头 <span class="font-mono">Authorization: Bearer &lt;acp_ Token&gt;</span></p>
      </div>

      <div class="grid grid-cols-3 gap-3">
        <div class="card-flat p-3"><div class="text-[10px] text-txt-3">已注册工具</div><div class="text-lg font-semibold mt-0.5" x-text="(info.tools||[]).length"></div></div>
        <div class="card-flat p-3"><div class="text-[10px] text-txt-3">acp_ 令牌</div><div class="text-lg font-semibold mt-0.5" x-text="info.token_count || 0"></div></div>
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
      <template x-for="t in info.tools || []" :key="t.name">
        <div class="card-flat px-3 py-2">
          <div class="flex items-center gap-1.5">
            <span class="text-[11px] font-mono text-brand-400 truncate" x-text="t.name"></span>
            <span class="badge badge-brand shrink-0" x-show="t.name==='delegate_to_autoflow'">出站</span>
          </div>
          <div class="text-[10px] text-txt-3 mt-0.5" x-text="toolDesc(t)"></div>
        </div>
      </template>
      <div x-show="!(info.tools||[]).length" class="empty py-6">暂无工具</div>
    </div>
  </div>
</div>

<!-- 入站：访问凭证（acp_ 令牌） -->
<div class="card p-5">
  <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
    <div>
      <h3 class="font-semibold">接入凭证（acp_ 令牌）</h3>
      <p class="text-[11px] text-txt-3 mt-0.5">服务端只保存哈希，明文仅在生成时展示一次，供对端 autoflow 调用本端</p>
    </div>
    <div class="flex gap-2">
      <input class="inp w-[200px]" x-model="newName" placeholder="凭证名称，如 autoflow-peer"
             @keydown.enter.prevent="create()">
      <button class="btn-primary shrink-0" @click="create()" :disabled="creating || !newName.trim()">
        <span x-show="creating" class="spinner"></span><span x-text="creating ? '生成中…' : '生成 Token'"></span>
      </button>
    </div>
  </div>

  <div class="overflow-x-auto -mx-5 px-5">
    <table class="tbl" x-show="tokens.length">
      <thead><tr><th>名称</th><th>前缀</th><th>创建时间</th><th>最近调用</th><th>调用次数</th><th class="text-right">操作</th></tr></thead>
      <tbody>
        <template x-for="t in tokens" :key="t.name">
          <tr>
            <td><span class="text-sm" x-text="t.name"></span></td>
            <td class="font-mono text-xs text-txt-2" x-text="(t.prefix || '—') + '…'"></td>
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
      <span>还没有签发任何 acp_ Token</span>
      <span class="text-[11px]">先取个名字，点右上角「生成 Token」</span>
    </div>
  </div>
</div>

<!-- 出站：对端 autoflow 委派配置 -->
<div class="card p-5">
  <div class="flex items-start justify-between mb-4">
    <div>
      <h3 class="font-semibold">出站委派对端（autoflow）</h3>
      <p class="text-[11px] text-txt-3 mt-0.5">本端作为 ACP client 主动委派任务时连接的对端地址与令牌</p>
    </div>
    <span class="badge" :class="peerConfigured ? 'badge-ok' : 'badge-warn'" x-text="peerConfigured ? '已配置' : '未配置'"></span>
  </div>

  <div class="grid md:grid-cols-2 gap-4">
    <div>
      <label class="lbl">对端 ACP URL</label>
      <input class="inp" x-model="peerUrl" placeholder="http://autoflow:8080">
      <p class="hint">对端 base 地址，连接时自动补 <span class="font-mono">/acp</span></p>
    </div>
    <div>
      <label class="lbl">对端 ACP Token</label>
      <input class="inp inp-mono" type="password" x-model="peerToken" placeholder="留空表示沿用已保存的令牌"
             autocomplete="new-password">
      <p class="hint" x-show="peerToken === '${MASK}'">已是已保存令牌，留空即为不修改</p>
    </div>
  </div>

  <div class="flex items-center gap-2 mt-4">
    <button class="btn-primary" @click="savePeer()" :disabled="peerSaving">
      <span x-show="peerSaving" class="spinner"></span><span x-text="peerSaving ? '保存中…' : '保存对端配置'"></span>
    </button>
    <button class="btn-ghost" @click="testPeer()" :disabled="peerTesting || !peerUrl.trim()">
      <span x-show="peerTesting" class="spinner"></span><span x-text="peerTesting ? '测试中…' : '测试连通性'"></span>
    </button>
  </div>

  <div x-show="peerTestResult" class="card-flat p-3 text-[11px] mt-3"
       :class="peerTestOk ? 'text-ok' : 'text-danger'" x-text="peerTestResult"></div>
</div>

<!-- 入站：客户端调用示例 -->
<div class="card p-5">
  <div class="flex items-center justify-between mb-4">
    <div>
      <h3 class="font-semibold">autoflow 调用本端示例</h3>
      <p class="text-[11px] text-txt-3 mt-0.5">把下面的片段贴进对端 autoflow 的 ACP 客户端配置，替换其中的 Token</p>
    </div>
  </div>
  <div class="relative">
    <button class="btn-ghost btn-xs absolute right-2 top-2 z-10" @click="copy(inboundSnippet)">复制</button>
    <div class="snippet thin-scroll max-h-[220px]" x-text="inboundSnippet || '// ACP 服务未就绪'"></div>
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
        <h3 class="font-semibold">acp_ Token 已生成</h3>
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
        <label class="lbl">autoflow 客户端调用片段</label>
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

export function acpPage() {
  return {
    tpl: TPL,
    info: { tools: [] },
    tokens: [],
    newName: '',
    creating: false,
    testing: false,
    testResult: '',
    testOk: false,
    created: null,

    // 出站对端配置
    peerUrl: '',
    peerToken: '',
    peerSaving: false,
    peerTesting: false,
    peerTestResult: '',
    peerTestOk: false,

    fmtTime,

    get endpointUrl() {
      return (window.location.origin || '') + (this.info.endpoint || '/acp');
    },
    get peerConfigured() {
      return !!this.peerUrl.trim() && !!(this.info.outbound && this.info.outbound.configured);
    },
    get inboundSnippet() {
      const url = (window.location.origin || '') + '/acp';
      return [
        'curl -N -X POST ' + url + ' \\',
        '  -H "Authorization: Bearer acp_<TOKEN>" \\',
        '  -H "Content-Type: application/json" \\',
        "  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}'"
      ].join('\n');
    },
    get createdSnippet() {
      const token = this.created ? this.created.token : '';
      const url = (window.location.origin || '') + '/acp';
      return [
        'curl -N -X POST ' + url + ' \\',
        '  -H "Authorization: Bearer ' + token + '" \\',
        '  -H "Content-Type: application/json" \\',
        "  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}'"
      ].join('\n');
    },

    init() { this.load(); },

    async load() {
      try {
        const [i, t] = await Promise.all([api.acpInfo(), api.acpTokens()]);
        this.info = i;
        this.tokens = t.tokens || [];
        this.peerUrl = (i.outbound && i.outbound.autoflow_url) || '';
      } catch (e) {
        this.$store.app.err('ACP 信息加载失败：' + e.message);
      }
      // 对端令牌为密钥，仅拉取一次用于「是否已配置」判断，不回显明文
      try {
        const cfg = await api.getConfig();
        this.peerUrl = cfg.autoflow_acp_url || this.peerUrl || '';
        this.peerToken = !cfg.autoflow_acp_token
          ? ''
          : (cfg.autoflow_acp_token === MASK ? MASK : cfg.autoflow_acp_token);
      } catch (e) { /* 配置读取失败不阻断；下次保存时再试 */ }
    },

    toolDesc(t) {
      return (t && t.description) || TOOL_DESC[t && t.name] || 'ACP 工具';
    },

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
        const d = await api.acpCreateToken(name);
        this.created = d;
        this.newName = '';
        this.$store.app.ok(d.message || 'acp_ Token 已生成');
        this.load();
        this.info.token_count = (this.info.token_count || 0) + 1;
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.creating = false;
      }
    },

    closeCreated() { this.created = null; },

    async revoke(t) {
      const yes = await this.$store.app.ask(
        '撤销 acp_ Token',
        '撤销后使用「' + t.name + '」的对端将立即失去调用本端的权限，确定继续？',
        '撤销'
      );
      if (!yes) return;
      try {
        const d = await api.acpRevokeToken(t.name);
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
        const d = await api.acpSelftest();
        this.testOk = true;
        const steps = (d.steps || []).map((s) => (s.ok ? '✓' : '✗') + s.step).join('；');
        this.testResult = (d.summary || 'ACP 自检通过') + (steps ? '（' + steps + '）' : '');
        this.$store.app.ok('ACP 握手正常');
      } catch (e) {
        this.testOk = false;
        this.testResult = e.message;
        this.$store.app.err('ACP 自检失败');
      } finally {
        this.testing = false;
      }
    },

    async savePeer() {
      if (!this.peerUrl.trim()) {
        this.$store.app.err('请填写对端 ACP URL');
        return;
      }
      this.peerSaving = true;
      try {
        const patch = { autoflow_acp_url: this.peerUrl.trim() };
        if (this.peerToken && this.peerToken !== MASK) {
          patch.autoflow_acp_token = this.peerToken;
        }
        const d = await api.saveConfig(patch);
        this.peerToken = MASK; // 保存后回显为掩码，避免明文残留
        this.info.outbound = {
          autoflow_url: patch.autoflow_acp_url,
          configured: !!patch.autoflow_acp_token || (this.info.outbound && this.info.outbound.configured)
        };
        this.$store.app.ok(d.message || '出站对端配置已保存');
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.peerSaving = false;
      }
    },

    async testPeer() {
      if (!this.peerUrl.trim()) {
        this.$store.app.err('请填写对端 ACP URL');
        return;
      }
      this.peerTesting = true;
      this.peerTestResult = '';
      try {
        const payload = { url: this.peerUrl.trim() };
        if (this.peerToken && this.peerToken !== MASK) payload.token = this.peerToken;
        const d = await api.acpTestOutbound(payload);
        this.peerTestOk = true;
        this.peerTestResult = d.detail || '对端 ACP 可达';
      } catch (e) {
        this.peerTestOk = false;
        this.peerTestResult = e.message;
        this.$store.app.err('出站连通性测试失败');
      } finally {
        this.peerTesting = false;
      }
    }
  };
}
