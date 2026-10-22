# 别名 overlay

落雪接口只提供国服曲目的别名，日服独有曲目一条都没有。这个目录存放**运行时叠加**
的补充别名，刷新落雪缓存不会覆盖它们。

## 文件

| 文件 | 是否入库 | 说明 |
| --- | --- | --- |
| `community.json` | 是 | 从社区数据导入，只能由脚本生成 |
| `local.json` | 否 | 群友通过 `/添加别名` 投稿，运行时写入 |

## community.json

数据来自 [AmethystTim/ChunithmUtil](https://github.com/AmethystTim/ChunithmUtil)
的 `data/alias.json` 与 `backup/alias_legacy.json`（群内用户累积的别名库）。
`cid` 是日服曲目编号，导入时按 `data/songs/songs.union.json` 的 `jp_idx` 映射。

重新生成：

```bash
python scripts/import_community_aliases.py
# 或使用本地快照
python scripts/import_community_aliases.py --fresh alias.json --legacy alias_legacy.json
```

脚本会丢弃空别名、超长别名、与曲名相同的别名、单字非 ASCII 别名，以及同时挂在
超过 5 首曲目上的歧义别名（例如「屎」）。输出按国服 `cn_id` / 日服 `jp_idx`
分组的 `{"cn": {...}, "jp": {...}}`。

## local.json

结构与 `community.json` 的 `cn` / `jp` 部分相同，但不含 `meta`。由
`/添加别名` 直接追加，属于运行时数据，不提交到仓库。
