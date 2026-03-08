# 安装文档

## 演示环境

- 操作系统：Ubuntu
- 管理面板：宝塔面板
- Python 版本：3.12.3
- pip 版本：24.0

---

## 安装步骤

### 1. 下载程序

访问 [Releases](https://github.com/FuRuiORG/ZJMF-UpstreamMonitor/releases) 页面：

![Release](https://github.com/user-attachments/assets/39d68fd5-8b5d-441e-bf90-d16b98b5ca0e)

下载最新版本的源码（Source code），放置到 Linux 服务器的 `/opt/ZJMFUpstreamMonitor` 目录下：

![目录](https://github.com/user-attachments/assets/d2bcde12-43e4-43b7-984a-9504dbff7cd9)

### 2. 解压文件

解压下载的文件，并将程序文件移动到 `/opt/ZJMFUpstreamMonitor` 目录：

![解压](https://github.com/user-attachments/assets/24844873-be03-4cd6-8718-dca01800a880)

### 3. 安装依赖

通过 SSH 连接到服务器，执行以下命令：

```bash
cd /opt/ZJMFUpstreamMonitor
```

> **注意**：部分系统可能未预装 Python 和 pip，需要手动安装。

安装项目依赖：

```bash
pip install -r requirements.txt
```

### 4. 配置初始化

执行配置命令（不同系统的 Python 命令可能有所不同）：

```bash
python3 upstream_monitor.py --config
```

按照提示完成配置：

![配置](https://github.com/user-attachments/assets/4238c066-9d43-42a3-8979-bd3edb9f8487)

配置 SMTP 邮件服务：

![SMTP](https://github.com/user-attachments/assets/889f7b00-aee5-4dd1-a2b9-507f5e5bb506)

配置完成后，输入 `6` 保存并退出。

### 5. 刷新初始状态

执行以下命令刷新初始状态：

```bash
python3 upstream_monitor.py
```

### 6. 设置计划任务

进入宝塔面板的计划任务页面，添加定时任务：

![计划任务](https://github.com/user-attachments/assets/ac4739b6-6bba-47ee-bd14-2604a3eadbf0)

保存后，系统将定时检测上游产品信息与状态。

---

## 效果展示

配置完成后，当 ~~视奸~~ 检测到变化时，你将收到邮件通知：

![邮件](https://github.com/user-attachments/assets/4e40c64c-f420-4efe-a79d-340fa63c59dd)
