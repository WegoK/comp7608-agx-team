# FPS 第三版候选：短暂丢失视野时保持瞄准

2026-11-24，北京时间。用户明确要求“改出第三版策略”。在 `codex/haoran-fps` 上以个人 FPS v2 源码 `e27acc998e08d5dc3f59bac6e0fa5e4a99329058`、包 `06bcdcc4ae39…` 为固定对照，修复 [8cf9d8f9 复盘](../R02-haoran-fps-8cf9d8f9/README.md) 中交战与守点反向切换的问题。这里的“第三版”是开发候选名，尚未取得平台 v3 回执。

## 这轮实际改动

- 可见交战仍使用原来的目标选择、身体部位、后坐力补偿、开火阈值和枪械逻辑。
- 只记住最近一次**本人实际看见**的目标位置、姿态和可见部位；短暂不可见时最长保持 18 ticks（0.3 秒）的蹲姿瞄准，停止移动，**强制 fire=False**。不预测隐藏敌人的新位置，不读取回放全知状态。
- 隐藏帧不会刷新期限；新目标可见时立即使用新目标。过期、倒退的 tick、回合/地图/攻守变更、购买、死亡、闪光、离地清除记忆；下包和拆包立即优先并清除记忆。
- 保持期间清除导航卡住计时及搜点扫描计时，避免把暂停误认为卡住或已搜点。因为暂停最长 0.3 秒，搜点连续扫描检查按实际恢复时刻计时；未缩短原有 150-tick 扫描要求。
- 路径、掉包回收、搜点顺序、购买规则和可见时的交战输出保持原代码。仅修改 `strategies/fps/agent.py`；`starter_player.py`、`waypoint_nav.py` 与 v2 字节核对一致。Racing 不改。

## 检查与证据

[offline-checks.json](offline-checks.json) 与 [objective-regression.json](objective-regression.json)：

- 16 项瞄准记忆保护检查通过，包含期限、隐藏时不开火、目标切换、生命周期重置和下包/拆包优先。
- 现有搜点、扫描中断、重置、交战优先与五地图检查通过；仅为新交战保持时间扩展相应扫描中断测试时间轴。
- 五张公开地图构造目标检查：100 项进攻回收、100 项防守行为、19 项保护检查通过。地图与图为构造观察，不是官方碰撞模拟。
- 两场完整历史观察共 **88,507** 次；各自冻结实战源码的请求动作全部重现。相比 v2，8cf9d8f9 的 40,776 次中 1,504 次动作改变，1,503 次进入隐藏保持；69259abb 的 47,731 次中 250 次动作改变，250 次隐藏保持。
- 所有当前可见交战动作、下包/拆包、非 live 和死亡阶段均与 v2 一致；隐藏保持强制不开火且无移动。一个额外动作变化来自暂停后导航状态的差异，并非新增可见时开火规则。

另做了一个**受限瞄准模型**：固定玩家位置，按观察到的控制比例积分 yaw/pitch，人工规定目标隔帧可见，姿态随动作变化。在该构造情景中，v2 的姿态切换 39 次、可见帧开火请求 0；候选姿态切换 0 次、可见帧开火请求 9、隐藏帧开火 0。这只说明能在该模型中打破瞄准重置循环，不是命中、击杀或胜率模拟。

历史日志复算时位置、视野仍是旧轨迹；因此本次不报告新击杀数或“修复后第22回合能赢”。本轮风险是短暂停留可能延误移动/搜点，或者在对手已离开时瞄准旧位置。0.3 秒是本轮受限设计，未拟合最佳值，需平台实测。

复现：

```powershell
python3 scripts/evaluate_fps_aim_hold.py --log dist/fps-inputs/training-8cf9d8f9-bf77-41ba-92cd-25b865de8d41.jsonl --log dist/fps-inputs/training-69259abb-fcc8-4474-874f-b88d0562750a.jsonl --output experiments/R02-haoran-fps-v3-aim-hold/offline-checks.json
python3 scripts/evaluate_fps_objectives.py --kit-root dist/fps-kit-map-check --output experiments/R02-haoran-fps-v3-aim-hold/objective-regression.json
```

## 阶段

| 阶段 | 状态 |
|---|---|
| 单项实现、历史观察对照、保护检查 | 完成 |
| 项目基础检查、固定提交与打包 | 清洁 LF 检出中 check.py、package.py fps 通过；两场完整复算与归档 JSON 一致；ZIP 三文件逐字节匹配 Git |
| 独立人工审阅 | 待进行 |
| 平台验证、激活、新候选实战 | 未进行；没有自动操作 AGX |

用户提供实战数据并确定优化方向；Codex 完成实现、用例、执行与文档。未补造人工审阅或实战贡献。下次日志应先核对 source_hash，再检查长时间无开火的交替状态是否减少、实际命中/击杀/死亡、目标暂停及拆包是否受影响；不同对手或地图的总比分不单独作为因果证据。

## 可上传包

源码 `65652c51f1ef60fb1f365b45bfff3eaa947c7f40`，包 [fps-65652c51f1ef.zip](../../releases/personal/haoran/fps/65652c51f1ef/fps-65652c51f1ef.zip)，SHA256 `67e64a8436febd4b5d4f3d1ea9659d03c9ebfca6b3ec71d375ad3b7049080b7b`。见[清单](package-manifest.json)与[打包检查](packaging-checks.json)。平台版本号仍待上传后确认；旧包 v2 `06bcdcc4ae39…` 保留回退。
