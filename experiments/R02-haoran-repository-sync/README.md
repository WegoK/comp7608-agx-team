# R02 · 版本索引与远端分支整理

日期：2026-11-23。用户明确要求把所有本地分支推至远端，标注两项比赛各版本的 commit/分支，并询问能否主要保留两个开发分支；随后提供两张平台版本历史截图。

## 已完成的本地核对

- 分开个人账号和 Wego 历史账号的版本序号，建立 [统一索引](../../releases/VERSION_MAP.md) 与 [结构化清单](../../releases/VERSION_MAP.json)。
- 个人 FPS v1/v2 和 Racing v1–v5 的截图前缀均与已核验的本地完整 ZIP SHA256 对应；Racing v4/v5 同包、同 commit。
- 八份个人准备包和清单归档到 `releases/personal/haoran/`，ZIP 内文件与固定 Git blob 逐字节一致。八份 Wego 历史 ZIP 及可还原源码逐项核验。
- 四条 Wego 原始提交记录在当前本库没有对象，保留原记录，并明确本库归档提交/路径；未捏造缺失的源提交。
- 所有原有本地分支保留，拟新增两个长期个人开发分支 `codex/haoran-fps`、`codex/haoran-racing`。二者初始共享最新完整历史；不改小组 main 或远端原有交接分支。
- 远端写入预检通过。原始日志、工具包和凭证不纳入本次归档。策略源码无改动；不重新生成比赛包。

## 同步阶段

已执行 `git push --atomic --all --set-upstream origin`，首次同步后的 **10 个本地分支逐一与远端 HEAD 一致**；远端共有 11 个分支，包含原先仅在远端的 `codex/r01-local-evaluation`。原 `main` 仍为 `639d89028595`，原交接分支仍为 `6b549f690f47`，无删除、强制改写或策略合入 main。

长期分支对应的游戏目录与固定源码提交核对通过：FPS 为 `e27acc998e08`，Racing 为 `ee12c93d76f6`。首次推送记录见 [first-push-verification.json](first-push-verification.json)；同步记录的后续文档提交仅快进两个长期分支，再推送核对。当前工作分支使用 `codex/haoran-racing`。

索引链接、JSON、CSV、8 份个人及 8 份 Wego 原 ZIP、截图 SHA256 已核验。只做文档/归档/分支操作，没有策略改动或新游戏测试；Git 推送不代替 AGX 上传、激活或邀请。

用户提供版本截图并授权 Git 同步；Codex 完成指纹、提交和归档核验及索引整理。截图中的“已通过验证”与是否实际生效、对局结果分开记录，没有新增比赛或策略收益结论。
