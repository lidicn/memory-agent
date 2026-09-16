/* 家庭成员页：成员卡片 + 详情抽屉（头像 / 关联房间 / 专属设备 / 生活习惯档案） */

import { api } from '../api.js';

const EMOJI_PRESETS = ['🦉', '🌅', '🍳', '📺', '🏠', '👨‍🍳', '👩‍💻', '🧘', '🏃', '🐱', '🐶', '👴', '👵', '👨', '👩', '👦', '👧', '🧑'];
const BG_PRESETS = ['#0EA5E9', '#6366F1', '#22C55E', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#14B8A6'];
const TAG_CATEGORIES = [
  { v: 'sleep', label: '作息' },
  { v: 'diet', label: '饮食' },
  { v: 'activity', label: '活动' },
  { v: 'media', label: '影音' },
  { v: 'hygiene', label: '卫生' },
  { v: 'other', label: '其他' },
];
// 外观档案选项（gender 存英文以对齐 VLM 输出 male/female）
const GENDER_OPTIONS = [
  { v: 'male', label: '男' },
  { v: 'female', label: '女' },
  { v: 'other', label: '其他' },
];
const BODY_TYPE_OPTIONS = ['高', '矮', '壮', '瘦', '偏瘦', '微胖', '胖', '偏胖', '中等'];

const TPL = `
<div class="space-y-5">
  <!-- 顶部栏 -->
  <div class="flex items-center justify-between gap-3">
    <div>
      <h2 class="text-xl font-semibold">家庭成员</h2>
      <p class="text-[12px] text-txt-3 mt-0.5">为每个家人建立生活习惯档案，AI 也会把发现的偏好写入这里</p>
    </div>
    <button class="btn-primary btn-sm" @click="openCreate()">
      <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
      <span>添加成员</span>
    </button>
  </div>

  <!-- 加载中 -->
  <div x-show="loading" class="card p-8 grid place-items-center text-txt-3">
    <span class="spinner"></span><span class="ml-2 text-xs">加载中…</span>
  </div>

  <!-- 空状态 -->
  <div x-show="!loading && !members.length" class="card p-10 text-center">
    <div class="text-5xl mb-3">🏡</div>
    <p class="font-medium">还没有家庭成员</p>
    <p class="text-[12px] text-txt-3 mt-1">添加第一位家人，开始沉淀 TA 的生活习惯档案</p>
    <button class="btn-primary btn-sm mt-4 mx-auto" @click="openCreate()">+ 添加成员</button>
  </div>

  <!-- 成员卡片网格 -->
  <div x-show="!loading && members.length" class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 items-start">
    <template x-for="m in members" :key="m.id">
      <div class="card p-6 flex flex-col items-center text-center cursor-pointer hover:border-brand/40 hover:shadow-glow transition group h-fit" @click="openDetail(m)">
        <!-- 大头像 -->
        <div class="relative mb-3">
          <template x-if="m.avatar_url">
            <img :src="m.avatar_url" class="member-avatar member-avatar-sm">
          </template>
          <template x-if="!m.avatar_url">
            <div class="member-avatar member-avatar-sm grid place-items-center" :style="'background:'+(m.avatar_bg||'#0EA5E9')">
              <span x-text="m.avatar_emoji || '🙂'"></span>
            </div>
          </template>
          <div class="avatar-edit-hint" @click.stop="openEdit(m)" title="编辑">
            <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>
          </div>
        </div>
        <p class="font-semibold text-lg truncate max-w-full" x-text="m.name"></p>
        <p class="text-[11px] text-txt-3 truncate max-w-full px-2" x-text="((m.rooms||[]).join(' · ') || '未关联房间')"></p>

        <!-- 生活习惯档案：头像下方散布 -->
        <div class="tag-cloud mt-4 w-full" x-show="(m.tags||[]).length">
          <template x-for="(t, idx) in (m.tags||[]).slice(0,8)" :key="t.tag">
            <span class="tag-cloud-item" :style="cloudStyle(t, idx)">
              <span x-text="t.emoji"></span>
              <span x-text="t.tag"></span>
            </span>
          </template>
          <span x-show="(m.tags||[]).length > 8" class="tag-cloud-item text-txt-3" :style="cloudStyle({tag:'more', confidence:0.4}, 99)">+<span x-text="(m.tags||[]).length - 8"></span></span>
        </div>
        <div class="mt-3 flex items-center gap-3 text-[11px] text-txt-3">
          <span x-text="(m.devices||[]).length + ' 台专属设备'"></span>
          <span x-show="(m.tags||[]).length" x-text="(m.tags||[]).length + ' 个标签'"></span>
        </div>
      </div>
    </template>
  </div>
</div>

<!-- 添加 / 编辑 成员弹窗 -->
<div class="fixed inset-0 z-50 grid place-items-center" x-show="formOpen" x-cloak>
  <div class="absolute inset-0 bg-black/80"></div>
  <div class="relative card bg-ink-900 w-[440px] max-w-[92vw] max-h-[85vh] flex flex-col anim-in">
    <div class="p-5 border-b border-white/10 flex items-center justify-between shrink-0">
      <h3 class="font-semibold" x-text="form.id ? '编辑成员' : '添加成员'"></h3>
      <button class="icon-btn" @click="closeForm()" title="关闭">
        <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
      </button>
    </div>

    <div class="flex-1 overflow-y-auto p-5 space-y-4">
      <!-- 头像上传预览 -->
      <div class="flex flex-col items-center gap-3">
        <div class="relative">
          <template x-if="form.avatar_url">
            <img :src="form.avatar_url" class="member-avatar">
          </template>
          <template x-if="!form.avatar_url">
            <div class="member-avatar grid place-items-center" :style="'background:'+(form.avatar_bg||'#0EA5E9')">
              <span class="text-5xl" x-text="form.avatar_emoji || '🙂'"></span>
            </div>
          </template>
          <label class="avatar-upload" title="上传图片">
            <input type="file" accept="image/*" class="hidden" @change="handleAvatarUpload($event)">
            <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
          </label>
        </div>
        <button class="btn-ghost btn-sm" @click="clearAvatarUrl()" x-show="form.avatar_url">恢复默认头像</button>
      </div>

      <div>
        <label class="lbl">姓名</label>
        <input class="inp" x-model="form.name" placeholder="如 爸爸 / 小明">
      </div>
      <div x-show="!form.avatar_url">
        <label class="lbl">Emoji 头像</label>
        <div class="flex flex-wrap gap-1.5">
          <template x-for="e in emojiPresets" :key="e">
            <button class="w-9 h-9 rounded-xl grid place-items-center text-xl transition" :class="form.avatar_emoji===e ? 'ring-2 ring-sky-400 bg-white/10' : 'bg-white/5 hover:bg-white/10'" @click="form.avatar_emoji = e" x-text="e"></button>
          </template>
        </div>
      </div>
      <div x-show="!form.avatar_url">
        <label class="lbl">底色</label>
        <div class="flex flex-wrap gap-2">
          <template x-for="c in bgPresets" :key="c">
            <button class="w-7 h-7 rounded-full transition" :style="'background:'+c" :class="form.avatar_bg===c ? 'ring-2 ring-white scale-110' : 'hover:scale-110'" @click="form.avatar_bg = c"></button>
          </template>
        </div>
      </div>
      <div>
        <label class="lbl">备注</label>
        <textarea class="inp" x-model="form.note" rows="2" placeholder="可选，记录 TA 的特点"></textarea>
      </div>

      <!-- 外观特征（多模态命名识别用）-->
      <div class="rounded-xl bg-white/5 p-3">
        <button type="button" class="flex items-center justify-between w-full text-left" @click="apOpen = !apOpen">
          <span class="font-medium text-sm">🎨 外观特征 <span class="text-[11px] text-txt-3 font-normal">填后可让摄像头识别到 TA 时显示姓名</span></span>
          <svg viewBox="0 0 24 24" class="w-4 h-4 transition" :class="apOpen && 'rotate-180'" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
        </button>
        <div x-show="apOpen" class="mt-3 space-y-3 anim-in" x-cloak>
          <p x-show="apErr" class="text-[12px] text-danger" x-text="apErr"></p>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="lbl">性别</label>
              <select class="inp" x-model="form.gender">
                <option value="">不填</option>
                <template x-for="g in genderOptions" :key="g.v">
                  <option :value="g.v" x-text="g.label"></option>
                </template>
              </select>
            </div>
            <div>
              <label class="lbl">年龄段</label>
              <input class="inp" x-model="form.approx_age" placeholder="如 30-35 或 32">
            </div>
            <div>
              <label class="lbl">身材</label>
              <select class="inp" x-model="form.body_type">
                <option value="">不填</option>
                <template x-for="b in bodyTypeOptions" :key="b">
                  <option :value="b" x-text="b"></option>
                </template>
              </select>
            </div>
            <div>
              <label class="lbl">体重 <span class="text-txt-3 text-[10px]">（可选）</span></label>
              <input class="inp" x-model="form.weight" placeholder="如 70kg">
            </div>
            <div>
              <label class="lbl">约略身高 <span class="text-txt-3 text-[10px]">（可选）</span></label>
              <input class="inp" x-model="form.height" placeholder="如 175cm">
            </div>
            <div>
              <label class="lbl">自定义身材 <span class="text-txt-3 text-[10px]">（可选）</span></label>
              <input class="inp" x-model="form.body_type_custom" placeholder="如 偏胖、结实">
            </div>
          </div>
          <div>
            <label class="lbl">穿搭风格 <span class="text-txt-3 text-[10px]">（颜色 / 款式 / 明显特征）</span></label>
            <textarea class="inp" x-model="form.clothing" rows="2" placeholder="如 深色休闲上衣、牛仔裤、戴眼镜"></textarea>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="lbl">发型 / 发色 <span class="text-txt-3 text-[10px]">（可选）</span></label>
              <input class="inp" x-model="form.hair" placeholder="如 短发 / 卷发">
            </div>
            <div>
              <label class="lbl">常出现区域 <span class="text-txt-3 text-[10px]">（可选）</span></label>
              <input class="inp" x-model="form.typical_location" placeholder="如 书房 / 客厅">
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="p-5 border-t border-white/10 flex justify-end gap-2 bg-ink-900 shrink-0">
      <button class="btn-ghost" @click="closeForm()">取消</button>
      <button class="btn-primary" @click="saveForm()" :disabled="saving"><span x-show="saving" class="spinner"></span>保存</button>
    </div>
  </div>
</div>

<!-- 成员详情抽屉 -->
<div class="fixed inset-0 z-40" x-show="drawerOpen" x-cloak>
  <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="closeDrawer()"></div>
  <div class="absolute right-0 top-0 h-full w-full max-w-xl bg-ink-900 border-l border-white/10 shadow-2xl overflow-y-auto anim-in"
       :class="drawerOpen ? 'translate-x-0' : 'translate-x-full'">
    <template x-if="detail">
      <div class="p-5 space-y-6">
        <!-- 头部：大头像 + 姓名 + 档案标签云 -->
        <div class="relative flex flex-col items-center text-center">
          <div class="relative">
            <img x-show="detail.avatar_url" :src="detail.avatar_url" class="member-avatar">
            <div x-show="!detail.avatar_url" class="member-avatar grid place-items-center" :style="'background:'+(detail.avatar_bg||'#0EA5E9')">
              <span class="text-5xl" x-text="detail.avatar_emoji || '🙂'"></span>
            </div>
            <label class="avatar-upload" title="更换头像" :class="avatarSaving && 'opacity-80 cursor-wait'">
              <input type="file" accept="image/*" class="hidden" @change="handleAvatarUploadDetail($event)" :disabled="avatarSaving">
              <span x-show="avatarSaving" class="spinner"></span>
              <svg x-show="!avatarSaving" viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
            </label>
          </div>
          <h3 class="text-lg font-semibold mt-3" x-text="detail.name"></h3>
          <p class="text-[12px] text-txt-3 mt-0.5 max-w-xs" x-text="(detail.note || '暂无备注')"></p>

          <!-- 生活习惯档案：头像下方散布 -->
          <div class="tag-cloud mt-4 w-full" x-show="(detail.tags||[]).length">
            <template x-for="(t, idx) in (detail.tags||[])" :key="t.tag">
              <span class="tag-cloud-item" :style="cloudStyle(t, idx)">
                <span x-text="t.emoji"></span>
                <span x-text="t.tag"></span>
                <span class="opacity-70 text-[10px]" x-text="Math.round((t.confidence||0)*100)+'%'"></span>
                <button class="ml-0.5 opacity-60 hover:opacity-100 hover:text-danger" @click.stop="removeTag(t.tag)" title="删除标签">
                  <svg viewBox="0 0 24 24" class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
                </button>
              </span>
            </template>
          </div>
          <div x-show="!(detail.tags||[]).length" class="mt-3 text-xs text-txt-3">还没有生活习惯标签。让 AI 助手分析历史数据，或手动添加。</div>
          <button class="icon-btn hover:!text-brand absolute right-14 top-5" @click="mergeOpen = true" title="合并到其他成员">
            <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 3H5a2 2 0 00-2 2v3M16 3h3a2 2 0 012 2v3M8 21H5a2 2 0 01-2-2v-3M16 21h3a2 2 0 002-2v-3"/><path d="M9 12h6M9 12l2-2M9 12l2 2M15 12l-2-2M15 12l-2 2"/></svg>
          </button>
          <button class="icon-btn hover:!text-danger absolute right-5 top-5" @click="removeMember(detail)" title="删除成员">
            <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
          </button>
        </div>

        <!-- 关联房间 -->
        <div>
          <p class="section-title">关联房间</p>
          <div class="flex flex-wrap gap-2 mt-2">
            <template x-for="r in roomsOptions" :key="r">
              <button class="chip transition" :class="(detail.rooms||[]).includes(r) ? 'chip-active' : 'opacity-60 hover:opacity-100'" @click="toggleRoom(r)" x-text="r"></button>
            </template>
            <span x-show="!roomsOptions.length" class="text-xs text-txt-3">尚未发现房间，请先在「数据采集」完成 HA 同步</span>
          </div>
        </div>

        <!-- 专属设备 -->
        <div>
          <p class="section-title">专属设备</p>
          <div class="mt-2">
            <input class="inp" placeholder="🔍 搜索设备中文名或 entity_id…" x-model="deviceSearch">
          </div>
          <div class="mt-2 space-y-1.5 max-h-56 overflow-y-auto pr-1">
            <template x-for="d in filteredDevices" :key="d.entity_id">
              <label class="flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg hover:bg-white/5 cursor-pointer" @click="toggleDevice(d.entity_id)">
                <div class="switch scale-75 shrink-0" :class="(detail.devices||[]).includes(d.entity_id) && 'on'"></div>
                <div class="min-w-0 flex-1">
                  <p class="text-[12px] truncate" x-text="d.friendly_name || d.entity_id"></p>
                  <p class="text-[10px] text-txt-3 truncate font-mono" x-text="d.entity_id"></p>
                </div>
                <span class="text-[10px] text-txt-3 shrink-0" x-text="d.room"></span>
              </label>
            </template>
            <p x-show="!filteredDevices.length" class="text-xs text-txt-3 py-2">无匹配设备</p>
          </div>
        </div>

        <!-- 外观特征 -->
        <div x-show="Object.keys(detailAppearance).length">
          <p class="section-title">外观特征</p>
          <div class="rounded-lg bg-white/5 p-3 text-[12px] space-y-1.5">
            <div class="flex flex-wrap gap-x-4 gap-y-1">
              <span x-show="detailAppearance.gender"><b class="text-txt-3 font-normal">性别</b> <span x-text="genderLabel(detailAppearance.gender)"></span></span>
              <span x-show="detailAppearance.approx_age"><b class="text-txt-3 font-normal">年龄</b> <span x-text="detailAppearance.approx_age"></span></span>
              <span x-show="detailAppearance.body_type"><b class="text-txt-3 font-normal">身材</b> <span x-text="detailAppearance.body_type"></span></span>
              <span x-show="detailAppearance.body_type_custom"><b class="text-txt-3 font-normal">自定义身材</b> <span x-text="detailAppearance.body_type_custom"></span></span>
              <span x-show="detailAppearance.weight"><b class="text-txt-3 font-normal">体重</b> <span x-text="detailAppearance.weight"></span></span>
              <span x-show="detailAppearance.height"><b class="text-txt-3 font-normal">身高</b> <span x-text="detailAppearance.height"></span></span>
              <span x-show="detailAppearance.hair"><b class="text-txt-3 font-normal">发型</b> <span x-text="detailAppearance.hair"></span></span>
              <span x-show="detailAppearance.typical_location"><b class="text-txt-3 font-normal">常出现</b> <span x-text="detailAppearance.typical_location"></span></span>
            </div>
            <div x-show="detailAppearance.clothing && clothingText(detailAppearance.clothing)">
              <b class="text-txt-3 font-normal">穿搭：</b><span x-text="clothingText(detailAppearance.clothing)"></span>
            </div>
          </div>
        </div>

        <!-- 手动添加标签 -->
        <div>
          <div class="flex items-center justify-between">
            <p class="section-title !mb-0">手动添加标签</p>
            <button class="text-[11px] text-brand hover:underline" @click="addTagOpen = !addTagOpen" x-text="addTagOpen ? '收起' : '+ 添加'"></button>
          </div>
          <div x-show="addTagOpen" class="mt-3 p-3 rounded-lg bg-white/5 space-y-2 anim-in">
            <div class="flex gap-2">
              <input class="inp flex-1" placeholder="标签名，如 夜猫子" x-model="tagForm.tag">
              <input class="inp w-16 text-center" placeholder="🦉" x-model="tagForm.emoji" maxlength="2">
            </div>
            <div class="flex gap-2 items-center">
              <select class="inp flex-1" x-model="tagForm.category">
                <template x-for="c in tagCategories" :key="c.v">
                  <option :value="c.v" x-text="c.label"></option>
                </template>
              </select>
              <button class="btn-primary btn-sm" @click="addManualTag()">添加</button>
            </div>
          </div>
        </div>

        <!-- 相关洞察反馈（v0.8-3 洞察反馈反哺成员档案）-->
        <div>
          <div class="flex items-center justify-between">
            <p class="section-title !mb-0">相关洞察反馈</p>
            <span class="text-[11px] text-txt-3" x-text="(feedbackSummary.up||0) + ' 👍 · ' + (feedbackSummary.down||0) + ' 👎'"></span>
          </div>
          <div class="mt-2 space-y-2">
            <template x-for="m in feedbackItems" :key="m.memory_id">
              <div class="rounded-lg bg-white/5 p-2.5">
                <p class="text-[12px] leading-relaxed text-txt-1" x-text="m.text"></p>
                <div class="flex items-center justify-between mt-1.5">
                  <span class="text-[10px] text-txt-3" x-text="'信任 ' + ((m.trust>=0?'+':'') + (m.trust||0).toFixed(2)) + ' · ' + m.state"></span>
                  <div class="flex gap-1">
                    <button class="icon-btn" title="有用" @click.stop="fbMemory(m, true)">👍</button>
                    <button class="icon-btn" title="无用" @click.stop="fbMemory(m, false)">👎</button>
                  </div>
                </div>
              </div>
            </template>
            <p x-show="!feedbackItems.length" class="text-xs text-txt-3">暂无该成员相关洞察（研究员产出的洞察会带 member: 标签）</p>
          </div>
        </div>
      </div>
    </template>
  </div>
</div>

<!-- 合并成员弹窗 -->
<div class="fixed inset-0 z-50 grid place-items-center" x-show="mergeOpen" x-cloak>
  <div class="absolute inset-0 bg-black/80" @click="mergeOpen = false"></div>
  <div class="relative card bg-ink-900 w-[420px] max-w-[92vw] anim-in">
    <div class="p-5 border-b border-white/10 flex items-center justify-between shrink-0">
      <h3 class="font-semibold">合并成员</h3>
      <button class="icon-btn" @click="mergeOpen = false" title="关闭">
        <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
      </button>
    </div>
    <div class="p-5 space-y-4">
      <p class="text-[13px] text-txt-2 leading-relaxed">
        把「<b x-text="detail && detail.name"></b>」合并进另一位成员。合并后<b class="text-danger">当前成员将被删除</b>，其数据并入目标成员。
      </p>
      <div>
        <label class="lbl">合并到</label>
        <select class="inp" x-model="mergeTargetId">
          <option value="">请选择目标成员…</option>
          <template x-for="m in members.filter(x => !detail || x.id !== detail.id)" :key="m.id">
            <option :value="m.id" x-text="m.name + ((m.rooms||[]).length ? '（' + m.rooms.join('、') + '）' : '')"></option>
          </template>
        </select>
      </div>
      <div class="rounded-lg bg-white/5 p-3 text-[11px] text-txt-3 space-y-1">
        <p class="font-medium text-txt-2">合并策略（非破坏式，目标优先）：</p>
        <p>· 关联房间与专属设备：取并集</p>
        <p>· 生活习惯标签：同名标签保留置信度更高的一条，证据合并</p>
        <p>· 外观特征 / 人脸 / 管家档案 / 备注：目标为空才继承</p>
      </div>
    </div>
    <div class="p-5 border-t border-white/10 flex justify-end gap-2 bg-ink-900 shrink-0">
      <button class="btn-ghost" @click="mergeOpen = false">取消</button>
      <button class="btn-danger" @click="doMerge()" :disabled="merging || !mergeTargetId">
        <span x-show="merging" class="spinner"></span>确认合并
      </button>
    </div>
  </div>
</div>
`;

export const membersPage = () => ({
  tpl: TPL,
  loading: false,
  members: [],
  roomsOptions: [],
  deviceOptions: [],
  deviceSearch: '',
  drawerOpen: false,
  detail: null,
  mergeOpen: false,
  mergeTargetId: '',
  merging: false,
  formOpen: false,
  saving: false,
  avatarSaving: false,
  addTagOpen: false,
  feedbackItems: [],
  feedbackSummary: {},
  form: { id: '', name: '', avatar_emoji: '🦉', avatar_bg: '#0EA5E9', avatar_url: '', note: '',
    gender: '', approx_age: '', body_type: '', body_type_custom: '', weight: '', height: '', clothing: '',
    hair: '', typical_location: '' },
  apOpen: false,
  apErr: '',
  tagForm: { tag: '', category: 'other', emoji: '' },
  emojiPresets: EMOJI_PRESETS,
  bgPresets: BG_PRESETS,
  tagCategories: TAG_CATEGORIES,
  genderOptions: GENDER_OPTIONS,
  bodyTypeOptions: BODY_TYPE_OPTIONS,

  init() {
    this.load();
    this.loadHierarchy();
  },

  async load() {
    this.loading = true;
    try {
      const d = await api.members();
      this.members = d.members || [];
    } catch (e) {
      this.$store.app.err(e.message || '加载成员失败');
    } finally {
      this.loading = false;
    }
  },

  async loadHierarchy() {
    try {
      const d = await api.haRooms();
      const rooms = d.rooms || {};
      this.roomsOptions = Object.keys(rooms);
      const devices = [];
      for (const [room, payload] of Object.entries(rooms)) {
        const ents = (payload && payload.entities) || {};
        for (const [entity_id, info] of Object.entries(ents)) {
          devices.push({
            entity_id,
            friendly_name: (info && info.friendly_name) || entity_id,
            room,
          });
        }
      }
      this.deviceOptions = devices;
    } catch (e) {
      this.roomsOptions = [];
      this.deviceOptions = [];
    }
  },

  get filteredDevices() {
    const q = (this.deviceSearch || '').trim().toLowerCase();
    return (this.deviceOptions || []).filter((d) => {
      if (!q) return true;
      const name = (d.friendly_name || d.name || '').toLowerCase();
      return name.includes(q) || d.entity_id.toLowerCase().includes(q);
    });
  },

  cloudStyle(t, idx) {
    const seed = this.hashCode(t.tag || '') + (idx || 0) * 31;
    const rotate = (seed % 14) - 7;
    const scale = 0.88 + ((t.confidence || 0.5) * 0.32);
    const tx = (seed % 18) - 9;
    const ty = (seed % 12) - 6;
    const fontSize = 0.78 + ((t.confidence || 0.5) * 0.42);
    return `transform: rotate(${rotate}deg) translate(${tx}px, ${ty}px) scale(${scale}); font-size: ${fontSize}rem;`;
  },

  hashCode(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h << 5) - h + s.charCodeAt(i);
    return h;
  },

  get detailAppearance() {
    return this.parseAp(this.detail);
  },

  genderLabel(v) {
    const g = (GENDER_OPTIONS || []).find((o) => o.v === v);
    return g ? g.label : v;
  },

  clothingText(c) {
    if (!c) return '';
    if (typeof c === 'string') return c;
    const parts = [];
    const top = [c.top_color, c.top_style].filter(Boolean).join(' ');
    const bottom = [c.bottom_color, c.bottom_style].filter(Boolean).join(' ');
    if (top) parts.push('上装：' + top);
    if (bottom) parts.push('下装：' + bottom);
    if (c.distinctive) parts.push(c.distinctive);
    return parts.join('；');
  },

  openCreate() {
    this.form = { id: '', name: '', avatar_emoji: '🦉', avatar_bg: '#0EA5E9', avatar_url: '', note: '',
      gender: '', approx_age: '', body_type: '', body_type_custom: '', weight: '', height: '', clothing: '',
      hair: '', typical_location: '' };
    this.apOpen = false;
    this.apErr = '';
    this.formOpen = true;
  },

  parseAp(m) {
    let ap = m && m.appearance_json;
    if (typeof ap === 'string') {
      try { ap = JSON.parse(ap); } catch (e) { ap = null; }
    }
    return (ap && typeof ap === 'object') ? ap : {};
  },

  openEdit(m) {
    const ap = this.parseAp(m);
    let clothingStr = '';
    if (typeof ap.clothing === 'string') {
      clothingStr = ap.clothing;
    } else if (ap.clothing && typeof ap.clothing === 'object') {
      clothingStr = Object.values(ap.clothing).filter(Boolean).join('、');
    }
    this.form = {
      id: m.id,
      name: m.name,
      avatar_emoji: m.avatar_emoji || '🦉',
      avatar_bg: m.avatar_bg || '#0EA5E9',
      avatar_url: m.avatar_url || '',
      note: m.note || '',
      gender: ap.gender || '',
      approx_age: ap.approx_age || '',
      body_type: ap.body_type || '',
      body_type_custom: ap.body_type_custom || '',
      weight: ap.weight || '',
      height: ap.height || '',
      clothing: clothingStr,
      hair: ap.hair || '',
      typical_location: ap.typical_location || '',
    };
    this.apOpen = !!clothingStr || !!ap.gender || !!ap.approx_age || !!ap.hair;
    this.apErr = '';
    this.formOpen = true;
  },

  closeForm() {
    this.formOpen = false;
  },

  compressImage(dataUrl, maxWidth = 320, quality = 0.80) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        let { width, height } = img;
        if (width > height) {
          if (width > maxWidth) { height = Math.round(height * maxWidth / width); width = maxWidth; }
        } else {
          if (height > maxWidth) { width = Math.round(width * maxWidth / height); height = maxWidth; }
        }
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#111827';
        ctx.fillRect(0, 0, width, height);
        ctx.drawImage(img, 0, 0, width, height);
        resolve(canvas.toDataURL('image/jpeg', quality));
      };
      img.onerror = () => reject(new Error('图片加载失败'));
      img.src = dataUrl;
    });
  },

  handleAvatarUpload(event, onLoaded) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      this.$store.app.err('图片大小请控制在 5MB 以内');
      event.target.value = '';
      return;
    }
    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        const compressed = await this.compressImage(e.target.result);
        this.form.avatar_url = compressed;
        if (typeof onLoaded === 'function') onLoaded(compressed);
      } catch (err) {
        this.$store.app.err(err.message || '图片压缩失败');
      }
      event.target.value = '';
    };
    reader.onerror = () => this.$store.app.err('图片读取失败');
    reader.readAsDataURL(file);
  },

  handleAvatarUploadDetail(event) {
    this.handleAvatarUpload(event, async (compressed) => {
      if (!this.detail || !compressed) return;
      const previous = this.detail.avatar_url;
      this.detail.avatar_url = compressed;
      this.avatarSaving = true;
      try {
        await this.saveAvatarUrlToDetail();
        this.$store.app.ok('头像已保存');
      } catch (err) {
        this.detail.avatar_url = previous;
        this.$store.app.err(err.message || '头像保存失败');
      } finally {
        this.avatarSaving = false;
      }
    });
  },

  async saveAvatarUrlToDetail() {
    if (!this.detail) return;
    await api.updateMember(this.detail.id, {
      avatar_url: this.detail.avatar_url,
      avatar_emoji: '',
    });
    await this.refreshDetail(this.detail.id);
  },

  clearAvatarUrl() {
    this.form.avatar_url = '';
  },

  buildAppearance() {
    const f = this.form;
    const ap = {};
    if (f.gender) ap.gender = f.gender;
    if (f.approx_age) ap.approx_age = f.approx_age;
    if (f.body_type) ap.body_type = f.body_type;
    if ((f.body_type_custom || '').trim()) ap.body_type_custom = f.body_type_custom.trim();
    if (f.weight) ap.weight = f.weight;
    if (f.height) ap.height = f.height;
    if ((f.clothing || '').trim()) ap.clothing = f.clothing.trim();
    if (f.hair) ap.hair = f.hair;
    if (f.typical_location) ap.typical_location = f.typical_location;
    return ap;
  },

  async saveForm() {
    const name = (this.form.name || '').trim();
    if (!name) {
      this.$store.app.err('请填写成员姓名');
      return;
    }
    const appearance = this.buildAppearance();
    const hasGender = !!appearance.gender;
    const hasDistinct = !!(
      appearance.approx_age || appearance.body_type || appearance.body_type_custom ||
      appearance.weight || appearance.hair ||
      appearance.height || appearance.typical_location || appearance.clothing
    );
    if (!hasGender && !hasDistinct) {
      this.apOpen = true;
      this.apErr = '外观档案至少填写「性别」或一项可区分特征（年龄 / 身材 / 穿搭 / 发型）';
      return;
    }
    this.apErr = '';
    this.saving = true;
    try {
      const payload = {
        name,
        avatar_emoji: this.form.avatar_url ? '' : this.form.avatar_emoji,
        avatar_bg: this.form.avatar_bg,
        avatar_url: this.form.avatar_url,
        note: this.form.note,
        appearance_json: Object.keys(appearance).length ? appearance : '',
      };
      if (this.form.id) {
        await api.updateMember(this.form.id, payload);
      } else {
        await api.createMember(payload);
      }
      this.$store.app.ok(this.form.id ? '成员已更新' : '成员已创建');
      this.formOpen = false;
      await this.load();
    } catch (e) {
      this.$store.app.err(e.message || '保存失败');
    } finally {
      this.saving = false;
    }
  },

  async removeMember(m) {
    if (!confirm('确定删除成员「' + m.name + '」？相关房间、设备与标签也会一并清除。')) return;
    try {
      await api.deleteMember(m.id);
      this.$store.app.ok('成员已删除');
      if (this.detail && this.detail.id === m.id) this.drawerOpen = false;
      await this.load();
    } catch (e) {
      this.$store.app.err(e.message || '删除失败');
    }
  },

  async doMerge() {
    if (!this.detail || !this.mergeTargetId) {
      this.$store.app.err('请选择要合并到的目标成员');
      return;
    }
    const source = this.detail;
    const target = this.members.find((m) => m.id === this.mergeTargetId);
    if (!target) {
      this.$store.app.err('目标成员不存在');
      return;
    }
    const yes = await this.$store.app.ask(
      '合并成员',
      '把「' + source.name + '」合并进「' + target.name + '」？合并后「' + source.name + '」将被删除，房间/设备/标签并入目标。此操作不可撤销。',
      '合并'
    );
    if (!yes) return;
    this.merging = true;
    try {
      await api.mergeMember(source.id, target.id);
      this.$store.app.ok('已把「' + source.name + '」合并进「' + target.name + '」');
      this.mergeOpen = false;
      this.mergeTargetId = '';
      this.drawerOpen = false;
      await this.load();
    } catch (e) {
      this.$store.app.err(e.message || '合并失败');
    } finally {
      this.merging = false;
    }
  },

  openDetail(m) {
    this.detail = m;
    this.deviceSearch = '';
    this.addTagOpen = false;
    this.drawerOpen = true;
    this.loadMemberFeedback(m.id);
  },

  closeDrawer() {
    this.drawerOpen = false;
  },

  async toggleRoom(room) {
    if (!this.detail) return;
    const id = this.detail.id;
    const set = new Set(this.detail.rooms || []);
    if (set.has(room)) set.delete(room);
    else set.add(room);
    try {
      await api.assignMemberRooms(id, [...set]);
      this.$store.app.ok('关联房间已更新');
      await this.refreshDetail(id);
    } catch (e) {
      this.$store.app.err(e.message || '更新失败');
    }
  },

  async toggleDevice(entity_id) {
    if (!this.detail) return;
    const id = this.detail.id;
    const set = new Set(this.detail.devices || []);
    if (set.has(entity_id)) set.delete(entity_id);
    else set.add(entity_id);
    try {
      await api.assignMemberDevices(id, [...set]);
      this.$store.app.ok('专属设备已更新');
      await this.refreshDetail(id);
    } catch (e) {
      this.$store.app.err(e.message || '更新失败');
    }
  },

  async refreshDetail(id) {
    await this.load();
    this.detail = this.members.find((m) => m.id === id) || this.detail;
  },

  async removeTag(tag) {
    if (!this.detail) return;
    const id = this.detail.id;
    try {
      await api.deleteMemberTag(id, tag);
      this.$store.app.ok('标签已删除');
      await this.refreshDetail(id);
    } catch (e) {
      this.$store.app.err(e.message || '删除失败');
    }
  },

  async addManualTag() {
    const tag = (this.tagForm.tag || '').trim();
    if (!tag) {
      this.$store.app.err('请填写标签名');
      return;
    }
    const id = this.detail.id;
    try {
      await api.addMemberTag(id, {
        tag,
        category: this.tagForm.category || 'other',
        emoji: this.tagForm.emoji || '',
        source: 'manual',
      });
      this.$store.app.ok('标签已添加');
      this.tagForm = { tag: '', category: 'other', emoji: '' };
      this.addTagOpen = false;
      await this.refreshDetail(id);
    } catch (e) {
      this.$store.app.err(e.message || '添加失败');
    }
    },

    async loadMemberFeedback(id) {
    try {
    const d = await api.memberInsightFeedback(id);
    this.feedbackItems = d.memories || [];
    this.feedbackSummary = { up: d.up || 0, down: d.down || 0 };
    } catch (e) {
    this.feedbackItems = [];
    this.feedbackSummary = {};
    }
    },

    async fbMemory(m, useful) {
    try {
    await api.agentMemoryFeedback(m.memory_id, useful);
    this.$store.app.ok(useful ? '已标记有用' : '已标记无用');
    if (this.detail) await this.loadMemberFeedback(this.detail.id);
    } catch (e) {
    this.$store.app.err(e.message || '反馈失败');
    }
    },
    });
