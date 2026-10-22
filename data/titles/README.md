# 中二节奏称号数据集

由 `scripts/build_title_report.py` 从落雪查分器公共接口生成，**不需要任何密钥**。

## 文件

| 文件 | 内容 |
| --- | --- |
| `trophies.all.json` | 全部 5620 个称号，含分类与条件 |
| `trophies.special.json` | 过滤后的 1357 个「特殊获得条件」称号 |
| `trophies.special.csv` | 同上，Excel 可直接打开（UTF-8 BOM） |
| `trophies.special.md` | 同上，按稀有度分层 + 分类的可读报告 |
| `trophies.kaomoji.json` | 30 个颜文字称号 |
| `trophies.kaomoji.md` | 颜文字称号报告 |

生成时间：2026-10-22 14:04:24 +0800

## 过滤逻辑

用户要的是「去掉固定称号（常规 AJ / 究极）和角色养成称号」，只留特殊获得条件。实测分布：

| 分类 | 数量 | 是否收录 | 判定规则 |
| --- | --- | --- | --- |
| `per_song_aj` | 2960 | ❌ 排除 | `required` 只有 `full_combo`、只涉及 1 首歌，且**称号名就是曲名** → 每个谱面的固定 AJ 称号 |
| `character_rank` | 1209 | ❌ 排除 | 无 `required`，`description` 含 `キャラクターランク` → 角色养成 |
| `per_song_rank` | 252 | ✅ 收录 | `required` 含 `rank`，只涉及 1~4 首歌 |
| `per_song_other` | 218 | ✅ 收录 | 单曲但只有「打一遍」之类条件 |
| `multiplayer` | 192 | ✅ 收录 | `description` 含 `マッチング` → 需要店内/好友联机 |
| `event` | 123 | ✅ 收录 | 无 `required` 且 `description` 为 `-` → 活动/联名限定 |
| `map_clear` | 108 | ✅ 收录 | `description` 含 `マップ制覇` |
| `per_song_aj_special` | 101 | ✅ 收录 | 同样是单曲 AJ/FC，但**称号名不是曲名**（或覆盖全难度）→ 专属成就称号，保留 |
| `ultima_aj` | 94 | ❌ 排除 | `color == ultima` → 称号名就是曲名，属于每个谱面的固定称号 |
| `inherited` | 85 | ✅ 收录 | `description` 含 `引き継ぎ` → 旧作继承 |
| `all_songs` | 57 | ✅ 收录 | 涉及曲目数 ≥ 40 → 某版本全部谱面的 ACHIEVE |
| `per_song_fc` | 51 | ✅ 收录 | `required` 只有 `full_combo=fullcombo` → 单曲 FULL COMBO（不是 AJ） |
| `other` | 40 | ✅ 收录 | 其余无法自动归类 |
| `login_days` | 39 | ✅ 收录 | `description` 含 `ログイン` |
| `rating` | 37 | ✅ 收录 | `description` 含 `RATING x.xx` |
| `one_credit` | 28 | ✅ 收录 | `description` 含 `クレジット中` |
| `matching_player` | 18 | ✅ 收录 | 需要和佩戴特定称号的玩家联机 |
| `player_level` | 7 | ✅ 收录 | `description` 含 `プレイヤーレベル` |
| `danger_skill` | 1 | ✅ 收录 | `description` 含 `デンジャースキル` |

**排除 4263 个（常规 AJ 2960 / 究极固定 94 / 角色养成 1209），保留 1357 个。**

## 颜色分布

| 颜色 | 全部 | 特殊称号中 |
| --- | --- | --- |
| `platina` | 2436 | 351 |
| `gold` | 1716 | 236 |
| `silver` | 639 | 638 |
| `rainbow` | 612 | 9 |
| `ultima` | 94 | 0 |
| `normal` | 66 | 66 |
| `image` | 57 | 57 |

> 注意：虹色（`rainbow`）称号共 612 个，
> 其中 603 个是角色养成（把某个角色练到指定等级）。
> 按需求已排除；如果盘点视频需要把它们算进来，重新生成时加
> `--include-character-rainbow`。

## 字段说明（JSON）

| 字段 | 说明 |
| --- | --- |
| `id` | 称号 ID |
| `name` | 称号文本 |
| `color` / `color_label` | 颜色枚举 / 中文标签 |
| `category` / `category_label` | 分类枚举 / 中文标签 |
| `tier` / `tier_label` | 自定义稀有度分层（1 最难 ~ 4 最普通） |
| `is_special` | 是否为特殊称号 |
| `excluded_reason` | 被排除的原因（空 = 收录） |
| `description` | 官方条件文本（原样保留） |
| `condition_text` | 可读条件（`description` 为空时用结构化条件拼） |
| `song_count` / `song_ids` / `song_titles` | 关联曲目 |
| `difficulties` / `rank` / `full_combo` / `full_chain` | 结构化条件 |
| `is_kaomoji` | 是否颜文字称号 |

## 日服 Wiki 考据补全

国服数据里有一批称号**没有真正的获得条件**：208 个的 `description` 是
`CHUNITHMから引き継ぎ` 或 `-`。本数据集从日服
[CHUNITHM 攻略 Wiki](https://wikiwiki.jp/chunithmwiki/) 抓取了 1983 条称号记录，
按标准化称号名匹配，补全了 **130** 个特殊称号的条件。

| `condition_source` | 数量 | 含义 |
| --- | ---: | --- |
| `lxns` | 1149 | 国服数据自带说明 |
| `wiki` | 130 | 日服 Wiki 补全，报告里带 `※日服Wiki` 标记 |
| `none` | 78 | 两处都查不到（多为最新活动称号） |

仍缺条件的多是 **WEC2026 / 全勤王 / 四倍率** 等最新活动称号——Wiki 也还没收录。

另外收录 **851** 个「日服有、国服没有」的称号（`jp_only.json`）。
它们的条件与颜色来自同一批 Wiki 表格：颜色按单元格背景色解析
（虹 41 / 铂金 203 / 金 152 /
银 341 / 普通 34 / 未标注 80）。

### 「CHUNITHMから引き継ぎ」是什么意思

Wiki 页面写得很清楚：2021/11/4 起，初代 CHUNITHM 的这批称号在 NEW 之后的机体上
**新规取得不可**，官方把入手条件的描述统一改成了「CHUNITHMからの引き継ぎ」。
所以它不是条件，而是「已经无法获得」的标记。

### 已知局限：同名冲突

极少数称号在**旧作与现行版本里同名但条件不同**（例如 `皇帝`）。
Wiki 侧按第一次出现的条目匹配，因此这类称号给出的可能是**现行版本**的条件。

## 复现

```bash
python scripts/fetch_reference_data.py            # 抓曲库 + 称号
python scripts/build_title_report.py              # 重新生成本目录
python scripts/build_title_report.py --include-character-rainbow
```

## 已知数据问题

* 官方文档写的颜色枚举是 `normal/copper/silver/gold/platinum/rainbow/image`，
  但**实际接口**返回 `platina`（不是 `platinum`）、额外有 `ultima`、没有 `copper`。
  本项目以实际数据为准，颜色用字符串保存并做标签映射，未知颜色不会解析失败。
* 全曲系称号（`Spirit/Tribute/Legend of <版本>`）只覆盖到 `VERSE`，
  最新版本（X-VERSE 等）尚未出现在接口数据中。
