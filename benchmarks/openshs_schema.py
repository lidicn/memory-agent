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
