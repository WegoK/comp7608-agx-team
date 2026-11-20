# FPS · 终点与障碍复测

在 Borgo、Molo 和 Nevaio 使用此前五组地图、种子及席位，其他九个角色仍运行 v2。按测试席位所在队伍计分。移动占比的分母是发出移动指令的有效时长，分子是其中至少 1 秒始终处于距起点 0.15 米以内的时长。停止发出移动指令也会降低这一指标，因此它需要和是否到达、回合比分一起读。

| 版本 | 胜–平–负 | 总回合比分 | 移动时持续无进展的占比 |
|---|---:|---:|---:|
| 当前参赛 v2 | 3–0–2 | 59:45 | 22.25% |
| 仅改最短路 | 3–0–2 | 56:45 | 21.64% |
| 仅修到点停步 | 1–2–2 | 50:60 | 8.25% |
| 停步和受阻恢复 | 2–1–2 | 54:57 | 0.00% |

第一份可提交的 FPS 包选用「停步和受阻恢复」。在新增的两种方案中，该版本能通过窄横障，整局取得两胜一平。线上 v2 在相同五组里赢了三场。保留 v2 作为正式计分版本，完整候选交给组员复核，尤其要回看 Molo 从 13:0 到 12:12 的那一场。

[逐场数据](fixtures.csv) · [终点片段与横障局部实验](window_and_barriers.json)。后一个文件中的 candidate 始终指含受阻恢复的实验版。

| 实验用代码 | 文件 |
|---|---|
| 仅改最短路 | [r01_shortest_path.py](../../../../simulation/fps/variants/r01_shortest_path.py) |
| 当前参赛 v2 | [agent.py](../../../../01_Official_Starter/FPS/agent.py) |
| 仅修到点停步 | [r01_stop_only.py](../../../../simulation/fps/variants/r01_stop_only.py) |
| 停步和受阻恢复 | [agent.py](../../../../strategies/local/fps/R01-recovery/agent.py) |

从桌面 Block4 目录运行。将 AGENT 换为表中所需的策略路径，每组使用单独的输出目录。可选的 --workers 控制并发数。

```sh
AGX_Local_Lab/.venv/bin/python AGX_Competition/simulation/fps/matched_recovery.py --agent AGENT --out AGX_Local_Lab/runs/my-check
```

## 学习公共回放

AGX 对参赛者公开赛果、录像、公开帧和事件。正式参赛的 Python 代码和权重只有本人账号可以下载。本人席位的完整观察和动作来自单独的训练日志。[官方接入指南](https://agx.cool/agent-guide.md)

从同一场 Borgo 的公开回放取首回合 6–11 秒的 121 帧，可计算各席位走过的水平折线路程。席位 9（本队）为 5.74 米，席位 1 为 13.99 米。两者出生点与目标不同，这里只展示怎样使用公开数据。[帧数据入口](https://agx.cool/api/v1/python-submissions/recordings/d283f152-9354-40de-8f88-6b6787ceb7ac/frames) · [逐帧累计距离](public_movement_example.csv)
