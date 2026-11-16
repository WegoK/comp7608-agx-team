# AGX 比赛工作区

当前版本：老师原始 starter，2026-11-12 从 Assignment Instructions 第 14–19 页恢复为代码文件。策略、参数和注释按原文保留，仅恢复 PDF 中的换行和缩进。

三人已组队；用户于 2026-11-12 确认课程登记与计分账号均已完成，两项 v1 已激活。当前处于定级阶段，等待结果与回放。当前阶段先使用基础代码完成平台验证和参赛设置，之后再做改进。

## 直接使用这两份上传包

| 游戏 | 上传 ZIP | 编辑器粘贴用的原始文件 |
|---|---|---|
| Formula Racing | [Racing_Official_Starter.zip](02_Upload/Racing_Official_Starter.zip) | [agent.py](01_Official_Starter/Racing/agent.py) + [design.json](01_Official_Starter/Racing/design.json) |
| Tactical FPS | [FPS_Official_Starter.zip](02_Upload/FPS_Official_Starter.zip) | [agent.py](01_Official_Starter/FPS/agent.py) |

ZIP 顶层直接放所需文件，没有额外目录或 macOS 隐藏文件。上传方式来自老师说明 p.3；实际是否接受以平台验证结果为准。

## 报名和激活

1. 11 月 12–14 日内完成 [课程登记表](https://docs.google.com/spreadsheets/d/1WbGzFBYMni39rQa5HQQW0GfHpqeMGpTpBcTeNKg_kxU/edit)，每队一行，确认成员和用于评分的 AGX 账号；账号细节按新班级公告核对。
2. 登录 [AGX](https://agx.cool)，分别注册 Racing 和 FPS。
3. 给对应游戏上传上表 ZIP，或将文件内容粘贴到平台编辑器。两个游戏的 `agent.py` 不同，请分别使用。
4. 等待 validation 通过，记录 accepted version ID，再点击 `Use this version in competition`。
5. 课程比赛 11 月 15 日开始后，核对计分账号在两个游戏里实际产生 ranked result，记录到 [参赛记录](Submission_Record.md)。

课程比赛于 2026-12-15 23:59:59（北京时间 UTC+8）结束。每项参与基础分依赖实际 ranked result；上传、验证和激活分别记录。依据：[原始说明](sources/Assignment_Instructions.pdf) pp.3,20–26。

## 文件与版本

- `01_Official_Starter/`：老师原始代码基线，后续保留用于对照。
- `02_Upload/`：本次两个原版上传包。
- `sources/`：本次使用的原始说明 PDF。
- [参赛记录](Submission_Record.md)：账号、版本、平台验证、激活与比赛结果。
- [来源核对](Source_Manifest.json)：PDF 页码与每段代码的行号对应、文件校验值。

后续改进使用新的版本目录，并保留官方 baseline。当前尚未建立改进版，没有修改策略或参数。

## 已完成检查

- Racing `agent.py` 129 行、`design.json` 19 行；FPS `agent.py` 177 行，与原课件标注一致。
- 三份文件已逐页独立核对代码内容与缩进，未发现逻辑或参数改动。
- 两个 Python 文件通过语法解析和编译检查；JSON 可正常解析。
- 两个 ZIP 的文件清单及解压内容与代码文件一致。
- 平台验证、激活和实战结果由实际返回更新，本轮未进行平台操作。

课程要求与评分细节：[Assignment 要求](/Users/wego/Documents/Curriculum/HKU_Semester1_Study/5_Block4_Large_Language_Models/Assignment/Requirements.md)。后续实验与贡献记录：[实验记录](/Users/wego/Documents/Curriculum/HKU_Semester1_Study/5_Block4_Large_Language_Models/Assignment/Experiment_Log.md)。
