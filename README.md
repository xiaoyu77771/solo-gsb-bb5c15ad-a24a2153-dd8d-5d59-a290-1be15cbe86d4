# 批次资金流水核销 API

基于 Python + FastAPI 的批次资金流水核销服务。接收两侧（left / right）各最多 12 笔流水，
在满足核销规则的全部合法候选组中，选出**核销金额绝对值总和最大**的互斥核销组集合。

## 快速启动

```bash
docker compose up --build
```

一条命令即可启动，服务地址：**http://localhost:8000**

- 接口文档（Swagger UI）：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

也可以不用 Docker 本地运行：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 接口说明

### `POST /reconcile`

**请求体**

```json
{
  "left":  [{ "id": "L1", "time": "2026-09-17T00:00:00Z", "amount": 300 }],
  "right": [
    { "id": "R1", "time": "2026-09-17T01:00:00Z", "amount": 100 },
    { "id": "R2", "time": "2026-09-17T02:00:00Z", "amount": 200 }
  ]
}
```

| 字段   | 说明                                                                 |
| ------ | -------------------------------------------------------------------- |
| `id`   | 非空字符串，全批次（两侧合并）唯一；重复 ID 返回 **400**              |
| `time` | ISO-8601 时间；带时区会归一化到 UTC，无时区按 UTC 处理                |
| `amount` | 非零带符号整数（单位：分）；`0`、布尔、浮点、字符串均返回 **422**   |

每侧 0～12 笔，超过 12 笔返回 **422**。

**核销组规则**

- 组形：一对一（1-1），或一侧单笔对另一侧 2～3 笔的一对多（1-2 / 1-3，两个方向均可）；
- 组内全部金额同号（全正或全负）；
- 组内最早与最晚时间差 ≤ 48 小时（含边界）；
- 两侧金额之和相等；
- 每笔流水最多进入一个核销组。

**优化目标与并列规则**

在所有合法候选组中选择互斥集合，使核销金额绝对值总和最大；存在多个最优解时，
按组编码 `(升序左侧 ID 列表, 升序右侧 ID 列表)` 排序后的列表字典序最小者胜出。

**响应示例**

```json
{
  "groups": [
    { "left_ids": ["L1"], "right_ids": ["R1", "R2"], "amount": 300 }
  ],
  "total_amount": 300,
  "unmatched_left_ids": [],
  "unmatched_right_ids": []
}
```

- `groups`：核销组列表（按组编码升序），`amount` 为该组带符号核销金额（分）；
- `total_amount`：各组核销金额绝对值之和（分）；
- `unmatched_left_ids` / `unmatched_right_ids`：两侧未匹配的 ID（升序）。

## 算法

1. 枚举全部合法候选组（每侧 ≤12 笔，候选上限约 7000 个）；
2. 以位掩码表示流水集合，用带备忘录的分支限界（MRV 选点 + 双侧金额和上界 +
   贪心下界）求任意子集的最大可核销权值；
3. 在达到最大权值的前提下，逐步选取能构成残余最优解中编码最小的候选组，
   得到字典序最小的最优解。

实测 12×12 极端稠密批次求解耗时约 50ms 以内。

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```

包含 API 行为测试与求解器对暴力枚举的随机差分测试。

## 项目结构

```
app/
  main.py    # FastAPI 入口与 /reconcile 端点
  models.py  # 请求/响应模型与校验
  solver.py  # 候选生成 + 最优互斥集求解
tests/       # pytest 测试
Dockerfile
docker-compose.yml
requirements.txt / requirements-dev.txt
```
