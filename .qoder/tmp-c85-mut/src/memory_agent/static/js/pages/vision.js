/* 视觉识别设置页：摄像头 / 多模态 LLM / 识别频率与光线门槛 / 运行状态 */

import { api } from '../api.js';

/* 本页可保存的配置键。settings 页 save() 只提交 settings 表单字段，
 * 这里的视觉字段由本页独立提交，两页互不覆盖。 */
const VISION_KEYS = [
  'vision_enabled', 'vision_device_token',
  'go2rtc_base_url', 'go2rtc_user', 'go2rtc_pass',
  'vlm_base_url', 'vlm_api_key', 'vlm_model', 'vlm_timeout_s', 'vlm_max_retries',
  'vlm_endpoint_path',
  'vision_cameras',
  'vision_cooldown_s', 'vision_max_per_hour', 'vision_no_tv_interval_s',
  'vision_light_gate', 'vision_snapshot_retention_days',
];

const SECRET_FIELDS = ['vision_device_token', 'go2rtc_pass', 'vlm_api_key'];

const TPL = `
<div class="grid lg:grid-cols-3 gap-5 items-start">

  <div class="lg:col-span-2 space-y-5">

    <!-- 总开关 -->
    <div class="card p-5">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg grad-violet grid place-items-center">
            <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
          </div>
          <h3 class="font-semibold">视觉识别（多模态行为识别）</h3>
        </div>
        <button class="btn-primary btn-sm" @click="save()" :disabled="saving">
          <span x-show="saving" class="spinner"></span>
          <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
          <span>保存配置</span>
        </button>
      </div>
      <div class="mb-3 mt-4 rounded-lg bg-brand/10 border border-brand/20 px-3 py-2 text-[11px] leading-relaxed text-txt-2">
        让家庭中枢「长眼睛」：摄像头画面 → 多模态大模型 → 行为记忆（谁 + 在哪 + 在干嘛）。
        <strong>光线门槛默认开启</strong>：房间照明灯开着才轮询识别，环境暗时画面无价值、纯烧调用。
        <span class="text-txt-3">识别失败只降级为缺数据，不影响其他功能。</span>
      </div>
      <label class="flex items-center gap-2.5 cursor-pointer">
        <div class="switch scale-90" :class="cfg.vision_enabled && 'on'" @click="cfg.vision_enabled = !cfg.vision_enabled"></div>
        <span class="text-xs text-txt-2">启用视觉识别（总开关：家人反馈不适时可立刻停用）</span>
      </label>
    </div>

    <!-- 摄像头设置 -->
    <div class="card p-5">
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg grad-aqua grid place-items-center">
            <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>
          </div>
          <h3 class="font-semibold">摄像头设置</h3>
        </div>
        <button class="btn-ghost btn-xs" @click="addCamera()">+ 添加摄像头</button>
      </div>
      <div class="space-y-4">
        <template x-for="(cam, i) in cfg.vision_cameras" :key="i">
          <div class="rounded-lg border border-white/10 p-4 space-y-3">
            <div class="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div>
                <label class="lbl">房间</label>
                <select class="inp" x-model="cam.room">
                  <template x-for="r in rooms" :key="r">
                    <option :value="r" x-text="r" :selected="cam.room === r"></option>
                  </template>
                </select>
              </div>
              <div>
                <label class="lbl">go2rtc 流名</label>
                <input class="inp inp-mono" x-model="cam.stream" placeholder="客厅 / 小黄人">
              </div>
              <div>
                <label class="lbl">房间类型</label>
                <select class="inp" x-model.number="cam.no_tv">
                  <option :value="false">有 TV（人脸事件触发）</option>
                  <option :value="true">无 TV（低频定时巡检）</option>
                </select>
              </div>
              <div class="flex items-end gap-2 flex-wrap">
                <label class="flex items-center gap-1.5 text-xs cursor-pointer">
                  <div class="switch scale-75" :class="cam.enabled && 'on'" @click="cam.enabled = !cam.enabled"></div>
                  <span>启用</span>
                </label>
                <button class="btn-ghost btn-xs" @click="testCamera(i)" :disabled="testing['cam' + i]">
                  <span x-show="testing['cam' + i]" class="spinner"></span><span>测试取帧</span>
                </button>
                <button class="btn-ghost btn-xs text-danger" @click="removeCamera(i)">删除</button>
              </div>
            </div>

            <!-- 光线门槛 -->
            <div class="rounded-lg bg-white/5 px-3 py-2.5">
              <label class="flex items-center gap-2 text-xs cursor-pointer">
                <div class="switch scale-75" :class="cam.light_gate && 'on'" @click="cam.light_gate = !cam.light_gate"></div>
                <span class="text-txt-2">光线门槛：本房间<strong>开灯</strong>才轮询识别</span>
              </label>
              <div class="mt-2 space-y-2" x-show="cam.light_gate">
                <div>
                  <label class="lbl text-[11px]">判定用的灯实体 ID（可填多个，用逗号/空格/换行分隔）</label>
                  <textarea class="inp inp-mono text-xs" rows="3" x-model="cam.light_entities_text" placeholder="light.living_room_main, switch.living_room_lamp"></textarea>
                </div>
                <p class="hint">留空 = 自动使用房间注册表下全部 <code>light.*</code> 实体；填写后只以填写的实体为准</p>
              </div>
            </div>

            <!-- 取帧预览 -->
            <template x-if="camTest[i] && camTest[i].preview">
              <div class="flex items-start gap-3">
                <img :src="camTest[i].preview" class="max-h-44 rounded-lg border border-white/10" alt="取帧预览">
                <div class="text-[11px] text-txt-3 leading-relaxed">
                  <div x-text="'大小 ' + (camTest[i].size/1024).toFixed(0) + ' KB'"></div>
                  <div x-text="'延迟 ' + camTest[i].latency_ms + 'ms'"></div>
                </div>
              </div>
            </template>
            <p class="text-[11px] text-danger" x-show="camTest[i] && camTest[i].error" x-text="camTest[i] && camTest[i].error"></p>
          </div>
        </template>
        <p class="text-sm text-txt-3 py-6 text-center" x-show="!(cfg.vision_cameras || []).length">
          还没有注册摄像头。点「+ 添加摄像头」把房间映射到 go2rtc 流名（如 客厅 → 客厅）。
        </p>
      </div>
    </div>

    <!-- 多模态 LLM -->
    <div class="card p-5">
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg grad-brand grid place-items-center">
            <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
          </div>
          <h3 class="font-semibold">多模态 LLM（图片识别网关）</h3>
        </div>
        <button class="btn-ghost btn-xs" @click="testLlm()" :disabled="testing.llm">
          <span x-show="testing.llm" class="spinner"></span><span>端到端测试</span>
        </button>
      </div>
      <p class="hint mb-3">此网关独立于「系统设置 → 大模型」，仅用于图片识别。默认走 OpenAI 兼容端点 <code>/v1/chat/completions</code>（Bearer key）；若用 doubao2api 可把「端点路径」改为其私有 <code>/v1/images/analyses</code>。测试会拉取第一个启用摄像头的画面并调一次识别；<strong>测试直接使用上方表单当前值，无需先保存</strong>。doubao2api 的 key 即其管理面板密码（如 longyin）。</p>
      <div class="grid sm:grid-cols-2 gap-4">
        <div><label class="lbl">网关地址</label><input class="inp inp-mono" x-model="cfg.vlm_base_url" placeholder="http://192.168.2.200:9090"></div>
        <div><label class="lbl">模型</label><input class="inp inp-mono" x-model="cfg.vlm_model" placeholder="doubao"></div>
        <div>
          <label class="lbl">API Key</label>
          <div class="flex gap-2">
            <input class="inp inp-mono" :type="revealed.vlm_api_key ? 'text' : 'password'" x-model="cfg.vlm_api_key">
            <button class="btn-ghost shrink-0 px-2.5" @click="reveal('vlm_api_key')" title="查看明文">
              <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div><label class="lbl">超时（秒）</label><input type="number" class="inp" x-model.number="cfg.vlm_timeout_s"></div>
          <div><label class="lbl">重试次数</label><input type="number" class="inp" x-model.number="cfg.vlm_max_retries"></div>
        </div>
      </div>
      <!-- 端到端测试结果 -->
      <div class="mt-3 rounded-lg px-3 py-2 text-[11px] leading-relaxed border" x-show="llmResult" x-cloak
           :class="llmResult && llmResult.ok ? 'bg-ok/10 border-ok/25 text-ok' : 'bg-danger/10 border-danger/25 text-danger'">
        <div x-text="llmResult && llmResult.message"></div>
        <div class="mt-1 whitespace-pre-wrap text-txt-2" x-show="llmResult && llmResult.answer" x-text="llmResult && llmResult.answer"></div>
        <div class="mt-1 text-txt-3" x-show="llmResult && llmResult.hint" x-text="llmResult && llmResult.hint"></div>
      </div>
    </div>

    <!-- 频率与门槛 -->
    <div class="card p-5">
      <div class="flex items-center gap-2.5 mb-4">
        <div class="w-8 h-8 rounded-lg grad-aqua grid place-items-center">
          <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
        </div>
        <h3 class="font-semibold">识别频率与光线门槛</h3>
      </div>
      <div class="grid sm:grid-cols-2 gap-4">
        <div><label class="lbl">同房间冷却（秒）</label><input type="number" class="inp" x-model.number="cfg.vision_cooldown_s"><p class="hint">两次识别最小间隔，画面没变就别再问</p></div>
        <div><label class="lbl">每小时上限（次/房间）</label><input type="number" class="inp" x-model.number="cfg.vision_max_per_hour"><p class="hint">硬上限，防误用打爆豆包</p></div>
        <div><label class="lbl">无 TV 房间巡检周期（秒）</label><input type="number" class="inp" x-model.number="cfg.vision_no_tv_interval_s"><p class="hint">默认 300 秒（5 分钟）</p></div>
        <div><label class="lbl">快照保留（天）</label><input type="number" class="inp" x-model.number="cfg.vision_snapshot_retention_days"><p class="hint">过期自动清理画面文件</p></div>
      </div>
      <div class="mt-4 space-y-3">
        <label class="flex items-center gap-2.5 cursor-pointer">
          <div class="switch scale-90" :class="cfg.vision_light_gate && 'on'" @click="cfg.vision_light_gate = !cfg.vision_light_gate"></div>
          <span class="text-xs text-txt-2">光线门槛全局总闸：房间开灯才轮询视频流（关灯 = 拦截）</span>
        </label>
      </div>
      <div class="mt-4 grid sm:grid-cols-2 gap-4">
        <div class="sm:col-span-2">
          <label class="lbl">设备上报令牌（TV / HA 自动化用）</label>
          <div class="flex gap-2">
            <input class="inp inp-mono" :type="revealed.vision_device_token ? 'text' : 'password'" x-model="cfg.vision_device_token" placeholder="留空则 /api/events/face 拒绝上报">
            <button class="btn-ghost shrink-0 px-2.5" @click="reveal('vision_device_token')" title="查看明文">
              <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
          <p class="hint">与 WebUI / MCP 凭证隔离；泄露只影响行为事件写入</p>
        </div>
        <div>
          <label class="lbl">go2rtc 地址</label><input class="inp inp-mono" x-model="cfg.go2rtc_base_url" placeholder="http://192.168.2.200:1984">
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div><label class="lbl">go2rtc 用户</label><input class="inp inp-mono" x-model="cfg.go2rtc_user"></div>
          <div>
            <label class="lbl">go2rtc 密码</label>
            <input class="inp inp-mono" :type="revealed.go2rtc_pass ? 'text' : 'password'" x-model="cfg.go2rtc_pass">
            <p class="hint">测试取帧用此凭据做 Basic Auth，必填</p>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部保存 -->
    <div class="card p-4">
      <button class="btn-primary w-full justify-center" @click="save()" :disabled="saving">
        <span x-show="saving" class="spinner"></span>
        <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
        <span>保存视觉识别配置</span>
      </button>
      <p class="hint mt-2 text-center">填写 go2rtc / LLM 凭据后必须保存，测试才会使用新值</p>
    </div>
  </div>

  <!-- 右：运行状态 -->
  <div class="space-y-5">
    <div class="card p-5">
      <div class="flex items-center justify-between mb-4">
        <h3 class="font-semibold">运行状态</h3>
        <button class="btn-ghost btn-xs" @click="refreshStatus()" :disabled="statusLoading">
          <span x-show="statusLoading" class="spinner"></span><span>刷新</span>
        </button>
      </div>
      <p class="text-[11px] text-txt-3 mb-3" x-show="status">每 15 秒自动刷新 · 拦截不报错，只计数</p>
      <div class="space-y-3" x-show="status && status.rooms.length">
        <template x-for="r in (status ? status.rooms : [])" :key="r.room">
          <div class="rounded-lg border border-white/10 p-3">
            <div class="flex items-center gap-2">
              <span class="w-2 h-2 rounded-full shrink-0"
                    :class="r.gate_on === true ? 'bg-ok' : (r.gate_on === false ? 'bg-txt-3' : 'bg-warn')"></span>
              <span class="text-sm font-medium" x-text="r.room"></span>
              <span class="ml-auto text-[10px] text-txt-3" x-text="r.enabled ? (r.no_tv ? '巡检' : '事件触发') : '停用'"></span>
            </div>
            <div class="mt-1.5 text-[11px] text-txt-3 leading-relaxed">
              <div>
                光线门槛：<span x-text="r.light_gate ? (r.gate_on === true ? '放行（有灯亮）' : r.gate_on === false ? '拦截（灯全关）' : (r.gate_reason === 'no_lights_configured' ? '未配置灯实体' : '无法判定（HA 不可达）')) : '关闭'"></span>
                <span class="ml-1" x-show="r.degraded" style="color:var(--warn, #eab308)">⚠</span>
              </div>
              <div x-text="'本小时 ' + r.calls_this_hour + '/' + r.max_per_hour + ' 次 · 冷却剩 ' + r.cooldown_remaining_s + 's'"></div>
              <div x-show="r.backoff_remaining_s > 0" class="text-danger" x-text="'退避中，剩 ' + r.backoff_remaining_s + 's'"></div>
              <div x-show="r.last_result && r.last_result.action" class="text-txt-2" x-text="'上次：' + (r.last_result.action || '')"></div>
              <div x-show="r.last_result && r.last_result.error" class="text-danger" x-text="'错误：' + (r.last_result.error || '')"></div>
              <div x-show="r.skips && Object.keys(r.skips).length" class="text-txt-3"
                   x-text="'拦截：' + Object.entries(r.skips).map(([k, v]) => k + '×' + v).join('，')"></div>
            </div>
          </div>
        </template>
      </div>
      <p class="text-sm text-txt-3 py-6 text-center" x-show="status && !status.rooms.length">尚未注册摄像头</p>
    </div>

    <div class="card p-5">
      <h3 class="font-semibold mb-3">手动识别</h3>
      <p class="hint mb-3">force 会绕过光线门槛与冷却（仍受每小时上限保护），用于调试。</p>
      <div class="flex gap-2">
        <select class="inp" x-model="manualRoom">
          <template x-for="c in (cfg.vision_cameras || [])" :key="c.room">
            <option :value="c.room" x-text="c.room"></option>
          </template>
        </select>
        <button class="btn-ghost btn-xs shrink-0" @click="manualAnalyze()" :disabled="testing.manual || !manualRoom">
          <span x-show="testing.manual" class="spinner"></span><span>识别一次</span>
        </button>
      </div>
      <div class="mt-3 rounded-lg px-3 py-2 text-[11px] leading-relaxed border" x-show="manualResult" x-cloak
           :class="manualResult && manualResult.ok ? 'bg-ok/10 border-ok/25 text-ok' : 'bg-danger/10 border-danger/25 text-danger'">
        <div x-text="manualResult && manualResult.message"></div>
        <div class="mt-1 text-txt-2" x-show="manualResult && manualResult.scene" x-text="'场景：' + (manualResult && manualResult.scene)"></div>
      </div>
    </div>
  </div>
</div>
`;

export function visionPage() {
  return {
    tpl: TPL,
    cfg: {},
    rooms: [],
    camTest: {},        // camera index -> {preview, size, latency_ms} | {error}
    llmResult: null,
    manualResult: null,
    manualRoom: '',
    status: null,
    revealed: {},
    saving: false,
    statusLoading: false,
    testing: {},
    statusTimer: null,

    init() {
      this.load();
      this.loadRooms();
      this.refreshStatus();
      this.statusTimer = setInterval(() => this.refreshStatus(), 15000);
    },

    destroy() {
      if (this.statusTimer) clearInterval(this.statusTimer);
    },

    async load() {
      try {
        const cfg = await api.getConfig();
        // 仅保留本页字段，避免把 settings 页字段顺手提交造成互相覆盖
        const scoped = {};
        VISION_KEYS.forEach((k) => { scoped[k] = cfg[k]; });
        scoped.vision_cameras = (cfg.vision_cameras || []).map((c) => {
          const merged = {
            room: '', stream: '', enabled: true, no_tv: false,
            light_gate: true, light_entities: [],
            ...c,
          };
          merged.light_entities_text = (merged.light_entities || []).join(', ');
          return merged;
        });
        this.cfg = scoped;
        if ((this.cfg.vision_cameras || []).length && !this.manualRoom) {
          this.manualRoom = this.cfg.vision_cameras[0].room;
        }
      } catch (e) {
        this.$store.app.err('配置加载失败：' + e.message);
      }
    },

    async loadRooms() {
      try {
        const d = await api.haRooms();
        this.rooms = Object.keys(d.rooms || {});
      } catch (e) { /* 房间注册表为空时允许手填 */ }
    },

    addCamera() {
      this.cfg.vision_cameras.push({
        room: this.rooms[0] || '', stream: '', enabled: true,
        no_tv: false, light_gate: true, light_entities: [],
        light_entities_text: '',
      });
    },

    removeCamera(i) {
      this.cfg.vision_cameras.splice(i, 1);
      this.camTest = {};
    },

    async save() {
      this.saving = true;
      try {
        const patch = {};
        VISION_KEYS.forEach((k) => { patch[k] = this.cfg[k]; });
        patch.vision_cameras = (this.cfg.vision_cameras || []).map((cam) => {
          const { light_entities_text, ...rest } = cam;
          return {
            ...rest,
            light_entities: (light_entities_text || '')
              .split(/[,\s]+/)
              .map((s) => s.trim())
              .filter(Boolean),
          };
        });
        await api.saveConfig(patch);
        this.$store.app.ok('视觉识别配置已保存');
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.saving = false;
      }
    },

    async reveal(field) {
      if (SECRET_FIELDS.indexOf(field) < 0) return;
      if (this.revealed[field]) { this.revealed[field] = false; return; }
      try {
        const d = await api.revealSecret(field);
        this.cfg[field] = d.value || '';
        this.revealed[field] = true;
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    /* 测试用表单当前值（未保存也能测）；密钥是掩码占位时由后端回退已保存值 */
    testOverrides() {
      return {
        go2rtc_user: this.cfg.go2rtc_user || '',
        go2rtc_pass: this.cfg.go2rtc_pass || '',
        vlm_base_url: this.cfg.vlm_base_url || '',
        vlm_api_key: this.cfg.vlm_api_key || '',
        vlm_model: this.cfg.vlm_model || '',
        vlm_endpoint_path: this.cfg.vlm_endpoint_path || '',
      };
    },

    async testCamera(i) {
      const cam = this.cfg.vision_cameras[i];
      if (!cam || !cam.stream) {
        this.camTest = { ...this.camTest, [i]: { error: '请先填写 go2rtc 流名' } };
        return;
      }
      this.testing = { ...this.testing, ['cam' + i]: true };
      try {
        const d = await api.visionCameraTest({ stream: cam.stream, ...this.testOverrides() });
        this.camTest = { ...this.camTest, [i]: d };
      } catch (e) {
        this.camTest = { ...this.camTest, [i]: { error: e.message } };
      } finally {
        this.testing = { ...this.testing, ['cam' + i]: false };
      }
    },

    async testLlm() {
      this.testing = { ...this.testing, llm: true };
      this.llmResult = null;
      try {
        const d = await api.visionTestLlm(this.testOverrides());
        this.llmResult = { ok: true, message: d.message, answer: d.answer };
      } catch (e) {
        this.llmResult = {
          ok: false,
          message: e.message,
          hint: (e.payload && e.payload.hint) || '',
        };
      } finally {
        this.testing = { ...this.testing, llm: false };
      }
    },

    async manualAnalyze() {
      if (!this.manualRoom) return;
      this.testing = { ...this.testing, manual: true };
      this.manualResult = null;
      try {
        const d = await api.visionAnalyze(this.manualRoom, true, true);
        this.manualResult = { ok: true, message: d.message, scene: d.scene };
        this.refreshStatus();
      } catch (e) {
        this.manualResult = { ok: false, message: e.message };
      } finally {
        this.testing = { ...this.testing, manual: false };
      }
    },

    async refreshStatus() {
      this.statusLoading = true;
      try {
        this.status = await api.visionStatus();
      } catch (e) { /* 静默：状态栏是背景动作 */ }
      finally { this.statusLoading = false; }
    },
  };
}
