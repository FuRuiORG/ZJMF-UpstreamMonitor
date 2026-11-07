# ZJMF-UpstreamMonitor

监控多个上游站点产品信息的命令行工具，检测价格与字段变化，保存JSON差异并写入SQLite，支持邮件通知。

## 环境

- Python 3.8+
- Linux下固定安装在`/opt/ZJMFUpstreamMonitor/`

## 安装

```bash
cd /opt
git clone https://github.com/FuRuiORG/ZJMF-UpstreamMonitor
cd ZJMFUpstreamMonitor
pip install -r requirements.txt
```

## 配置

```bash
python3 upstream_monitor.py --config
```

向导可增删上游、设置API地址与SMTP。配置保存在`upstream_config.json`。

## 运行

```bash
python3 upstream_monitor.py
```

首次运行保存初始快照；后续运行输出差异、发送邮件并更新快照。数据目录为`upstream_data/`，数据库为`upstream_monitor.db`。

## 定时

```
*/30 * * * * cd /opt/ZJMFUpstreamMonitor && python3 upstream_monitor.py >> /var/log/upstream_monitor.log 2>&1
```

## 数据表

`change_records`记录产品ID、分组、变化类型、字段、旧值、新值、价格变化与检测时间。

## License

MIT，见`LICENSE`。
