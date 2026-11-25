# 平台版本、源码提交与开发分支

更新：2026-11-23。先看下面的**个人账号**表；Wego 的历史版本另列，两个账号的同名 v1/v2 不代表同一份代码。

`vN` 是平台版本序号；commit 是源码快照；分支会随开发移动；ZIP SHA256 是实际上传文件的指纹。还原某个旧版本应找固定 commit 或原 ZIP，不应只依赖分支当前名称。完整 SHA256、Git SHA、证据和核验方式见 [VERSION_MAP.json](VERSION_MAP.json)。

## 个人账号：按用户提供的版本历史截图确认

证据：[FPS 历史](../experiments/fps_record1123.JPG)、[Racing 历史](../experiments/racing_record.JPG)。下表的指纹是截图显示的前 12 位，已与本地 ZIP 的完整 SHA256 对应；截图均显示包已通过验证。它不等于激活回执，也不能把跨版本累计分数全部归到某一版本。

### FPS

| 平台版本 | 指纹前缀 | 源码 commit | 代码内容 | 包 / 分支 |
|---|---|---|---|---|
| v1 | `2fb651073041` | `4f5747cbde42` | R08、导航效率、掉包回收 | [原 ZIP](personal/haoran/fps/4f5747cbde42/fps-4f5747cbde42.zip)；历史分支 `codex/haoran-fps-navigation` |
| v2 | `06bcdcc4ae39` | `e27acc998e08` | 未知包位搜索、转点回防 | [原 ZIP](personal/haoran/fps/e27acc998e08/fps-e27acc998e08.zip)；今后用 `codex/haoran-fps`，历史分支 `codex/haoran-fps-69259abb` |
| v3–v5 | — | — | 目前截图只有 v1、v2，未取得后续版本记录 | 不用本地实验次数补造平台版本号 |

另归档了准备过的 [老师 baseline](personal/haoran/fps/952e480a14ca/fps-952e480a14ca.zip) 和 [仅效率优化候选](personal/haoran/fps/2de04594295a/fps-2de04594295a.zip)，它们没有对应的个人平台版本序号证据。

### Racing

| 平台版本 | 指纹前缀 | 源码 commit | 代码内容 | 包 / 分支 |
|---|---|---|---|---|
| v1 | `f5eb50075471` | `952e480a14ca` | 老师 baseline | [原 ZIP](personal/haoran/racing/952e480a14ca/racing-952e480a14ca.zip)；`codex/haoran-baseline` |
| v2 | `4a3466ba671b` | `41367f1bf548` | R07 加侧向切入预测 | [原 ZIP](personal/haoran/racing/41367f1bf548/racing-41367f1bf548.zip)；`codex/haoran-racing-strategy` |
| v3 | `bfaeb7418390` | `d6268a7f50ad` | 湿地稳定行驶的参考制动修正 | [原 ZIP](personal/haoran/racing/d6268a7f50ad/racing-d6268a7f50ad.zip)；`codex/haoran-racing-64728852` |
| v4 | `0ce291ac5e30` | `ee12c93d76f6` | 有上限的发车响应候选 | [原 ZIP](personal/haoran/racing/ee12c93d76f6/racing-ee12c93d76f6.zip)；`codex/haoran-racing-20261123` |
| v5 | `0ce291ac5e30` | `ee12c93d76f6` | **与 v4 同一个包，没有新策略改动** | 同上；今后用 `codex/haoran-racing` |

Racing v4/v5 的平台显示上传时间分别为 11/23 10:36:05 和 10:36:08；不能把这次重复上传当成新的实验。此前完整本人日志已分别核实 v2 对应包（比赛 `64728852…`）和 v3 对应包（比赛 `33d17cb4…`）的采用；尚未取得 v4/v5 的计分对局日志。

原始打包 JSON 中的 `not_run` 保留为打包时状态；当前截图验证通过证据记录在本索引和 [平台历史补录](../experiments/R02-haoran-racing-launch/platform-history-20261123.json)，不改写旧事件。

## 今后主要使用两个开发分支

| 长期分支 | 主要维护目录 | 当前该游戏源码 | 用途 |
|---|---|---|---|
| `codex/haoran-fps` | `strategies/fps/` | `596c5387f21d`，第五版防护购买候选（待上传）；最近实战已核实第四版包179919156da8 | FPS后续开发、实验和报告 |
| `codex/haoran-racing` | `strategies/racing/` | `ee12c93d76f6`，个人 Racing v4/v5 | Racing 后续开发、实验和报告 |

这两个分支从整理后的最新完整历史开始。Git 分支是**整份仓库**，因此两边都会看到 FPS、Racing 和报告；“两个开发分支”是工作分工，不是把文件拆成两个仓库。新实验主要改对应游戏目录，完成后再按团队审阅约定整合。`main` 保持原来的小组稳定交接状态，本次没有合入策略。

所有旧分支保留并推送，作为归档，不删除、不改写历史：

| 旧分支 | 查什么 |
|---|---|
| `codex/haoran-baseline` | 个人 baseline 准备及校验记录 |
| `codex/haoran-fps-navigation` | FPS 节点效率与掉包回收，最终源码 `4f5747cbde42` |
| `codex/haoran-fps-69259abb` | FPS 回防候选，源码 `e27acc998e08` |
| `codex/haoran-racing-strategy` | Racing 侧向预测，源码 `41367f1bf548` |
| `codex/haoran-results-20261121` | 当时两游戏面板与运行状态 |
| `codex/haoran-racing-64728852` | Racing 湿地候选与两场复盘，源码 `d6268a7f50ad` |
| `codex/haoran-racing-20261123` | Racing 发车候选与本次版本整理，源码 `ee12c93d76f6` |

远端原有 `codex/r01-local-evaluation` 是小组历史交接分支，也保留。多数个人实验分支已在同一条开发历史上，baseline 分支另有独立记录；无需为减少名字而强行合并或删除。

## Wego 历史账号：与个人版本分开

“本库可还原提交”可能是归档导入提交，**不是**原作者最初开发提交。四条原记录中的 Git SHA 在本仓库没有对应对象，下面同时保留其旧记录和可实际取出的快照，避免提供无法 checkout 的指引。

### FPS 历史 v1–v5

| 平台版本 | 原 ZIP 指纹 | 本库可还原 commit / 路径 | 原记录的提交 |
|---|---|---|---|
| [v1](competition/fps/v1/FPS_Official_Starter.zip) | `8c1145d12672` | `952e480a14ca` / `strategies/fps/` | 未记录 |
| [v2](competition/fps/v2/FPS_Official_Starter.zip) | `8c1145d12672` | 同 v1，同一个原 ZIP | 未记录 |
| [v3](competition/fps/v3/FPS_Route_Mixed.zip) | `d7ecab804fc0` | `6d73ed44f312` / `strategies/local/fps/R01-route-mixed/` | 未记录 |
| [v4](competition/fps/v4/FPS_R06_Dust2.zip) | `94357aa70cea` | `6d73ed44f312` / `strategies/local/fps/R06/` | `66171b6bedfb`，本库对象缺失 |
| [v5](competition/fps/v5/FPS_R08.zip) | `7231baa0d8db` | `6d73ed44f312` / `strategies/local/fps/R08/` | `a3af1bbab4ae`，本库对象缺失 |

### Racing 历史 v1–v5

| 平台版本 | 原 ZIP 指纹 | 本库可还原 commit / 路径 | 原记录的提交 |
|---|---|---|---|
| [v1](competition/racing/v1/RACING_v1.zip) | `1029b9b56fde` | `6d73ed44f312` / 上述原 ZIP；没有找到全部源文件一致的独立源码目录 | 未记录 |
| [v2](competition/racing/v2/RACING_R03.zip) | `477e4e546740` | `6d73ed44f312` / `strategies/local/racing/R03/` | `1338d206388d`，本库对象缺失 |
| [v3](competition/racing/v3/RACING_R07.zip) | `9708d0db1a87` | `6d73ed44f312` / `strategies/local/racing/R07/` | `0b6d75a9a829`，本库对象缺失 |
| v4、v5 | — | 当前 Wego 归档没有这两版的证据 | 不与个人 v4、v5 混用 |

## 还原与取包

直接下载表中的原 ZIP 最容易保持平台指纹。即使源码相同，不同 ZIP 包装也可能产生不同指纹；例如 FPS 官方源码在不同归档中的 ZIP 指纹并不必然相同。

需要开发旧源码时，用独立工作区，避免覆盖当前两项策略：

```sh
git fetch origin
git -c core.autocrlf=false worktree add --detach ../agx-racing-v3 d6268a7f50ad
# 代码在 ../agx-racing-v3/strategies/racing/
```

若重打包，在该干净工作区使用 `python3 scripts/check.py`、`python3 scripts/package.py racing`。Windows 应保留 LF、使用 UTF-8 Python；基础检查不等于游戏环境测试。同步 Git 仓库与 AGX 上传/激活是两个独立步骤。

## FPS 第三版开发候选（2026-11-24）

源码 `65652c51f1ef`，指纹 `67e64a8436fe`，[原 ZIP](personal/haoran/fps/65652c51f1ef/fps-65652c51f1ef.zip)。短暂不可见时最长0.3秒保持瞄准与蹲姿，隐藏帧不开火；详见[实验](../experiments/R02-haoran-fps-v3-aim-hold/README.md)。检查、打包完成；没有平台上传、验证、激活或新策略赛果，平台 v3 序号待用户上传确认，不改写上方历史 v1/v2。长期分支表为本分支文档快照，Racing 后续记录见其独立开发分支。

2026-11-25：FPS第三版开发包65652c51f1ef/67e64a8436fe已由[完整Mirage本人日志](../experiments/R02-haoran-fps-da9ec3c5/README.md)核实实际采用，冻结版本d0a8c994-b169-45ab-ba1b-4031c01e790f。本队9:13；此前未上传/未实战文字为打包当时状态，平台序号仍需历史回执。

## FPS第四版开发候选（2026-11-25）

源码 `c979ee08a85e`，指纹 `179919156da8`，[ZIP](personal/haoran/fps/c979ee08a85e/fps-c979ee08a85e.zip)。仅Mirage修复纯Z起伏重置恢复、同目标跳跃冷却和空中重接；保留第三版瞄准。见[实验](../experiments/R02-haoran-fps-v4-horizontal-recovery/README.md)。本地检查/打包完成，未上传、验证、激活或取得新候选赛果，不预分配平台v4。

2026-11-25：第四版开发包c979ee08a85e/179919156da8已由[4f66f223完整本人日志](../experiments/R02-haoran-fps-4f66f223/README.md)核实采用；Borgo2:13，冻结版本c1325814-e1b0-45df-9089-bb67761c64dd。此前候选未上传等文字为打包时事件，不据此推定平台v4序号。

## FPS第五版开发候选（2026-11-25）

源码 `596c5387f21d`，指纹 `e839b6f6efc8`，[ZIP](personal/haoran/fps/596c5387f21d/fps-596c5387f21d.zip)。只新增防护购买覆盖：主武器→护甲→满护甲头盔升级→防守拆包器，并处理拒绝回执；瞄准和导航保持。见[实验](../experiments/R02-haoran-fps-v5-protection-buy/README.md)。本地检查/打包完成，未上传、验证、激活或取得新候选赛果，不预分配平台v5。
