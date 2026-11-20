# COMP7608 · AGX 四人协作比赛

两项比赛：多人赛车 Racing、多人枪战 FPS。四人每四天完成一轮可验证的策略实验，代码、结果和报告同步更新。仓库已公开，供课程小组协作。

## 版本交接 · 2026 年 11 月 20 日

| 比赛 | 当前 AGX 计分版 | 从这里继续改 | 各版本的问题、改动与结果 |
|---|---|---|---|
| FPS | [v5 · R08 提交包](releases/competition/fps/v5/FPS_R08.zip) | [R08 源码](strategies/fps/) | [FPS 版本记录](releases/FPS版本.md) |
| 赛车 | [v3 · R07 提交包](releases/competition/racing/v3/RACING_R07.zip) | [R07 源码](strategies/racing/) | [赛车版本记录](releases/赛车版本.md) |

[本地模拟版本源码](strategies/local/)按实验编号保存。[正式比赛 ZIP](releases/competition/)按 AGX 版本号保存。

## 从这里开始

1. 阅读 [协作流程](docs/WORKFLOW.md) 和 [四人分工与日历](docs/TEAM_AND_SCHEDULE.md)。
2. 在个人分支修改 `strategies/racing/` 或 `strategies/fps/`，保留官方代码不动。
3. 复制 [实验模板](experiments/TEMPLATE.md)，记录假设、版本、测试、比赛证据与结论。
4. 提交 PR，请另一名组员审阅；同步补充 [持续报告](report/REPORT.md) 和 [贡献记录](report/CONTRIBUTIONS.md)。
5. 发布负责人按 [发布清单](docs/RELEASE.md) 上传候选包，记录平台版本、激活及实际比赛结果。

## 目录

| 路径 | 用途 |
|---|---|
| `01_Official_Starter/` | 原始基线，禁止修改；来源与校验值见 Source_Manifest.json |
| `02_Upload/` | 已有官方原版 ZIP，原样保留 |
| `strategies/racing/`、`strategies/fps/` | 当前计分版源码与下一轮开发入口 |
| `experiments/` | 每人每轮实验记录，包括失败和回退 |
| `report/` | 持续报告、结果表和贡献记录 |
| `releases/` | Git 提交与平台版本对应记录 |
| `evidence/` | 截图、回放链接和可核验的结果 |
| `scripts/` | 基础检查与打包工具，仅依赖 Python 标准库 |
| `sources/` | 老师说明与历史工作区记录 |

## 本地检查与打包

```bash
python3 scripts/check.py
python3 scripts/package.py racing
python3 scripts/package.py fps
```

生成文件在 `dist/`，不提交到 Git；工具同时生成包含 Git 提交号和文件校验值的清单。打包前先提交代码，保持工作区干净。基础检查覆盖语法、JSON、官方基线完整性；平台运行与策略效果需实战验证。

课程比赛结束时间按现有说明为 **2026-12-15 23:59:59，北京时间**。报告提交渠道、格式与截止时间仍需核对最新课程通知。来源：[Assignment Instructions](sources/Assignment_Instructions.pdf)。不要上传密码、API 密钥或完整私人聊天。

首轮本地候选保存在 [releases/R01](releases/R01/README.md)。
