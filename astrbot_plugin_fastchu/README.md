# FastChu 查歌（AstrBot 插件）

中二节奏（CHUNITHM）查歌 / 查称号。挂在 AstrBot 现有 QQ 连接上，**不需要另装 NapCat**。

## 用法

群里直接发：

```
/Rebellion          直接查（曲名 / 别名 / #曲目ID 都行）
/世征               别名也能查
/wv
/#464   /464
/称号 Legend        查称号
/称号               用法提示（查称号 / 按条件查 / 随机）
/授权               绑定落雪账号（可选，查歌/称号显示个人数据）
/授权状态           查看绑定（不显示令牌）
/取消授权           解绑落雪账号
/帮助               命令卡片（图片）
```

### 落雪账号绑定（可选）

不绑定也能正常查歌/查称号。绑定后查歌详情多一段「各难度最高分」，
称号详情多一段「已获得 / 未获得」。

管理员在 **FastChu 项目目录的 `.env`** 里配置（插件启动时会自动读取其中的
`FASTCHU_*`，真实环境变量优先）：

```dotenv
FASTCHU_OAUTH_ENABLED=true
FASTCHU_LXNS_OAUTH_CLIENT_ID=你的ClientID
FASTCHU_LXNS_OAUTH_CLIENT_SECRET=你的ClientSecret
FASTCHU_LXNS_OAUTH_SCOPES=read_player
```

落雪 OAuth 应用**不要填回调地址**（无回调模式），权限勾选 `read_player`。
用户发 `/授权` 打开链接，同意后把页面显示的一次性授权码私聊发回机器人。
授权记录保存在项目 `data/auth/oauth_tokens.json`（已在 `.gitignore` 中），
Client Secret 与令牌都不要提交或发到群里。

### 候选列表与翻页

命中多首时每页 8 条，标题带 `（1/6 页）`；每条候选两行（名字 + 难度）。
回 `下一页` / `上一页` 翻页，回序号看当前页对应条目。

命中多首时直接回一个序号即可：

```
@FastChu world      → 列出 1~6
2                   → 直接出「Worlds End」的详情
取消                 → 放弃选择
```

候选按「会话 + 用户」保存，90 秒有效，只对发起查询的人生效。
发新歌名会覆盖旧候选；序号越界会提示范围并保留候选。

曲目详情包含：**曲师 / 分类 / 曲速 / 版本 / 相关特殊称号**。
相关称号只保留特殊的（常规 AJ、究极固定、角色养成、版本全完成称号不显示），并标出**颜色**与**所需条件**。
国服与日服定数不同时会同时标注：

```
Aether Crest: Celestial
void (Mournfinale) × 水野健治 · 原创 · BPM 180 · VERSE
EXP 14(14.3国/14.2日) · MAS 15+(15.5国/15.6日)
```

`?` 表示日服定数是推定值（官方未公布）。

## 安装

插件已经装到 AstrBot 的 `data/plugins/astrbot_plugin_fastchu`。之后改代码要重新同步：

```bash
# 在 FastChu 项目根目录
python scripts/build_astrbot_plugin.py --install
```

然后重启 AstrBot，或在 WebUI 的「插件」页重载。

也可以直接在 AstrBot 的 WebUI 里「安装插件」→ 填本地路径 `D:\agent\dsh_workspace\FastChu\astrbot_plugin_fastchu`。

## 装到「新建的 AstrBot 实例」上

AstrBot Launcher 里的实例是各自独立的（`~/.astrbot_launcher/instances/<实例ID>/`），
插件要装到**目标实例**的 `core/data/plugins/` 下。

**顺序很重要**：先让新实例启动一次（Launcher 里点「启动」，它会创建 `data/` 目录结构），
再装插件。否则 Launcher 首次配置时重建 `data/` 会把插件冲掉（实测踩过）。

```bash
# 1) 看有哪些实例
dir %USERPROFILE%\.astrbot_launcher\instances

# 2) 装到指定实例
python scripts/build_astrbot_plugin.py --install --target ^
  "%USERPROFILE%\.astrbot_launcher\instances\<实例ID>\core\data\plugins"
```

只有一个实例时可以不写 `--target`，脚本会自动找到它。

装完重启该实例（或在它的 WebUI 里重载插件）即可。

## 目录结构

```
astrbot_plugin_fastchu/
├── main.py            # 插件入口（AstrBot 适配层，唯一与框架相关的文件）
├── metadata.yaml      # 插件元数据（名称/简介/版本/作者）
├── requirements.txt   # 空的：不需要额外依赖
├── _paths.json        # 构建时写入的 FastChu 项目路径（用来复用已有缓存）
└── _vendor/fastchu/   # 随插件附带的框架无关核心层（由脚本生成，勿手改）
```

`_vendor/fastchu/` 是**生成产物**，源在 FastChu 项目根目录的 `fastchu/`。
改核心逻辑请改项目里的源，然后重跑构建脚本。

## 为什么把核心层「复制」进插件

FastChu 的核心层（`models` / `api` / `services` / `render`）不依赖 NoneBot，也不依赖 AstrBot。
把它作为 `_vendor/` 附带，好处是：

- **不用 `pip install`**，不会往 AstrBot 的 venv 塞 `nonebot2` 等依赖；
- 避免和 AstrBot 自带的 `pydantic` / `httpx` 版本冲突；
- 插件目录自包含，拷走就能用。

代价是代码有副本。用构建脚本保证副本始终来自源码，就不会漂移。

## 数据缓存

默认**复用 FastChu 项目里已经抓好的缓存**（曲库 1466 首、称号 5620 个、国服日服并集），
路径由 `_paths.json` 里的 `project_dir` 决定。

优先级：

1. 环境变量 `FASTCHU_DATA_DIR`（直接指定数据目录）
2. 环境变量 `FASTCHU_PROJECT_DIR`（指定项目根）
3. `_paths.json` 里的 `project_dir`/data
4. 都找不到 → 用插件目录下的 `data/`（首次查询会自动抓取，约 6 MB）

所以如果以后移动了 FastChu 项目目录，重新跑一次 `build_astrbot_plugin.py --install` 即可。

## 和框架命令的冲突

`/歌曲名` 这条路径由一个自定义过滤器处理：文本以 `/` 开头、且首个词**不是任何已注册命令**
（包括 AstrBot 内置的 `/help`、`/reset`，以及本插件的 `/查歌`、`/称号`、`/帮助`）时，
才当作歌名去查。已注册命令是通过 AstrBot 的 `star_handlers_registry` 运行时枚举的，
所以装了新插件也能自动避开。

另外保留了显式写法 `/查歌 <关键词>`，万一某首歌名和某个命令撞了可以用它。

## 配置项

本插件没有面板配置项，需要调整时用环境变量（前缀 `FASTCHU_`）：

**面板配置**（AstrBot WebUI → 插件 → FastChu 查歌 → 配置）：

| 配置项 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| 物量显示 `notes_mode` | 下拉 | `compact` | `off` 取消 / `compact` 简洁（只给总物量）/ `full` 完整（带分项） |
| `jacket_separate` | 开关 | 关 | 曲绘单独发一条消息（QQ 里图片显示更大） |
| `related_title_limit` | 整数 | `8` | 详情里最多列几个相关称号 |

**环境变量**（前缀 `FASTCHU_`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `FASTCHU_JACKET_MODE` | `url` | `url` 只发地址（最轻量）/ `base64` 内联 / `off` 关闭 |
| `FASTCHU_QUERY_COOLDOWN` | `3` | 同一用户冷却秒数 |
| `FASTCHU_OAUTH_ENABLED` | `true` | 是否允许用户绑定落雪账号 |
| `FASTCHU_LXNS_OAUTH_CLIENT_ID` | 空 | 落雪 OAuth 应用 Client ID（留空则绑定不可用） |
| `FASTCHU_LXNS_OAUTH_CLIENT_SECRET` | 空 | OAuth Client Secret（可选） |
| `FASTCHU_LXNS_OAUTH_SCOPES` | `read_player` | 申请的权限范围 |
| `FASTCHU_LXNS_OAUTH_TOKEN_PATH` | 项目 `data/auth/oauth_tokens.json` | 授权记录路径（含令牌） |
| `FASTCHU_PERSONAL_SCORE_TTL` | `300` | 个人成绩缓存秒数 |
| `FASTCHU_PERSONAL_TITLE_TTL` | `600` | 已获得称号缓存秒数 |
| `FASTCHU_PERSONAL_TIMEOUT` | `8` | 个人数据请求超时秒数 |
| `FASTCHU_SHOW_NOTES_IN_DETAIL` | `true` | 详情是否带物量行 |
| `FASTCHU_RELATED_TITLE_LIMIT` | `8` | 详情里最多列几个相关称号 |

在 AstrBot 的启动环境里设置即可（例如系统环境变量）。

## 已知限制

- **LLM 可能重复回复**：插件回复后会调用 `event.stop_event()` 阻止事件继续传播；
  如果你的 AstrBot 配置了「命令未命中就走 LLM」，个别情况下仍可能多一条 LLM 回复，
  可把该群设为「只响应指令」或在指令配置里关掉 LLM。
- **曲绘**默认只发 URL，由 OneBot 实现自行下载；若你的实现访问不了外网，
  改成 `FASTCHU_JACKET_MODE=base64`。
- 数据是**公共接口**快照，版本更新后需要重新抓：
  `python scripts/fetch_reference_data.py` + `fetch_jp_reference.py` + `build_*`。
