#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
上游产品信息监控脚本
监控多个上游 URL 的产品信息变化，保存差异到 JSON 文件，发送价格变化邮件通知
"""

import json
import os
import sys
import hashlib
import sqlite3
import smtplib
import platform
from datetime import datetime
from pathlib import Path
from typing import Any, List, Dict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

import requests


def get_data_dir() -> Path:
    """
    获取数据目录路径
    
    Windows: 脚本所在目录
    Linux: /opt/ZJMFUpstreamMonitor
    
    Returns:
        数据目录路径
    """
    if platform.system() == "Windows":
        return Path(__file__).parent
    else:
        return Path("/opt/ZJMFUpstreamMonitor")


DATA_DIR = get_data_dir()


class DatabaseManager:
    """数据库管理器"""
    
    def __init__(self, db_path: str = None):
        """
        初始化数据库
        
        Args:
            db_path: 数据库文件路径，默认使用 DATA_DIR
        """
        if db_path is None:
            db_path = str(DATA_DIR / "upstream_monitor.db")
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """初始化数据库表结构"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 变化记录表（包含价格变化和其他字段变化）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS change_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT,
                product_name TEXT,
                upstream_name TEXT NOT NULL,
                upstream_url TEXT,
                group_name TEXT,
                first_group_id TEXT,
                second_group_id TEXT,
                change_type TEXT NOT NULL,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                price_change REAL,
                check_time TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建索引
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_changes_product 
            ON change_records(product_id, upstream_name)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_changes_time 
            ON change_records(check_time)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_changes_type 
            ON change_records(change_type)
        ''')
        
        conn.commit()
        conn.close()
    
    def save_change_record(self, change_data: Dict):
        """
        保存变化记录到数据库
        
        Args:
            change_data: 变化数据
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO change_records 
            (product_id, product_name, upstream_name, upstream_url, 
             group_name, first_group_id, second_group_id,
             change_type, field_name, old_value, new_value, price_change, check_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            change_data.get('product_id'),
            change_data.get('product_name'),
            change_data['upstream_name'],
            change_data.get('upstream_url'),
            change_data.get('group_name'),
            change_data.get('first_group_id'),
            change_data.get('second_group_id'),
            change_data['change_type'],
            change_data.get('field_name'),
            change_data.get('old_value'),
            change_data.get('new_value'),
            change_data.get('price_change'),
            change_data['check_time']
        ))
        
        conn.commit()
        conn.close()
    
    def get_recent_changes(self, hours: int = 24) -> List[Dict]:
        """
        获取最近一段时间的价格变化记录
        
        Args:
            hours: 小时数
            
        Returns:
            价格变化记录列表
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM price_changes 
            WHERE created_at >= datetime('now', ?)
            ORDER BY created_at DESC
        ''', (f'-{hours} hours',))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]


class SMTPNotifier:
    """SMTP 邮件通知器"""
    
    def __init__(self, smtp_config: Dict):
        """
        初始化 SMTP 通知器
        
        Args:
            smtp_config: SMTP 配置字典
        """
        self.smtp_server = smtp_config.get('smtp_server', 'smtp.qq.com')
        self.smtp_port = smtp_config.get('smtp_port', 465)
        self.sender_email = smtp_config.get('sender_email')
        self.sender_password = smtp_config.get('sender_password')
        self.recipients = smtp_config.get('recipients', [])
    
    def send_change_email(self, changes: List[Dict], upstream_name: str, change_type: str = "价格"):
        """
        发送变化邮件通知
        
        Args:
            changes: 变化记录列表
            upstream_name: 上游名称
            change_type: 变化类型（价格/数据）
        """
        if not changes:
            return
        
        # 创建邮件
        msg = MIMEMultipart('alternative')
        msg['From'] = self.sender_email
        msg['To'] = ', '.join(self.recipients)
        msg['Subject'] = Header(f"{change_type}变化通知 - {upstream_name} - {datetime.now().strftime('%Y-%m-%d %H:%M')}", 'utf-8')
        
        # 生成邮件内容
        text_content = self._generate_text_email(changes, upstream_name, change_type)
        html_content = self._generate_html_email(changes, upstream_name, change_type)
        
        msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
        
        # 发送邮件
        try:
            server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port)
            server.login(self.sender_email, self.sender_password)
            server.sendmail(self.sender_email, self.recipients, msg.as_string())
            server.quit()
            print(f"✓ 邮件通知已发送（{len(changes)} 条价格变化）")
        except Exception as e:
            print(f"✗ 邮件发送失败：{str(e)}")
    
    def _format_value_for_email(self, value: Any, max_length: int = 500) -> str:
        """
        格式化值用于邮件显示，截断过长的内容
        
        Args:
            value: 要格式化的值
            max_length: 最大长度
            
        Returns:
            格式化后的字符串
        """
        try:
            if isinstance(value, (dict, list)):
                text = json.dumps(value, ensure_ascii=False, indent=2)
            else:
                text = str(value)
            
            if len(text) > max_length:
                text = text[:max_length] + f"... [截断，共{len(text)}字符]"
            return text
        except Exception:
            return str(value)[:max_length]
    
    def _generate_text_email(self, changes: List[Dict], upstream_name: str, change_type: str = "价格") -> str:
        """生成纯文本邮件内容"""
        content = f"上游监控 - {change_type}变化通知\n"
        content += f"上游：{upstream_name}\n"
        content += f"检测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        content += "=" * 60 + "\n\n"
        
        for change in changes:
            # 价格变化
            if 'change_type' in change and change['change_type'] in ['increase', 'decrease']:
                price_change_type = "涨价" if change['change_type'] == 'increase' else "降价"
                content += f"【价格变化】\n"
                content += f"产品名称：{change.get('product_name', 'N/A')}\n"
                content += f"所属分组：{change.get('group_name', 'N/A')}\n"
                content += f"产品 ID: {change.get('product_id', 'N/A')}\n"
                content += f"原价：¥{change.get('old_price', 'N/A')}\n"
                content += f"现价：¥{change.get('new_price', 'N/A')}\n"
                content += f"变化幅度：¥{abs(change.get('price_change', 0)):.2f} ({price_change_type})\n"
                content += f"购买链接：{change.get('product_url', change.get('upstream_url', ''))}\n"
            # 数据变化（新增/删除/修改）
            elif 'change_category' in change:
                category = change['change_category']
                content += f"【{category}】\n"
                content += f"上游：{change.get('upstream_name', 'N/A')}\n"
                content += f"产品ID：{change.get('product_id', 'N/A')}\n"
                content += f"产品名称：{change.get('product_name', 'N/A')}\n"
                content += f"所属分组：{change.get('group_name', 'N/A')}\n"
                content += f"修改字段：{change.get('field_name', 'N/A')}\n"
                # 添加产品链接
                upstream_url = change.get('upstream_url', '')
                first_group_id = change.get('first_group_id', '')
                second_group_id = change.get('second_group_id', '')
                if upstream_url and first_group_id and second_group_id:
                    content += f"产品链接：{upstream_url}/cart?fid={first_group_id}&gid={second_group_id}\n"
                if category == "修改":
                    content += f"旧值：{self._format_value_for_email(change.get('old_value', 'N/A'))}\n"
                    content += f"新值：{self._format_value_for_email(change.get('new_value', 'N/A'))}\n"
                else:
                    content += f"值：{self._format_value_for_email(change.get('new_value', change.get('value', 'N/A')))}\n"
            else:
                content += f"变化：{self._format_value_for_email(change)}\n"
            content += "-" * 40 + "\n"
        
        return content
    
    def _generate_html_email(self, changes: List[Dict], upstream_name: str, change_type: str = "价格") -> str:
        """生成 HTML 邮件内容"""
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #4CAF50; color: white; }}
                .increase {{ background-color: #ffcccc; }}
                .decrease {{ background-color: #ccffcc; }}
                .price-up {{ color: red; font-weight: bold; }}
                .price-down {{ color: green; font-weight: bold; }}
                .added {{ background-color: #ccffcc; }}
                .removed {{ background-color: #ffcccc; }}
                .modified {{ background-color: #ffffcc; }}
                a {{ color: #0066cc; text-decoration: none; }}
                a:hover {{ text-decoration: underline; }}
                pre {{ background-color: #f4f4f4; padding: 10px; border-radius: 4px; overflow-x: auto; }}
            </style>
        </head>
        <body>
            <h2>上游监控 - {change_type}变化通知</h2>
            <p><strong>上游：</strong>{upstream_name}</p>
            <p><strong>检测时间：</strong>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p><strong>变化数量：</strong>{len(changes)} 条</p>
            
            <table>
                <tr>
                    <th>变化类型</th>
                    <th>详细信息</th>
                </tr>
        """
        
        for change in changes:
            # 价格变化
            if 'change_type' in change and change['change_type'] in ['increase', 'decrease']:
                price_change_type = "涨价" if change['change_type'] == 'increase' else "降价"
                change_class = "increase" if change['change_type'] == 'increase' else "decrease"
                price_class = "price-up" if change['change_type'] == 'increase' else "price-down"
                product_url = change.get('product_url', change.get('upstream_url', ''))
                
                html += f"""
                <tr class="{change_class}">
                    <td><strong>【价格变化】{price_change_type}</strong></td>
                    <td>
                        <strong>产品：</strong>{change.get('product_name', 'N/A')}<br>
                        <strong>分组：</strong>{change.get('group_name', 'N/A')}<br>
                        <strong>产品 ID：</strong>{change.get('product_id', 'N/A')}<br>
                        <strong>原价：</strong>¥{change.get('old_price', 'N/A')}<br>
                        <strong>现价：</strong>¥{change.get('new_price', 'N/A')}<br>
                        <strong>变化：</strong><span class="{price_class}">{'↑' if change['change_type'] == 'increase' else '↓'} ¥{abs(change.get('price_change', 0)):.2f}</span><br>
                        <strong>链接：</strong><a href="{product_url}">查看产品</a>
                    </td>
                </tr>
                """
            # 数据变化（新增/删除/修改）
            elif 'change_category' in change:
                category = change['change_category']
                if category == "新增":
                    change_class = "added"
                elif category == "删除":
                    change_class = "removed"
                else:
                    change_class = "modified"
                
                html += f"""
                <tr class="{change_class}">
                    <td><strong>【{category}】</strong></td>
                    <td>
                        <strong>上游：</strong>{change.get('upstream_name', 'N/A')}<br>
                        <strong>产品ID：</strong>{change.get('product_id', 'N/A')}<br>
                        <strong>产品名称：</strong>{change.get('product_name', 'N/A')}<br>
                        <strong>所属分组：</strong>{change.get('group_name', 'N/A')}<br>
                        <strong>修改字段：</strong>{change.get('field_name', 'N/A')}<br>
                """
                # 添加产品链接
                upstream_url = change.get('upstream_url', '')
                first_group_id = change.get('first_group_id', '')
                second_group_id = change.get('second_group_id', '')
                if upstream_url and first_group_id and second_group_id:
                    html += f"""<strong>产品链接：</strong><a href="{upstream_url}/cart?fid={first_group_id}&gid={second_group_id}">查看产品</a><br>"""
                
                if category == "修改":
                    html += f"""
                        <strong>旧值：</strong><pre>{self._format_value_for_email(change.get('old_value', ''), 1000)}</pre><br>
                        <strong>新值：</strong><pre>{self._format_value_for_email(change.get('new_value', ''), 1000)}</pre>
                    """
                else:
                    html += f"""
                        <strong>值：</strong><pre>{self._format_value_for_email(change.get('new_value', change.get('value', '')), 1000)}</pre>
                    """
                html += "</td></tr>"
            else:
                html += f"""
                <tr>
                    <td><strong>【其他变化】</strong></td>
                    <td><pre>{self._format_value_for_email(change, 1000)}</pre></td>
                </tr>
                """
        
        html += """
            </table>
            <br>
            <p style="color: #666; font-size: 12px;">此邮件由上游监控系统自动发送</p>
        </body>
        </html>
        """
        
        return html


class UpstreamMonitor:
    """上游产品信息监控器"""
    
    def __init__(self, config_file: str = None):
        """
        初始化监控器

        Args:
            config_file: 配置文件路径，默认使用 DATA_DIR
        """
        if config_file is None:
            config_file = str(DATA_DIR / "upstream_config.json")
        self.config_file = Path(config_file)
        self.data_dir = DATA_DIR / "upstream_data"
        self.diff_dir = DATA_DIR / "upstream_diffs"
        
        # 创建数据目录
        self.data_dir.mkdir(exist_ok=True)
        self.diff_dir.mkdir(exist_ok=True)
        
        # 加载配置
        self.config = self._load_config()
        
        # 初始化数据库
        self.db = DatabaseManager()
        
        # 初始化 SMTP 通知器
        smtp_config = self.config.get('smtp', {})
        if smtp_config.get('enabled', False):
            self.notifier = SMTPNotifier(smtp_config)
        else:
            self.notifier = None

    def _load_config(self) -> dict:
        """加载配置文件"""
        if not self.config_file.exists():
            # 创建默认配置
            default_config = {
                "upstreams": [
                    {
                        "name": "美得云",
                        "base_url": "https://www.meidecloud.com/",
                        "api_url": "https://www.meidecloud.com/v1/products",
                        "enabled": True
                    }
                ],
                "request_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json"
                },
                "timeout": 30,
                "smtp": {
                    "enabled": False,
                    "smtp_server": "smtp.qq.com",
                    "smtp_port": 465,
                    "sender_email": "your_email@qq.com",
                    "sender_password": "your_password",
                    "recipients": ["recipient1@qq.com", "recipient2@163.com"]
                }
            }
            self._save_config(default_config)
            return default_config
        
        with open(self.config_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_config(self, config: dict):
        """保存配置文件"""
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def _get_data_hash(self, data: Any) -> str:
        """计算数据的 MD5 哈希值"""
        data_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(data_str.encode("utf-8")).hexdigest()

    def _get_initial_data_file(self, name: str) -> Path:
        """获取初始数据文件路径"""
        safe_name = "".join(c for c in name if c.isalnum() or c in " _-")
        return self.data_dir / f"{safe_name}_initial.json"

    def _get_diff_file(self, name: str, timestamp: datetime) -> Path:
        """获取差异文件路径"""
        safe_name = "".join(c for c in name if c.isalnum() or c in " _-")
        time_str = timestamp.strftime("%Y%m%d_%H%M%S")
        return self.diff_dir / f"{safe_name}_{time_str}.json"

    def _fetch_data(self, url: str) -> dict:
        """
        获取上游数据

        Args:
            url: API URL

        Returns:
            获取的数据
        """
        headers = self.config.get("request_headers", {})
        timeout = self.config.get("timeout", 30)
        
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        
        return response.json()

    def _compare_data(self, old_data: Any, new_data: Any, path: str = "") -> dict:
        """
        比较两个数据集的差异

        Args:
            old_data: 旧数据
            new_data: 新数据
            path: 当前路径（用于递归）

        Returns:
            差异信息
        """
        diff = {
            "path": path,
            "added": [],
            "removed": [],
            "modified": [],
            "unchanged": 0
        }

        # 如果是字典
        if isinstance(old_data, dict) and isinstance(new_data, dict):
            old_keys = set(old_data.keys())
            new_keys = set(new_data.keys())
            
            # 新增的键
            for key in new_keys - old_keys:
                diff["added"].append({
                    "path": f"{path}.{key}" if path else key,
                    "value": new_data[key]
                })
            
            # 删除的键
            for key in old_keys - new_keys:
                diff["removed"].append({
                    "path": f"{path}.{key}" if path else key,
                    "value": old_data[key]
                })
            
            # 修改的键
            for key in old_keys & new_keys:
                sub_diff = self._compare_data(
                    old_data[key], 
                    new_data[key], 
                    f"{path}.{key}" if path else key
                )
                
                if sub_diff["added"] or sub_diff["removed"] or sub_diff["modified"]:
                    diff["modified"].append({
                        "path": f"{path}.{key}" if path else key,
                        "old_value": old_data[key],
                        "new_value": new_data[key],
                        "detail": sub_diff
                    })
                else:
                    diff["unchanged"] += sub_diff["unchanged"]
        
        # 如果是列表
        elif isinstance(old_data, list) and isinstance(new_data, list):
            if len(old_data) != len(new_data):
                diff["modified"].append({
                    "path": path,
                    "old_value": old_data,
                    "new_value": new_data,
                    "change_type": "length_changed",
                    "old_length": len(old_data),
                    "new_length": len(new_data)
                })
            else:
                for i, (old_item, new_item) in enumerate(zip(old_data, new_data)):
                    sub_diff = self._compare_data(
                        old_item, 
                        new_item, 
                        f"{path}[{i}]"
                    )
                    
                    if sub_diff["added"] or sub_diff["removed"] or sub_diff["modified"]:
                        diff["modified"].append({
                            "path": f"{path}[{i}]",
                            "old_value": old_item,
                            "new_value": new_item,
                            "detail": sub_diff
                        })
                    else:
                        diff["unchanged"] += sub_diff["unchanged"]
        
        # 如果是基本类型
        else:
            if old_data != new_data:
                diff["modified"].append({
                    "path": path,
                    "old_value": old_data,
                    "new_value": new_data
                })
            else:
                diff["unchanged"] = 1
        
        return diff

    def _extract_products(self, data: dict, upstream_name: str, upstream_url: str) -> List[Dict]:
        """
        从 API 响应数据中提取产品信息
        
        Args:
            data: API 响应数据
            upstream_name: 上游名称
            upstream_url: 上游 URL
            
        Returns:
            产品信息列表
        """
        products = []
        
        try:
            # 遍历数据结构
            if 'data' in data and 'first_group' in data['data']:
                for first_group in data['data']['first_group']:
                    first_group_id = str(first_group.get('id', ''))
                    if 'group' in first_group:
                        for group in first_group['group']:
                            second_group_id = str(group.get('id', ''))
                            group_name = group.get('name', '未分组')
                            if 'products' in group:
                                for product in group['products']:
                                    product_id = str(product.get('id', ''))
                                    product_name = product.get('name', '')
                                    original_price = product.get('product_price', '0')
                                    
                                    # 拼接产品 URL
                                    base_url = upstream_url.rstrip('/')
                                    product_url = f"{base_url}/cart?fid={first_group_id}&gid={second_group_id}"
                                    
                                    # 转换价格为数字
                                    try:
                                        price_numeric = float(original_price)
                                    except (ValueError, TypeError):
                                        price_numeric = 0.0
                                    
                                    products.append({
                                        'product_id': product_id,
                                        'product_name': product_name,
                                        'upstream_name': upstream_name,
                                        'upstream_url': upstream_url,
                                        'group_name': group_name,
                                        'first_group_id': first_group_id,
                                        'second_group_id': second_group_id,
                                        'product_url': product_url,
                                        'original_price': original_price,
                                        'current_price': original_price,
                                        'price_numeric': price_numeric,
                                        'check_time': datetime.now().isoformat()
                                    })
        except Exception as e:
            print(f"提取产品信息时出错：{str(e)}")
        
        return products

    def _check_price_changes(self, old_data: dict, new_data: dict, 
                            upstream_name: str, upstream_url: str) -> List[Dict]:
        """
        检查价格变化
        
        Args:
            old_data: 旧数据
            new_data: 新数据
            upstream_name: 上游名称
            upstream_url: 上游 URL
            
        Returns:
            价格变化列表
        """
        changes = []
        
        # 提取新旧产品数据
        old_products = self._extract_products(old_data, upstream_name, upstream_url)
        new_products = self._extract_products(new_data, upstream_name, upstream_url)
        
        # 创建产品 ID 到产品的映射
        old_product_map = {p['product_id']: p for p in old_products}
        new_product_map = {p['product_id']: p for p in new_products}
        
        # 检查现有产品的价格变化
        for product_id, new_product in new_product_map.items():
            old_product = old_product_map.get(product_id)
            
            if old_product:
                old_price = old_product['price_numeric']
                new_price = new_product['price_numeric']
                
                # 如果价格不同，记录变化
                if old_price != new_price:
                    price_change = new_price - old_price
                    change_type = 'increase' if price_change > 0 else 'decrease'
                    
                    changes.append({
                        'product_id': product_id,
                        'product_name': new_product['product_name'],
                        'upstream_name': upstream_name,
                        'upstream_url': upstream_url,
                        'group_name': new_product['group_name'],
                        'first_group_id': new_product['first_group_id'],
                        'second_group_id': new_product['second_group_id'],
                        'product_url': new_product['product_url'],
                        'old_price': old_product['original_price'],
                        'new_price': new_product['original_price'],
                        'price_change': price_change,
                        'change_type': change_type,
                        'check_time': datetime.now().isoformat()
                    })
        
        return changes

    def _save_changes_to_db(self, changes: List[Dict], check_time: str):
        """
        保存变化记录到数据库
        
        Args:
            changes: 变化列表
            check_time: 检测时间
        """
        for change in changes:
            change_record = {
                "product_id": change.get("product_id"),
                "product_name": change.get("product_name"),
                "upstream_name": change.get("upstream_name"),
                "upstream_url": change.get("upstream_url"),
                "group_name": change.get("group_name"),
                "first_group_id": change.get("first_group_id"),
                "second_group_id": change.get("second_group_id"),
                "change_type": change.get("change_type") or change.get("change_category", "修改"),
                "field_name": change.get("field_name"),
                "old_value": change.get("old_value") or change.get("old_price"),
                "new_value": change.get("new_value") or change.get("new_price"),
                "price_change": change.get("price_change"),
                "check_time": check_time
            }
            self.db.save_change_record(change_record)

    def monitor_upstream(self, upstream: dict) -> dict:
        """
        监控单个上游

        Args:
            upstream: 上游配置

        Returns:
            监控结果
        """
        name = upstream["name"]
        api_url = upstream["api_url"]
        base_url = upstream.get("base_url", api_url.rsplit("/v1/products", 1)[0] if "/v1/products" in api_url else api_url)
        
        print(f"\n{'='*60}")
        print(f"正在监控：{name}")
        print(f"API URL: {api_url}")
        print(f"{'='*60}")
        
        # 获取新数据
        try:
            new_data = self._fetch_data(api_url)
            print(f"✓ 成功获取新数据")
        except Exception as e:
            print(f"✗ 获取数据失败：{str(e)}")
            return {
                "success": False,
                "error": str(e),
                "upstream": name
            }
        
        # 获取初始数据文件路径
        initial_data_file = self._get_initial_data_file(name)
        
        # 检查是否存在初始数据
        if not initial_data_file.exists():
            # 首次运行，保存初始数据
            print(f"首次运行，保存初始数据到：{initial_data_file}")
            with open(initial_data_file, "w", encoding="utf-8") as f:
                json.dump(new_data, f, ensure_ascii=False, indent=2)
            
            return {
                "success": True,
                "upstream": name,
                "is_first_run": True,
                "message": "已保存初始数据"
            }
        
        # 加载初始数据
        with open(initial_data_file, "r", encoding="utf-8") as f:
            old_data = json.load(f)
        
        # 计算哈希值判断是否有变化
        old_hash = self._get_data_hash(old_data)
        new_hash = self._get_data_hash(new_data)
        
        if old_hash == new_hash:
            print(f"✓ 数据无变化")
            return {
                "success": True,
                "upstream": name,
                "has_changes": False,
                "message": "数据无变化"
            }
        
        # 比较差异
        print(f"检测到数据变化，正在比较差异...")
        timestamp = datetime.now()
        diff = self._compare_data(old_data, new_data)
        
        # 检查价格变化
        price_changes = self._check_price_changes(old_data, new_data, name, base_url)
        print(f"检测到 {len(price_changes)} 个产品价格变化")
        
        # 保存差异文件 - 只保存差异部分
        diff_file = self._get_diff_file(name, timestamp)
        
        # 过滤掉未变化的内容，只保留有变化的部分
        filtered_diff = {
            "path": diff.get("path", ""),
            "added": diff.get("added", []),
            "removed": diff.get("removed", []),
            "modified": diff.get("modified", [])
        }
        
        diff_result = {
            "timestamp": timestamp.isoformat(),
            "upstream": name,
            "api_url": api_url,
            "old_hash": old_hash,
            "new_hash": new_hash,
            "summary": {
                "added_count": len(diff["added"]),
                "removed_count": len(diff["removed"]),
                "modified_count": len(diff["modified"]),
                "price_changes_count": len(price_changes)
            },
            "diff": filtered_diff,
            "price_changes": price_changes
        }
        
        with open(diff_file, "w", encoding="utf-8") as f:
            json.dump(diff_result, f, ensure_ascii=False, indent=2)
        
        print(f"✓ 差异已保存到：{diff_file}")
        print(f"  - 新增：{len(diff['added'])} 项")
        print(f"  - 删除：{len(diff['removed'])} 项")
        print(f"  - 修改：{len(diff['modified'])} 项")
        print(f"  - 价格变化：{len(price_changes)} 项")
        
        # 发送邮件通知 - 只要有任何变化就发送
        if self.notifier:
            all_changes = []
            
            # 添加价格变化
            if price_changes:
                all_changes.extend(price_changes)
            
            # 添加其他数据变化（新增、删除、修改但非价格）
            if diff.get("added") or diff.get("removed") or diff.get("modified"):
                # 从产品角度提取变化信息
                def extract_product_changes(diff_item, old_data_map, new_data_map):
                    """提取产品级别的变化信息"""
                    changes = []
                    
                    # 处理新增的产品
                    for item in diff_item.get("added", []):
                        path = item.get("path", "")
                        # 尝试解析路径获取产品信息
                        product_info = parse_product_from_path(path, item.get("value"), "新增")
                        if product_info:
                            changes.append(product_info)
                    
                    # 处理删除的产品
                    for item in diff_item.get("removed", []):
                        path = item.get("path", "")
                        product_info = parse_product_from_path(path, item.get("value"), "删除")
                        if product_info:
                            changes.append(product_info)
                    
                    # 处理修改的产品
                    for item in diff_item.get("modified", []):
                        path = item.get("path", "")
                        detail = item.get("detail")
                        
                        # 如果有子差异，递归处理
                        if detail and (detail.get("added") or detail.get("removed") or detail.get("modified")):
                            # 检查是否是产品级别的路径
                            import re
                            if re.search(r'products\[\d+\]', path):
                                # 产品级别变化，获取产品信息
                                product_info = get_product_info_from_path(path, old_data, new_data)
                                if product_info:
                                    # 提取具体字段变化
                                    field_changes = extract_field_changes(detail, product_info)
                                    changes.extend(field_changes)
                            else:
                                # 无法获取产品信息，直接递归处理子差异
                                sub_changes = extract_product_changes(detail, old_data, new_data)
                                changes.extend(sub_changes)
                        else:
                            # 叶子节点，直接显示字段变化
                            product_info = get_product_info_from_path(path, old_data, new_data)
                            if product_info:
                                field_name = path.split(".")[-1] if "." in path else path.split("[")[-1].replace("]", "") if "[" in path else "未知字段"
                                # 排除价格变化（已单独处理）
                                if not any(pc.get("product_id") == str(product_info.get("product_id")) for pc in price_changes):
                                    changes.append({
                                        "change_category": "修改",
                                        "type": product_info.get("type", "product"),
                                        "product_id": product_info.get("product_id", "N/A"),
                                        "product_name": product_info.get("product_name", "N/A"),
                                        "group_name": product_info.get("group_name", "N/A"),
                                        "first_group_id": product_info.get("first_group_id", ""),
                                        "second_group_id": product_info.get("second_group_id", ""),
                                        "upstream_name": name,
                                        "upstream_url": base_url,
                                        "field_name": field_name,
                                        "old_value": item.get("old_value", ""),
                                        "new_value": item.get("new_value", "")
                                    })
                    
                    return changes
                
                def parse_product_from_path(path, value, change_type):
                    """从路径解析产品信息"""
                    # 尝试从产品数据中提取信息
                    if isinstance(value, dict):
                        return {
                            "change_category": change_type,
                            "product_id": value.get("id", "N/A"),
                            "product_name": value.get("name", "N/A"),
                            "group_name": "N/A",
                            "upstream_name": name,
                            "upstream_url": base_url,
                            "field_name": "整个产品",
                            "old_value": "" if change_type == "新增" else value,
                            "new_value": value if change_type == "新增" else ""
                        }
                    return None
                
                def get_product_info_from_path(path, old_data_full, new_data_full):
                    """从路径获取产品或分组信息"""
                    import re
                    # 匹配产品级别路径: data.first_group[数字].group[数字].products[数字]
                    product_match = re.search(r'first_group\[(\d+)\]\.group\[(\d+)\]\.products\[(\d+)\]', path)
                    if product_match:
                        first_group_idx = int(product_match.group(1))
                        second_group_idx = int(product_match.group(2))
                        product_idx = int(product_match.group(3))
                        
                        # 从新数据中提取产品信息
                        try:
                            if 'data' in new_data_full and 'first_group' in new_data_full['data']:
                                first_groups = new_data_full['data']['first_group']
                                if first_group_idx < len(first_groups):
                                    first_group = first_groups[first_group_idx]
                                    first_group_id = str(first_group.get('id', ''))
                                    if 'group' in first_group:
                                        groups = first_group['group']
                                        if second_group_idx < len(groups):
                                            group = groups[second_group_idx]
                                            second_group_id = str(group.get('id', ''))
                                            group_name = group.get('name', '未分组')
                                            if 'products' in group:
                                                products = group['products']
                                                if product_idx < len(products):
                                                    product = products[product_idx]
                                                    return {
                                                        "type": "product",
                                                        "product_id": product.get('id', 'N/A'),
                                                        "product_name": product.get('name', 'N/A'),
                                                        "group_name": group_name,
                                                        "first_group_id": first_group_id,
                                                        "second_group_id": second_group_id
                                                    }
                        except Exception:
                            pass
                        
                        # 如果新数据中没有，尝试从旧数据中提取
                        try:
                            if 'data' in old_data_full and 'first_group' in old_data_full['data']:
                                first_groups = old_data_full['data']['first_group']
                                if first_group_idx < len(first_groups):
                                    first_group = first_groups[first_group_idx]
                                    first_group_id = str(first_group.get('id', ''))
                                    if 'group' in first_group:
                                        groups = first_group['group']
                                        if second_group_idx < len(groups):
                                            group = groups[second_group_idx]
                                            second_group_id = str(group.get('id', ''))
                                            group_name = group.get('name', '未分组')
                                            if 'products' in group:
                                                products = group['products']
                                                if product_idx < len(products):
                                                    product = products[product_idx]
                                                    return {
                                                        "type": "product",
                                                        "product_id": product.get('id', 'N/A'),
                                                        "product_name": product.get('name', 'N/A'),
                                                        "group_name": group_name,
                                                        "first_group_id": first_group_id,
                                                        "second_group_id": second_group_id
                                                    }
                        except Exception:
                            pass
                    
                    # 匹配分组级别路径: data.first_group[数字].group[数字]
                    group_match = re.search(r'first_group\[(\d+)\]\.group\[(\d+)\](?:\.|$)', path)
                    if group_match:
                        first_group_idx = int(group_match.group(1))
                        second_group_idx = int(group_match.group(2))
                        
                        # 从新数据中提取分组信息
                        try:
                            if 'data' in new_data_full and 'first_group' in new_data_full['data']:
                                first_groups = new_data_full['data']['first_group']
                                if first_group_idx < len(first_groups):
                                    first_group = first_groups[first_group_idx]
                                    first_group_id = str(first_group.get('id', ''))
                                    if 'group' in first_group:
                                        groups = first_group['group']
                                        if second_group_idx < len(groups):
                                            group = groups[second_group_idx]
                                            second_group_id = str(group.get('id', ''))
                                            group_name = group.get('name', '未分组')
                                            return {
                                                "type": "group",
                                                "product_id": "N/A",
                                                "product_name": group_name,
                                                "group_name": group_name,
                                                "first_group_id": first_group_id,
                                                "second_group_id": second_group_id
                                            }
                        except Exception:
                            pass
                        
                        # 如果新数据中没有，尝试从旧数据中提取
                        try:
                            if 'data' in old_data_full and 'first_group' in old_data_full['data']:
                                first_groups = old_data_full['data']['first_group']
                                if first_group_idx < len(first_groups):
                                    first_group = first_groups[first_group_idx]
                                    first_group_id = str(first_group.get('id', ''))
                                    if 'group' in first_group:
                                        groups = first_group['group']
                                        if second_group_idx < len(groups):
                                            group = groups[second_group_idx]
                                            second_group_id = str(group.get('id', ''))
                                            group_name = group.get('name', '未分组')
                                            return {
                                                "type": "group",
                                                "product_id": "N/A",
                                                "product_name": group_name,
                                                "group_name": group_name,
                                                "first_group_id": first_group_id,
                                                "second_group_id": second_group_id
                                            }
                        except Exception:
                            pass
                    
                    return None
                
                def extract_field_changes(detail, product_info):
                    """提取字段级别的变化"""
                    field_changes = []
                    
                    for sub_item in detail.get("modified", []):
                        sub_path = sub_item.get("path", "")
                        field_name = sub_path.split(".")[-1] if "." in sub_path else sub_path
                        
                        sub_detail = sub_item.get("detail")
                        if sub_detail and (sub_detail.get("added") or sub_detail.get("removed") or sub_detail.get("modified")):
                            # 继续递归
                            nested_changes = extract_field_changes(sub_detail, product_info)
                            field_changes.extend(nested_changes)
                        else:
                            # 排除价格变化（已单独处理）
                            if not any(pc.get("product_id") == str(product_info.get("product_id")) for pc in price_changes):
                                field_changes.append({
                                    "change_category": "修改",
                                    "product_id": product_info.get("product_id", "N/A"),
                                    "product_name": product_info.get("product_name", "N/A"),
                                    "group_name": product_info.get("group_name", "N/A"),
                                    "first_group_id": product_info.get("first_group_id", ""),
                                    "second_group_id": product_info.get("second_group_id", ""),
                                    "upstream_name": name,
                                    "upstream_url": base_url,
                                    "field_name": field_name,
                                    "old_value": sub_item.get("old_value", ""),
                                    "new_value": sub_item.get("new_value", "")
                                })
                    
                    return field_changes
                
                # 从产品角度提取变化
                product_changes = extract_product_changes(diff, old_data, new_data)
                all_changes.extend(product_changes)
            
            if all_changes:
                self.notifier.send_change_email(all_changes, name, "数据")
                # 保存变化记录到数据库
                self._save_changes_to_db(all_changes, timestamp.isoformat())
                print(f"✓ 已保存 {len(all_changes)} 条变化记录到数据库")
        
        # 更新初始数据为新数据
        with open(initial_data_file, "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
        print(f"✓ 初始数据已更新")
        
        return {
            "success": True,
            "upstream": name,
            "has_changes": True,
            "diff_file": str(diff_file),
            "summary": diff_result["summary"],
            "price_changes": len(price_changes)
        }

    def run(self):
        """运行监控"""
        print(f"上游产品信息监控（带价格检测和邮件通知）")
        print(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        results = []
        for upstream in self.config.get("upstreams", []):
            if not upstream.get("enabled", True):
                print(f"\n跳过已禁用的上游：{upstream['name']}")
                continue
            
            result = self.monitor_upstream(upstream)
            results.append(result)
        
        # 输出总结
        print(f"\n{'='*60}")
        print(f"监控完成")
        print(f"结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        for result in results:
            upstream_name = result.get("upstream", "未知")
            if not result.get("success"):
                print(f"✗ {upstream_name}: 失败 - {result.get('error', '未知错误')}")
            elif result.get("is_first_run"):
                print(f"✓ {upstream_name}: 首次运行，已保存初始数据")
            elif not result.get("has_changes"):
                print(f"✓ {upstream_name}: 数据无变化")
            else:
                summary = result.get("summary", {})
                print(f"✓ {upstream_name}: 检测到变化 "
                      f"(新增:{summary.get('added_count', 0)}, "
                      f"删除:{summary.get('removed_count', 0)}, "
                      f"修改:{summary.get('modified_count', 0)}, "
                      f"价格变化:{summary.get('price_changes_count', 0)})")
        
        return results


class ConfigWizard:
    """配置向导"""
    
    def __init__(self, config_file: str = None):
        if config_file is None:
            config_file = str(DATA_DIR / "upstream_config.json")
        self.config_file = Path(config_file)
    
    def run(self):
        """运行配置向导"""
        print("=" * 60)
        print("上游监控配置向导")
        print("=" * 60)
        
        # 加载现有配置
        config = self._load_config()
        
        while True:
            print("\n请选择操作：")
            print("1. 查看当前配置")
            print("2. 添加上游")
            print("3. 修改上游")
            print("4. 删除上游")
            print("5. 配置邮件通知")
            print("6. 保存并退出")
            print("0. 退出不保存")
            
            choice = input("\n请输入选项 [0-6]: ").strip()
            
            if choice == "1":
                self._show_config(config)
            elif choice == "2":
                self._add_upstream(config)
            elif choice == "3":
                self._edit_upstream(config)
            elif choice == "4":
                self._delete_upstream(config)
            elif choice == "5":
                self._config_email(config)
            elif choice == "6":
                self._save_config(config)
                print("\n✓ 配置已保存")
                break
            elif choice == "0":
                print("\n已退出，配置未保存")
                break
            else:
                print("\n无效选项，请重新输入")
    
    def _load_config(self) -> dict:
        """加载配置文件"""
        if self.config_file.exists():
            with open(self.config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "upstreams": [],
            "email": {
                "enabled": False,
                "smtp_server": "",
                "smtp_port": 465,
                "sender_email": "",
                "sender_password": "",
                "recipients": []
            }
        }
    
    def _save_config(self, config: dict):
        """保存配置文件"""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    
    def _show_config(self, config: dict):
        """显示当前配置"""
        print("\n" + "=" * 60)
        print("当前配置")
        print("=" * 60)
        
        # 显示上游配置
        upstreams = config.get("upstreams", [])
        if upstreams:
            print(f"\n上游列表 (共 {len(upstreams)} 个):")
            for i, upstream in enumerate(upstreams, 1):
                status = "启用" if upstream.get("enabled", True) else "禁用"
                print(f"  {i}. {upstream.get('name', 'N/A')} [{status}]")
                print(f"     API: {upstream.get('api_url', 'N/A')}")
                print(f"     Base: {upstream.get('base_url', 'N/A')}")
        else:
            print("\n暂无上游配置")
        
        # 显示邮件配置
        email = config.get("email", {})
        print(f"\n邮件通知: {'已启用' if email.get('enabled', False) else '未启用'}")
        if email.get("enabled"):
            print(f"  SMTP服务器: {email.get('smtp_server', 'N/A')}")
            print(f"  SMTP端口: {email.get('smtp_port', 465)}")
            print(f"  发件人: {email.get('sender_email', 'N/A')}")
            print(f"  收件人: {', '.join(email.get('recipients', []))}")
    
    def _add_upstream(self, config: dict):
        """添加上游"""
        print("\n--- 添加上游 ---")
        
        name = input("上游名称 (如: 美得云): ").strip()
        if not name:
            print("名称不能为空")
            return
        
        api_url = input("API URL (如: https://www.example.com/v1/products): ").strip()
        if not api_url:
            print("API URL 不能为空")
            return
        
        base_url = input("Base URL (如: https://www.example.com，留空则自动提取): ").strip()
        if not base_url:
            # 自动从 API URL 提取
            if "/v1/products" in api_url:
                base_url = api_url.rsplit("/v1/products", 1)[0]
            else:
                base_url = api_url.rsplit("/", 1)[0] if "/" in api_url else api_url
        
        enabled = input("是否启用? [Y/n]: ").strip().lower()
        enabled = enabled != "n"
        
        upstream = {
            "name": name,
            "api_url": api_url,
            "base_url": base_url,
            "enabled": enabled
        }
        
        config.setdefault("upstreams", []).append(upstream)
        print(f"\n✓ 已添加上游: {name}")
    
    def _edit_upstream(self, config: dict):
        """修改上游"""
        upstreams = config.get("upstreams", [])
        if not upstreams:
            print("\n暂无上游配置")
            return
        
        print("\n--- 修改上游 ---")
        for i, upstream in enumerate(upstreams, 1):
            print(f"  {i}. {upstream.get('name', 'N/A')}")
        
        try:
            idx = int(input("\n请选择要修改的上游编号: ").strip()) - 1
            if idx < 0 or idx >= len(upstreams):
                print("无效编号")
                return
        except ValueError:
            print("请输入有效数字")
            return
        
        upstream = upstreams[idx]
        print(f"\n当前配置: {upstream.get('name')}")
        print("(直接回车保持原值)")
        
        name = input(f"名称 [{upstream.get('name')}]: ").strip()
        if name:
            upstream["name"] = name
        
        api_url = input(f"API URL [{upstream.get('api_url')}]: ").strip()
        if api_url:
            upstream["api_url"] = api_url
        
        base_url = input(f"Base URL [{upstream.get('base_url')}]: ").strip()
        if base_url:
            upstream["base_url"] = base_url
        
        enabled = input(f"是否启用? [{'Y' if upstream.get('enabled', True) else 'n'}/n]: ").strip().lower()
        if enabled == "y":
            upstream["enabled"] = True
        elif enabled == "n":
            upstream["enabled"] = False
        
        print(f"\n✓ 已修改上游: {upstream.get('name')}")
    
    def _delete_upstream(self, config: dict):
        """删除上游"""
        upstreams = config.get("upstreams", [])
        if not upstreams:
            print("\n暂无上游配置")
            return
        
        print("\n--- 删除上游 ---")
        for i, upstream in enumerate(upstreams, 1):
            print(f"  {i}. {upstream.get('name', 'N/A')}")
        
        try:
            idx = int(input("\n请选择要删除的上游编号: ").strip()) - 1
            if idx < 0 or idx >= len(upstreams):
                print("无效编号")
                return
        except ValueError:
            print("请输入有效数字")
            return
        
        deleted = upstreams.pop(idx)
        print(f"\n✓ 已删除上游: {deleted.get('name')}")
    
    def _config_email(self, config: dict):
        """配置邮件"""
        print("\n--- 配置邮件通知 ---")
        
        email = config.setdefault("email", {})
        
        enabled = input(f"启用邮件通知? [{'Y' if email.get('enabled', False) else 'n'}/n]: ").strip().lower()
        if enabled == "y":
            email["enabled"] = True
        elif enabled == "n":
            email["enabled"] = False
            print("\n邮件通知已禁用")
            return
        
        print("\n(直接回车保持原值)")
        
        smtp_server = input(f"SMTP服务器 [{email.get('smtp_server', '')}]: ").strip()
        if smtp_server:
            email["smtp_server"] = smtp_server
        
        smtp_port = input(f"SMTP端口 [{email.get('smtp_port', 465)}]: ").strip()
        if smtp_port:
            try:
                email["smtp_port"] = int(smtp_port)
            except ValueError:
                print("端口格式错误，保持原值")
        
        sender_email = input(f"发件人邮箱 [{email.get('sender_email', '')}]: ").strip()
        if sender_email:
            email["sender_email"] = sender_email
        
        sender_password = input(f"发件人密码/授权码 [{'*' * 8 if email.get('sender_password') else ''}]: ").strip()
        if sender_password:
            email["sender_password"] = sender_password
        
        recipients_str = ", ".join(email.get("recipients", []))
        recipients = input(f"收件人邮箱 (多个用逗号分隔) [{recipients_str}]: ").strip()
        if recipients:
            email["recipients"] = [r.strip() for r in recipients.split(",") if r.strip()]
        
        print("\n✓ 邮件配置已更新")


def main():
    """主函数"""
    if len(sys.argv) > 1 and sys.argv[1] == "--config":
        # 配置模式
        wizard = ConfigWizard()
        wizard.run()
    else:
        # 监控模式
        monitor = UpstreamMonitor()
        monitor.run()


if __name__ == "__main__":
    main()
