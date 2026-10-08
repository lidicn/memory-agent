"""OpenSHS ↔ Memory-Agent 活动识别基准 —— 字段与标签映射。

OpenSHS 数据集是「宽表」格式：每行 = 某秒所有传感器的 0/1 状态 + 一个
``Activity`` 活动标签 + 时间戳。Memory-Agent 的 ``infer_activities`` 则消费
「长表」格式的事件流（``entity_id`` / ``new_state`` / ``room`` / ``ts``），
并用 ``_TAG_RULES`` 从 entity_id 子串打标。

本模块把 29 个 OpenSHS 传感器列映射到 MA 的实体 / 房间 / 标签，使转换后的
事件流能被 MA 的检测器正确识别，而无需配置 ``name_map``（打标只看 entity_id
子串，``_tags_of`` 已覆盖）。
"""

from __future__ import annotations

# OpenSHS 宽表列顺序（与仓库示例 dataset.csv 一致）。
OPENSHS_COLUMNS = [
    "wardrobe", "tv", "oven", "officeLight", "officeDoorLock", "officeDoor",
    "officeCarp", "office", "mainDoorLock", "mainDoor", "livingLight",
    "livingCarp", "kitchenLight", "kitchenDoorLock", "kitchenDoor",
    "kitchenCarp", "hallwayLight", "fridge", "couch", "bedroomLight",
    "bedroomDoorLock", "bedroomDoor", "bedroomCarp", "bedTableLamp", "bed",
    "bathroomLight", "bathroomDoorLock", "bathroomDoor", "bathroomCarp",
    "Activity", "timestamp",
]

# 列名 -> (entity_id, 房间, domain 前缀)。
# entity_id 故意带 MA ``_TAG_RULES`` 识别的子串（media / switch. / light. /
# presence / door），保证空 name_map 下也能正确打标。
SENSOR_MAP: dict[str, tuple[str, str, str]] = {
    "wardrobe":          ("binary_sensor.openshs_wardrobe",          "卧室",   "binary_sensor"),
    "tv":                ("media_player.openshs_tv",                 "客厅",   "media_player"),
    "oven":              ("switch.openshs_oven",                    "厨房",   "switch"),
    "officeLight":       ("light.openshs_office_light",             "书房",   "light"),
    "officeDoorLock":    ("binary_sensor.openshs_office_door_lock", "书房",   "binary_sensor"),
    "officeDoor":        ("binary_sensor.openshs_office_door",      "书房",   "binary_sensor"),
    "officeCarp":        ("binary_sensor.openshs_office_presence",  "书房",   "binary_sensor"),
    "office":            ("binary_sensor.openshs_office_room",      "书房",   "binary_sensor"),
    "mainDoorLock":      ("binary_sensor.openshs_main_door_lock",   "玄关",   "binary_sensor"),
    "mainDoor":          ("binary_sensor.openshs_main_door",        "玄关",   "binary_sensor"),
    "livingLight":       ("light.openshs_living_light",             "客厅",   "light"),
    "livingCarp":        ("binary_sensor.openshs_living_presence",  "客厅",   "binary_sensor"),
    "kitchenLight":      ("light.openshs_kitchen_light",            "厨房",   "light"),
    "kitchenDoorLock":   ("binary_sensor.openshs_kitchen_door_lock","厨房",   "binary_sensor"),
    "kitchenDoor":       ("binary_sensor.openshs_kitchen_door",     "厨房",   "binary_sensor"),
    "kitchenCarp":       ("binary_sensor.openshs_kitchen_presence", "厨房",   "binary_sensor"),
    "hallwayLight":      ("light.openshs_hallway_light",            "走廊",   "light"),
    "fridge":            ("switch.openshs_fridge",                  "厨房",   "switch"),
    "couch":             ("binary_sensor.openshs_couch",            "客厅",   "binary_sensor"),
    "bedroomLight":      ("light.openshs_bedroom_light",            "卧室",   "light"),
    "bedroomDoorLock":   ("binary_sensor.openshs_bedroom_door_lock","卧室",   "binary_sensor"),
    "bedroomDoor":       ("binary_sensor.openshs_bedroom_door",     "卧室",   "binary_sensor"),
    "bedroomCarp":       ("binary_sensor.openshs_bedroom_presence", "卧室",   "binary_sensor"),
    "bedTableLamp":      ("light.openshs_bed_table_lamp",           "卧室",   "light"),
    "bed":               ("binary_sensor.openshs_bed",              "卧室",   "binary_sensor"),
    "bathroomLight":     ("light.openshs_bathroom_light",           "卫生间", "light"),
    "bathroomDoorLock":  ("binary_sensor.openshs_bathroom_door_lock","卫生间","binary_sensor"),
    "bathroomDoor":      ("binary_sensor.openshs_bathroom_door",    "卫生间", "binary_sensor"),
    "bathroomCarp":      ("binary_sensor.openshs_bathroom_presence","卫生间", "binary_sensor"),
}

# OpenSHS 活动标签 -> MA 活动名。只映射有 MA 对应物的活动；
# 不在表中的 OpenSHS 活动（如 relax / read）在评估时被忽略（既不算 TP 也不算 FP/FN）。
ACTIVITY_MAP: dict[str, str] = {
    "sleep":       "sleeping",
    "cook":        "cooking",
    "watchTV":     "watching_tv",
    "work":        "working",
    "bathe":       "bathing",
    "useToilet":   "bathing",
    "leaveHouse":  "away",
    "eat":         "cooking",
}

# 评估时纳入对比的 MA 活动集合（与 ACTIVITY_MAP 的值域一致）。
EVALUATED_ACTIVITIES = sorted(set(ACTIVITY_MAP.values()))

# 真实 OpenSHS 公开数据集（plolutta/dataset，OpenSHS 模拟生成）采用「粗粒度 7 分类」
# 标注：sleep / eat / work / leisure / personal / other / anomaly。其中 only
# sleep / eat / work 与 MA 活动一一对应；leisure（居家休闲，以看电视为主）与
# personal（个人护理，如洗澡/如厕）分别近似映射到 MA 的 watching_tv / bathing。
# other / anomaly 无 MA 对应物，评估时忽略。
#
# 这是直接可下载的「真实」OpenSHS 数据集；更细粒度的 Mendeley「Smart Home
# Dataset」（fillKettle/boilWater/makeTea/...）标注更贴合 ACTIVITY_MAP，但需
# 经 Mendeley 交互下载，本机网络不可达，故用此粗粒度集跑真实基线，并在报告中
# 说明该局限。
COARSE_ACTIVITY_MAP: dict[str, str] = {
    "sleep": "sleeping",
    "eat": "cooking",
    "work": "working",
    "leisure": "watching_tv",
    "personal": "bathing",
}


def get_activity_map(name: str = "fine") -> dict[str, str]:
    """返回评估用的标签映射。'fine' = ACTIVITY_MAP（样本/细粒度数据集），
    'coarse' = COARSE_ACTIVITY_MAP（真实粗粒度 7 分类数据集）。"""
    return COARSE_ACTIVITY_MAP if name == "coarse" else ACTIVITY_MAP


# ── 方案A 适配：旧活动名 → 时段启发式标签（benchmark 侧，不动生产代码）──────────
# ``infer_activities``（insights/service.py ``_label_activity`` /
# ``_infer_activities``）已从「规则识别具体活动」重构为「按连续活跃时段打
# 时段启发式标签」。生产侧可能输出的**全部标签**枚举如下（改动生产标签时须
# 同步本表，否则基准口径断裂）：
#   * 夜间活动           —— 时段整体结束于 05 时（end_hour < 6）
#   * 晨间活动           —— 时段起始 < 09 时
#   * 日间活动           —— 时段起始 < 18 时
#   * 晚间娱乐           —— 起始 ≥ 18 时且主导 domain 为 media_player
#   * 晚间照明/开关调整   —— 起始 ≥ 18 时且主导 domain 为 light / switch
#   * 晚间活动           —— 起始 ≥ 18 时的其余情况
#   * 可能离家           —— 09:00-18:00 内出现 ≥3 小时连续无事件静默
PERIOD_LABELS: tuple[str, ...] = (
    "夜间活动", "晨间活动", "日间活动",
    "晚间娱乐", "晚间照明/开关调整", "晚间活动",
    "可能离家",
)

# 旧 MA 活动名 → 该活动在家庭作息下**可能**落入的时段标签集合（确定性多对多
# 映射；以生产侧时段桶语义为准，不臆造标签）。未列出的活动回退为「标签名自
# 身」口径（legacy 规则匹配输出与预测同名时仍可直接命中）。
#   sleeping    睡眠跨 00-05（夜间）与晨起 06-08（晨间）；
#   cooking     早/午/晚三餐 → 晨间、日间、晚间（厨房开关/灯主导 →
#               晚间照明/开关调整 或 其余主导 → 晚间活动）；
#   watching_tv 电视以晚间为主（media 主导 → 晚间娱乐；binary 主导 → 晚间活动），
#               日间观看 → 日间活动；
#   working     工作以日间为主，加班/晚间书房 → 晚间活动；
#   bathing     晨浴/午浴/晚浴皆可能；
#   away        「可能离家」即静默判离家，直接对应 leaveHouse。
LEGACY_TO_PERIOD: dict[str, frozenset[str]] = {
    "sleeping":    frozenset({"夜间活动", "晨间活动"}),
    "cooking":     frozenset({"晨间活动", "日间活动", "晚间活动",
                              "晚间照明/开关调整"}),
    "watching_tv": frozenset({"日间活动", "晚间娱乐", "晚间活动"}),
    "working":     frozenset({"日间活动", "晚间活动"}),
    "bathing":     frozenset({"晨间活动", "日间活动", "晚间活动",
                              "晚间照明/开关调整"}),
    "away":        frozenset({"可能离家"}),
}


def legacy_activity_hit(act: str, pred_labels) -> bool:
    """判定口径（GT 侧旧活动名 vs 预测侧时段标签）：

    当天预测出的时段标签集合与 ``LEGACY_TO_PERIOD[act]`` 有交集，即视为
    「该活动被预测到」。ground truth 与预测两侧经此函数统一口径。
    """
    return bool(set(pred_labels or ())
                & LEGACY_TO_PERIOD.get(act, frozenset({act})))


def period_to_legacy(labels) -> set[str]:
    """时段标签 → 可能对应的旧活动名（LEGACY_TO_PERIOD 的逆向展开）。

    多对一坍缩是方案A 语义的固有代价：一个「日间活动」记录同时兼容
    cooking/working/bathing/watching_tv。仅供 day/slot/segment 级评估
    保持口径一致；结论应理解为「时段覆盖度」而非子活动判别。
    """
    out: set[str] = set()
    for lb in (labels or ()):
        for act, periods in LEGACY_TO_PERIOD.items():
            if lb in periods:
                out.add(act)
    return out
