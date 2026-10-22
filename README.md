# FastChu

中二节奏（CHUNITHM）查歌 / 查称号 QQ 机器人，核心逻辑与 QQ 框架解耦，可挂 **NoneBot2** 或 **AstrBot**。

默认只访问落雪公共接口，不需要任何密钥；绑定落雪账号后，可在查歌/查称号时附带自己的成绩与获取情况。

## 功能

- **查歌**：曲名、别名、艺术家或 `#ID` → 曲师、分类、BPM、版本、定数、物量与相关特殊称号。
- **查称号**：按关键词或获得条件检索，显示颜色、条件与关联曲目，支持随机称号。
- **个人数据（可选）**：`/授权` 绑定落雪账号后，查歌显示个人成绩，查称号显示是否已获得。
- **轻量输出**：短消息、每行一条；曲绘默认只传 URL，正文和图片合并为一条消息。
- **多适配器**：核心层不依赖 NoneBot，两个适配器共用同一套命令与渲染。

## 快速开始

```bash
git clone https://github.com/drurrid/FastChu.git
cd FastChu
python -m venv .venv
# Windows: .venv\Scripts\activate
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# 可选：预抓曲库与称号；不执行也会在首次查询时自动抓取
python scripts/fetch_reference_data.py

# 命令行试查
python -m fastchu song "#159"
python -m fastchu title "ラグナロク"

# 启动 NoneBot（默认监听 127.0.0.1:8080）
python bot.py
```

不想接 QQ 时，可以先启动本地网页测试台：

```bash
python tools/testpage/server.py   # http://127.0.0.1:8899
```

## 命令

| 命令 | 说明 |
| --- | --- |
| `/歌曲名` / `/别名` | 直接查歌，例如 `/Rebellion`、`/世征` |
| `/#<ID>` / `/<ID>` | 按曲目 ID 查，例如 `/#464` |
| `/查歌 <关键词>` | 显式查歌（与裸查询等价） |
| `/称号 <关键词>` | 查称号颜色、条件与关联曲目 |
| `/称号条件 <条件>` | 按获得条件反查称号 |
| `/随机称号` | 随机抽一个称号 |
| `/谁写的 <歌名> [难度]` | 查谱师，难度可写 `MAS`/`EXP`/`ULT` 或 `紫`/`红`/`黑` |
| `/添加别名 <曲目> <别名>` | 补充别名，一次最多 5 个 |
| `/授权` | 绑定落雪账号（可选） |
| `/授权状态` `/取消授权` | 查看或删除绑定 |
| `/帮助` | 命令卡片（图片） |

命中多首时回复序号查看详情，候选支持 `p2` / `下一页` 翻页、`取消` 放弃选择。群聊中裸查询需要 @机器人，带前缀的命令不需要。

## 接入 QQ

### AstrBot（推荐）

```powershell
python scripts/build_astrbot_plugin.py --install --target `
  "$env:USERPROFILE\.astrbot_launcher\instances\<实例ID>\core\data\plugins"
```

重载插件或重启实例后即可使用。插件目录是构建产物，改完核心代码后重新执行上面的命令。详见 [插件说明](astrbot_plugin_fastchu/README.md) 和 [部署教程](docs/deploy-astrbot.md)。

### NoneBot + NapCat

1. 运行 `python bot.py`（或双击 `start-bot.cmd`）；
2. NapCat 新增反向 WebSocket 客户端，地址 `ws://127.0.0.1:8080/onebot/v11/ws`，消息格式选 `array`；
3. 群里发送 `/帮助` 验证。

## 落雪账号绑定（可选）

不配置也完全不影响查歌/查称号。启用步骤：

1. 在落雪创建 OAuth 应用，**不填回调地址**，权限选 `read_player`；
2. 在 `.env` 中填写：

```dotenv
FASTCHU_OAUTH_ENABLED=true
FASTCHU_LXNS_OAUTH_CLIENT_ID=你的ClientID
FASTCHU_LXNS_OAUTH_CLIENT_SECRET=你的ClientSecret
FASTCHU_LXNS_OAUTH_SCOPES=read_player
```

3. 重启机器人。用户发 `/授权` 获取链接，授权后把一次性授权码发回机器人即可。

访问令牌 15 分钟、刷新令牌 30 天，自动轮换并保存在 `data/auth/`（已 gitignore）。未绑定、未配置或接口超时都会保持原回复；个人数据只是附加内容。

## 项目结构

```text
fastchu/
├── api/              # 落雪公共接口、OAuth、个人接口
├── services/         # 曲库、搜索、称号、命令、个人数据
├── render/           # 文本回复与帮助卡片
├── plugins/song/     # NoneBot 适配层
└── cli.py            # 命令行入口
astrbot_plugin_fastchu/  # AstrBot 适配层（构建产物）
scripts/                 # 数据抓取与插件构建
tests/ tests_e2e/        # 单元测试与真实 NoneBot 端到端测试
data/                    # 曲库/称号数据快照与运行缓存
tools/testpage/          # 本地网页测试台
docs/                    # 部署文档
```

## 数据与配置

- 曲库、别名、称号和定数来自落雪公共接口；日服独有曲目与称号由日服 Wiki 补充。
- 首次查询自动抓取并缓存到 `data/cache/`，之后走本地缓存；数据文件可用 `scripts/` 下的脚本重新生成。
- 全部配置通过 `.env` 或环境变量设置，前缀 `FASTCHU_`。完整列表见 [`.env.example`](.env.example)。

常用项：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `FASTCHU_JACKET_MODE` | `url` | `url` / `base64` / `off` |
| `FASTCHU_QUERY_COOLDOWN` | `3` | 同一用户查询冷却（秒） |
| `FASTCHU_SEARCH_RESULT_LIMIT` | `8` | 候选列表每页条数 |
| `FASTCHU_SONG_CACHE_TTL` | `86400` | 曲库/称号缓存有效期（秒） |
| `FASTCHU_MAX_MESSAGE_LENGTH` | `1800` | 单条消息最大长度 |

## 测试

```bash
python -m pytest -q
```

测试离线运行，使用 `tests/data/` 的固定夹具；`tests_e2e/` 会在真实 NoneBot 运行时里驱动命令。

## 许可

MIT
