# 发布与回退清单

1. 确认候选有实验记录与审阅；记录完整 Git SHA，运行 `python3 scripts/check.py`。
2. 在干净工作区运行 `python3 scripts/package.py racing` 或 `fps`。检查 dist 中 ZIP 和 JSON 清单，根目录有 agent.py，Racing 另有 design.json。
3. 发布负责人先记录当前稳定平台版本和回退包，再上传候选。服务器验证通过后才激活。
4. 在 releases/RELEASE_LOG.csv 记录游戏、Git SHA、ZIP SHA256、平台完整版本 ID、验证状态、激活时间、操作者和回退版本。
5. 等待实际比赛结果，记录版本对应的 match ID 与回放。上传成功、激活成功、实际比赛完成分别记录。
6. 更新实验、结果表、报告与贡献记录，提交这次记录变更。记录中的代码 SHA 指向打包时提交，报告提交号可以不同。
7. 候选异常时切回已知稳定版本，记录原因和时间，验证平台生效状态。归档实验，保留失败证据。

生成 ZIP 不会自动上传或切换平台。若加入运行依赖或模型权重，先核对平台支持范围，再更新打包规则和检查；当前仅接受 .py、.json、.npz 文件。

组员加入：仓库所有者在 GitHub Settings → Collaborators 中按准确用户名邀请；私有仓库链接本身不授予访问权。不要共享 GitHub 或 AGX 密码。
