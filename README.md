# STX 交易机器人

> Stacks (STX/USDT) 永续合约多因子量化交易系统
> 支持欧易（OKX）与币安（Binance），强制 1 倍杠杆，支持多空双向。

---

## 目录

1. [项目简介](#一项目简介)
2. [项目结构](#二项目结构)
3. [环境要求](#三环境要求)
4. [Windows 一键部署到 `D:\Projects\stx_bot`](#四windows-一键部署)
5. [GCP 免费层 e2-micro 部署](#五gcp-免费层-e2-micro-部署)
6. [tmux 部署与后台运行](#六tmux-部署与后台运行)
7. [配置说明](#七配置说明)
8. [回测使用](#八回测使用)
9. [上线前检查清单](#九上线前检查清单)
10. [诚实的局限性说明](#十诚实的局限性说明)
11. [常见问题](#十一常见问题)
12. [免责声明](#十二免责声明)

---

## 一、项目简介

本项目是一个针对 **Stacks (STX/USDT)** 永续合约的多因子共振量化交易系统。

### 核心特性

| 特性 | 说明 |
|------|------|
| 标的 | `STX/USDT:USDT` 永续合约 |
| 交易所 | OKX / Binance USDM |
| 杠杆 | **强制 1 倍**（代码级约束，无法通过配置绕过） |
| 方向 | 支持做多、做空 |
| 模式 | Paper（模拟盘，无需密钥）/ Live（实盘，需 API Key） |
| 策略 | BTC + STX 多周期（15m/1h/4h）均线共振 + KDJ + RSI + 成交量 + 资金费率 |
| 风控 | 固定 1% 单笔风险 + 2% 止损 + 4% 止盈 + 日亏损熔断 + 连亏熔断 |

### 影响 STX 价格的主要因素与权重

| 权重 | 因素 | 量化方式 |
|------|------|---------|
| 0.40 | 比特币相关性 | BTC + STX 多周期均线共振 |
| 0.15 | 网络与生态需求 | STX 成交量变化率代理 |
| 0.10 | 代币经济与供给端 | 永续合约资金费率代理 |
| 0.15 | 市场结构与流动性 | KDJ 金叉死叉 + RSI 超买超卖 |
| 0.05 | 叙事与情绪 | 成交量突增代理 |
| 0.05 | 协议升级/治理 | 权重预留（未接入数据源） |
| 0.05 | 宏观监管 | 权重预留（未接入数据源） |
| 0.05 | 竞争因素 | 权重预留（未接入数据源） |

---

## 二、项目结构

```
D:\Projects\stx_bot\
├── config.py              # 全局配置（参数集中于此）
├── strategy.py            # 多因子策略信号生成
├── risk_manager.py        # 风控与下单执行
├── data_fetcher.py        # 行情数据拉取封装
├── executor.py            # 单轮交易编排器
├── backtester.py          # 简化回测器
├── main.py                # 主入口
├── requirements.txt       # Python 依赖
├── .env.example           # 环境变量模板
├── .env                   # 你自己复制并填写的密钥文件（不提交）
├── run_paper.bat          # Windows Paper 一键启动
├── run_live.bat           # Windows Live 一键启动
└── README.md
```

---

## 三、环境要求

| 项目 | 最低要求 | 推荐 |
|------|---------|------|
| Python | 3.10 | 3.11 / 3.12 |
| 内存 | 512 MB | 1 GB+ |
| 磁盘 | 500 MB | 1 GB+ |
| 网络 | 可访问交易所 API | 稳定低延迟 |

---

## 四、Windows 一键部署

### 步骤 1：安装 Python

1. 访问 <https://www.python.org/downloads/> 下载 Python 3.11 或 3.12
2. **安装时务必勾选** ☑ `Add Python to PATH`
3. 安装完成后打开 CMD，输入 `python --version` 应能看到版本号

### 步骤 2：创建项目目录

打开 PowerShell（Win + X → Windows PowerShell），执行：

```powershell
# 创建项目目录
New-Item -ItemType Directory -Force -Path "D:\Projects\stx_bot"
Set-Location "D:\Projects\stx_bot"
```

### 步骤 3：写入项目文件

将本 README 顶部列出的 **全部 12 个文件** 保存到 `D:\Projects\stx_bot\` 目录下。目录结构应与上文"项目结构"一致。

> 提示：可以使用 `notepad 文件名` 快速创建并编辑。

### 步骤 4：配置环境变量

```powershell
# 复制模板为 .env
Copy-Item ".env.example" ".env"
# 使用记事本编辑
notepad .env
```

在 `.env` 中至少确认以下项：

```dotenv
TRADING_MODE=paper   # 首次务必用 paper
LOG_LEVEL=INFO
```

**Paper 模式不需要填写任何 API 密钥。**

### 步骤 5：双击启动

在 `D:\Projects\stx_bot\` 目录中，直接**双击**：

```
run_paper.bat
```

首次运行会自动：

1. 创建虚拟环境 `venv\`
2. 升级 pip
3. 安装 `requirements.txt` 中的依赖
4. 以 Paper 模式启动交易机器人

看到类似以下的日志即表示运行成功：

```
2025-01-01 12:00:00 | INFO     | stx_quant.main | ============================================
2025-01-01 12:00:00 | INFO     | stx_quant.main | STX/USDT 永续合约交易机器人
2025-01-01 12:00:00 | INFO     | stx_quant.main |   运行模式  : PAPER
...
2025-01-01 12:00:00 | INFO     | stx_quant.main | [#1] 价格=2.1500 | 动作=wait  | 方向=flat  | 得分=+0.123 | 权益=10000.00 | ...
```

按 `Ctrl+C` 优雅退出。

---

## 五、GCP 免费层 e2-micro 部署

Google Cloud 免费层提供 **1 台 e2-micro 实例（us-west1 / us-central1 / us-east1）永久免费**，配置完全够跑本机器人。

### 推荐配置

| 项目 | 配置 |
|------|------|
| 机器类型 | `e2-micro` |
| 区域 | `us-west1` / `us-central1` / `us-east1`（必须选其一，否则会收费） |
| 启动磁盘 | 标准永久磁盘 `standard persistent disk`，**30 GB**（免费上限） |
| 镜像 | Ubuntu 22.04 LTS 或 Debian 12 |
| 网络 | 默认 |
| 防火墙 | 不开放任何入口，仅 SSH |

### 部署步骤

1. **创建实例**：GCP Console → Compute Engine → 创建实例
   - 名称：`stx-bot`
   - 区域：`us-west1`（或其他免费区域）
   - 机器类型：`e2-micro`
   - 启动磁盘：Ubuntu 22.04 LTS，30 GB 标准永久磁盘
   - 防火墙：仅勾选"允许 SSH"

2. **SSH 登录**：点击实例旁的 SSH 按钮

3. **执行以下命令**（一次性）：

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装 Python 与工具
sudo apt install -y python3.11 python3.11-venv python3-pip git tmux htop

# 配置 swap（e2-micro 只有 1GB 内存，swap 可显著提升稳定性）
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 创建项目目录
mkdir -p ~/stx_bot && cd ~/stx_bot

# 上传项目文件（方式见下方）
```

4. **上传项目文件**（二选一）：

   **方式 A：使用 gcloud CLI 从本地 scp**

   ```bash
   # 在本地 Windows PowerShell 执行
   gcloud compute scp --recurse "D:\Projects\stx_bot\*" stx-bot:~/stx_bot/
   ```

   **方式 B：使用 GitHub 中转**

   ```bash
   # 在服务器上执行
   cd ~/stx_bot
   git clone https://github.com/你的用户名/stx_bot.git .
   ```

5. **安装依赖并试运行**：

```bash
cd ~/stx_bot
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 首次用 Paper 模式试运行
cp .env.example .env
nano .env          # 确认 TRADING_MODE=paper
python main.py
```

---

## 六、tmux 部署与后台运行

tmux 是 Linux 上的终端复用器，可以让程序在关闭 SSH 后继续运行。

### 常用命令速查

| 操作 | 命令 |
|------|------|
| 新建会话 | `tmux new -s stx_bot` |
| 列出会话 | `tmux ls` |
| 重新连接 | `tmux attach -t stx_bot` |
| **分离会话** | `Ctrl+B` 然后按 `D` |
| 关闭会话 | 在会话中输入 `exit` |
| 强制杀死会话 | `tmux kill-session -t stx_bot` |

### 完整启动流程

```bash
# 1. 登录服务器
ssh 用户@GCP外网IP

# 2. 进入项目目录并激活虚拟环境
cd ~/stx_bot
source venv/bin/activate

# 3. 创建 tmux 会话
tmux new -s stx_bot

# 4. 在 tmux 会话中设置环境变量并启动
export TRADING_MODE=paper    # 换成 live 时务必三思
python main.py

# 5. 按 Ctrl+B，然后按 D，分离会话
# 程序现在会在后台持续运行

# 6. 之后随时重连查看日志
tmux attach -t stx_bot
```

### 优雅停止

```bash
# 重连会话
tmux attach -t stx_bot

# 在会话中按 Ctrl+C，程序会完成本轮并优雅退出

# 退出 tmux
exit
```

### 查看日志

```bash
# 若使用 systemd（见下）：
sudo journalctl -u stx_bot -f

# 若使用 tmux，建议重定向输出到文件：
python main.py 2>&1 | tee -a logs/$(date +%F).log
```

### （可选）用 systemd 做开机自启

创建 `/etc/systemd/system/stx_bot.service`：

```ini
[Unit]
Description=STX Trading Bot
After=network-online.target

[Service]
Type=simple
User=你的用户名
WorkingDirectory=/home/你的用户名/stx_bot
Environment="TRADING_MODE=paper"
ExecStart=/home/你的用户名/stx_bot/venv/bin/python /home/你的用户名/stx_bot/main.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable stx_bot
sudo systemctl start stx_bot
sudo systemctl status stx_bot
```

---

## 七、配置说明

所有参数集中在 `config.py`，通过环境变量注入的只有以下几项：

| 环境变量 | 取值 | 说明 |
|---------|------|------|
| `TRADING_MODE` | `paper` / `live` | 运行模式，默认 `paper` |
| `EXCHANGE_NAME` | `okx` / `binance` | 交易所，默认 `okx` |
| `EXCHANGE_TESTNET` | `true` / `false` | 是否使用测试网，默认 `false` |
| `EXCHANGE_API_KEY` | 字符串 | Live 模式必填 |
| `EXCHANGE_API_SECRET` | 字符串 | Live 模式必填 |
| `EXCHANGE_API_PASSPHRASE` | 字符串 | OKX 必填，Binance 不需要 |
| `LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | 日志级别，默认 `INFO` |

修改策略参数请直接编辑 `config.py` 中的 `IndicatorConfig` / `WeightConfig` / `RiskConfig`，不要通过环境变量注入（避免配置漂移）。

---

## 八、回测使用

```bash
# 激活虚拟环境后执行
python backtester.py
```

回测会：

1. 拉取 STX 与 BTC 的 15m / 1h / 4h 历史 K 线（默认每个周期 1000 根）
2. 使用**与实盘完全相同的策略代码路径**逐 bar 回放
3. 输出总收益率、胜率、最大回撤等指标
4. **打印诚实的局限性说明**（务必阅读）

### ⚠️ 关于回测结果的重要提示

回测结果与实盘收益存在**系统性偏差**。本项目在 `backtester.py` 的 `_limitations()` 方法中明确列出了 9 项已知局限，包括：

- 未模拟永续合约资金费率
- 未模拟盘中极值插针
- 未模拟保证金变动与强平
- 数据量有限，存在过拟合风险

**请勿将回测收益等同于实盘收益。**

---

## 九、上线前检查清单

切换到 Live 模式前，**逐项确认**：

### 🔒 安全

- [ ] API Key 已关闭**提现**与**划转**权限
- [ ] API Key 已**绑定服务器 IP 白名单**
- [ ] API Key 仅开通**合约交易**权限
- [ ] `.env` 文件权限已限制（Linux：`chmod 600 .env`）
- [ ] `.env` 未提交到任何 Git 仓库（已在 `.gitignore` 中）
- [ ] 服务器 SSH 使用密钥登录，禁用密码登录

### 🧪 测试

- [ ] Paper 模式已连续运行 **≥ 2 周**且无异常崩溃
- [ ] 已完成至少 30 笔模拟交易并检查信号合理性
- [ ] 已用 `EXCHANGE_TESTNET=true` 完成测试网下单验证
- [ ] 断网/断网恢复的场景已测试（重启后能恢复运行）

### 💰 资金

- [ ] 账户起始资金为**可以完全承受损失**的金额
- [ ] 交易所账户已设置**逐仓模式**
- [ ] 已手动确认交易所杠杆为 **1 倍**
- [ ] 已理解"1% 单笔风险 + 2% 止损"意味着 4 连亏即触发熔断

### 🛡️ 风控

- [ ] `config.py` 中 `RiskConfig` 各参数符合自身风险偏好
- [ ] 已理解"日亏损 5%"触发熔断后**不会自动恢复**（需手动重置或等新一天）
- [ ] 已知悉"信号反向不会自动反手"，需要等平仓后下一轮才可能反向开仓

### 📊 监控

- [ ] 已配置日志持久化（推荐 tee 到日期文件或 systemd journal）
- [ ] 已知悉如何通过 tmux / journalctl 查看实时日志
- [ ] 已知悉交易所 API 出现异常时的排查路径

---

## 十、诚实的局限性说明

本系统**不是银弹**。请务必理解以下限制：

### 1. 策略层面

- **多因子权重是启发式的**：0.40 / 0.15 / 0.10 ... 这些权重基于行业经验，**并未经过严格的最优化或样本外验证**。
- **代理指标存在失真**：链上需求、代币经济、叙事情绪这三项都用了价格/成交量代理，**无法反映真实基本面**。
- **三项因子（协议升级/宏观/竞争）权重预留**：当前版本**完全没有**接入事件驱动信号。
- **策略滞后于价格**：均线共振本质是趋势跟随，**在震荡市会频繁止损**。

### 2. 数据层面

- **依赖交易所公开 API**：若交易所限流或调整接口，策略会失败或信号延迟。
- **BTC 与 STX 相关性会随时间变化**：0.6/0.4 的固定权重无法自适应市场结构变化。
- **KDJ / RSI 是经典指标，不是优势**：市场参与者普遍使用它们，**不具备 alpha**。

### 3. 执行层面

- **止损为市价单触发**：极端行情下可能产生远超 2% 的滑点。
- **资金费率变化未被监控**：持仓期间若资金费率剧烈变化，会侵蚀利润。
- **无自适应仓位**：无论信号强弱，只要达到阈值就按固定风险比例下单。

### 4. 回测层面

请完整阅读 `backtester.py` 输出的 9 条局限性说明。核心观点：**回测是方向性参考，不是收益预测**。

### 5. 系统层面

- **无 GUI / 无告警**：需要用户自己查看日志，或自行接入 Telegram / 邮件告警。
- **单机部署无高可用**：进程崩溃后不会自动切换到备用节点。
- **无跨交易所套利/对冲**：只在单一交易所交易。

### 6. 法律与合规

- 加密货币永续合约在**部分国家和地区受到严格监管或禁止**。
- 用户需自行确认所在地法律法规，**风险自担**。

---

## 十一、常见问题

### Q1：Paper 模式下没有任何交易？

可能原因：
- 综合信号得分未达到阈值 `min_signal_score = 0.35`
- 需要多因子方向一致才会产生强信号，**这是刻意的保守设计**

排查：把 `LOG_LEVEL` 调到 `DEBUG`，可以看到每轮各分项的得分明细。

### Q2：OKX 报 `Invalid API Key`？

- 检查 `EXCHANGE_API_KEY` / `EXCHANGE_API_SECRET` / `EXCHANGE_API_PASSPHRASE` 是否都填写
- OKX 的 Passphrase 是**你创建 API Key 时自己设置的口令**，不是登录密码

### Q3：如何切换到 Binance？

修改 `.env`：

```dotenv
EXCHANGE_NAME=binance
```

Binance 不需要 `EXCHANGE_API_PASSPHRASE`。

### Q4：如何降低交易频率？

编辑 `config.py`：

```python
min_signal_score: float = 0.50   # 提高阈值
poll_interval_sec: int = 300      # 拉长轮询间隔
```

### Q5：进程崩溃后如何恢复？

Paper 模式：直接重启即可（Paper 账户数据不持久化）。
Live 模式：重启后 `RiskManager` 会通过 `fetch_positions` 读取交易所实际持仓，但**本地缓存会丢失**，因此**首轮会重新建立缓存**。若已有持仓，`has_open_position()` 会因缓存为空而返回 False，可能导致重复开仓 —— **请手动在交易所平仓后再启动程序**。

---

## 十二、免责声明

本项目仅供**技术学习与研究**之用。

- 加密货币交易具有**极高风险**，可能导致**本金全部损失**。
- 本项目作者不对任何直接或间接的交易损失负责。
- 使用本项目即表示你已充分理解上述风险，并自愿承担全部后果。
- **请在完全了解策略逻辑与风险控制机制后再考虑使用 Live 模式。**
- 强烈建议先用 Paper 模式长期验证，再用**极小资金**进行 Live 试运行。

---

**最后提醒：市场永远是对的，代码永远有 bug。**
