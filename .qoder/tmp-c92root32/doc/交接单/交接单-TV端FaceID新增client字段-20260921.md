# 交接单：TV 端 FaceID 上报新增 client 字段

> 日期：2026-09-21 | 来源：TV 端（Arcface）
> 目标读者：Memory Agent (MA) 开发者

---

## 一、一段话

TV 端 Arcface 的 `/arcface/current_user` 接口新增了 `client` 查询参数。当小甜菜/mytv 等 APK 调用此接口时，会带上自己的标识（如 `client=xiaotiancai`），Arcface 会把这个 `client` 字段一起上报到 MA 的 `/api/events/face` 接口。

**MA 需要接收并存储这个 `client` 字段。**

---

## 二、变更内容

### 1. TV 端调用方式

```
GET http://192.168.2.238:8080/arcface/current_user?client=xiaotiancai
```

- `client` 参数：调用方标识
- 可选值：`xiaotiancai`（小甜菜）、`mytv`（我的电视）、其他

### 2. TV 端上报 MA 的事件格式

```
POST http://192.168.2.200:8086/api/events/face
Authorization: Bearer longyin1003
Content-Type: application/json

{
  "room": "客厅",
  "client": "xiaotiancai",
  "persons": [
    {
      "name": "lidicn",
      "confidence": 0.95,
      "via": "face"
    }
  ]
}
```

---

## 三、MA 需要做的

### 接收 `client` 字段

在 `/api/events/face` 接口中，接收并存储 `client` 字段。

### 数据结构建议

| 字段 | 类型 | 说明 |
|------|------|------|
| `client` | String | 调用方标识（xiaotiancai/mytv/...），可选，无此字段表示 TV 端自动检测 |

### 用途

MA 可以记录：
- 小甜菜在什么时间用了 FaceID
- mytv 在什么时间用了 FaceID
- 哪个 APK 触发了人脸识别

---

## 四、向后兼容

- `client` 字段是**可选的**
- 没有 `client` 字段的事件（TV 端自动检测）照常处理
- 不影响现有功能

---

## 五、示例

### 小甜菜启动时调用 FaceID

```
小甜菜 → GET /arcface/current_user?client=xiaotiancai
Arcface → 识别到 lidicn → 上报 MA:
{
  "room": "客厅",
  "client": "xiaotiancai",
  "persons": [{"name": "lidicn", "confidence": 0.95, "via": "face"}]
}
```

### mytv 调用 FaceID

```
mytv → GET /arcface/current_user?client=mytv
Arcface → 识别到 Kevin → 上报 MA:
{
  "room": "客厅",
  "client": "mytv",
  "persons": [{"name": "Kevin", "confidence": 0.88, "via": "face"}]
}
```

### TV 端自动检测（无 client）

```
Arcface 每 5 秒自动检测 → 上报 MA:
{
  "room": "客厅",
  "persons": [{"name": "lidicn", "confidence": 0.92, "via": "face"}]
}
```

---

## 六、时间线

- **TV 端**：已完成代码修改，待编译安装
- **MA 端**：待接收 `client` 字段并存储

---

*交接人：豆包*
*交接日期：2026-09-21*
