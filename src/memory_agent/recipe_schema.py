"""recipe_schema —— 查询剧本（Recipe）数据结构 / 校验 / 序列化。

职责边界（严格遵守本单约束）：
只定义 schema、内容确定性 ID、结构与语义校验、JSON 序列化、参数占位符替换；
不实现回填、召回、存储、调度等任何业务逻辑。

关键语义：
* recipe_id 由"语义核心"确定性生成（intent / object_type / metric / time_window /
  person / tool_sequence），与 created_at / confidence / sample_count / status /
  source_session 无关。不同 session 产生的同构查询会收敛到同一个 id，便于回填去重。
* validate_recipe() 永不抛异常，返回错误列表；列表非空即视为非法（fail-closed 在调用方）。
* deserialize_recipe() 对结构错误 fail-closed（抛 ValueError）；对枚举值漂移
  fail-open（保留原值），保证未来新增取值也能无损 round-trip。
* resolve_params() 对未知占位符 / 畸形花括号保留原样（fail-open），
  不会因为缺上下文而抛错。
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union

__all__ = [
    "Recipe",
    "ToolStep",
    "KNOWN_INTENTS",
    "KNOWN_METRICS",
    "KNOWN_OBJECT_TYPES",
    "KNOWN_TIME_WINDOWS",
    "KNOWN_STATUSES",
    "REQUIRED_FIELDS",
    "OPTIONAL_FIELDS",
    "make_recipe_id",
    "validate_recipe",
    "serialize_recipe",
    "deserialize_recipe",
    "resolve_params",
    "list_known_intents",
    "list_known_metrics",
    "list_known_object_types",
    "list_known_time_windows",
    "list_known_statuses",
    "build_recipe",
]

# ---------------------------------------------------------------- 枚举契约

KNOWN_INTENTS = ("device_usage", "compare", "anomaly", "presence", "routine", "arrival")
KNOWN_OBJECT_TYPES = ("device", "room", "activity", "person", "whole_house")
KNOWN_METRICS = ("duration", "count", "numeric_sum", "state_share", "none")
KNOWN_TIME_WINDOWS = (
    "today", "yesterday", "last_7_days", "last_30_days", "week_over_week", "custom",
)
KNOWN_STATUSES = ("staging", "live", "revoked")

REQUIRED_FIELDS = (
    "recipe_id", "intent", "object_type", "metric", "time_window", "tool_sequence",
    "confidence", "sample_count", "created_at", "source_session", "status",
)
OPTIONAL_FIELDS = ("person",)

RECIPE_ID_PREFIX = "recipe_"
RECIPE_ID_BODY_LENGTH = 8
_HEX_DIGITS = frozenset("0123456789abcdef")

# {placeholder} 的最小语法：首字符字母/下划线，其余字母/数字/下划线。
_PLACEHOLDER_FIRST = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
)
_PLACEHOLDER_REST = _PLACEHOLDER_FIRST | frozenset("0123456789")


# ---------------------------------------------------------------- 数据结构

@dataclass
class ToolStep:
    """工具调用序列中的一步。"""

    tool_name: str
    params: Dict[str, Any] = field(default_factory=dict)
    depends_on: str = ""

    def __post_init__(self):
        if isinstance(self.params, Mapping):
            self.params = _jsonable(self.params)   # 深规范化 + 深拷贝，杜绝别名共享
        if self.depends_on is None:
            self.depends_on = ""

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        # type: (Mapping[str, Any]) -> "ToolStep"
        if not isinstance(data, Mapping):
            raise ValueError(
                "ToolStep.from_dict: expected a mapping, got %s" % type(data).__name__
            )
        params = data.get("params", {})
        if params is None:
            params = {}
        return cls(
            tool_name=data.get("tool_name", ""),
            params=params,
            depends_on=data.get("depends_on", "") or "",
        )


@dataclass
class Recipe:
    """一条查询剧本（recipe），字段语义见交付单 §3。"""

    recipe_id: str
    intent: str
    object_type: str
    metric: str
    time_window: str
    tool_sequence: List[ToolStep]
    confidence: float = 0.0
    sample_count: int = 0
    created_at: str = ""
    source_session: str = ""
    status: str = "staging"
    person: str = ""

    def __post_init__(self):
        if isinstance(self.tool_sequence, (list, tuple)):
            steps = []
            for item in self.tool_sequence:
                step = _coerce_tool_step(item)
                steps.append(item if step is None else step)
            self.tool_sequence = steps
        if self.person is None:
            self.person = ""

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        # type: (Mapping[str, Any]) -> "Recipe"
        """结构层 fail-closed：缺字段/错类型抛 ValueError；枚举漂移保留原值。"""
        if not isinstance(data, Mapping):
            raise ValueError(
                "Recipe.from_dict: expected a JSON object, got %s" % type(data).__name__
            )
        errors = _structural_errors(data)
        if errors:
            raise ValueError("Recipe.from_dict: " + "; ".join(errors))
        values = {name: data[name] for name in REQUIRED_FIELDS}
        for name in OPTIONAL_FIELDS:
            if name in data and data[name] is not None:
                values[name] = data[name]
        steps = []
        for item in values["tool_sequence"]:
            step = _coerce_tool_step(item)
            if step is None:
                raise ValueError("Recipe.from_dict: unsupported tool step %r" % (item,))
            steps.append(step)
        values["tool_sequence"] = steps
        return cls(**values)


# ---------------------------------------------------------------- 内部工具

def _jsonable(value):
    if isinstance(value, Mapping):
        return {
            (key if isinstance(key, str) else str(key)): _jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _coerce_tool_step(item):
    if isinstance(item, ToolStep):
        return item
    if isinstance(item, Mapping):
        return ToolStep.from_dict(item)
    return None


def _is_digits(text):
    return len(text) > 0 and all(ch in "0123456789" for ch in text)


def _is_recipe_id(value):
    if not isinstance(value, str) or not value.startswith(RECIPE_ID_PREFIX):
        return False
    body = value[len(RECIPE_ID_PREFIX):]
    return len(body) == RECIPE_ID_BODY_LENGTH and all(ch in _HEX_DIGITS for ch in body)


def _is_iso8601_timestamp(value):
    """接受常见 ISO 8601 变体：T/空格分隔、可选小数秒、可选 Z / ±HH:MM / ±HHMM。"""
    if not isinstance(value, str) or len(value) < 19:
        return False
    if value[4] != "-" or value[7] != "-":
        return False
    if not (_is_digits(value[0:4]) and _is_digits(value[5:7]) and _is_digits(value[8:10])):
        return False
    month = int(value[5:7])
    day = int(value[8:10])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return False
    if value[10] not in ("T", "t", " "):
        return False
    if value[13] != ":" or value[16] != ":":
        return False
    if not (_is_digits(value[11:13]) and _is_digits(value[14:16]) and _is_digits(value[17:19])):
        return False
    if not (int(value[11:13]) < 24 and int(value[14:16]) < 60 and int(value[17:19]) < 60):
        return False
    tail = value[19:]
    if tail.startswith("."):
        digits = tail[1:]
        count = 0
        while count < len(digits) and _is_digits(digits[count]):
            count += 1
        if count == 0:
            return False
        tail = digits[count:]
    if tail == "" or tail in ("Z", "z"):
        return True
    if tail[0] in ("+", "-"):
        zone = tail[1:]
        if len(zone) == 5 and zone[2] == ":" and _is_digits(zone[0:2]) and _is_digits(zone[3:5]):
            return int(zone[0:2]) <= 23 and int(zone[3:5]) <= 59
        if len(zone) == 4 and _is_digits(zone):
            return int(zone[0:2]) <= 23 and int(zone[2:4]) <= 59
        return False
    return False


def _scan_placeholder(text, start):
    """若 text[start:] 以合法 {name} 开头，返回 (name, 结束位置)，否则 None。"""
    if start >= len(text) or text[start] != "{":
        return None
    index = start + 1
    if index >= len(text) or text[index] not in _PLACEHOLDER_FIRST:
        return None
    index += 1
    while index < len(text) and text[index] in _PLACEHOLDER_REST:
        index += 1
    if index < len(text) and text[index] == "}":
        return text[start + 1:index], index + 1
    return None


def _substitute_embedded(text, context):
    if "{" not in text:
        return text
    out = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "{":
            hit = _scan_placeholder(text, index)
            if hit is not None:
                name, end = hit
                bound = context.get(name)
                if name in context and bound is not None:
                    out.append(str(bound))       # 嵌入字符串 → 字符串化
                else:
                    out.append(text[index:end])  # 未绑定 → 保留原样
                index = end
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _resolve_value(value, context):
    if isinstance(value, str):
        hit = _scan_placeholder(value, 0)
        if hit is not None and hit[1] == len(value):
            name = hit[0]
            if name in context and context[name] is not None:
                return context[name]             # 整串占位符 → 保留原始类型
            return value
        return _substitute_embedded(value, context)
    if isinstance(value, Mapping):
        return {key: _resolve_value(item, context) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_resolve_value(item, context) for item in value]
    return value                                 # 数字/布尔/None 原样返回


def _step_errors(item, path):
    errors = []  # type: List[str]
    if isinstance(item, ToolStep):
        data = item.to_dict()
    elif isinstance(item, Mapping):
        data = item
    else:
        return ["%s: expected mapping or ToolStep, got %s" % (path, type(item).__name__)]
    if "tool_name" not in data or data.get("tool_name") is None:
        errors.append("%s.tool_name: missing required field" % path)
    elif not isinstance(data["tool_name"], str):
        errors.append("%s.tool_name: expected str, got %s" % (path, type(data["tool_name"]).__name__))
    elif data["tool_name"] == "":
        errors.append("%s.tool_name: must not be empty" % path)
    params = data.get("params")
    if "params" in data and params is not None:
        if not isinstance(params, Mapping):
            errors.append("%s.params: expected dict, got %s" % (path, type(params).__name__))
        else:
            for key in params:
                if not isinstance(key, str):
                    errors.append("%s.params: keys must be str, got %r" % (path, key))
    depends_on = data.get("depends_on")
    if "depends_on" in data and depends_on is not None and not isinstance(depends_on, str):
        errors.append("%s.depends_on: expected str, got %s" % (path, type(depends_on).__name__))
    return errors


def _structural_errors(data):
    """结构层校验：必填存在 + 非 null + 类型正确（不含枚举/区间/格式）。"""
    errors = []  # type: List[str]
    for name in REQUIRED_FIELDS:
        if name not in data:
            errors.append("%s: missing required field" % name)
        elif data[name] is None:
            errors.append("%s: must not be null" % name)

    simple_str = ("intent", "object_type", "metric", "time_window",
                  "created_at", "source_session", "status")
    for name in simple_str:
        value = data.get(name)
        if name in data and value is not None and not isinstance(value, str):
            errors.append("%s: expected str, got %s" % (name, type(value).__name__))

    recipe_id = data.get("recipe_id")
    if "recipe_id" in data and recipe_id is not None and not isinstance(recipe_id, str):
        errors.append("recipe_id: expected str, got %s" % type(recipe_id).__name__)

    person = data.get("person")
    if "person" in data and person is not None and not isinstance(person, str):
        errors.append("person: expected str, got %s" % type(person).__name__)

    confidence = data.get("confidence")
    if "confidence" in data and confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            errors.append("confidence: expected number, got %s" % type(confidence).__name__)

    sample_count = data.get("sample_count")
    if "sample_count" in data and sample_count is not None:
        if isinstance(sample_count, bool) or not isinstance(sample_count, int):
            errors.append("sample_count: expected int, got %s" % type(sample_count).__name__)

    steps = data.get("tool_sequence")
    if "tool_sequence" in data and steps is not None:
        if not isinstance(steps, (list, tuple)):
            errors.append("tool_sequence: expected list of tool steps, got %s" % type(steps).__name__)
        else:
            for index, item in enumerate(steps):
                errors.extend(_step_errors(item, "tool_sequence[%d]" % index))
    return errors


def _check_enum(data, name, allowed, errors):
    value = data.get(name)
    if not isinstance(value, str):
        return
    if value not in allowed:
        errors.append("%s: unknown value %r (expected one of: %s)" % (name, value, ", ".join(allowed)))


def _as_mapping_copy(recipe):
    if isinstance(recipe, Recipe):
        return recipe.to_dict()
    if isinstance(recipe, Mapping):
        return dict(recipe)
    raise TypeError("expected Recipe or mapping, got %s" % type(recipe).__name__)


def _semantic_identity(data):
    """进入哈希的语义核心：不含 recipe_id 与生命周期元数据。"""
    steps = []
    raw_steps = data.get("tool_sequence") or []
    if isinstance(raw_steps, (list, tuple)):
        for item in raw_steps:
            if isinstance(item, ToolStep):
                step = item.to_dict()
            elif isinstance(item, Mapping):
                step = dict(item)
            else:
                step = {"tool_name": str(item), "params": {}, "depends_on": ""}
            params = step.get("params")
            if params is None:
                params = {}
            steps.append({
                "tool_name": step.get("tool_name", ""),
                "params": params,
                "depends_on": step.get("depends_on", "") or "",
            })
    return {
        "intent": data.get("intent", "") or "",
        "object_type": data.get("object_type", "") or "",
        "metric": data.get("metric", "") or "",
        "time_window": data.get("time_window", "") or "",
        "person": data.get("person", "") or "",
        "tool_sequence": steps,
    }


# ---------------------------------------------------------------- 公共 API

def make_recipe_id(recipe):
    # type: (Union[Recipe, Mapping[str, Any]]) -> str
    """确定性 id：同内容同 id，不同内容不同 id。

    id 只取决于语义核心，与 recipe_id / created_at / confidence / sample_count /
    status / source_session 无关。
    """
    data = _as_mapping_copy(recipe)
    identity = _jsonable(_semantic_identity(data))
    try:
        payload = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except TypeError as exc:
        raise TypeError("make_recipe_id: recipe content is not JSON-serializable: %s" % exc)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:RECIPE_ID_BODY_LENGTH]
    return RECIPE_ID_PREFIX + digest


def validate_recipe(recipe, check_id_consistency=False):
    # type: (Union[Recipe, Mapping[str, Any]], bool) -> List[str]
    """返回错误列表；空列表 = 通过。永不抛异常，非空即应拒绝。"""
    errors = []  # type: List[str]
    if isinstance(recipe, Recipe):
        data = recipe.to_dict()
    elif isinstance(recipe, Mapping):
        data = dict(recipe)
    else:
        return ["recipe: expected mapping or Recipe, got %s" % type(recipe).__name__]

    errors.extend(_structural_errors(data))
    _check_enum(data, "intent", KNOWN_INTENTS, errors)
    _check_enum(data, "object_type", KNOWN_OBJECT_TYPES, errors)
    _check_enum(data, "metric", KNOWN_METRICS, errors)
    _check_enum(data, "time_window", KNOWN_TIME_WINDOWS, errors)
    _check_enum(data, "status", KNOWN_STATUSES, errors)

    recipe_id = data.get("recipe_id")
    if isinstance(recipe_id, str):
        if recipe_id == "":
            errors.append("recipe_id: must not be empty")
        elif not _is_recipe_id(recipe_id):
            errors.append(
                "recipe_id: invalid format %r (expected 'recipe_' + 8 lowercase hex chars)" % recipe_id
            )

    created_at = data.get("created_at")
    if isinstance(created_at, str):
        if created_at == "":
            errors.append("created_at: must not be empty")
        elif not _is_iso8601_timestamp(created_at):
            errors.append("created_at: invalid ISO 8601 timestamp %r" % created_at)

    confidence = data.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        if confidence < 0 or confidence > 1:
            errors.append("confidence: must be within [0, 1], got %r" % (confidence,))

    sample_count = data.get("sample_count")
    if isinstance(sample_count, int) and not isinstance(sample_count, bool):
        if sample_count < 0:
            errors.append("sample_count: must be >= 0, got %r" % (sample_count,))

    steps = data.get("tool_sequence")
    if isinstance(steps, (list, tuple)) and len(steps) == 0:
        errors.append("tool_sequence: at least one tool step is required")

    if check_id_consistency and isinstance(recipe_id, str) and recipe_id != "":
        expected = make_recipe_id(data)
        if recipe_id != expected:
            errors.append("recipe_id: %r does not match content hash, expected %r" % (recipe_id, expected))
    return errors


def serialize_recipe(recipe):
    # type: (Union[Recipe, Mapping[str, Any]]) -> str
    """Recipe（或合法 mapping）→ JSON 字符串（稳定键序、中文不转义、可读缩进）。"""
    if isinstance(recipe, Recipe):
        data = recipe.to_dict()
    elif isinstance(recipe, Mapping):
        data = Recipe.from_dict(recipe).to_dict()
    else:
        raise TypeError("serialize_recipe: expected Recipe or mapping, got %s" % type(recipe).__name__)
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)


def deserialize_recipe(json_str):
    # type: (str) -> Recipe
    """JSON 字符串 → Recipe。结构错误 fail-closed（ValueError），枚举漂移 fail-open。"""
    if not isinstance(json_str, str):
        raise TypeError("deserialize_recipe: expected str, got %s" % type(json_str).__name__)
    try:
        data = json.loads(json_str)
    except ValueError as exc:
        raise ValueError("deserialize_recipe: invalid JSON: %s" % exc)
    return Recipe.from_dict(data)


def resolve_params(params_template, context_dict=None):
    # type: (Mapping[str, Any], Optional[Mapping[str, Any]]) -> Dict[str, Any]
    """替换 {placeholder}。

    * 整串就是占位符（如 "{days}"）→ 直接换成 context 原始值，保留类型；
    * 占位符嵌在字符串里（如 "last {days} days"）→ 结果为字符串；
    * 未知占位符 / 畸形花括号 / context 值为 None → 原样保留，不报错；
    * dict、list 递归处理；数字/布尔/None 原样返回；输入不会被修改。
    """
    if not isinstance(params_template, Mapping):
        raise TypeError(
            "resolve_params: params_template must be a mapping, got %s" % type(params_template).__name__
        )
    if context_dict is None:
        context_dict = {}
    if not isinstance(context_dict, Mapping):
        raise TypeError(
            "resolve_params: context_dict must be a mapping, got %s" % type(context_dict).__name__
        )
    return {key: _resolve_value(value, context_dict) for key, value in params_template.items()}


def list_known_intents():
    # type: () -> List[str]
    return list(KNOWN_INTENTS)


def list_known_metrics():
    # type: () -> List[str]
    return list(KNOWN_METRICS)


def list_known_object_types():
    # type: () -> List[str]
    return list(KNOWN_OBJECT_TYPES)


def list_known_time_windows():
    # type: () -> List[str]
    return list(KNOWN_TIME_WINDOWS)


def list_known_statuses():
    # type: () -> List[str]
    return list(KNOWN_STATUSES)


def build_recipe(intent, object_type, metric, time_window, tool_sequence,
                 created_at, source_session, person="",
                 confidence=0.5, sample_count=0, status="staging"):
    """从语义字段组装 Recipe：自动补 recipe_id（确定性）并返回。

    created_at 由调用方注入（本模块不取系统时间，见 §7）。
    """
    draft = {
        "intent": intent,
        "object_type": object_type,
        "metric": metric,
        "time_window": time_window,
        "person": person,
        "tool_sequence": tool_sequence,
        "confidence": confidence,
        "sample_count": sample_count,
        "created_at": created_at,
        "source_session": source_session,
        "status": status,
    }
    draft["recipe_id"] = make_recipe_id(draft)
    return Recipe.from_dict(draft)
