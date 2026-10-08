# TV 端 ArcFace 人脸识别连接 MA 配置说明

## 一、MA 服务基本信息

| 项目 | 值 |
|---|---|
| MA 服务地址 | `http://192.168.2.200:8086` |
| 设备令牌（vision_device_token） | `longyin1003` |
| 认证方式 | HTTP Header `Authorization: Bearer longyin1003` |

## 二、需要 TV 端实现的 API 调用

### 1. 节点注册（启动时调用一次）

```
POST http://192.168.2.200:8086/api/face/node/register
Header: Authorization: Bearer longyin1003
Content-Type: application/json

Body:
{
  "node_id": "tv_living_room",        // 节点唯一ID，建议用 tv_房间名 格式
  "node_type": "tv",                   // 固定为 "tv"
  "url": "http://<TV端IP>:<端口>",     // TV端自己的HTTP服务地址，MA会回调此地址做识别
  "room": "客厅"                        // 房间名，必须与MA配置中的房间名一致
}
```

成功返回：
```json
{
  "ok": true,
  "message": "节点已注册: tv_living_room（tv）",
  "node_id": "tv_living_room",
  "registered_at": "..."
}
```

### 2. 心跳续活（每 30~60 秒调用一次）

```
POST http://192.168.2.200:8086/api/face/node/heartbeat
Header: Authorization: Bearer longyin1003
Content-Type: application/json

Body:
{
  "node_id": "tv_living_room"
}
```

> ⚠️ 节点注册是**进程内**的，MA 重启后节点会丢失，TV 端需要重新注册。心跳超时也会被标记为离线。

### 3. 人脸库下发（注册后调用，获取成员人脸特征）

```
GET http://192.168.2.200:8086/api/face/node/lib
Header: Authorization: Bearer longyin1003
```

返回已注册人脸特征的成员清单，TV 端用这些特征做本地识别。

### 4. 人脸事件上报（识别到人脸时调用）

```
POST http://192.168.2.200:8086/api/events/face
Header: Authorization: Bearer longyin1003
Content-Type: application/json

Body:
{
  "room": "客厅",
  "trigger": "face_detect",          // 触发原因，可选
  "ts": "2026-09-21T00:30:00",       // 事件时间，可选，不传用MA服务器时间
  "camera": "cam_客厅",                // 摄像头标识，可选
  "persons": [
    {
      "name": "lidicn",               // 识别到的成员名，必须与MA成员名一致
      "confidence": 0.95,             // 识别置信度 0~1
      "via": "face",                   // 固定为 "face"，MA会归一化为 "arcface"
      "bbox": [x, y, w, h]            // 人脸框，可选
    }
  ]
}
```

成功返回：
```json
{
  "ok": true,
  "accepted": true,
  "deduped": false,
  "vlm_dispatched": false
}
```

## 三、MA 侧已配置的房间（需要 TV 端的）

| 房间 | 摄像头标识 | 是否需要 TV 端 |
|---|---|---|
| 客厅 | cam_客厅 | ✅ 是（no_tv=false） |
| 书房 | cam_书房 | ✅ 是（no_tv=false） |
| 起居室 | cam_小黄人 | ❌ 否（no_tv=true，走VLM巡检） |

> TV 端只需为「客厅」和「书房」各注册一个节点。

## 四、MA 成员列表（人脸库对应的成员名）

| 成员名 | 说明 |
|---|---|
| lidicn | 阿迪（男主人） |
| Kevin | 凯文（男孩） |
| Emily | 小公主（女孩） |

> TV 端识别到的 `name` 必须与上表一致，MA 才能正确关联 member_id。

## 五、常见问题排查

### 问题1：调用 API 返回 401 "无效的设备令牌"
- 检查 Header 是否为 `Authorization: Bearer longyin1003`
- 注意 Bearer 后面有一个空格
- token 是 `longyin1003`，不是 `long********1003`（那个是掩码占位符）

### 问题2：节点注册成功但很快变离线
- 检查心跳是否正常发送（建议每 30 秒一次）
- MA 重启后节点会丢失，需要重新注册

### 问题3：人脸事件上报成功但 MA 里查不到记录
- 检查 `persons` 数组是否非空（空数组不会写入）
- 检查 `name` 是否与 MA 成员名一致
- 检查 `room` 是否与 MA 配置的房间名一致

### 问题4：MA 回调 TV 端 URL 失败
- 检查 TV 端的 `url` 是否可从 MA 所在机器访问
- 检查 TV 端 HTTP 服务是否正常监听
- MA 会通过此 URL 做主动识别请求（WebUI 测试用）

## 六、TV 端最小实现伪代码

```python
import requests
import time

MA_URL = "http://192.168.2.200:8086"
TOKEN = "longyin1003"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# 1. 启动时注册节点
requests.post(f"{MA_URL}/api/face/node/register", headers=HEADERS, json={
    "node_id": "tv_living_room",
    "node_type": "tv",
    "url": "http://192.168.2.xxx:8080",
    "room": "客厅"
})

# 2. 拉取人脸库
lib = requests.get(f"{MA_URL}/api/face/node/lib", headers=HEADERS).json()
members = lib["members"]

# 3. 心跳线程
def heartbeat():
    while True:
        requests.post(f"{MA_URL}/api/face/node/heartbeat", headers=HEADERS, json={
            "node_id": "tv_living_room"
        })
        time.sleep(30)

# 4. 识别到人脸时上报
def on_face_detected(person_name, confidence):
    requests.post(f"{MA_URL}/api/events/face", headers=HEADERS, json={
        "room": "客厅",
        "persons": [{
            "name": person_name,
            "confidence": confidence,
            "via": "face"
        }]
    })
```

---

文档版本：v1.0
更新时间：2026-09-21
维护：MA 开发者
