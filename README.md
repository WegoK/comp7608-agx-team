# COMP7608 · AGX 比赛

四人小组，每四天一轮策略实验。

## 个人开发与版本定位（haoran）

完整的 **平台 v1–v5 → 指纹 → commit → 分支/原 ZIP** 对照见 [版本与分支索引](releases/VERSION_MAP.md)。个人账号与 Wego 历史账号分开；个人 Racing v4/v5 是同一个包。

| 后续开发 | 长期分支 | 当前该游戏源码 |
|---|---|---|
| FPS | [codex/haoran-fps](https://github.com/WegoK/comp7608-agx-team/tree/codex/haoran-fps) | `e27acc998e08`，个人 v2 |
| Racing | [codex/haoran-racing](https://github.com/WegoK/comp7608-agx-team/tree/codex/haoran-racing) | `ee12c93d76f6`，个人 v4/v5 发车候选 |

旧实验分支保留作归档。个人版本包及清单见 [releases/personal/haoran](releases/personal/haoran/)，按固定源码提交存放；分支包含整份仓库，后续主要修改对应游戏目录。

## 小组历史交接

下表为 Wego 账号 2026 年 11 月 20 日交接时的版本；不是个人账号的同名平台版本。当前工作区策略可能已继续开发，需按上述固定提交定位。

| 比赛 | 继续开发 | 正式参赛包 | 问题、改动与结果 |
|---|---|---|---|
| FPS | [R08 源码](strategies/fps/) | [v5 · FPS_R08.zip](releases/competition/fps/v5/FPS_R08.zip) | [逐版对照](releases/FPS版本.md) |
| 赛车 | [R07 源码](strategies/racing/) | [v3 · RACING_R07.zip](releases/competition/racing/v3/RACING_R07.zip) | [逐版对照](releases/赛车版本.md) |

逐版对照里可以分别查到本地模拟结果和正式赛果。

## 做一轮实验

1. 从对应比赛的源码建立个人分支，选一个问题，填写[实验记录](experiments/TEMPLATE.md)。
2. 对照[本地历史策略](strategies/local/)测试改动，记录同条件下的差值。正式上场后，把赛果补到对应的版本记录。
3. 把结论写入[小组报告](report/REPORT.md)，提交合并请求，请另一位组员审阅。

[分工与轮次](docs/TEAM_AND_SCHEDULE.md) · [分支协作](docs/WORKFLOW.md) · [检查与打包](docs/RELEASE.md) · [老师的作业说明与示例](sources/README.md)
