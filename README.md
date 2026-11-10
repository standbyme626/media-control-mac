<div align="center">

# media-control-mac

**躺在沙发上，用手机控制 Mac 放歌，声音从蓝牙音箱出来**

受控浏览器 + CDP · B 站搜索直连 · 扫码登录 · IOBluetooth · 10 分钟自动刷新

![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)
![macOS](https://img.shields.io/badge/macOS-13%2B-000000?logo=apple&logoColor=white)
![Edge](https://img.shields.io/badge/Edge%20%2F%20Chrome-CDP-0078D7?logo=microsoftedge&logoColor=white)
![Linux](https://img.shields.io/badge/Server-Linux%20systemd-FCC624?logo=linux&logoColor=black)
![No dependencies](https://img.shields.io/badge/Server-0%20dependencies-16A34A)
![License](https://img.shields.io/badge/License-MIT-green)

[项目概览](#1-项目概览) · [核心特性](#2-核心特性) · [界面](#3-界面) · [系统架构](#4-系统架构) · [快速开始](#6-快速开始) · [API](#7-api) · [踩坑](#11-工程实践与踩坑)

</div>

> **项目定位：家庭局域网的个人工具，不是远程桌面。**
> 只做「媒体控制」一件事：看现在放什么、暂停/继续、调音量、搜歌点播。
> 是 [media-control](https://github.com/standbyme626/media-control)（Windows 版）的 macOS 版本。

**2 个进程** · **976 行代码** · **0 第三方依赖（服务端与前端）** · **搜索约 2 秒** · **10 分钟自动刷新**

---

## 目录

- [1. 项目概览](#1-项目概览)
- [2. 核心特性](#2-核心特性)
- [3. 界面](#3-界面)
- [4. 系统架构](#4-系统架构)
- [5. 关键设计](#5-关键设计)
- [6. 快速开始](#6-快速开始)
- [7. API](#7-api)
- [8. 部署](#8-部署)
- [9. 测试与验证](#9-测试与验证)
- [10. 已知限制](#10-已知限制)
- [11. 工程实践与踩坑](#11-工程实践与踩坑)
- [12. 目录结构](#12-目录结构)
- [13. 安全须知](#13-安全须知)
- [14. Roadmap](#14-roadmap)

---

## 1. 项目概览

### 1.1 要解决的问题

MacBook 接着一个蓝牙音箱，人在沙发上刷手机。想切歌就得起来掀盖子、解锁、找到窗口。

远程桌面同样太重。真正需要的只有三件事：**现在放的是什么、暂停还是继续、下一首放什么**。

### 1.2 方案

把「放」和「控」拆开：

- **放**：Mac 上一个受控浏览器（独立 profile）负责打开 B 站并播放，声音走系统蓝牙输出；
- **控**：服务器上的一个网页负责展示状态、发指令；
- **桥**：Mac 上的小服务，把网页指令翻译成 CDP 动作和蓝牙查询。

### 1.3 与 Windows 版的差异

| | Windows 版 | macOS 版 |
|---|---|---|
| 播放控制 | WinRT SMTC + 媒体键回退 | CDP 直接操作 `<video>` |
| 音量 | pycaw 调系统主音量 | CDP 调播放器音量 |
| B 站搜索 | 抓搜索页 DOM | 直接调 B 站搜索接口 |
| 蓝牙 | 不涉及 | IOBluetooth 查状态 / 连接 |
| 服务端 | 1034 行 | 165 行 |

macOS 上没有 WinRT 和 pycaw，所以这版**全部走 CDP**——只要能通过浏览器放出来的声音，都能控。

## 2. 核心特性

| 能力 | 实现 | 说明 |
|---|---|---|
| B 站搜索 | 页面内 `fetch` 官方接口 | 同源、自动带 cookie，不依赖 DOM 渲染 |
| 点播 | CDP `Page.navigate` | 打开视频页并 `video.play()` |
| 播放 / 暂停 | CDP `Runtime.evaluate` | 直接操作播放器元素 |
| 音量 | `video.volume` | 控播放器音量（系统音量在控制面外） |
| 自动刷新 | 前端 10 分钟定时 | 页面不可见时不刷，回来补刷 |
| 换一批 | `page + 1` | 翻到下一页结果 |
| 扫码登录 | CDP 截图登录页 | 网页里直接显示二维码，手机扫 |
| 蓝牙状态 | IOBluetooth | 显示连接状态，支持一键重连 |

## 3. 界面

![界面截图](docs/screenshot.png)

左侧是遥控器（当前播放、进度、暂停/播放、音量），右侧是 B 站搜索结果网格。窄屏自动退回单列。

## 4. 系统架构

### 4.1 总览

```
手机 / 电脑浏览器
        │ HTTP（局域网，可选 Basic 认证）
        ▼
music-web（服务器，Python 标准库，:8098）
        │ SSH 固定命令（参数白名单，不经 shell）
        ▼
music-bridge（Mac，127.0.0.1:8765）
        ├─ CDP ws://127.0.0.1:9222（受控浏览器）
        └─ IOBluetooth（蓝牙音箱状态 / 连接）
        ▼
受控浏览器播放 → macOS 音频输出 → 蓝牙音箱
```

### 4.2 一次点歌的完整过程

```
1. 手机输入关键词 → GET /api/search?q=xxx&page=1
2. music-web 校验参数 → ssh mac "curl 127.0.0.1:8765/search?q=...&page=1"
3. 桥在当前页面上下文 fetch B 站搜索接口（带 cookie）
4. 接口返回 JSON → 桥清洗字段 → 前端渲染网格
5. 用户点卡片 → POST /api/open?bv=BVxxxx
6. 桥导航到视频页 → 等待加载 → video.play()
7. 前端每 4 秒轮询 /api/status 更新进度
```

## 5. 关键设计

### 5.1 为什么搜索走接口而不是抓 DOM

第一版抓搜索页的 `.bili-video-card`，实测不工作：搜索页骨架正常加载（导航栏、结果计数「99+」都在），但**结果列表区域始终空白**，DOM 里一个视频卡片都没有。

排查结论：UA 正常（不是 headless）、`navigator.webdriver === false`、网络请求没有任何 4xx/5xx、页面已登录（`DedeUserID` 存在），但列表就是渲染不出来。

而同一页面上下文里直接 fetch 官方接口，返回 `{"code": 0, "n": 20}`，数据完全正常。

所以改成**在页面上下文里调接口**：同源、自动带 cookie、不依赖前端渲染，顺带把搜索耗时从约 20 秒降到约 2 秒。

### 5.2 为什么音量走 `video.volume`

用 `osascript` 调系统音量在交互式 SSH 里没问题，但在**后台进程**（nohup 起的桥）里会挂死——`set volume` 等 5 秒超时。所以音量改走 CDP 设置 `video.volume`，绕开系统音频 API；系统音量交给 Mac 自己。

### 5.3 为什么不用 blueutil

`brew install blueutil` 需要完整 Xcode.app 编译，只装 Command Line Tools 会失败。改用 **PyObjC 直接调 macOS 的 `IOBluetooth` 框架**，纯 Python 不需要编译：

```python
from IOBluetooth import IOBluetoothDevice
dev = IOBluetoothDevice.deviceWithAddressString_("AA-BB-CC-DD-EE-FF")
dev.isConnected()      # 查状态
dev.openConnection()   # 重连
```

### 5.4 target id 会变

浏览器每次导航（比如跳登录页）后，CDP 的 target id 可能失效，报 `No such target id`。所以桥里所有求值都走 `eval_safe()`：**每次重新取标签，失败重试 3 次**。

## 6. 快速开始

### 6.1 组件清单

| 位置 | 组件 | 依赖 |
|---|---|---|
| Mac | 受控浏览器 | Edge 或 Chrome（Safari 不支持 CDP） |
| Mac | `bridge/bridge.py` | `websocket-client`、`pyobjc-framework-IOBluetooth` |
| 服务器 | `server/app.py` + `page.html` | 无（Python 标准库） |
| 服务器 | SSH 免密到 Mac | 无 |

### 6.2 配置

参考 `config.example.env`：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MW_PORT` | 8098 | 网页端口 |
| `MW_MAC` | mac | SSH 到 Mac 的主机别名 |
| `MW_BRIDGE` | http://127.0.0.1:8765 | 桥地址（经 SSH 访问） |
| `MW_AUTH` | 0 | 设 1 启用 Basic 认证 |
| `MW_USER` / `MW_PASSWORD` | media / 空 | 密码为空时拒绝所有请求 |
| `MB_PORT` | 8765 | 桥端口（只绑 127.0.0.1） |
| `MB_CDP_PORT` | 9222 | 受控浏览器调试端口 |
| `MB_BT_ADDR` | 空 | 蓝牙音箱地址，留空关闭蓝牙功能 |
| `MB_BT_NAME` | 空 | 音箱名称，不填自动从 IOBluetooth 读 |

### 6.3 Mac 侧启动

```bash
# 1. 装浏览器（二选一）
brew install --cask microsoft-edge
brew install --cask google-chrome

# 2. Python 依赖
python3 -m pip install --user websocket-client pyobjc-framework-IOBluetooth

# 3. 放桥的代码
mkdir -p ~/music-bridge && cp bridge/bridge.py ~/music-bridge/

# 4. 起受控浏览器（首次扫码登录 B 站）
./scripts/start-browser.sh

# 5. 起桥
MB_BT_ADDR="AA-BB-CC-DD-EE-FF" ./scripts/start-bridge.sh
```

### 6.4 服务器侧启动

```bash
# 1. 配 SSH 免密（服务器 → Mac）
ssh-copy-id 你的用户名@Mac的IP
# ~/.ssh/config 里加一个 Host mac

# 2. 部署网页
mkdir -p /opt/music-web
cp server/app.py server/page.html /opt/music-web/

# 3. 起服务
MW_MAC=mac python3 /opt/music-web/app.py
```

浏览器打开 `http://服务器IP:8098/`。

## 7. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 控制页面 |
| GET | `/api/status` | 播放状态、进度、音量、登录态、蓝牙 |
| GET | `/api/search?q=&page=` | B 站搜索（接口） |
| POST | `/api/open?bv=` | 打开并播放指定视频 |
| POST | `/api/play` / `/api/pause` | 播放 / 暂停 |
| POST | `/api/volume?level=` | 播放器音量 0–100 |
| GET | `/api/qr` | 打开登录页并返回二维码 |
| POST | `/api/bt?action=connect` | 连接蓝牙音箱 |

桥（Mac 侧，只绑 127.0.0.1）暴露同名路径，由服务器经 SSH 转发。

## 8. 部署

```bash
cp deploy/music-web.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now music-web
```

Mac 侧常驻用 LaunchAgent（模板见 `deploy/launchagents/`）：

```bash
cp deploy/launchagents/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.musicweb.bridge.plist
```

## 9. 测试与验证

| 检查 | 方法 |
|---|---|
| 桥存活 | `curl 127.0.0.1:8765/bt` |
| CDP 可用 | `curl 127.0.0.1:9222/json/version` |
| 搜索链路 | 网页搜一个词，约 2 秒出结果 |
| 播放链路 | 点卡片，音频应从蓝牙音箱出来 |
| 蓝牙状态 | 网页顶部徽章显示「音响已连接」 |

## 10. 已知限制

- 点歌到出声约 2–5 秒（导航 + 视频起播），不是秒响应。
- Mac 休眠 / 合盖会断，需要保持唤醒（`caffeinate`）。
- 音量控的是播放器音量，不是系统音量。
- 蓝牙音箱若声明不支持 AVRCP 绝对音量，软件调不了它的硬件音量。
- B 站二维码 180 秒过期，页面里可点「刷新二维码」。
- Mac 重启后需要重新拉起受控浏览器和桥（除非配了 LaunchAgent）。

## 11. 工程实践与踩坑

| 坑 | 现象 | 处理 |
|---|---|---|
| 搜索结果页不渲染 | 结果区全白，但计数显示 99+ | 改调接口，不抓 DOM |
| CDP origin 被拒 | `403 Forbidden ... rejected WebSocket` | 启动加 `--remote-allow-origins=*` |
| zsh 把 URL 的 `?` 当通配符 | `no matches found` | 参数用 `shlex.quote` 包住 |
| osascript 在后台进程挂死 | 音量接口超时 5 秒 | 改走 CDP 设 `video.volume` |
| brew 装 blueutil 失败 | 需要完整 Xcode.app | 换 PyObjC + IOBluetooth |
| profile 单例锁残留 | 新浏览器实例直接退出 | 启动前删除 `Singleton*` |
| target id 失效 | `No such target id` | `eval_safe` 重新取标签重试 |
| Edge 从 SSH 启动不了 | `open -a` 返回 1 | 直接跑二进制 + nohup |

## 12. 目录结构

```
media-control-mac/
├── server/app.py               # 网页与 API（165 行，标准库）
├── server/page.html            # 单文件前端（363 行）
├── bridge/bridge.py            # Mac 侧 CDP 桥（448 行）
├── scripts/start-browser.sh    # 启动受控浏览器
├── scripts/start-bridge.sh     # 启动 / 重启桥
├── deploy/music-web.service    # systemd 模板
├── deploy/launchagents/        # LaunchAgent 模板
├── config.example.env
└── docs/screenshot.png
```

## 13. 安全须知

- 默认**不带认证**，面向家庭局域网；要暴露公网请设 `MW_AUTH=1` 并加 HTTPS 反代。
- 网页只能触发固定动作：BV 号经 `^BV[0-9A-Za-z]{8,14}$` 白名单，音量只接受 0–100。
- 服务器不执行传入字符串，转发 URL 经 `shlex.quote` 包裹。
- 桥只监听 `127.0.0.1`，外部必须经 SSH 访问。
- B 站登录态只存在那台 Mac 的专用 profile 里。

## 14. Roadmap

- [ ] 网易云 / QQ 音乐支持
- [ ] 播放队列与「下一首」
- [ ] 歌词显示
- [ ] 手机端 PWA 离线壳

## License

MIT
