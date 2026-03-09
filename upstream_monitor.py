#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
上游产品信息监控脚本
监控多个上游 URL 的产品信息变化，保存差异到 JSON 文件，发送价格变化邮件通知
"""

import json
import os
import sys
import time
import hashlib
import sqlite3
import smtplib
import logging
import platform
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, List, Dict, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

import requests

# 配置日志
logger = logging.getLogger(__name__)


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
    
    @contextmanager
    def _get_connection(self):
        """获取数据库连接的上下文管理器"""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()
    
    def _init_database(self):
        """初始化数据库表结构"""
        with self._get_connection() as conn:
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
    
    def save_change_record(self, change_data: Dict):
        """
        保存变化记录到数据库
        
        Args:
            change_data: 变化数据
        """
        with self._get_connection() as conn:
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
    
    def get_recent_changes(self, hours: int = 24) -> List[Dict]:
        """
        获取最近一段时间的价格变化记录
        
        Args:
            hours: 小时数
            
        Returns:
            价格变化记录列表
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM change_records 
                WHERE created_at >= datetime('now', ?)
                ORDER BY created_at DESC
            ''', (f'-{hours} hours',))
            
            rows = cursor.fetchall()
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
    
    def send_change_email(self, changes: List[Dict], upstream_name: str, change_type: str = "价格", reshuffling_info: Dict = None):
        """
        发送变化邮件通知
        
        Args:
            changes: 变化记录列表
            upstream_name: 上游名称
            change_type: 变化类型（价格/数据）
            reshuffling_info: 产品重组信息
        """
        if not changes:
            return
        
        msg = MIMEMultipart('alternative')
        msg['From'] = self.sender_email
        msg['To'] = ', '.join(self.recipients)
        
        subject_prefix = "⚠️ 产品重组通知" if (reshuffling_info and reshuffling_info.get('is_reshuffling')) else f"{change_type}变化通知"
        msg['Subject'] = Header(f"{subject_prefix} - {upstream_name} - {datetime.now().strftime('%Y-%m-%d %H:%M')}", 'utf-8')
        
        text_content = self._generate_text_email(changes, upstream_name, change_type, reshuffling_info)
        html_content = self._generate_html_email(changes, upstream_name, change_type, reshuffling_info)
        
        msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
        
        try:
            server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port)
            server.login(self.sender_email, self.sender_password)
            server.sendmail(self.sender_email, self.recipients, msg.as_string())
            server.quit()
            print(f"✓ 邮件通知已发送（{len(changes)} 条价格变化）")
        except Exception as e:
            logger.error(f"邮件发送失败：{str(e)}")
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
    
    def _generate_text_email(self, changes: List[Dict], upstream_name: str, change_type: str = "价格", reshuffling_info: Dict = None) -> str:
        """生成纯文本邮件内容"""
        content = f"上游监控 - {change_type}变化通知\n"
        content += f"上游：{upstream_name}\n"
        content += f"检测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        content += "=" * 60 + "\n\n"
        
        if reshuffling_info and reshuffling_info.get('is_reshuffling'):
            content += "【⚠️ 检测到产品信息重组】\n"
            content += f"类型：{'循环交换' if reshuffling_info.get('reshuffling_type') == 'circular_exchange' else '互换'}\n"
            content += f"概述：{reshuffling_info.get('summary', 'N/A')}\n\n"
            
            for group in reshuffling_info.get('groups', []):
                content += f"分组：{group.get('group_name', 'N/A')}\n"
                content += f"涉及产品数：{group.get('reshuffled_count', 0)} 个\n"
                content += f"模式：{'循环交换' if group.get('is_circular') else '互换'}\n"
                content += "产品身份转移详情：\n"
                
                for mapping in group.get('mappings', []):
                    content += f"  • 原 ID {mapping.get('original_id')} ({mapping.get('original_name')}，¥{mapping.get('original_price')})\n"
                    content += f"    → 转移到 ID {mapping.get('moved_to_id')}\n"
                    content += f"    （该 ID 现在的名称：{mapping.get('new_name_at_old_id')}，价格：¥{mapping.get('new_price_at_old_id')}）\n"
                
                content += "\n"
            
            content += "=" * 60 + "\n"
            content += "以下是详细的字段变化记录：\n"
            content += "=" * 60 + "\n\n"
        
        for change in changes:
            if 'is_reshuffling_item' in change:
                continue
            
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
            elif 'change_category' in change:
                category = change['change_category']
                content += f"【{category}】\n"
                content += f"上游：{change.get('upstream_name', 'N/A')}\n"
                content += f"产品ID：{change.get('product_id', 'N/A')}\n"
                content += f"产品名称：{change.get('product_name', 'N/A')}\n"
                content += f"所属分组：{change.get('group_name', 'N/A')}\n"
                content += f"修改字段：{change.get('field_name', 'N/A')}\n"
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
    
    def _generate_html_email(self, changes: List[Dict], upstream_name: str, change_type: str = "价格", reshuffling_info: Dict = None) -> str:
        """生成 HTML 邮件内容"""
        html = f"""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>上游监控通知</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
                
                body {{
                    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'HarmonyOS Sans', sans-serif;
                    background-color: #ffffff;
                    color: #171717;
                    margin: 0;
                    padding: 0;
                    line-height: 1.6;
                }}
                
                .email-container {{
                    max-width: 640px;
                    margin: 0 auto;
                    padding: 40px 24px;
                }}
                
                .header {{
                    border-bottom: 1px solid #e5e5e5;
                    padding-bottom: 24px;
                    margin-bottom: 32px;
                }}
                
                .logo {{
                    font-size: 18px;
                    font-weight: 600;
                    color: #171717;
                    letter-spacing: -0.02em;
                    margin-bottom: 16px;
                }}
                
                .title {{
                    font-size: 24px;
                    font-weight: 600;
                    color: #171717;
                    letter-spacing: -0.02em;
                    margin: 0 0 8px 0;
                }}
                
                .subtitle {{
                    font-size: 14px;
                    color: #737373;
                    margin: 0;
                }}
                
                .meta-info {{
                    background-color: #fafafa;
                    border: 1px solid #e5e5e5;
                    padding: 16px 20px;
                    margin-bottom: 24px;
                }}
                
                .meta-row {{
                    display: flex;
                    justify-content: space-between;
                    font-size: 13px;
                    margin-bottom: 8px;
                }}
                
                .meta-row:last-child {{
                    margin-bottom: 0;
                }}
                
                .meta-label {{
                    color: #737373;
                }}
                
                .meta-value {{
                    color: #171717;
                    font-weight: 500;
                }}
                
                .reshuffling-banner {{
                    background-color: #171717;
                    color: #ffffff;
                    padding: 20px 24px;
                    margin-bottom: 24px;
                }}
                
                .reshuffling-banner h3 {{
                    font-size: 16px;
                    font-weight: 600;
                    margin: 0 0 8px 0;
                    display: flex;
                    align-items: center;
                    gap: 8px;
                }}
                
                .reshuffling-banner p {{
                    font-size: 13px;
                    color: #a3a3a3;
                    margin: 4px 0;
                }}
                
                .reshuffling-group {{
                    background-color: #fafafa;
                    border: 1px solid #e5e5e5;
                    border-left: 2px solid #171717;
                    padding: 16px 20px;
                    margin-bottom: 16px;
                }}
                
                .reshuffling-group h4 {{
                    font-size: 14px;
                    font-weight: 600;
                    color: #171717;
                    margin: 0 0 12px 0;
                }}
                
                .reshuffling-group p {{
                    font-size: 13px;
                    color: #525252;
                    margin: 0 0 4px 0;
                }}
                
                .mapping-item {{
                    background-color: #ffffff;
                    border: 1px solid #e5e5e5;
                    padding: 12px 16px;
                    margin-top: 12px;
                    font-size: 13px;
                }}
                
                .mapping-item strong {{
                    color: #171717;
                }}
                
                .mapping-item small {{
                    color: #737373;
                }}
                
                .section-title {{
                    font-size: 14px;
            font-weight: 600;
                    color: #171717;
                    margin: 32px 0 16px 0;
                    padding-bottom: 8px;
                    border-bottom: 1px solid #e5e5e5;
                }}
                
                .change-card {{
                    border: 1px solid #e5e5e5;
                    margin-bottom: 1px;
                }}
                
                .change-card:last-child {{
                    margin-bottom: 16px;
                }}
                
                .change-header {{
                    padding: 12px 16px;
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    border-bottom: 1px solid #f5f5f5;
                }}
                
                .change-type {{
                    font-size: 12px;
                    font-weight: 600;
                    letter-spacing: 0.02em;
                    padding: 4px 8px;
                }}
                
                .type-price-up {{
                    background-color: #fef2f2;
                    color: #dc2626;
                }}
                
                .type-price-down {{
                    background-color: #f0fdf4;
                    color: #16a34a;
                }}
                
                .type-added {{
                    background-color: #f0fdf4;
                    color: #16a34a;
                }}
                
                .type-removed {{
                    background-color: #fef2f2;
                    color: #dc2626;
                }}
                
                .type-modified {{
                    background-color: #fffbeb;
                    color: #d97706;
                }}
                
                .change-body {{
                    padding: 16px;
                }}
                
                .change-row {{
                    font-size: 13px;
                    margin-bottom: 8px;
                    display: flex;
                }}
                
                .change-row:last-child {{
                    margin-bottom: 0;
                }}
                
                .change-label {{
                    color: #737373;
                    min-width: 80px;
                }}
                
                .change-value {{
                    color: #171717;
                    flex: 1;
                }}
                
                .price-change {{
                    font-weight: 600;
                }}
                
                .price-up {{
                    color: #dc2626;
                }}
                
                .price-down {{
                    color: #16a34a;
                }}
                
                .change-link {{
                    color: #171717;
                    text-decoration: underline;
                    text-underline-offset: 2px;
                }}
                
                .change-link:hover {{
                    color: #525252;
                }}
                
                .code-block {{
                    background-color: #fafafa;
                    border: 1px solid #e5e5e5;
                    padding: 12px 16px;
                    font-family: 'SF Mono', 'Monaco', 'Inconsolata', monospace;
                    font-size: 12px;
                    overflow-x: auto;
                    white-space: pre-wrap;
                    word-break: break-all;
                    max-height: 200px;
                    overflow-y: auto;
                }}
                
                .footer {{
                    margin-top: 40px;
                    padding-top: 24px;
                    border-top: 1px solid #e5e5e5;
                    text-align: center;
                }}
                
                .footer p {{
                    font-size: 12px;
                    color: #a3a3a3;
                    margin: 0;
                }}
                
                .stats-grid {{
                    display: grid;
                    grid-template-columns: repeat(4, 1fr);
                    gap: 1px;
                    background-color: #e5e5e5;
                    border: 1px solid #e5e5e5;
                    margin-bottom: 24px;
                }}
                
                .stat-item {{
                    background-color: #ffffff;
                    padding: 16px;
                    text-align: center;
                }}
                
                .stat-number {{
                    font-size: 20px;
                    font-weight: 600;
                    color: #171717;
                }}
                
                .stat-label {{
                    font-size: 11px;
                    color: #737373;
                    margin-top: 4px;
                }}
            </style>
        </head>
        <body>
            <div class="email-container">
                <div class="header">
                    <div class="logo">FuRuiORG Monitor</div>
                    <h1 class="title">{change_type}变化通知</h1>
                    <p class="subtitle">上游产品监控检测到数据变化</p>
                </div>
                
                <div class="meta-info">
                    <div class="meta-row">
                        <span class="meta-label">上游</span>
                        <span class="meta-value">{upstream_name}</span>
                    </div>
                    <div class="meta-row">
                        <span class="meta-label">检测时间</span>
                        <span class="meta-value">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</span>
                    </div>
                    <div class="meta-row">
                        <span class="meta-label">变化数量</span>
                        <span class="meta-value">{len(changes)} 条</span>
                    </div>
                </div>
        """
        
        price_increase_count = sum(1 for c in changes if c.get('change_type') == 'increase')
        price_decrease_count = sum(1 for c in changes if c.get('change_type') == 'decrease')
        added_count = sum(1 for c in changes if c.get('change_category') == '新增')
        removed_count = sum(1 for c in changes if c.get('change_category') == '删除')
        modified_count = sum(1 for c in changes if c.get('change_category') == '修改')
        reshuffling_item_count = sum(1 for c in changes if 'is_reshuffling_item' in c)
        
        field_name_map = {
            'id': '产品ID',
            'name': '产品名称',
            'description': '产品描述',
            'product_price': '价格',
            'stock_control': '库存控制',
            'qty': '库存数量',
            'type': '产品类型',
            'billingcycle': '计费周期',
            'setup_fee': '初装费',
            'ontrial': '试用设置',
            'headline': '标题',
            'tagline': '标语',
            'fields': '自定义字段',
        }
        
        def get_field_display_name(field_name):
            return field_name_map.get(field_name, field_name)
        
        modified_fields = set()
        for c in changes:
            if c.get('field_name'):
                display_name = get_field_display_name(c.get('field_name'))
                modified_fields.add(display_name)
            elif c.get('change_type') in ['increase', 'decrease']:
                modified_fields.add('价格')
        
        html += f"""
                <div class="section-title">变化概况</div>
                <div class="stats-grid" style="grid-template-columns: repeat(5, 1fr);">
                    <div class="stat-item">
                        <div class="stat-number" style="color: #dc2626;">{price_increase_count}</div>
                        <div class="stat-label">涨价</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-number" style="color: #16a34a;">{price_decrease_count}</div>
                        <div class="stat-label">降价</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-number" style="color: #d97706;">{modified_count}</div>
                        <div class="stat-label">修改</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-number" style="color: #16a34a;">{added_count}</div>
                        <div class="stat-label">新增</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-number" style="color: #dc2626;">{removed_count}</div>
                        <div class="stat-label">删除</div>
                    </div>
                </div>
        """
        
        if modified_fields:
            fields_str = '、'.join(sorted(modified_fields))
            total_modified = modified_count + price_increase_count + price_decrease_count
            html += f"""
                <div class="meta-info" style="margin-bottom: 24px;">
                    <div class="meta-row">
                        <span class="meta-label">涉及字段</span>
                        <span class="meta-value">{fields_str}</span>
                    </div>
                    <div class="meta-row">
                        <span class="meta-label">总变化数</span>
                        <span class="meta-value">{total_modified} 条</span>
                    </div>
                </div>
        """
        
        if reshuffling_info and reshuffling_info.get('is_reshuffling'):
            reshuffling_type_text = '循环交换' if reshuffling_info.get('reshuffling_type') == 'circular_exchange' else '互换'
            html += f"""
                <div class="reshuffling-banner">
                    <h3>⚠️ 检测到产品信息重组</h3>
                    <p><strong>类型：</strong>{reshuffling_type_text}</p>
                    <p><strong>概述：</strong>{reshuffling_info.get('summary', 'N/A')}</p>
                </div>
                
                <div class="section-title">重组详情</div>
            """
            
            for group in reshuffling_info.get('groups', []):
                group_type = '循环交换' if group.get('is_circular') else '互换'
                html += f"""
                <div class="reshuffling-group">
                    <h4>分组：{group.get('group_name', 'N/A')}</h4>
                    <p><strong>涉及产品数：</strong>{group.get('reshuffled_count', 0)} 个</p>
                    <p><strong>模式：</strong>{group_type}</p>
                    <p style="margin-top: 12px;"><strong>产品身份转移详情：</strong></p>
                """
                
                for mapping in group.get('mappings', []):
                    html += f"""
                    <div class="mapping-item">
                        <strong>原 ID {mapping.get('original_id')}</strong> 
                        ({mapping.get('original_name')}，¥{mapping.get('original_price')})<br>
                        → <strong>转移到 ID {mapping.get('moved_to_id')}</strong><br>
                        <small>（该 ID 现在的名称：{mapping.get('new_name_at_old_id')}，价格：¥{mapping.get('new_price_at_old_id')}）</small>
                    </div>
                    """
                
                html += "</div>"
            
            html += """
                <div class="section-title">详细字段变化记录</div>
            """
        
        reshuffling_product_ids = set()
        if reshuffling_info and reshuffling_info.get('is_reshuffling'):
            for group in reshuffling_info.get('groups', []):
                for mapping in group.get('mappings', []):
                    reshuffling_product_ids.add(str(mapping.get('original_id')))
        
        change_index = 0
        for change in changes:
            if 'is_reshuffling_item' in change:
                continue
            
            change_index += 1
            is_reshuffling_product = str(change.get('product_id', '')) in reshuffling_product_ids
            
            if 'change_type' in change and change['change_type'] in ['increase', 'decrease']:
                price_change_type = "涨价" if change['change_type'] == 'increase' else "降价"
                type_class = "type-price-up" if change['change_type'] == 'increase' else "type-price-down"
                price_class = "price-up" if change['change_type'] == 'increase' else "price-down"
                arrow = "↑" if change['change_type'] == 'increase' else "↓"
                product_url = change.get('product_url', change.get('upstream_url', ''))
                
                reshuffling_badge = '<span class="change-type" style="background-color: #171717; color: #fff; margin-left: 8px;">重组</span><span style="font-size: 11px; color: #737373; margin-left: 8px;">（修改内容可能不准确，仅参考）</span>' if is_reshuffling_product else ''
                
                html += f"""
                <div class="change-card">
                    <div class="change-header">
                        <span style="font-size: 13px; font-weight: 500; color: #171717;"><span style="color: #737373; margin-right: 8px;">#{change_index}</span>{change.get('product_name', 'N/A')}</span>
                        <div>
                            <span class="change-type {type_class}">{price_change_type}</span>{reshuffling_badge}
                        </div>
                    </div>
                    <div class="change-body">
                        <div class="change-row">
                            <span class="change-label">所属分组</span>
                            <span class="change-value">{change.get('group_name', 'N/A')}</span>
                        </div>
                        <div class="change-row">
                            <span class="change-label">产品 ID</span>
                            <span class="change-value">{change.get('product_id', 'N/A')}</span>
                        </div>
                        <div class="change-row">
                            <span class="change-label">原价</span>
                            <span class="change-value">¥{change.get('old_price', 'N/A')}</span>
                        </div>
                        <div class="change-row">
                            <span class="change-label">现价</span>
                            <span class="change-value">¥{change.get('new_price', 'N/A')}</span>
                        </div>
                        <div class="change-row">
                            <span class="change-label">变化幅度</span>
                            <span class="change-value price-change {price_class}">{arrow} ¥{abs(change.get('price_change', 0)):.2f}</span>
                        </div>
                        <div class="change-row">
                            <span class="change-label">购买链接</span>
                            <span class="change-value"><a href="{product_url}" class="change-link">查看产品</a></span>
                        </div>
                    </div>
                </div>
                """
            elif 'change_category' in change:
                category = change['change_category']
                if category == "新增":
                    type_class = "type-added"
                elif category == "删除":
                    type_class = "type-removed"
                else:
                    type_class = "type-modified"
                
                reshuffling_badge = '<span class="change-type" style="background-color: #171717; color: #fff; margin-left: 8px;">重组</span><span style="font-size: 11px; color: #737373; margin-left: 8px;">（修改内容可能不准确，仅参考）</span>' if is_reshuffling_product else ''
                
                field_display_name = get_field_display_name(change.get('field_name', 'N/A'))
                
                html += f"""
                <div class="change-card">
                    <div class="change-header">
                        <span style="font-size: 13px; font-weight: 500; color: #171717;"><span style="color: #737373; margin-right: 8px;">#{change_index}</span>{change.get('product_name', 'N/A')}</span>
                        <div>
                            <span class="change-type {type_class}">{category}</span>{reshuffling_badge}
                        </div>
                    </div>
                    <div class="change-body">
                        <div class="change-row">
                            <span class="change-label">上游</span>
                            <span class="change-value">{change.get('upstream_name', 'N/A')}</span>
                        </div>
                        <div class="change-row">
                            <span class="change-label">产品 ID</span>
                            <span class="change-value">{change.get('product_id', 'N/A')}</span>
                        </div>
                        <div class="change-row">
                            <span class="change-label">所属分组</span>
                            <span class="change-value">{change.get('group_name', 'N/A')}</span>
                        </div>
                        <div class="change-row">
                            <span class="change-label">修改字段</span>
                            <span class="change-value">{field_display_name}</span>
                        </div>
                """
                upstream_url = change.get('upstream_url', '')
                first_group_id = change.get('first_group_id', '')
                second_group_id = change.get('second_group_id', '')
                if upstream_url and first_group_id and second_group_id:
                    html += f"""
                        <div class="change-row">
                            <span class="change-label">产品链接</span>
                            <span class="change-value"><a href="{upstream_url}/cart?fid={first_group_id}&gid={second_group_id}" class="change-link">查看产品</a></span>
                        </div>
                    """
                
                if category == "修改":
                    html += f"""
                        <div class="change-row" style="flex-direction: column;">
                            <span class="change-label" style="margin-bottom: 8px;">旧值</span>
                            <div class="code-block">{self._format_value_for_email(change.get('old_value', ''), 1000)}</div>
                        </div>
                        <div class="change-row" style="flex-direction: column; margin-top: 12px;">
                            <span class="change-label" style="margin-bottom: 8px;">新值</span>
                            <div class="code-block">{self._format_value_for_email(change.get('new_value', ''), 1000)}</div>
                        </div>
                    """
                else:
                    html += f"""
                        <div class="change-row" style="flex-direction: column;">
                            <span class="change-label" style="margin-bottom: 8px;">值</span>
                            <div class="code-block">{self._format_value_for_email(change.get('new_value', change.get('value', '')), 1000)}</div>
                        </div>
                    """
                html += "</div></div>"
            else:
                html += f"""
                <div class="change-card">
                    <div class="change-header">
                        <span style="font-size: 13px; font-weight: 500; color: #171717;"><span style="color: #737373; margin-right: 8px;">#{change_index}</span>其他变化</span>
                        <span class="change-type type-modified">其他</span>
                    </div>
                    <div class="change-body">
                        <div class="code-block">{self._format_value_for_email(change, 1000)}</div>
                    </div>
                </div>
                """
        
        html += """
                <div class="footer">
                    <p>此邮件由 FuRuiORG 上游监控系统自动发送</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html


class UpstreamMonitor:
    """上游产品信息监控器"""
    
    @staticmethod
    def get_stock_notify_mode(config: Dict) -> str:
        """
        获取库存通知模式，支持新旧配置格式
        
        Args:
            config: 配置字典
            
        Returns:
            库存通知模式：'full', 'status_only', 或 'disabled'
        """
        stock_notify_mode = config.get('stock_notify_mode')
        if stock_notify_mode is None:
            # 向后兼容旧配置 compare_stock
            compare_stock = config.get('compare_stock', True)
            stock_notify_mode = "disabled" if not compare_stock else "full"
        return stock_notify_mode
    
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
        
        # 创建数据目录
        self.data_dir.mkdir(exist_ok=True)
        
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

    def _fetch_data(self, url: str, max_retries: int = 3, retry_delay: float = 1.0) -> dict:
        """
        获取上游数据，支持重试

        Args:
            url: API URL
            max_retries: 最大重试次数
            retry_delay: 基础重试延迟（秒），实际使用指数退避

        Returns:
            获取的数据

        Raises:
            Exception: 所有重试失败后抛出最后一次错误
        """
        headers = self.config.get("request_headers", {})
        timeout = self.config.get("timeout", 30)
        
        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                response = requests.get(url, headers=headers, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, json.JSONDecodeError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    actual_delay = retry_delay * (2 ** attempt)  # 指数退避
                    logger.warning(f"请求失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}")
                    print(f"请求失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}")
                    print(f"将在 {actual_delay:.1f} 秒后重试...")
                    time.sleep(actual_delay)
        
        logger.error(f"请求最终失败: {url}")
        raise last_error or Exception("未知错误")

    # 库存相关字段列表
    STOCK_FIELDS = {'qty', 'stock_control'}
    
    def _get_stock_status(self, value: Any) -> str:
        """
        获取库存状态
        
        Args:
            value: 库存值
            
        Returns:
            'in_stock' 或 'out_of_stock'
        """
        if value is None:
            return 'out_of_stock'
        try:
            num_value = float(value)
            return 'in_stock' if num_value > 0 else 'out_of_stock'
        except (ValueError, TypeError):
            return 'out_of_stock'
    
    def _compare_data(self, old_data: Any, new_data: Any, path: str = "", stock_notify_mode: str = "full") -> dict:
        """
        比较两个数据集的差异

        Args:
            old_data: 旧数据
            new_data: 新数据
            path: 当前路径（用于递归）
            stock_notify_mode: 库存通知模式
                - "full": 完全对比库存变化（默认）
                - "status_only": 只在库存状态变化时通知（有货↔无货）
                - "disabled": 不监控库存

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
                # 库存字段处理
                if key in self.STOCK_FIELDS:
                    if stock_notify_mode == "disabled":
                        continue
                    # status_only 模式下，新增字段也算状态变化（从无到有）
                diff["added"].append({
                    "path": f"{path}.{key}" if path else key,
                    "value": new_data[key]
                })
            
            # 删除的键
            for key in old_keys - new_keys:
                # 库存字段处理
                if key in self.STOCK_FIELDS:
                    if stock_notify_mode == "disabled":
                        continue
                    # status_only 模式下，删除字段也算状态变化
                diff["removed"].append({
                    "path": f"{path}.{key}" if path else key,
                    "value": old_data[key]
                })
            
            # 修改的键
            for key in old_keys & new_keys:
                # 库存字段特殊处理
                if key in self.STOCK_FIELDS:
                    if stock_notify_mode == "disabled":
                        diff["unchanged"] += 1
                        continue
                    
                    old_value = old_data[key]
                    new_value = new_data[key]
                    
                    # 值相同，跳过
                    if old_value == new_value:
                        diff["unchanged"] += 1
                        continue
                    
                    # status_only 模式：只在状态变化时记录
                    if stock_notify_mode == "status_only":
                        old_status = self._get_stock_status(old_value)
                        new_status = self._get_stock_status(new_value)
                        
                        if old_status == new_status:
                            # 状态未变化，跳过
                            diff["unchanged"] += 1
                            continue
                        
                        # 状态变化，记录并添加状态信息
                        diff["modified"].append({
                            "path": f"{path}.{key}" if path else key,
                            "old_value": old_value,
                            "new_value": new_value,
                            "stock_status_change": {
                                "old_status": old_status,
                                "new_status": new_status,
                                "change_text": "缺货" if new_status == "out_of_stock" else "补货"
                            },
                            "detail": {"added": [], "removed": [], "modified": [], "unchanged": 0}
                        })
                    else:
                        # full 模式：记录所有变化
                        diff["modified"].append({
                            "path": f"{path}.{key}" if path else key,
                            "old_value": old_value,
                            "new_value": new_value,
                            "detail": {"added": [], "removed": [], "modified": [], "unchanged": 0}
                        })
                    continue
                    
                sub_diff = self._compare_data(
                    old_data[key], 
                    new_data[key], 
                    f"{path}.{key}" if path else key,
                    stock_notify_mode=stock_notify_mode
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
            # 检测是否是 products 列表，需要按产品ID匹配而不是按索引
            is_products_list = path.endswith('.products') or path.endswith('products')
            
            if is_products_list:
                # 按 ID 匹配产品，避免顺序变化导致误报
                old_by_id = {}
                new_by_id = {}
                
                for item in old_data:
                    if isinstance(item, dict) and 'id' in item:
                        old_by_id[str(item['id'])] = item
                
                for item in new_data:
                    if isinstance(item, dict) and 'id' in item:
                        new_by_id[str(item['id'])] = item
                
                old_ids = set(old_by_id.keys())
                new_ids = set(new_by_id.keys())
                
                # 新增的产品
                for product_id in new_ids - old_ids:
                    new_item = new_by_id[product_id]
                    diff["added"].append({
                        "path": f"{path}[id:{product_id}]",
                        "value": new_item
                    })
                
                # 删除的产品
                for product_id in old_ids - new_ids:
                    old_item = old_by_id[product_id]
                    diff["removed"].append({
                        "path": f"{path}[id:{product_id}]",
                        "value": old_item
                    })
                
                # 修改的产品（同一个ID的产品内容变化）
                for product_id in old_ids & new_ids:
                    old_item = old_by_id[product_id]
                    new_item = new_by_id[product_id]
                    
                    sub_diff = self._compare_data(
                        old_item,
                        new_item,
                        f"{path}[id:{product_id}]",
                        stock_notify_mode=stock_notify_mode
                    )
                    
                    if sub_diff["added"] or sub_diff["removed"] or sub_diff["modified"]:
                        diff["modified"].append({
                            "path": f"{path}[id:{product_id}]",
                            "old_value": old_item,
                            "new_value": new_item,
                            "detail": sub_diff
                        })
                    else:
                        diff["unchanged"] += sub_diff["unchanged"]
            
            elif len(old_data) != len(new_data):
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
                        f"{path}[{i}]",
                        stock_notify_mode=stock_notify_mode
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
            logger.error(f"提取产品信息时出错：{str(e)}")
            print(f"提取产品信息时出错：{str(e)}")
        
        return products

    def _check_price_changes(self, old_products: List[Dict], new_products: List[Dict]) -> List[Dict]:
        """
        检查价格变化
        
        Args:
            old_products: 旧产品列表（已提取）
            new_products: 新产品列表（已提取）
            
        Returns:
            价格变化列表
        """
        changes = []
        
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
                        'upstream_name': new_product['upstream_name'],
                        'upstream_url': new_product['upstream_url'],
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

    def _detect_product_reshuffling(self, old_products: List[Dict], new_products: List[Dict],
                                    old_desc_map: Dict[str, str], new_desc_map: Dict[str, str]) -> Dict:
        """
        检测产品信息重组模式
        
        产品重组是指：多个产品之间发生了身份交换（名称、价格、配置等属性整体转移）
        典型特征：
        1. 同一分组内多个产品的ID发生了循环交换
        2. 产品的属性（名称、描述、价格等）从一个ID转移到了另一个ID
        
        Args:
            old_products: 旧产品列表（已提取）
            new_products: 新产品列表（已提取）
            old_desc_map: 旧产品描述映射
            new_desc_map: 新产品描述映射
            
        Returns:
            重组检测结果，包含是否检测到重组、重组详情等
        """
        result = {
            "is_reshuffling": False,
            "reshuffling_type": None,
            "groups": [],
            "summary": ""
        }
        
        old_by_id = {p['product_id']: p for p in old_products}
        new_by_id = {p['product_id']: p for p in new_products}
        
        old_by_group = {}
        for p in old_products:
            group_key = f"{p['first_group_id']}|{p['second_group_id']}"
            if group_key not in old_by_group:
                old_by_group[group_key] = {'name': p['group_name'], 'products': []}
            old_by_group[group_key]['products'].append(p)
        
        new_by_group = {}
        for p in new_products:
            group_key = f"{p['first_group_id']}|{p['second_group_id']}"
            if group_key not in new_by_group:
                new_by_group[group_key] = {'name': p['group_name'], 'products': []}
            new_by_group[group_key]['products'].append(p)
        
        reshuffling_groups = []
        
        for group_key in old_by_group:
            if group_key not in new_by_group:
                continue
            
            old_group = old_by_group[group_key]
            new_group = new_by_group[group_key]
            
            old_ids = set(p['product_id'] for p in old_group['products'])
            new_ids = set(p['product_id'] for p in new_group['products'])
            
            if old_ids != new_ids:
                continue
            
            if len(old_group['products']) < 2:
                continue
            
            id_mapping = {}
            identity_mappings = []
            
            for old_p in old_group['products']:
                old_id = old_p['product_id']
                new_p = new_by_id.get(old_id)
                
                if not new_p:
                    continue
                
                identity_match = None
                
                if new_p['product_name'] == old_p['product_name'] and new_p['original_price'] == old_p['original_price']:
                    continue
                
                for candidate_p in old_group['products']:
                    candidate_id = candidate_p['product_id']
                    if candidate_id == old_id:
                        continue
                    
                    new_candidate = new_by_id.get(candidate_id)
                    if not new_candidate:
                        continue
                    
                    name_match = old_p['product_name'] == new_candidate['product_name']
                    price_match = old_p['original_price'] == new_candidate['original_price']
                    
                    # 使用预构建的描述映射
                    old_desc = old_desc_map.get(old_id, '')
                    new_desc = new_desc_map.get(candidate_id, '')
                    desc_match = old_desc == new_desc
                    
                    if name_match and price_match and desc_match:
                        identity_match = candidate_id
                        break
                
                if identity_match:
                    id_mapping[old_id] = {
                        'target_id': identity_match,
                        'old_product': old_p,
                        'new_product': new_by_id[identity_match]
                    }
                    identity_mappings.append({
                        'original_id': old_id,
                        'original_name': old_p['product_name'],
                        'original_price': old_p['original_price'],
                        'moved_to_id': identity_match,
                        'new_name_at_old_id': new_p['product_name'],
                        'new_price_at_old_id': new_p['original_price']
                    })
            
            if len(identity_mappings) >= 2:
                is_circular = self._check_circular_mapping(identity_mappings)
                
                reshuffling_groups.append({
                    'group_name': old_group['name'],
                    'group_key': group_key,
                    'product_count': len(old_group['products']),
                    'reshuffled_count': len(identity_mappings),
                    'is_circular': is_circular,
                    'mappings': identity_mappings
                })
        
        if reshuffling_groups:
            total_reshuffled = sum(g['reshuffled_count'] for g in reshuffling_groups)
            has_circular = any(g['is_circular'] for g in reshuffling_groups)
            
            result['is_reshuffling'] = True
            result['reshuffling_type'] = 'circular_exchange' if has_circular else 'direct_swap'
            result['groups'] = reshuffling_groups
            
            if len(reshuffling_groups) == 1:
                g = reshuffling_groups[0]
                if g['is_circular']:
                    result['summary'] = f"检测到分组「{g['group_name']}」内 {g['reshuffled_count']} 个产品发生了循环交换"
                else:
                    result['summary'] = f"检测到分组「{g['group_name']}」内 {g['reshuffled_count']} 个产品发生了互换"
            else:
                group_names = '、'.join(g['group_name'] for g in reshuffling_groups)
                result['summary'] = f"检测到 {len(reshuffling_groups)} 个分组（{group_names}）共 {total_reshuffled} 个产品发生了重组"
        
        return result
    
    def _build_product_description_map(self, data: dict) -> Dict[str, str]:
        """构建产品ID到描述的映射，提高效率"""
        desc_map = {}
        try:
            if 'data' in data and 'first_group' in data['data']:
                for first_group in data['data']['first_group']:
                    if 'group' in first_group:
                        for group in first_group['group']:
                            if 'products' in group:
                                for product in group['products']:
                                    product_id = str(product.get('id', ''))
                                    desc_map[product_id] = product.get('description', '')
        except Exception:
            pass
        return desc_map
    
    def _check_circular_mapping(self, mappings: List[Dict]) -> bool:
        """检查映射是否形成循环"""
        if len(mappings) < 2:
            return False
        
        id_to_target = {m['original_id']: m['moved_to_id'] for m in mappings}
        
        visited = set()
        for start_id in id_to_target:
            if start_id in visited:
                continue
            
            chain = []
            current = start_id
            while current in id_to_target and current not in chain:
                chain.append(current)
                current = id_to_target[current]
            
            if current == start_id and len(chain) > 1:
                return True
            
            visited.update(chain)
        
        return False

    def _save_changes_to_db(self, changes: List[Dict], check_time: str):
        """
        保存变化记录到数据库
        
        Args:
            changes: 变化列表
            check_time: 检测时间
        """
        for change in changes:
            # 处理 old_value 和 new_value，确保可以存入数据库
            old_value = change.get("old_value") or change.get("old_price")
            new_value = change.get("new_value") or change.get("new_price")
            
            # 如果是字典或列表，转换为 JSON 字符串
            if isinstance(old_value, (dict, list)):
                old_value = json.dumps(old_value, ensure_ascii=False)
            if isinstance(new_value, (dict, list)):
                new_value = json.dumps(new_value, ensure_ascii=False)
            
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
                "old_value": old_value,
                "new_value": new_value,
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
        
        # 获取库存通知模式（支持新旧配置格式）
        stock_notify_mode = self.get_stock_notify_mode(self.config)
        
        mode_text = {
            "full": "完全对比库存",
            "status_only": "仅在库存状态变化时通知（有货↔无货）",
            "disabled": "已禁用库存对比"
        }
        print(f"  （库存模式：{mode_text.get(stock_notify_mode, stock_notify_mode)}）")
        
        diff = self._compare_data(old_data, new_data, stock_notify_mode=stock_notify_mode)
        
        # 预先提取产品信息和描述映射（避免重复计算）
        old_products = self._extract_products(old_data, name, base_url)
        new_products = self._extract_products(new_data, name, base_url)
        old_desc_map = self._build_product_description_map(old_data)
        new_desc_map = self._build_product_description_map(new_data)
        
        # 检查价格变化
        price_changes = self._check_price_changes(old_products, new_products)
        print(f"检测到 {len(price_changes)} 个产品价格变化")
        
        # 检测产品重组
        reshuffling_info = self._detect_product_reshuffling(
            old_products, new_products, old_desc_map, new_desc_map
        )
        if reshuffling_info.get('is_reshuffling'):
            print(f"⚠️ 检测到产品信息重组：{reshuffling_info.get('summary', '')}")
        
        print(f"检测到变化：")
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
                    # 匹配产品级别路径（新格式）: products[id:xxx] 或 data.first_group[数字].group[数字].products[id:xxx]
                    product_id_match = re.search(r'products\[id:([^\]]+)\]', path)
                    if product_id_match:
                        target_product_id = product_id_match.group(1)
                        
                        # 在数据中按ID查找产品
                        def find_product_by_id(data_full, target_id):
                            try:
                                if 'data' in data_full and 'first_group' in data_full['data']:
                                    for first_group in data_full['data']['first_group']:
                                        first_group_id = str(first_group.get('id', ''))
                                        if 'group' in first_group:
                                            for group in first_group['group']:
                                                second_group_id = str(group.get('id', ''))
                                                group_name = group.get('name', '未分组')
                                                if 'products' in group:
                                                    for product in group['products']:
                                                        if str(product.get('id', '')) == target_id:
                                                            return {
                                                                "type": "product",
                                                                "product_id": str(product.get('id', 'N/A')),
                                                                "product_name": product.get('name', 'N/A'),
                                                                "group_name": group_name,
                                                                "first_group_id": first_group_id,
                                                                "second_group_id": second_group_id
                                                            }
                            except Exception:
                                pass
                            return None
                        
                        # 优先从新数据中查找
                        result = find_product_by_id(new_data_full, target_product_id)
                        if result:
                            return result
                        
                        # 如果新数据中没有，从旧数据中查找
                        result = find_product_by_id(old_data_full, target_product_id)
                        if result:
                            return result
                    
                    # 匹配产品级别路径（旧格式兼容）: data.first_group[数字].group[数字].products[数字]
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
                self.notifier.send_change_email(all_changes, name, "数据", reshuffling_info)
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
            "summary": {
                "added_count": len(diff["added"]),
                "removed_count": len(diff["removed"]),
                "modified_count": len(diff["modified"]),
                "price_changes_count": len(price_changes),
                "is_reshuffling": reshuffling_info.get('is_reshuffling', False)
            },
            "price_changes": len(price_changes),
            "reshuffling_info": reshuffling_info if reshuffling_info.get('is_reshuffling') else None
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
                reshuffling_info = result.get("reshuffling_info")
                
                if reshuffling_info and reshuffling_info.get('is_reshuffling'):
                    print(f"⚠️ {upstream_name}: 检测到产品重组！{reshuffling_info.get('summary', '')}")
                else:
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
            print("6. 配置库存对比")
            print("7. 保存并退出")
            print("0. 退出不保存")
            
            choice = input("\n请输入选项 [0-7]: ").strip()
            
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
                self._config_stock_compare(config)
            elif choice == "7":
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
        
        # 显示库存对比配置
        stock_notify_mode = UpstreamMonitor.get_stock_notify_mode(config)
        
        mode_text = {
            "full": "完全对比",
            "status_only": "状态变化通知",
            "disabled": "已禁用"
        }
        print(f"\n库存监控: {mode_text.get(stock_notify_mode, stock_notify_mode)}")
        
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
    
    def _config_stock_compare(self, config: dict):
        """配置库存对比"""
        print("\n--- 配置库存监控 ---")
        
        # 获取当前模式（支持新旧配置格式）
        stock_notify_mode = UpstreamMonitor.get_stock_notify_mode(config)
        
        mode_text = {
            "full": "完全对比 - 监控所有库存变化",
            "status_only": "状态变化 - 仅在有货/缺货状态切换时通知",
            "disabled": "禁用 - 不监控库存变化"
        }
        
        print(f"\n当前设置: {mode_text.get(stock_notify_mode, stock_notify_mode)}")
        print("\n请选择库存监控模式：")
        print("  1. 完全对比 - 监控所有库存数量变化")
        print("  2. 状态变化 - 仅在库存状态切换时通知（有货↔缺货）")
        print("  3. 禁用监控 - 完全不监控库存变化")
        
        choice = input("\n请选择 [1-3]: ").strip()
        
        if choice == "1":
            config["stock_notify_mode"] = "full"
            # 清理旧配置
            config.pop("compare_stock", None)
            print("\n✓ 已设置为完全对比库存变化")
        elif choice == "2":
            config["stock_notify_mode"] = "status_only"
            config.pop("compare_stock", None)
            print("\n✓ 已设置为仅在库存状态变化时通知")
        elif choice == "3":
            config["stock_notify_mode"] = "disabled"
            config.pop("compare_stock", None)
            print("\n✓ 已禁用库存监控")
        else:
            print("\n无效选项，保持原设置")


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
