# 国服 / 日服曲库并集

- 生成时间：2026-10-21 03:55:18 +0800
- 国服来源：落雪查分器 `GET /api/v0/chunithm/song/list`（1414 首普通曲 + 52 个 WORLD'S END）
- 日服来源：chunirec `music/showall` 镜像（reiwa.f5.si）（1691 首普通曲 + 76 个 WORLD'S END）

## 统计

| 项目 | 数量 |
| --- | --- |
| 两服都有（普通曲） | **1414** |
| 两服都有（WORLD'S END） | 44 |
| 仅国服 | 8 |
| 仅日服（普通曲） | **277** |
| 仅日服（WORLD'S END） | 32 |
| 定数不同的（曲目, 难度）组合 | **33** |
| 涉及曲目数 | 29 |
| 日服标记为「定数推定」的谱面 | 581 |

## 为什么要单独处理 WORLD'S END

两服都把 WE 当独立曲目（国服是 `8xxx` 号段），但标题规则不同：
日服带修改符后缀（`幾四音-Ixion-【止】`），国服不带（`幾四音-Ixion-`）。
如果和普通曲混在同一个标题空间里配对，WE 会错配到同名的普通曲上。
另外 WE 用 ★ 难度、定数恒为 0，**不参与定数比较**。

## 字段

| 字段 | 说明 |
| --- | --- |
| `kind` | `song`（普通曲）/ `we`（WORLD'S END） |
| `regions` | `["cn","jp"]` / `["cn"]` / `["jp"]` |
| `cn_id` / `jp_id` | 两服各自的曲目 ID（单服独有时另一侧为 `null`） |
| `jp_release` / `jp_version` | 日服发行日期 / 所属版本（Mate、X-VERSE 等，由稼働日表换算） |
| `jp_idx` | 日服谱面 ID（与国服 `id` 同一套编号；WE 无此字段） |
| `jp_img` | 日服曲绘文件名哈希（`db.chunirec.net/lmg/music/jkt/{jp_img}.jpg`；WE 无此字段） |
| `genres.cn` / `genres.jp` | 两服分类名（日服分类已映射出国服写法） |
| `charts.<难度>` | `cn` / `jp` 两侧的标级与定数，`const_differs` 标出差异 |
| `charts.<难度>.jp.const_unknown` | 日服该定数是否为推定值（官方未公布） |

## 定数差异样例

| 曲目 | 难度 | 国服 | 日服 | 日服定数推定 |
| --- | --- | --- | --- | --- |
| 8-EM | EXP | 12.1 | 12.5 | 否 |
| Aether Crest: Celestial | EXP | 14.3 | 14.2 | 否 |
| Aether Crest: Celestial | MAS | 15.5 | 15.6 | 否 |
| Ai Nov | EXP | 12.3 | 12.5 | 否 |
| Burn!!! | MAS | 14.1 | 14.0 | 否 |
| Challenger!! | MAS | 14.0 | 14.1 | 否 |
| Flap&Clap | MAS | 14.0 | 14.1 | 否 |
| HERA | MAS | 15.0 | 15.1 | 否 |
| LibrariA | MAS | 15.2 | 15.3 | 否 |
| MegiddØ | EXP | 14.4 | 14.5 | 否 |
| MyPaceInvention | MAS | 12.2 | 12.3 | 否 |
| Paqqin (tpz Despair Remix) | EXP | 12.4 | 12.5 | 否 |
| PRIVATE SERVICE | ULT | 14.9 | 15.0 | 否 |
| Pump!n | ADV | 6.0 | 7 | 否 |
| RE Aoharu | EXP | 10.2 | 10 | 是 |
| Star Saveler | MAS | 14.4 | 14.5 | 否 |
| Stronghold LandEater | EXP | 11.1 | 11 | 是 |
| Tru'nembra | EXP | 14.4 | 14.3 | 否 |
| Tru'nembra | MAS | 15.6 | 15.5 | 否 |
| Unwelcome School | EXP | 10.8 | 10.5 | 是 |

## 同名未完全消歧（已尽量按艺术家配对）

- volcanic
