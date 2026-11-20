# 本地复现

在桌面 Block4 打开 AGX_Local_Lab，双击「快速复跑.command」。脚本读取首轮实验代码和原始示例，完成三条赛道的常规及压力测试、FPS 导航回归与地图路线抽样。每次结果另存到 AGX_Local_Lab/runs 下带时间的目录。首轮原始数据见 [R01](../experiments/R01/README.md)。

同学模拟器的 CPU 源码、作者说明和回放地图在 AGX_Local_Lab，两个官方工具包和下载的地图保留在 Block4。当前环境使用 Python 3.12 与 NumPy 1.26.4。原生对局比较使用文件名带 (2) 的修订包。它同时附有 CUDA 源码，Mac 上此次测量使用 CPU。作者提供的显卡吞吐和平台一致性数据是另一套测试条件。

从桌面 Block4 目录执行以下命令。quick 约需数十秒，matches 会顺序运行十场，耗时依机器负载而变。logs 读取 Downloads 中三份本人导出，仅保存统计摘要。

```sh
AGX_Local_Lab/.venv/bin/python AGX_Competition/simulation/reproduce.py quick
AGX_Local_Lab/.venv/bin/python AGX_Competition/simulation/reproduce.py matches
AGX_Local_Lab/.venv/bin/python AGX_Competition/simulation/reproduce.py logs
```

Borgo、Molo、Nevaio 的 CPU 整局使用真实回放场景。Dust2 与 Mirage 的导航数据足以测算路线，原生整局还需相应的碰撞网格和材质文件。五张官方 mapdata 均可作格点近似测试。

线上赛车原版四个文件在 AGX_Local_Lab/racing_v1。首轮候选在 strategies/local/racing/R01，以 weights/ 存放两份原数值权重。quick 的单圈实验只比较老师示例与前视控制，logs 同时复现真实参赛版的逐帧对照与起步横向模型。

首轮的短路线版本保存在 simulation/fps/variants/r01_shortest_path.py，quick 和 logs 的路径对照始终读取这份快照。matches 使用归档的 R01 停步与脱困策略，logs 还会复现 Borgo 的终点片段及不同宽度的横障。整局复测及版本选择见 [FPS 复测](../experiments/R01/fps/recovery/README.md)。
