/* AI 助手页：通用对话 + 行为分析助手（均为 SSE 流式） */

import { api } from '../api.js';
import { fmtNum, renderMarkdown, todayStr, copyText } from '../util.js';

const CAT_LABEL = {
  sleep: '作息', meal: '饮食', hygiene: '清洁', appliance: '电器',
  security: '安防', energy: '能耗', comfort: '舒适',   other: '其他'
};

// 通用对话历史持久化到 localStorage：重启网关 / 刷新页面不丢记录
const CHAT_KEY = 'mw.chat.messages';

const TPL = `
<div class="flex flex-col gap-5 h-[calc(100vh-8.5rem)]">

  <!-- 顶部工具条 -->
  <div class="flex flex-wrap items-center gap-3 shrink-0">
    <div class="tabs">
      <button class="tab" :class="tab==='chat' && 'tab-active'" @click="tab='chat'">通用对话</button>
      <button class="tab" :class="tab==='analyze' && 'tab-active'" @click="tab='analyze'">行为分析助手</button>
    </div>

    <div class="ml-auto flex items-center gap-2">
      <span class="badge" :class="meta.configured ? 'badge-ok' : 'badge-danger'"
            x-text="meta.configured ? '模型已就绪' : '未配置 API Key'"></span>
      <select class="sel w-[190px] py-1.5 text-xs" x-model="model">
        <template x-for="m in meta.models" :key="m"><option :value="m" x-text="m"></option></template>
      </select>
      <button class="btn-ghost btn-xs" @click="tab==='chat' ? clearChat() : resetAnalyze()">清空</button>
    </div>
  </div>

  <!-- ══════ 通用对话 ══════ -->
  <div x-show="tab==='chat'" class="card flex-1 min-h-0 flex flex-col overflow-hidden">
    <div class="flex-1 overflow-y-auto thin-scroll p-5 space-y-5" x-ref="chatBox">

      <div x-show="!messages.length" class="empty h-full">
        <div class="w-14 h-14 rounded-2xl grad-brand grid place-items-center mb-1">
          <svg viewBox="0 0 24 24" class="w-7 h-7" fill="none" stroke="white" stroke-width="1.8" stroke-linecap="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
        </div>
        <p class="text-sm text-txt-2">问点什么吧，它了解这个家</p>
        <div class="flex flex-wrap gap-2 justify-center mt-2 max-w-[560px]">
          <template x-for="s in samples" :key="s">
            <button class="badge badge-mute cursor-pointer hover:text-txt-1" @click="input=s; send()" x-text="s"></button>
          </template>
        </div>
      </div>

      <template x-for="(m, i) in messages" :key="i">
        <div class="flex" :class="m.role==='user' ? 'justify-end' : 'justify-start'">
          <div :class="m.role==='user' ? 'bubble bubble-user' : 'bubble bubble-bot'" class="anim-in">
            <div x-show="m.reasoning" class="mb-2">
              <button class="text-[11px] text-txt-3 hover:text-txt-2 flex items-center gap-1"
                      @click="m.showReasoning = !m.showReasoning">
                <svg class="w-3 h-3 transition-transform" :class="m.showReasoning && 'rotate-90'"
                     viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M9 18l6-6-6-6"/></svg>
                思考过程
              </button>
              <div x-show="m.showReasoning" class="reasoning-box mt-1.5 thin-scroll" x-text="m.reasoning"></div>
            </div>
            <div x-show="m.role==='user'" class="text-[13.5px] leading-relaxed whitespace-pre-wrap" x-text="m.content"></div>
            <div x-show="m.role!=='user'" class="md" :class="m.streaming && !m.content && 'blink-cursor'" x-html="md(m.content)"></div>
            <div x-show="m.role!=='user' && m.truncated" class="text-[11px] mt-2" style="color:#f59e0b">输出被截断，未能形成完整结论。请尝试把问题拆小或换用非 reasoning 模型。</div>
            <div x-show="m.role!=='user' && !m.streaming && m.usage" class="text-[10px] text-txt-3 mt-2 pt-2 border-t border-white/5"
                 x-text="usageText(m.usage)"></div>
          </div>
        </div>
      </template>
    </div>

    <div class="shrink-0 border-t border-white/5 p-4">
      <div class="flex gap-3 items-end">
        <textarea class="ta flex-1 min-h-[46px] max-h-[160px]" rows="1" x-model="input"
                  placeholder="问问家里的作息、能耗或异常…（Enter 发送，Shift+Enter 换行）"
                  @keydown.enter.prevent="if(!$event.shiftKey) send()"
                  @input="autoGrow($event.target)"></textarea>
        <button x-show="!sending" class="btn-primary h-[42px]" @click="send()" :disabled="!input.trim()">发送</button>
        <button x-show="sending" class="btn-danger h-[42px]" @click="stop()">停止</button>
      </div>
    </div>
  </div>

  <!-- ══════ 行为分析 ══════ -->
  <div x-show="tab==='analyze'" class="flex-1 min-h-0 grid lg:grid-cols-5 gap-5 overflow-hidden">

    <!-- 参数 -->
    <div class="card p-5 lg:col-span-2 overflow-y-auto thin-scroll">
      <h3 class="font-semibold mb-4">分析范围</h3>

      <div class="tabs w-full mb-4">
        <button class="tab flex-1" :class="af.mode==='days' && 'tab-active'" @click="af.mode='days'">最近 N 天</button>
        <button class="tab flex-1" :class="af.mode==='range' && 'tab-active'" @click="af.mode='range'">自定义区间</button>
      </div>

      <div x-show="af.mode==='days'" class="mb-4">
        <label class="lbl">天数</label>
        <div class="flex gap-1.5">
          <template x-for="d in [3,7,14,30]" :key="d">
            <button class="badge cursor-pointer" :class="af.days===d ? 'badge-brand' : 'badge-mute'"
                    @click="af.days=d" x-text="d + ' 天'"></button>
          </template>
        </div>
      </div>

      <div x-show="af.mode==='range'" class="grid grid-cols-2 gap-3 mb-4">
        <div><label class="lbl">开始</label><input type="date" class="inp" x-model="af.start_day"></div>
        <div><label class="lbl">结束</label><input type="date" class="inp" x-model="af.end_day"></div>
      </div>

      <div class="mb-4">
        <label class="lbl">房间（不选＝全部）</label>
        <div class="flex flex-wrap gap-1.5 max-h-[90px] overflow-y-auto thin-scroll">
          <template x-for="r in rooms" :key="r">
            <button class="badge cursor-pointer" :class="af.rooms.includes(r) ? 'badge-brand' : 'badge-mute'"
                    @click="toggleRoom(r)" x-text="r"></button>
          </template>
          <span x-show="!rooms.length" class="text-[11px] text-txt-3">暂无房间数据</span>
        </div>
      </div>

      <div class="mb-4">
        <label class="lbl">关注成员（可留空）</label>
        <input class="inp" x-model="af.person" placeholder="例如：爸爸 / 小孩">
      </div>

      <div class="mb-4">
        <label class="lbl">分析侧重点</label>
        <textarea class="ta" x-model="af.focus" rows="3"
                  placeholder="例如：找出晚间照明和空调的联动规律，给出可直接落到 Node-RED 的建议"></textarea>
      </div>

      <div class="flex gap-2">
        <button class="btn-ghost flex-1 justify-center" @click="preview()" :disabled="previewing || analyzing">
          <span x-show="previewing" class="spinner"></span><span>预览数据量</span>
        </button>
        <button x-show="!analyzing" class="btn-primary flex-1 justify-center" @click="runAnalyze()">开始分析</button>
        <button x-show="analyzing" class="btn-danger flex-1 justify-center" @click="stopAnalyze()">停止</button>
      </div>

      <div x-show="digest" class="card-flat mt-4 p-3 text-[11px] space-y-1">
        <div class="flex justify-between"><span class="text-txt-3">时间范围</span><span class="font-mono" x-text="digest && (digest.range ? digest.range.start + ' ~ ' + digest.range.end : '')"></span></div>
        <div class="flex justify-between"><span class="text-txt-3">事件总量</span><span class="font-mono" x-text="digest && fmtNum(digest.total_events)"></span></div>
        <div class="flex justify-between"><span class="text-txt-3">覆盖天数</span><span class="font-mono" x-text="digest && digest.days_covered"></span></div>
        <div class="flex justify-between" x-show="digest && digest.digest_chars">
          <span class="text-txt-3">上下文规模</span><span class="font-mono" x-text="digest && (digest.digest_chars + ' 字符')"></span>
        </div>
      </div>
    </div>

    <!-- 输出 -->
    <div class="card lg:col-span-3 flex flex-col overflow-hidden">
      <div class="flex items-center justify-between px-5 py-3.5 border-b border-white/5 shrink-0">
        <h3 class="font-semibold text-sm">分析结果</h3>
        <div class="flex items-center gap-2">
          <span x-show="analyzing" class="chip-live"><span class="w-1.5 h-1.5 rounded-full bg-ok pulse-dot"></span><span class="text-[11px]">生成中</span></span>
          <button class="btn-ghost btn-xs" x-show="output" @click="copyOut()">复制</button>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto thin-scroll p-5" x-ref="outBox">
        <div x-show="!output && !analyzing" class="empty h-full">
          <span>配置左侧范围后点「开始分析」</span>
          <span class="text-[11px]">分析会先把事件压缩成数字画像，再交给模型，不会把原始明细全量外发</span>
        </div>

        <div x-show="reasoning" class="mb-4">
          <button class="text-[11px] text-txt-3 hover:text-txt-2 flex items-center gap-1" @click="showReasoning=!showReasoning">
            <svg class="w-3 h-3 transition-transform" :class="showReasoning && 'rotate-90'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M9 18l6-6-6-6"/></svg>
            思考过程
          </button>
          <div x-show="showReasoning" class="reasoning-box mt-1.5 thin-scroll" x-text="reasoning"></div>
        </div>

        <div class="md" :class="analyzing && !output && 'blink-cursor'" x-html="md(output)"></div>
      </div>

      <!-- 结构化洞察 -->
      <div x-show="insight" class="shrink-0 border-t border-white/5 p-4 bg-brand-500/5">
        <div class="flex items-start gap-3">
          <div class="w-9 h-9 rounded-xl grad-ok grid place-items-center shrink-0">
            <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M20 6L9 17l-5-5"/></svg>
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="text-sm font-semibold" x-text="insight && insight.name"></span>
              <span class="badge badge-brand" x-text="insight && catLabel(insight.category)"></span>
              <span class="badge badge-mute" x-text="insight && ('置信度 ' + Math.round((insight.confidence||0)*100) + '%')"></span>
              <span class="badge badge-mute" x-text="insight && ((insight.entities||[]).length + ' 个实体')"></span>
            </div>
            <p class="text-[11px] text-txt-3 mt-1 line-clamp-2" x-text="insight && insight.description"></p>
          </div>
          <button class="btn-primary btn-xs shrink-0" @click="saveInsight()" :disabled="savingInsight">
            <span x-show="savingInsight" class="spinner"></span><span>存为洞察模板</span>
          </button>
        </div>
      </div>

      <div x-show="parseError" class="shrink-0 border-t border-white/5 px-4 py-3 text-[11px] text-warn"
           x-text="parseError"></div>
    </div>
  </div>
</div>
`;

export function assistantPage() {
  return {
    tpl: TPL,
    tab: 'chat',

    meta: { models: [], configured: false, current: '' },
    model: '',
    rooms: [],

    // 对话
    messages: [],
    input: '',
    sending: false,
    _chatAbort: null,
    samples: [
      '最近一周家里几点睡得最多？',
      '哪个房间的灯最费电？',
      '有没有异常的开关行为？',
      '给我一条可以直接用的自动化建议'
    ],

    // 分析
    af: { mode: 'days', days: 7, start_day: todayStr(-6), end_day: todayStr(), rooms: [], person: '', focus: '' },
    digest: null,
    output: '',
    reasoning: '',
    showReasoning: false,
    insight: null,
    parseError: '',
    analyzing: false,
    previewing: false,
    savingInsight: false,
    _anaAbort: null,

    fmtNum,

    init() {
      this.loadMeta();
      this.loadRooms();
      this.loadChat();
    },

    destroy() {
      this.stop();
      this.stopAnalyze();
    },

    async loadMeta() {
      try {
        const d = await api.llmModels();
        this.meta = d;
        this.model = d.current || (d.models && d.models[0]) || '';
        if (!d.configured) this.$store.app.warn('尚未配置大模型 API Key，请先到「系统设置」填写');
      } catch (e) {
        this.$store.app.err('模型信息加载失败：' + e.message);
      }
    },

    async loadRooms() {
      try {
        const d = await api.collectStats();
        this.rooms = d.rooms || [];
      } catch (e) { /* 非关键 */ }
    },

    md(text) { return renderMarkdown(text); },
    catLabel(c) { return CAT_LABEL[c] || c || '其他'; },
    usageText(u) {
      if (!u) return '';
      const t = u.total_tokens || ((u.prompt_tokens || 0) + (u.completion_tokens || 0));
      return t ? ('本次消耗 ' + fmtNum(t) + ' tokens') : '';
    },

    autoGrow(el) {
      el.style.height = 'auto';
      el.style.height = Math.min(160, el.scrollHeight) + 'px';
    },

    scrollTo(ref) {
      this.$nextTick(() => {
        const box = this.$refs[ref];
        if (box) box.scrollTop = box.scrollHeight;
      });
    },

    // ── 通用对话 ─────────────────────────────────────────
    clearChat() {
      this.stop();
      this.messages = [];
      try { localStorage.removeItem(CHAT_KEY); } catch (e) { /* ignore */ }
    },

    // 对话历史落盘（localStorage），只保留 role/content/tool_calls/usage，
    // 去掉 streaming 等运行时标记，重载后不会显示成「生成中」。
    saveChat() {
      try {
        const clean = this.messages.map((m) => {
          const item = { role: m.role, content: m.content || '' };
          if (m.role === 'assistant' && m.tool_calls) item.tool_calls = m.tool_calls;
          if (m.usage) item.usage = m.usage;
          return item;
        });
        localStorage.setItem(CHAT_KEY, JSON.stringify(clean));
      } catch (e) { /* 隐私模式忽略 */ }
    },

    // 启动时恢复历史；顺带清掉可能残留的「空 assistant」与流式标记。
    loadChat() {
      try {
        const raw = localStorage.getItem(CHAT_KEY);
        if (!raw) return;
        const arr = JSON.parse(raw);
        if (!Array.isArray(arr) || !arr.length) return;
        this.messages = arr.map((m) => ({
          role: m.role,
          content: m.content || '',
          reasoning: '',
          showReasoning: false,
          streaming: false,
          error: false,
          usage: m.usage || null,
          tool_calls: m.tool_calls || null
        }));
        const last = this.messages[this.messages.length - 1];
        if (last && last.role === 'assistant' && !last.content) {
          this.messages.pop();
        }
      } catch (e) { /* 数据损坏忽略 */ }
    },

    async send() {
      const text = this.input.trim();
      if (!text || this.sending) return;
      if (!this.meta.configured) { this.$store.app.err('请先在「系统设置」配置大模型 API Key'); return; }

      // 用数组重新赋值确保 Alpine 追踪到新增消息
      this.messages = [...this.messages, { role: 'user', content: text }];
      this.input = '';
      const replyIdx = this.messages.length;
      this.messages = [...this.messages, { role: 'assistant', content: '', reasoning: '', showReasoning: false, streaming: true, usage: null, truncated: false }];
      this.sending = true;
      this.scrollTo('chatBox');

          const history = this.messages
            .filter((m) => !m.streaming)
            .map((m) => {
              const item = { role: m.role, content: m.content };
              if (m.role === "assistant" && m.tool_calls) {
                item.tool_calls = m.tool_calls;
              }
              if (m.role === "tool") {
                item.tool_call_id = m.tool_call_id;
                item.name = m.name;
              }
              return item;
            });

      let scrollTimer = null;
      const throttledScroll = () => {
        if (scrollTimer) return;
        scrollTimer = setTimeout(() => { scrollTimer = null; this.scrollTo('chatBox'); }, 120);
      };

      this._chatAbort = new AbortController();
      try {
        await api.chatStream(
          { messages: history, model: this.model },
          (event, data) => {
            const m = this.messages[replyIdx];
            if (!m) return;
            if (event === 'content') {
              this.messages[replyIdx] = { ...m, content: (m.content || '') + (data.delta || '') };
              throttledScroll();
            } else if (event === 'reasoning') {
              this.messages[replyIdx] = { ...m, reasoning: (m.reasoning || '') + (data.delta || '') };
            } else if (event === 'usage') {
              this.messages[replyIdx] = { ...m, usage: data };
            } else if (event === 'truncated') {
              this.messages[replyIdx] = { ...m, truncated: true };
              this.$store.app.warn(data.message || '模型输出被截断');
            } else if (event === 'done') {
              this.messages[replyIdx] = { ...m, streaming: false };
            } else if (event === 'error') {
              this.messages[replyIdx] = { ...m, error: true, content: data.message || '生成失败' };
              this.$store.app.err(data.message || '生成失败');
            } else if (event === 'backend') {
              // 代理池实际切换到其后端：更新模型徽标，发生切换时提示
              const label = data.name || data.model || data.provider || '未知模型';
              const switched = data.model && this.model && data.model !== this.model;
              this.model = data.model || this.model;
              if (switched) this.$store.app.info('代理池已切换到：' + label);
            }
          },
          this._chatAbort.signal
        );
      } catch (e) {
        if (e.name !== 'AbortError') {
          const m = this.messages[replyIdx];
          if (m) this.messages[replyIdx] = { ...m, error: true, content: e.message || '请求失败' };
          this.$store.app.err(e.message || '请求失败');
        }
      } finally {
        this.sending = false;
        const m = this.messages[replyIdx];
        if (m && m.streaming) {
          this.messages[replyIdx] = { ...m, streaming: false };
        }
        if (m && !m.content && !m.reasoning && !m.error) {
          this.messages[replyIdx] = { ...m, content: '（模型未返回内容）' };
        }
        this.scrollTo('chatBox');
        this.saveChat();
        this._chatAbort = null;
      }
    },

    stop() {
      if (this._chatAbort) { this._chatAbort.abort(); this._chatAbort = null; }
      this.sending = false;
      const last = this.messages[this.messages.length - 1];
      if (last && last.streaming) last.streaming = false;
    },

    // ── 行为分析 ─────────────────────────────────────────
    toggleRoom(r) {
      const i = this.af.rooms.indexOf(r);
      if (i === -1) this.af.rooms.push(r); else this.af.rooms.splice(i, 1);
    },

    analyzePayload() {
      const p = { rooms: this.af.rooms, person: this.af.person, focus: this.af.focus, model: this.model };
      if (this.af.mode === 'range') { p.start_day = this.af.start_day; p.end_day = this.af.end_day; }
      else { p.days = this.af.days; }
      return p;
    },

    resetAnalyze() {
      this.stopAnalyze();
      this.output = '';
      this.reasoning = '';
      this.insight = null;
      this.digest = null;
      this.parseError = '';
    },

    async preview() {
      this.previewing = true;
      try {
        const d = await api.llmPreview(this.analyzePayload());
        this.digest = d;
        if (!d.total_events) this.$store.app.warn('所选范围没有事件数据，请先采集或调整范围');
        else this.$store.app.info('画像已生成：' + fmtNum(d.total_events) + ' 条事件 / ' + d.digest_chars + ' 字符上下文');
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.previewing = false;
      }
    },

    async runAnalyze() {
      if (this.analyzing) return;
      if (!this.meta.configured) { this.$store.app.err('请先在「系统设置」配置大模型 API Key'); return; }

      this.output = '';
      this.reasoning = '';
      this.insight = null;
      this.parseError = '';
      this.analyzing = true;
      this._anaAbort = new AbortController();

      try {
        await api.analyzeStream(
          this.analyzePayload(),
          (event, data) => {
            if (event === 'digest') {
              this.digest = Object.assign({}, this.digest || {}, data);
            } else if (event === 'start') {
              this.digest = Object.assign({}, this.digest || {}, { range: data.range });
            } else if (event === 'content') {
              this.output += data.delta || '';
              this.scrollTo('outBox');
            } else if (event === 'reasoning') {
              this.reasoning += data.delta || '';
            } else if (event === 'insight') {
              if (data.ok) this.insight = data.insight;
              else this.parseError = (data.error || '未能解析结构化结果') + '，可复制上方文本后手工整理';
            } else if (event === 'error') {
              this.$store.app.err(data.message || '分析失败');
            }
          },
          this._anaAbort.signal
        );
      } catch (e) {
        if (e.name !== 'AbortError') this.$store.app.err(e.message);
      } finally {
        this.analyzing = false;
        this._anaAbort = null;
      }
    },

    stopAnalyze() {
      if (this._anaAbort) { this._anaAbort.abort(); this._anaAbort = null; }
      this.analyzing = false;
    },

    async copyOut() {
      const okFlag = await copyText(this.output);
      okFlag ? this.$store.app.ok('已复制到剪贴板') : this.$store.app.err('复制失败，请手动选择文本');
    },

    async saveInsight() {
      if (!this.insight) return;
      this.savingInsight = true;
      try {
        const d = await api.llmSaveInsight(this.insight);
        this.$store.app.ok(d.message || '已保存');
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.savingInsight = false;
      }
    }
  };
}
