# 剧场无线话筒频点排演

一个用于剧场无线话筒**频点编排 / 排演**的小系统：录入房间互扰关系、每台话筒的
可用频点、保护间隔、启用时段与优先级（可锁定频点），系统自动给出在全部约束下
**获配高优先级话筒最多、其次获配总数最多**的频点分配方案，并清楚列出未获配设备。

- 技术栈：**Python 3.11 + Flask + SQLite + 原生 HTML/JS**（无前端构建）
- 求解器：自研精确搜索（冲突图连通分量分解 + 候选点最大权独立集，
  位掩码 MRV / LCV / 团覆盖上界 / 记忆化分支限界），无需外部求解器
- 数据持久化在 SQLite（WAL 模式），容器内挂载到 `/data` 卷

---

## 一、一键启动（推荐 Docker Compose）

```bash
docker compose up -d --build
```

启动后打开浏览器访问：

> **http://localhost:8000**

首次启动会自动建表并写入一套演示数据（3 个房间、2 条互扰关系、8 台话筒，
含锁定频点与"可用频点不足"的示例）。

停止 / 查看日志：

```bash
docker compose down          # 停止（数据卷保留）
docker compose logs -f       # 查看日志
docker compose down -v       # 停止并删除数据卷（彻底清空）
```

### 修改宿主机端口

默认映射宿主机 `8000` 端口。若端口被占用，可用环境变量 `HOST_PORT` 覆盖
（容器内始终监听 8000）：

```bash
HOST_PORT=9000 docker compose up -d --build
# 然后访问 http://localhost:9000
```

### 不用 Compose，直接 Docker

```bash
docker build -t theater-freq-scheduler .
docker run -d --name freq -p 8000:8000 \
  -v freq-data:/data -e SEED_DEMO=1 theater-freq-scheduler
```

---

## 二、本地直接运行（不使用 Docker）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 初始化数据库（库为空时按 SEED_DEMO 决定是否写演示数据）
SEED_DEMO=1 python -m app.initdb

# 启动（开发服务器）
PORT=8000 python -m app

# 或用 gunicorn（生产）
python -m gunicorn --workers 2 --bind 0.0.0.0:8000 'app:create_app()'
```

数据库默认写到 `./data/freq.db`，可用环境变量 `FREQ_DB_PATH` 修改。

---

## 三、页面与使用

页面地址端口：`http://<主机>:8000`（根路径 `/` 即页面，另有健康检查 `/healthz`）。

1. **房间**：新增 / 编辑 / 删除（房间下仍有话筒时删除会被拒绝）。
2. **房间互扰关系**：选择两个房间建立互扰（对称，只需建一次）。同房间始终视为互扰。
3. **话筒**：录入
   - 名称、所属房间、优先级（**高 / 普通**）；
   - 启用时段 `[开始, 结束)`，**整数**（建议为当天 00:00 起算的分钟数，如
     18:00–22:00 填 `1080`–`1320`；页面会显示为 `HH:MM`），采用**半开区间**；
   - 保护间隔，**整数 kHz**；
   - 可用频点列表，**整数 kHz**，逗号 / 空格分隔；
   - 可选**锁定频点**（必须属于可用频点列表）。
4. 点击右上角 **「开始编排」**。
5. 右侧显示：
   - 汇总（高优先级获配 X/Y、普通获配、获配总数、未获配数）；
   - **获配设备**及其分配频点（锁定项以 🔒 与紫色标出）；
   - **未获配设备**列表。

> 任何输入（房间、互扰、话筒）的增删改，都会让**既有方案立刻标记为"已过期"**，
> 页面顶部黄色横幅提示，需重新点击「开始编排」生成新方案。历史方案保留在
> "历史方案"折叠区。

---

## 四、编排规则（约束与目标）

设话筒 A、B：

- 当 A、B 位于**同一房间**，或所在房间**存在互扰关系**，且两者启用时段
  `[start, end)` **有重叠**时，它们构成冲突对；
- 冲突对所配频点之差必须满足

  ```
  | freq_A - freq_B | ≥ max(保护间隔_A, 保护间隔_B)
  ```

- **锁定**话筒必须保留其锁定频点。

优化目标按字典序依次为：

1. **最大化获配的高优先级话筒数**；
2. 在其基础上，**最大化获配话筒总数**。

实现上给每台话筒赋权 `N+1`（高优先级）或 `1`（普通），高优先级单台权重大于
所有普通话筒权重之和，因此"最大化权重和"与上述两级目标完全等价。

### 拒绝编排与"尽力而为"

- **锁定项互冲**（两台锁定话筒冲突，或锁定频点不在其可用列表）、**时段非法**
  （开始 ≥ 结束）时，系统**拒绝编排**，并在错误横幅中点名相关设备，同时在
  话筒表格中高亮对应行；
- 当**可用频点不足**时，系统不会报错，而是给出当前条件下的**最大可行方案**，
  并把无法获配的话筒列入"未获配设备"；
- 极端规模下若搜索达到内置状态上限，会返回当前最优的可行近似方案，并在结果
  横幅中注明（典型剧场规模远在上限内，结果为全局最优）。

---

## 五、配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FREQ_DB_PATH` | 容器内 `/data/freq.db` | SQLite 数据库文件路径 |
| `SEED_DEMO` | `1` | 首次初始化且库为空时是否写入演示数据（`1` 是 / `0` 否） |
| `HOST_PORT` | `8000` | Compose 映射到宿主机的端口（仅 compose 用到） |
| `PORT` | `8000` | 容器 / 进程监听端口（一般无需改） |
| `GUNICORN_WORKERS` | `2` | gunicorn worker 数量 |

---

## 六、HTTP API（可供脚本调用）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 获取全部房间 / 互扰 / 话筒 / 当前方案 / 历史 |
| POST/PUT/DELETE | `/api/rooms[/<id>]` | 房间增改删 |
| POST/DELETE | `/api/interferences[/<id>]` | 互扰关系增删 |
| POST/PUT/DELETE | `/api/microphones[/<id>]` | 话筒增改删 |
| POST | `/api/solve` | 执行编排，返回最新状态（含新方案） |
| GET | `/api/schemes/<id>` | 查询指定历史方案 |
| POST | `/api/reset` | 清空并重新写入演示数据 |
| GET | `/healthz` | 健康检查 |

话筒录入 JSON 示例：

```json
{
  "name": "主持1号",
  "room_id": 1,
  "priority": "high",
  "start_time": 1080,
  "end_time": 1320,
  "guard_khz": 400,
  "freqs": [600000, 602000, 604000, 606000],
  "locked_freq": 600000
}
```

错误响应形如：

```json
{ "ok": false, "error": "锁定频点互冲（…）：…", "devices": ["主持1号", "主持2号"] }
```

---

## 七、目录结构

```
.
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh          # 建库 + 启动 gunicorn
├── requirements.txt
├── README.md
└── app
    ├── __init__.py        # Flask 应用工厂
    ├── __main__.py        # python -m app 开发入口
    ├── db.py              # SQLite 连接（WAL、外键）
    ├── schema.sql         # 表结构
    ├── initdb.py          # 建表 + 演示数据
    ├── solver.py          # 频点编排求解器（纯标准库）
    ├── routes.py          # HTTP API
    ├── state.py           # 页面状态序列化
    └── static
        ├── index.html     # 单页界面
        ├── style.css
        └── app.js
```
