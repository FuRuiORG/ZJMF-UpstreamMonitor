# ZJMF 上游产品信息监控器

监控多个上游 URL 的产品信息变化，自动保存差异到 JSON 文件，支持邮件通知和价格历史数据库记录。

**⚠️ 重要提示（Linux 用户）**  
本项目在 Linux 系统上**必须**安装在 `/opt/ZJMFUpstreamMonitor/` 目录下，否则将无法正确读取配置文件和保存数据。  
这是因为在某些环境（如宝塔面板定时任务）中，脚本可能在临时目录执行，导致相对路径错误。因此代码已固定使用此绝对路径。**请务必确保项目放置于该目录**，否则监控功能将不可用。

## 功能特点

- ✅ 支持配置多个上游 URL 同时监控
- ✅ 首次运行自动保存初始数据快照
- ✅ 每次运行检测数据变化（新增、删除、修改）
- ✅ 详细记录产品变化信息
- ✅ 差异保存到带时间戳的 JSON 文件
- ✅ 自动更新初始数据为最新状态
- ✅ 支持自定义请求头和超时设置
- ✅ **邮件通知** - 价格变化和产品变动自动发送邮件
- ✅ **数据库记录** - SQLite 数据库存储价格历史和变化记录
- ✅ **交互式配置** - 命令行配置向导，便捷配置上游和邮件
- ✅ **跨平台支持** - Windows/Linux 自动适配数据目录

## 项目结构

```
ZJMF-UpstreamMonitor/
├── upstream_monitor.py          # 主程序文件
├── .gitignore                   # Git 忽略文件
├── README.md                    # 说明文档
├── requirements.txt             # Python 依赖
└── query_db.py                  # 数据库查询工具（可选）
```

## 数据目录

### Linux
```
/opt/ZJMFUpstreamMonitor/
├── upstream_config.json         # 配置文件
├── upstream_data/               # 初始数据存储目录
│   └── {上游名称}_initial.json
├── upstream_diffs/              # 差异数据文件目录
│   └── {上游名称}_{时间戳}.json
└── upstream_monitor.db          # SQLite 数据库文件
```

### Windows
```
{脚本所在目录}/
├── upstream_config.json         # 配置文件
├── upstream_data/               # 初始数据存储目录
│   └── {上游名称}_initial.json
├── upstream_diffs/              # 差异数据文件目录
│   └── {上游名称}_{时间戳}.json
└── upstream_monitor.db          # SQLite 数据库文件
```

## 数据库结构

### change_records 表（变化记录）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER | 主键 |
| `product_id` | TEXT | 产品 ID |
| `product_name` | TEXT | 产品名称 |
| `upstream_name` | TEXT | 上游名称 |
| `upstream_url` | TEXT | 上游 URL |
| `group_name` | TEXT | 分组名称 |
| `first_group_id` | TEXT | 一级分组 ID |
| `second_group_id` | TEXT | 二级分组 ID |
| `change_type` | TEXT | 变化类型（increase/decrease/新增/删除/修改） |
| `field_name` | TEXT | 变化的字段名 |
| `old_value` | TEXT | 旧值 |
| `new_value` | TEXT | 新值 |
| `price_change` | REAL | 价格变化数值（仅价格变化时有效） |
| `check_time` | TIMESTAMP | 检测时间 |
| `created_at` | TIMESTAMP | 记录创建时间 |

## 安装部署

### 1. 克隆/下载项目

```bash
# Linux
cd /opt
git clone <repository-url> ZJMFUpstreamMonitor
cd ZJMFUpstreamMonitor

# Windows
# 直接下载到任意目录
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

依赖列表：
- `requests>=2.28.0` - HTTP 请求库

### 3. 配置

运行配置向导：

```bash
python upstream_monitor.py --config
```

配置向导界面：

```
============================================================
上游监控配置向导
============================================================

请选择操作：
1. 查看当前配置
2. 添加上游
3. 修改上游
4. 删除上游
5. 配置邮件通知
6. 保存并退出
0. 退出不保存

请输入选项 [0-6]:
```

## 使用方法

### 配置模式

```bash
python upstream_monitor.py --config
```

配置向导功能：
- **查看配置**：显示所有上游和邮件配置
- **添加上游**：输入名称、API URL、Base URL（自动提取）
- **修改上游**：选择已有上游进行修改
- **删除上游**：删除指定上游
- **配置邮件**：设置 SMTP 服务器、发件人、收件人等

### 监控模式

```bash
python upstream_monitor.py
```

首次运行会：
- 创建配置文件（如果不存在）
- 创建数据目录
- 获取并保存初始数据快照

后续运行会：
- 检测数据变化
- 保存差异文件
- 发送邮件通知（如果启用）
- 更新初始数据

## 配置文件说明

配置文件 `upstream_config.json` 示例：

```json
{
  "upstreams": [
    {
      "name": "XX云",
      "base_url": "https://www.XXX.com",
      "api_url": "https://www.XXX.com/v1/products",
      "enabled": true
    }
  ],
  "email": {
    "enabled": true,
    "smtp_server": "smtp.qq.com",
    "smtp_port": 465,
    "sender_email": "your_email@qq.com",
    "sender_password": "your_smtp_password",
    "recipients": ["recipient1@qq.com", "recipient2@example.com"]
  }
}
```

### 配置字段说明

#### 上游配置 (`upstreams`)

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 上游名称，用于文件命名和日志显示 |
| `base_url` | string | 否 | 上游基础 URL，用于生成产品链接 |
| `api_url` | string | 是 | 产品数据 API 地址 |
| `enabled` | boolean | 是 | 是否启用该上游监控 |

#### 邮件配置 (`email`)

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `enabled` | boolean | 是 | 是否启用邮件通知 |
| `smtp_server` | string | 是 | SMTP 服务器地址 |
| `smtp_port` | integer | 是 | SMTP 端口（QQ邮箱使用 465） |
| `sender_email` | string | 是 | 发件人邮箱地址 |
| `sender_password` | string | 是 | SMTP 授权码/密码 |
| `recipients` | array | 是 | 收件人邮箱列表 |

## 邮件通知

当检测到数据变化时，会发送邮件通知，内容包括：

- **产品ID、产品名称、所属分组**
- **修改的字段名称**
- **旧值 → 新值**
- **产品链接**（格式：`{base_url}/cart?fid={xx}&gid={xx}`）

邮件支持：
- 纯文本格式
- HTML 格式（带样式）
- 价格变化特殊标识（涨价红色、降价绿色）
- 字段变化分类显示

## 定时执行

### Linux Cron

```bash
crontab -e
```

添加定时任务（每 30 分钟执行一次）：

```
*/30 * * * * cd /opt/ZJMFUpstreamMonitor && python3 upstream_monitor.py >> /var/log/upstream_monitor.log 2>&1
```

### Linux Systemd Timer（推荐）

```bash
# 创建服务文件
sudo tee /etc/systemd/system/upstream-monitor.service << EOF
[Unit]
Description=ZJMF Upstream Monitor
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/opt/ZJMFUpstreamMonitor
ExecStart=/usr/bin/python3 /opt/ZJMFUpstreamMonitor/upstream_monitor.py
User=www-data
EOF

# 创建定时器文件
sudo tee /etc/systemd/system/upstream-monitor.timer << EOF
[Unit]
Description=Run ZJMF Upstream Monitor every 30 minutes

[Timer]
OnCalendar=*:0/30
Persistent=true

[Install]
WantedBy=timers.target
EOF

# 启用并启动定时器
sudo systemctl daemon-reload
sudo systemctl enable upstream-monitor.timer
sudo systemctl start upstream-monitor.timer
```

### Windows 任务计划程序

1. 打开"任务计划程序"
2. 创建基本任务
3. 设置触发器（如每 30 分钟执行一次）
4. 操作：启动程序
   - 程序：`python.exe`
   - 参数：`upstream_monitor.py`
   - 起始于：脚本所在目录

## 故障排查

### 邮件发送失败

1. 检查 SMTP 配置是否正确
2. 确认邮箱已开启 SMTP 服务
3. 使用邮箱提供的授权码而非登录密码
4. 检查防火墙是否允许 SMTP 端口通信

### API 请求失败

1. 检查网络连接
2. 确认 `api_url` 配置正确
3. 检查是否需要特殊的请求头

### 配置文件问题

1. 运行 `python upstream_monitor.py --config` 重新配置
2. 检查 JSON 格式是否正确

## 许可证

MIT License
