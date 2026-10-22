# FastChu 部署教程（AstrBot 方案）

> 目标：用**一个新的 AstrBot 实例**驱动**一个新的 QQ 号**，提供 FastChu 查歌 / 查称号。

---

## 0. 先说端口：新实例该用什么

AstrBot 的默认端口是 **6185（WebUI）** 和 **6199（OneBot v11 反连入口）**。
同一台机器上跑两个实例时，这两组端口**必须错开**，否则第二个实例起不来（端口占用）。

推荐按「老实例 +100」的方式规划，好记也不容易撞：

| 用途 | 老实例（默认） | **新实例（FastChu 用这个）** |
| --- | --- | --- |
| AstrBot WebUI（管理面板） | 6185 | **6285** |
| OneBot v11 反连入口（`ws_reverse_port`） | 6199 | **6299** |
| NapCat WebUI | 6099 | **6098** |
| （可选）QQ 官方接口适配器 | 6196 | 6296 |
| （可选）Telegram 适配器 | 6198 | 6298 |

> 只跑一个实例的话，用默认的 6185 / 6199 就行，不用改。

动手前先确认端口没被占（PowerShell）：

```powershell
foreach ($p in 6285, 6299, 6098) {
  $x = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
  if ($x) { "$p 被占用 (PID $($x[0].OwningProcess))" } else { "$p 空闲" }
}
```

---

## 1. 架构长什么样

```
QQ 客户端（新号）
   ↕  (QQ 协议，由 NapCat 接管)
NapCat（登录新 QQ，暴露 OneBot v11）
   ↓  反向 WebSocket：ws://127.0.0.1:6299/ws
AstrBot 新实例（aiocqhttp 适配器在 6299 上收）
   ↓  加载插件
astrbot_plugin_fastchu
   ↓  读取本地缓存
FastChu 项目 data/（曲库 1466 首 / 称号 5620 / 国服日服并集）
```

关键点：

- **一个 QQ 号对应一个 NapCat**。两个号就要两个 NapCat 进程，各自端口、各自扫码登录。
- **一个 AstrBot 实例对应一个 OneBot 反连端口**。NapCat 连哪个端口，消息就进哪个实例。
- FastChu 插件本身**不联网**（除了首次抓数据），聊天走的是 AstrBot 的连接。

---

## 2. 新建 AstrBot 实例

用 **AstrBot Launcher**（`D:\AstrBot\AstrBot Launcher\astrbot-launcher.exe`）：

1. 打开 Launcher → **新建实例**
2. 版本选 **v4.29.1**（Launcher 已缓存；比现有实例的 v4.22 新）
3. 记下实例 ID（一串 UUID），它对应目录：

```
%USERPROFILE%\.astrbot_launcher\instances\<实例ID>\
├── core\          # AstrBot 源码与 data
└── venv\          # 该实例独立的 Python 环境
```

4. **点「启动」让实例先跑一次**

> ⚠️ **顺序很重要**：实例首次启动会创建并初始化 `core\data\` 目录。
> 如果你抢先往里塞插件，会被初始化过程冲掉（这个坑我已经踩过）。

---

## 3. 登录 WebUI 并改端口

1. 浏览器打开 `http://127.0.0.1:6285`（若启动后日志里显示的是 6185，说明还没改，见下）

   - 默认账号 `astrbot`，密码首次登录时设置（初始为空）
2. 进 **配置** → 找到这两项改成上表的端口：

| 配置项 | 改成 |
| --- | --- |
| `dashboard.port` | `6285` |
| `platform_settings` 里 aiocqhttp 的 `ws_reverse_port` | `6299` |

3. 保存 → **重启实例**

也可以直接改文件（实例没跑的时候改更稳）：

```
%USERPROFILE%\.astrbot_launcher\instances\<实例ID>\core\data\cmd_config.json
```

搜 `"port": 6185` 改成 `6285`，搜 `"ws_reverse_port": 6199` 改成 `6299`。

改完启动，日志里应出现监听 6285 / 6299。

---

## 4. 安装 FastChu 插件

在 **FastChu 项目根目录**执行（把 `<实例ID>` 换成你的）：

```powershell
python scripts/build_astrbot_plugin.py --install --target `
  "$env:USERPROFILE\.astrbot_launcher\instances\<实例ID>\core\data\plugins"
```

脚本会做四件事：

1. 把 `fastchu/` 里**框架无关的核心层**复制到插件的 `_vendor/fastchu/`（不污染 AstrBot 的 venv）；
2. 写入 `_paths.json`，让插件找到本项目已抓好的缓存；
3. 把头像复制成 `logo.png`（AstrBot 插件列表会显示企鹅图标）；
4. 整体装到目标实例的 `data/plugins/astrbot_plugin_fastchu`。

只有一个实例时 `--target` 可省略。装完在 WebUI 的「插件」页点重载，或重启实例。

看到日志里出现 `astrbot_plugin_fastchu` 加载成功即可。

---

## 4.5 如果用的是 QQ 官方机器人（不需要 NapCat）

AstrBot 支持两种 QQ 官方接入：

| 类型 | 说明 | 需要公网回调 | 需要 NapCat |
| --- | --- | --- | --- |
| `qq_official` | **WebSocket 模式**，只填 appid / secret | ❌ | ❌ |
| `qq_official_webhook` | Webhook 模式，要公网 HTTPS 地址 | ✅ | ❌ |

选 `qq_official`（WebSocket）最省事：在 WebUI「平台」→ 新增 → **QQ 官方机器人**，
填 AppID / AppSecret 即可，然后到 QQ 开放平台把机器人加进目标群 / 频道。

### 触发方式（和 NapCat 方案不同，已适配）

AstrBot 的唤醒检查会**先把 wake_prefix 剥掉**：

- 群里 `@机器人 /End Time` → 插件拿到的是 `End Time`（`/` 已消失）
- 群里 `@机器人 End Time` → 不带任何前缀
- 私聊直接发 `End Time`

所以插件不要求 `/` 前缀，判定条件是「**已被唤醒 + 内容不是框架命令**」。这几种都能用：

```
@FastChu End Time
@FastChu /End Time
@FastChu 称号 Legend
私聊：Rebellion
```

> 反过来：这个实例里**任何 @机器人 的消息都会被当成查歌**（因为它是专用查歌实例）。
> 如果你还想用它聊别的，就让 AstrBot 的 LLM 走另一个实例。

### 让「指令」出现在 QQ 客户端里（平台侧配置）

聊天里的命令引导由 `/帮助` 提供（命令卡片图片，只列命令，不写多余说明）。
如果你还想让指令出现在 QQ 的输入框菜单里（点一下就能选），那是
**QQ 开放平台**侧的配置，不在代码里：

1. 登录 [QQ 开放平台](https://bot.q.qq.com/) → 你的机器人 → **指令配置**
2. 新建指令，例如：

| 指令名 | 说明 |
| --- | --- |
| `查歌` | 查中二节奏曲目信息 |
| `称号` | 查称号与获得条件 |
| `帮助` | 命令卡片（图片） |

3. 提交后需要审核，通过后客户端里才会出现

> `查歌` 这类「固定词 + 参数」的指令适合放在平台指令集里；
> 而「直接发歌名查」这种自由形式只能靠聊天引导。

---

## 5. 安装 NapCat 并登录新 QQ（仅 OneBot v11 方案需要）

> 前置：已经装好 QQ NT（本机是 `D:\QQNT\QQ.exe`）。

1. 下载 NapCat（GitHub：`NapNeko/NapCatQQ` 的 Releases，选 `NapCat.Shell` 或一键安装包）
2. 解压到一个独立目录，例如：

```
D:\NapCat-A\      # 给新 QQ 用
```

3. 把 QQ 路径指向 `D:\QQNT\QQ.exe`（NapCat 启动器里可配置）
4. 启动 NapCat → **用新 QQ 扫码登录**

> 两个 QQ 号 → 两套 NapCat（`D:\NapCat-A`、`D:\NapCat-B`），**不要共用同一份 NapCat 目录**，
> 否则登录态会互相覆盖。

---

## 6. 让 NapCat 连到新的 AstrBot 实例

NapCat 的 WebUI 默认在 `http://127.0.0.1:6099`（新实例按上表改成 **6098** 避免冲突）。

1. 打开 NapCat WebUI → **网络配置** → 新建 **WebSocket 客户端**
2. 填：

| 项 | 值 |
| --- | --- |
| URL | `ws://127.0.0.1:6299/ws` |
| 消息格式 | `array` |
| Token | 与 AstrBot 的 `ws_reverse_token` 一致（默认都为空） |
| 启用 | ✅ |

3. 保存后 NapCat 日志里应出现「已连接到 ...」或 AstrBot 日志出现「aiocqhttp 客户端已连接」

> 如果 AstrBot 日志报连接被拒，先确认 AstrBot 实例确实在 6299 上监听：
> `Get-NetTCPConnection -LocalPort 6299 -State Listen`

---

## 7. 验证

在**新 QQ 所在的群**里发：

```
/帮助                 → 应返回 FastChu 命令卡片
/End Time             → 应返回曲目详情（含 [铂金] 賢者タイム）
/称号 Legend of VERSE  → 应返回称号详情
```

能收到就说明整条链路通了。

### 没有真 QQ 时先验通（可选）

AstrBot 的 6299 就是一个标准 OneBot v11 反连入口，可以用模拟客户端探活：

```powershell
python scripts/verify_onebot_ws.py --url ws://127.0.0.1:6299/ws --text "/End Time"
```

它会连上去、发一条假消息、把 AstrBot 实际要发的内容打印出来。
（若 AstrBot 校验收发方身份而拒绝，跳过这步，直接用真 QQ 验证。）

---

## 8. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 新实例启动报端口占用 | 6285/6299 被占，先用第 0 节的命令查是谁，换端口或停掉占用者 |
| 装完插件重启后插件没了 | 实例首次启动的初始化把 `data/` 重建了。**先启动一次，再装插件** |
| 群里发命令没反应 | ① 实例没连上 NapCat（看两边日志）② 插件未启用（WebUI 插件页确认） |
| 命令有反应但多一条 LLM 回复 | AstrBot 把 `/xxx` 当普通聊天交给了 LLM。在该群或全局把触发模式设为「只响应指令」，或在指令配置里禁用 LLM |
| 两个 bot 抢着回同一句话 | 两个 NapCat 登了**同一个 QQ 号**。一个号只能有一个 NapCat |
| 图片不显示 | 默认只发图片 URL，由 NapCat 下载。若 NapCat 所在机器不能访问 `assets2.lxns.net`，把插件环境变量设成 `FASTCHU_JACKET_MODE=base64` |
| 查询太快被拦 | 同一用户 3 秒冷却，改 `FASTCHU_QUERY_COOLDOWN` |
| 曲目/称号数据过期 | 见下一节 |

---

## 9. 日常维护

### 更新数据（游戏版本更新后）

```powershell
cd D:\agent\dsh_workspace\FastChu
python scripts/fetch_reference_data.py     # 国服曲库 + 称号
python scripts/fetch_jp_reference.py --force  # 日服曲库 + 定数
python scripts/fetch_jp_titles.py --force     # 日服 Wiki 称号条件
python scripts/build_title_report.py       # 重新生成称号数据集
python scripts/build_song_regions.py       # 重新生成曲库并集
```

数据是**共享**的：两个实例都会读同一份 `data/`（由插件的 `_paths.json` 指向本项目）。
所以数据只需要更新一次。

### 更新插件代码

```powershell
python scripts/build_astrbot_plugin.py --install --target <实例>\core\data\plugins
```

### 建议备份

```
<实例>\core\data\cmd_config.json      # 实例配置（含端口、平台、LLM）
<实例>\core\data\plugins\             # 插件
D:\agent\dsh_workspace\FastChu\data\  # 曲线图/称号/并集缓存（可重新抓，但重抓要几分钟）
```

---

## 10. 如果想让两个实例的数据互不干扰

默认它们共用 FastChu 项目的 `data/`。要让每个实例各用各的，在**该实例的启动环境**里设：

```powershell
FASTCHU_DATA_DIR=<该实例>\core\data\fastchu
```

插件会优先用它，找不到缓存时自行抓取（每个实例各抓一份，约 6 MB）。

## 长期挂机（Windows 开机自启）

AstrBot Launcher 本身**没有「开机自启实例」的可靠开关**（实测：Launcher 启动后
不会主动拉起上次的实例），所以用「启动文件夹 + 兜底脚本」两段式：

| 文件 | 作用 |
| --- | --- |
| `%APPDATA%\...\Startup\AstrBot 自启.vbs` | 登录时以隐藏窗口调用下面的脚本 |
| `%USERPROFILE%\.astrbot_launcher\autostart-fastchu.ps1` | 起 Launcher → 等 60 秒 → 实例没起来就直接启动实例 |

脚本行为（幂等，可重复执行）：

1. 实例已在跑 → 直接退出，不会重复启动；
2. Launcher 没在跑 → 启动它；
3. 等 60 秒（`FASTCHU_AUTOSTART_WAIT` 可覆盖）；
4. 实例仍未起来 → 用实例自己的 venv 直接 `python core\main.py`（隐藏窗口）。

日志：`%USERPROFILE%\.astrbot_launcher\autostart-fastchu.log`
（只记「做了什么」，不记 AstrBot 自身输出。）

**两个坑**（都踩过）：

* **VBS 必须纯 ASCII**——cscript 按 ANSI/GBK 解析，UTF-8 中文注释的多字节序列
  会把后面的换行吞掉，导致 `Set shell` 被注释掉、跑起来报「缺少对象: shell」。
* **PowerShell 脚本要存成带 BOM 的 UTF-8**——Windows PowerShell 5.1 读无 BOM 的
  UTF-8 会按 ANSI 解码，中文注释会把引号吃掉 → 语法错误。

停用：删掉启动文件夹里的 `AstrBot 自启.vbs` 即可（脚本本身留着无害）。
局限：**需要用户登录**才会触发；关机/睡眠期间不会在线。

## 必须关闭 QQ 官方平台的 markdown

AstrBot 的 `qq_official` 平台配置项 `use_markdown` 默认**开启**，会把消息当 markdown 发送。
本插件输出的是纯文本，颜文字称号（`(*｀・▽・´*)` 之类）里的 `*` `_` 会被 QQ 当格式符吃掉，
表现就是「乱码 / 缺字」。

在实例的 `core/data/cmd_config.json` 里把对应平台的配置改成：

```json
{
  "type": "qq_official",
  "use_markdown": false
}
```

改完需要重启实例。文件是**带 BOM 的 UTF-8**，脚本改写时要保留 BOM（`utf-8-sig`），
否则 AstrBot 读配置会报错。

## 当前部署实况（NapCat 迁移后）

| 组件 | 位置 / 配置 |
| --- | --- |
| NapCat | `D:\NapCat`（NapCat.Shell.Windows.Node v4.18.38） |
| 机器人 QQ | `<机器人 QQ>` |
| NapCat 数据/缓存 | `%USERPROFILE%\Documents\Tencent Files\NapCat\data` |
| NapCat WebUI | `http://127.0.0.1:6099/webui`（token 见 `napcat\config\webui.json`） |
| AstrBot 平台 | `aiocqhttp`，反向 WS 监听 `0.0.0.0:6199` |
| NapCat 侧 | `napcat\config\onebot11_<机器人 QQ>.json` → `websocketClients` → `ws://127.0.0.1:6199/ws` |

启动（已并入 `autostart-fastchu.ps1`）：

```bat
D:\NapCat\node.exe D:\NapCat\index.js -q <机器人 QQ>
```

`-q` 走快速登录；不加每次都要扫码。

**注意**：

* 第三方客户端非官方登录，**有封号风险**，新号尤其容易被风控；
* NapCat 的 QQ 数据目录与日常 QQ 分开，互不影响；
* `qq_official` 平台已 `enable: false`，要退回官方 bot 改回 `true` 即可；
* AstrBot 配置是**带 BOM 的 UTF-8**，脚本改配置要用 `utf-8-sig` 读写。
