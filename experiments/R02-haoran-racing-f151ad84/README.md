# Racing f151ad84：Parkland 前两圈损失复盘

2026-11-25（北京时间记录）。用户给出 [回放](https://agx.cool/#/matches/f151ad84-7597-4f65-8cb7-5b8fc12a5e84) 与日志。文件位于 `dist/fps-inputs/`，但metadata明确为 **racing**；按内容在 `codex/haoran-racing` 复盘，未移动用户原文件。此场不能验证刚准备的FPS第五版防护购买。

## 新赛车包实际采用

- 实际包 `b0957ee4e8625a1e46cf622afc54d2362d3eb560a6bbc141f54f5b71203cacba`，源码 `9c58b2da29a7d90949ece7aadfc2413301e7929f`，冻结版本 `9c068b47-f0e1-4217-890c-459a1ac37622`；本人零基座位5。
- 赛道 **Parkland**，长度5795.843m，规则 `rsi-racing-4.4`。走主Driver；最新Crossover 27–60m入口未执行。主Driver、设计、权重与上包cb01eb0460ac一致，不能将本场结果归因于Crossover改动。
- 排位 **第5、101.146689秒有效圈**；正赛 **第5**，四圈 **445.755秒**，落后冠军6号车 **17.026秒**，官方最快圈 **104.699秒（第4圈）**。
- 完整 **16,628** 次决策、58,022,698字节；SHA256 `4e12a1975b709ffe89e655f9692ba51a4bc3f5e89f74547bf36d22a0b66b9687`。尾记录行数、字节、哈希、seat_calls核对通过，冻结包全部动作重现，fault=0，公开本车incident=0。

证据：[review.json](review.json)、[constraints.json](constraints.json)。原始日志与事件不进Git。公共事件在策略复算后读取，不能作为控制器的隐藏信息来源。

## 主要差距在进站前两圈

| 事件 | 本车global时间 | 冠军6号车global时间 | 时间差 |
|---|---|---|---|
| 第一次进站入口（两车均第2圈） | 1308.4000 | 1291.6500 | 16.7500秒 |
| 同次进站出口 | 1344.1667 | 1327.4167 | 16.7500秒 |
| 最终比赛总时间 | 445.755 | 428.729 | 17.026秒 |

两车均一次soft→medium停站，静止换胎 **2.4秒**，公开进站区总耗时均 **35.765836秒**。进站口时间差已接近最终差距，出站后直到终点只再增加约0.276秒，因此没有证据支持优先缩短静止换胎或跳过停站。进站区总时间不等于净损失时间；此场湿度低，不能擅自取消换胎。

本车按距离过线插值圈时为108.1953、105.2653、127.5960、104.6984秒。第一圈包含网格发车计时，第三圈包含进站；不混同全部官方单圈，也不与其他地图/天气的总时间直接比较。

## 前两圈停车保护频繁限速

| 圈 | 决策数 | 停车保护比上一层跟车上限再降低>3m/s | 主动保护制动回调 |
|---|---|---|---|
| 1 | 3246 | 1481（45.6%） | 303 |
| 2 | 3158 | 1476（46.7%） | 432 |
| 4 | 3141 | 454（14.5%） | 306 |

第一圈数学上可匹配的限速前车主要是9号（1323次）、1号（22次），另有136次该诊断未匹配到单一限速对象，不补造归因；第二圈9号263次、1号1213次，第四圈匹配1号454次。

例如第二圈global=1233.7s，本车65.60m/s、前车1号72.10m/s，距离88.46m、横向分离-3.751m，保护上限76.11m/s、上一层80.69m/s。该帧上限高于本车当前速度，**不能把所有上限下降都算成实际刹车或误刹**。另一些帧确实触发主动制动，表中单独统计。

全程没有本车公开overtake事件；首圈2529次、第二圈1394次处于PASS模式，说明模式名不等于成功超车。下一项如继续优化，应针对 **Parkland低湿车流中的选线、横向冲突预测及保护制动**，先证明哪些限制可以通过更安全的独立路径避开，不能仅因计数多就调低刹车能力或放宽碰撞条件。

正赛湿度0.100028–0.399972，尚未达到先前新增湿地入口的0.5门槛；所以这场既没有测到Crossover新入口，也未测到该Parkland湿地入口。这里是已有低湿主驾驶器的表现。

## 阶段、复现与贡献

```powershell
python3 scripts/review_racing_export.py --log dist/fps-inputs/training-f151ad84-7597-4f65-8cb7-5b8fc12a5e84.jsonl --events dist/racing-inputs/public-events-f151ad84.jsonl --manifest releases/personal/haoran/racing/9c58b2da29a7/racing-9c58b2da29a7.json --archive releases/personal/haoran/racing/9c58b2da29a7/racing-9c58b2da29a7.zip --output experiments/R02-haoran-racing-f151ad84/review.json
python3 scripts/audit_racing_constraints.py --log dist/fps-inputs/training-f151ad84-7597-4f65-8cb7-5b8fc12a5e84.jsonl --source-ref 9c58b2da29a7d90949ece7aadfc2413301e7929f --bundle-sha256 b0957ee4e8625a1e46cf622afc54d2362d3eb560a6bbc141f54f5b71203cacba --laps 0,1,3 --output experiments/R02-haoran-racing-f151ad84/constraints.json
```

| 阶段 | 状态 |
|---|---|
| 新赛车包采用、完整性、全量动作、事件与保护约束审计 | 完成 |
| 优化 | 仅复盘建议，未修改策略/出包 |
| 新实现、平台操作、激活及新候选实战 | 未执行 |
| 项目基础检查、清洁检出复现 | 清洁LF检出check.py通过，两份完整审计再次运行、JSON与归档完全一致；原包匹配Git，见verification.json。基础检查不等于游戏环境测试 |

用户提供数据；Codex根据metadata纠正游戏分类、复算并扩展多圈保护诊断、维护报告。保护对象诊断保留原输出且全部动作重现；未运行新轨迹、轮胎/对手响应或反事实圈时模拟，不报告新收益或人工审阅。
