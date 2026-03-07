#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库查询脚本 - 查看变化记录
"""

import sqlite3
import platform
from pathlib import Path


def get_db_path() -> str:
    """获取数据库路径"""
    if platform.system() == "Windows":
        return str(Path(__file__).parent / "upstream_monitor.db")
    else:
        return "/opt/ZJMFUpstreamMonitor/upstream_monitor.db"


def query_database():
    """查询数据库"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("=" * 80)
    print("变化记录（最近 20 条）")
    print("=" * 80)
    
    cursor.execute('''
        SELECT product_id, product_name, upstream_name, group_name,
               change_type, field_name, old_value, new_value, price_change, check_time
        FROM change_records 
        ORDER BY created_at DESC 
        LIMIT 20
    ''')
    
    rows = cursor.fetchall()
    if not rows:
        print("暂无变化记录")
    else:
        for row in rows:
            change_type = row['change_type']
            if change_type == 'increase':
                change_type_str = "涨价"
                arrow = "↑"
            elif change_type == 'decrease':
                change_type_str = "降价"
                arrow = "↓"
            elif change_type == '新增':
                change_type_str = "新增"
                arrow = "+"
            elif change_type == '删除':
                change_type_str = "删除"
                arrow = "-"
            else:
                change_type_str = "修改"
                arrow = "*"
            
            print(f"产品 ID: {row['product_id'] or 'N/A'}")
            print(f"产品名称：{row['product_name'] or 'N/A'}")
            print(f"上游：{row['upstream_name']}")
            print(f"分组：{row['group_name'] or 'N/A'}")
            print(f"变化类型：{arrow} {change_type_str}")
            
            if change_type in ['increase', 'decrease']:
                print(f"原价：¥{row['old_value']}")
                print(f"现价：¥{row['new_value']}")
                print(f"变化：{arrow} ¥{abs(row['price_change'] or 0):.2f}")
            else:
                print(f"字段：{row['field_name'] or 'N/A'}")
                print(f"旧值：{row['old_value'] or 'N/A'}")
                print(f"新值：{row['new_value'] or 'N/A'}")
            
            print(f"检测时间：{row['check_time']}")
            print("-" * 60)
    
    conn.close()


if __name__ == "__main__":
    query_database()
