#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clash订阅节点测速筛选系统
支持配置文件解析、字符替换、节点测速和API写入
"""

import asyncio
import aiohttp
import yaml
import base64
import json
import time
import re
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, parse_qs
import os
import sys

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('clash_tester.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

class ClashNodeTester:
    def __init__(self, config_file: str = 'config.txt', max_latency: int = 3000, should_keep_old = false):
        """
        初始化测速器

        Args:
            config_file: 配置文件路径
            max_latency: 最大延迟阈值（毫秒）
            should_keep_old: 是否保留旧配置
        """
        self.config_file = config_file
        self.max_latency = max_latency
        self.should_keep_old = should_keep_old
        self.session = None
        self.good_nodes = []

    async def __aenter__(self):
        """异步上下文管理器入口"""
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=10,
            ttl_dns_cache=300,
            use_dns_cache=True,
        )
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={'User-Agent': 'ClashNodeTester/1.0'}
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        if self.session:
            await self.session.close()

    def replace_placeholders(self, url: str) -> str:
        """
        替换URL中的占位符

        Args:
            url: 原始URL

        Returns:
            替换后的URL
        """
        now = datetime.now()
        replacements = {
            '%Y': now.strftime('%Y'),      # 4位数年份
            '%y': now.strftime('%y'),      # 2位数年份
            '%m': now.strftime('%m'),      # 月份
            '%d': now.strftime('%d'),      # 日期
            '%H': now.strftime('%H'),      # 小时
            '%M': now.strftime('%M'),      # 分钟
            '%S': now.strftime('%S'),      # 秒
            '%D': now.strftime('%Y%m%d'),  # 日期YYYYMMDD
            '%T': now.strftime('%H%M%S'),  # 时间HHMMSS
        }

        result = url
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)

        logger.debug(f"URL替换: {url} -> {result}")
        return result

    def read_config(self) -> List[str]:
        """
        读取配置文件中的订阅链接

        Returns:
            处理后的订阅链接列表
        """
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            urls = []
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):  # 忽略空行和注释
                    processed_url = self.replace_placeholders(line)
                    urls.append(processed_url)

            logger.info(f"成功读取 {len(urls)} 个订阅链接")
            return urls

        except FileNotFoundError:
            logger.error(f"配置文件 {self.config_file} 不存在")
            return []
        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")
            return []

    async def fetch_subscription(self, url: str) -> Optional[List[Dict]]:
        """
        获取订阅内容并解析节点

        Args:
            url: 订阅链接

        Returns:
            节点列表
        """
        try:
            logger.info(f"获取订阅: {url}")
            async with self.session.get(url) as response:
                if response.status != 200:
                    logger.warning(f"订阅获取失败 {url}: HTTP {response.status}")
                    return None

                content = await response.text()

                # 尝试解析为base64编码的内容
                try:
                    decoded_content = base64.b64decode(content).decode('utf-8')
                    content = decoded_content
                except:
                    pass  # 如果不是base64编码，使用原内容

                # 解析YAML格式的Clash配置
                try:
                    config = yaml.safe_load(content)
                    if isinstance(config, dict) and 'proxies' in config:
                        nodes = config['proxies']
                        logger.info(f"从订阅获取到 {len(nodes)} 个节点")
                        return nodes
                except yaml.YAMLError:
                    pass

                # 如果不是YAML格式，尝试解析为节点链接列表
                nodes = []
                for line in content.split('\n'):
                    line = line.strip()
                    if line and (line.startswith('ss://') or line.startswith('vmess://') or
                               line.startswith('trojan://') or line.startswith('vless://')):
                        node = self.parse_node_url(line)
                        if node:
                            nodes.append(node)

                if nodes:
                    logger.info(f"从订阅解析到 {len(nodes)} 个节点")
                    return nodes

                logger.warning(f"无法解析订阅内容: {url}")
                return None

        except Exception as e:
            logger.error(f"获取订阅失败 {url}: {e}")
            return None

    def parse_node_url(self, url: str) -> Optional[Dict]:
        """
        解析节点URL为标准格式

        Args:
            url: 节点URL

        Returns:
            节点配置字典
        """
        try:
            if url.startswith('ss://'):
                return self.parse_shadowsocks(url)
            elif url.startswith('vmess://'):
                return self.parse_vmess(url)
            elif url.startswith('trojan://'):
                return self.parse_trojan(url)
            elif url.startswith('vless://'):
                return self.parse_vless(url)
            else:
                return None
        except Exception as e:
            logger.debug(f"解析节点URL失败 {url}: {e}")
            return None

    def parse_shadowsocks(self, url: str) -> Optional[Dict]:
        """解析Shadowsocks URL"""
        try:
            # ss://base64(method:password)@server:port#name
            url = url[5:]  # 移除 ss://
            if '#' in url:
                url, name = url.rsplit('#', 1)
            else:
                name = "SS Node"

            if '@' in url:
                auth, server_port = url.split('@', 1)
                server, port = server_port.split(':', 1)

                # 解码认证信息
                try:
                    auth = base64.b64decode(auth).decode('utf-8')
                    method, password = auth.split(':', 1)
                except:
                    return None

                return {
                    'name': name,
                    'type': 'ss',
                    'server': server,
                    'port': int(port),
                    'cipher': method,
                    'password': password
                }
        except:
            return None

    def parse_vmess(self, url: str) -> Optional[Dict]:
        """解析VMess URL"""
        try:
            # vmess://base64(json)
            url = url[8:]  # 移除 vmess://
            config_json = base64.b64decode(url).decode('utf-8')
            config = json.loads(config_json)

            return {
                'name': config.get('ps', 'VMess Node'),
                'type': 'vmess',
                'server': config.get('add'),
                'port': int(config.get('port', 443)),
                'uuid': config.get('id'),
                'alterId': int(config.get('aid', 0)),
                'cipher': config.get('scy', 'auto'),
                'network': config.get('net', 'tcp'),
                'tls': config.get('tls') == 'tls'
            }
        except:
            return None

    def parse_trojan(self, url: str) -> Optional[Dict]:
        """解析Trojan URL"""
        try:
            # trojan://password@server:port#name
            parsed = urlparse(url)

            return {
                'name': parsed.fragment or 'Trojan Node',
                'type': 'trojan',
                'server': parsed.hostname,
                'port': parsed.port or 443,
                'password': parsed.username,
                'sni': parsed.hostname
            }
        except:
            return None

    def parse_vless(self, url: str) -> Optional[Dict]:
        """解析VLESS URL"""
        try:
            # vless://uuid@server:port?params#name
            parsed = urlparse(url)
            params = parse_qs(parsed.query)

            return {
                'name': parsed.fragment or 'VLESS Node',
                'type': 'vless',
                'server': parsed.hostname,
                'port': parsed.port or 443,
                'uuid': parsed.username,
                'flow': params.get('flow', [''])[0],
                'network': params.get('type', ['tcp'])[0],
                'tls': params.get('security', [''])[0] in ['tls', 'reality']
            }
        except:
            return None

    async def test_node_latency(self, node: Dict) -> Optional[int]:
        """
        测试节点延迟

        Args:
            node: 节点配置

        Returns:
            延迟值（毫秒），None表示不可访问
        """
        try:
            server = node.get('server')
            port = node.get('port')

            if not server or not port:
                return None

            start_time = time.time()

            # 简单的TCP连接测试
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(server, port),
                    timeout=5.0
                )
                writer.close()
                await writer.wait_closed()

                latency = int((time.time() - start_time) * 1000)
                logger.debug(f"节点 {node.get('name')} 延迟: {latency}ms")
                return latency

            except asyncio.TimeoutError:
                logger.debug(f"节点 {node.get('name')} 连接超时")
                return None

        except Exception as e:
            logger.debug(f"测试节点延迟失败 {node.get('name')}: {e}")
            return None

    async def test_nodes_batch(self, nodes: List[Dict], batch_size: int = 50) -> List[Dict]:
        """
        批量测试节点

        Args:
            nodes: 节点列表
            batch_size: 批次大小

        Returns:
            可用节点列表
        """
        good_nodes = []

        for i in range(0, len(nodes), batch_size):
            batch = nodes[i:i + batch_size]
            logger.info(f"测试批次 {i//batch_size + 1}: 节点 {i+1}-{min(i+batch_size, len(nodes))}")

            tasks = []
            for node in batch:
                task = self.test_node_latency(node)
                tasks.append((node, task))

            # 等待所有测试完成
            for node, task in tasks:
                try:
                    latency = await task
                    if latency is not None and latency <= self.max_latency:
                        node['latency'] = latency
                        good_nodes.append(node)
                        logger.info(f"✓ {node.get('name')} - {latency}ms")
                    else:
                        logger.warning(f"✗ {node.get('name')} - 延迟过高或不可访问")
                except Exception as e:
                    logger.warning(f"✗ {node.get('name')} - 测试异常: {e}")

            # 批次间休息
            if i + batch_size < len(nodes):
                await asyncio.sleep(1)

        return good_nodes

    async def read_last_nodes(self, filename: str = 'good_nodes.json') -> List[Dict]:
        """
        读取上次保存的节点
        """
        if not self.should_keep_old:
            return []
        
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)["nodes"]
        except Exception as e:
            logger.error(f"读取上次保存的节点失败: {e}")
            return []

    async def process_all_subscriptions(self) -> List[Dict]:
        """
        处理所有订阅

        Returns:
            所有可用节点列表
        """
        urls = self.read_config()
        if not urls:
            return []

        all_nodes = await self.read_last_nodes()
        logger.info(f"上次保存的节点: {len(all_nodes)} 个")

        for url in urls:
            nodes = await self.fetch_subscription(url)
            if nodes:
                all_nodes.extend(nodes)

        if not all_nodes:
            logger.warning("未获取到任何节点")
            return []

        logger.info(f"总计获取到 {len(all_nodes)} 个节点，开始测速...")

        # 去重
        unique_nodes = []
        seen = set()
        for node in all_nodes:
            key = (node.get('server'), node.get('port'), node.get('type'))
            if key not in seen:
                seen.add(key)
                unique_nodes.append(node)

        logger.info(f"去重后剩余 {len(unique_nodes)} 个节点")

        # 批量测试
        good_nodes = await self.test_nodes_batch(unique_nodes)

        # 按延迟排序
        good_nodes.sort(key=lambda x: x.get('latency', float('inf')))

        logger.info(f"筛选出 {len(good_nodes)} 个可用节点")
        return good_nodes

    def generate_clash_config(self, nodes: List[Dict]) -> str:
        """
        生成标准的Clash配置文件内容

        Args:
            nodes: 节点列表

        Returns:
            Clash配置文件内容
        """
        # 基础配置模板
        config = {
            'port': 7890,
            'socks-port': 7891,
            'allow-lan': False,
            'mode': 'Rule',
            'log-level': 'info',
            'external-controller': '127.0.0.1:9090',
            'dns': {
                'enable': True,
                'ipv6': False,
                'listen': '0.0.0.0:53',
                'enhanced-mode': 'fake-ip',
                'fake-ip-range': '198.18.0.1/16',
                'nameserver': [
                    '223.5.5.5',
                    '119.29.29.29',
                    '8.8.8.8'
                ],
                'fallback': [
                    'tls://1.1.1.1:853',
                    'tls://8.8.4.4:853'
                ]
            },
            'proxies': [],
            'proxy-groups': [
                {
                    'name': '🚀 节点选择',
                    'type': 'select',
                    'proxies': ['♻️ 自动选择', '🔯 故障转移', '🔮 负载均衡', 'DIRECT']
                },
                {
                    'name': '♻️ 自动选择',
                    'type': 'url-test',
                    'proxies': [],
                    'url': 'http://www.gstatic.com/generate_204',
                    'interval': 300
                },
                {
                    'name': '🔯 故障转移',
                    'type': 'fallback',
                    'proxies': [],
                    'url': 'http://www.gstatic.com/generate_204',
                    'interval': 300
                },
                {
                    'name': '🔮 负载均衡',
                    'type': 'load-balance',
                    'proxies': [],
                    'url': 'http://www.gstatic.com/generate_204',
                    'interval': 300
                }
            ],
            'rules': [
                'DOMAIN-SUFFIX,local,DIRECT',
                'IP-CIDR,127.0.0.0/8,DIRECT',
                'IP-CIDR,172.16.0.0/12,DIRECT',
                'IP-CIDR,192.168.0.0/16,DIRECT',
                'IP-CIDR,10.0.0.0/8,DIRECT',
                'IP-CIDR,17.0.0.0/8,DIRECT',
                'IP-CIDR,100.64.0.0/10,DIRECT',
                'DOMAIN-SUFFIX,cn,DIRECT',
                'GEOIP,CN,DIRECT',
                'MATCH,🚀 节点选择'
            ]
        }

        # 清理节点数据，移除延迟信息，确保格式正确
        proxy_names = []
        for node in nodes:
            # 创建节点副本并移除延迟信息
            clean_node = {k: v for k, v in node.items() if k != 'latency'}

            # 确保节点名称唯一
            base_name = clean_node.get('name', 'Unknown')
            name = base_name
            counter = 1
            while name in proxy_names:
                name = f"{base_name}_{counter}"
                counter += 1
            clean_node['name'] = name
            proxy_names.append(name)

            # 根据节点类型调整配置
            if clean_node.get('type') == 'ss':
                # Shadowsocks节点
                clean_node.setdefault('udp', True)
            elif clean_node.get('type') == 'vmess':
                # VMess节点
                clean_node.setdefault('network', 'tcp')
                clean_node.setdefault('cipher', 'auto')
                clean_node.setdefault('alterId', 0)
            elif clean_node.get('type') == 'trojan':
                # Trojan节点
                clean_node.setdefault('skip-cert-verify', False)
                clean_node.setdefault('udp', True)
            elif clean_node.get('type') == 'vless':
                # VLESS节点
                clean_node.setdefault('network', 'tcp')
                clean_node.setdefault('skip-cert-verify', False)

            config['proxies'].append(clean_node)

        # 为代理组添加节点
        for group in config['proxy-groups']:
            if group['name'] in ['♻️ 自动选择', '🔯 故障转移', '🔮 负载均衡']:
                group['proxies'] = proxy_names.copy()

        # 在节点选择组中添加所有节点
        config['proxy-groups'][0]['proxies'].extend(proxy_names)

        # 转换为YAML格式
        yaml_content = yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)

        # 添加注释头部
        header = f"""# Clash配置文件
# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# 节点数量: {len(nodes)}
# 平均延迟: {sum(node.get('latency', 0) for node in nodes) / len(nodes):.1f}ms

"""

        return header + yaml_content

    async def upload_config_to_api(self, clash_config: str, api_url: str, api_key: str = None):
        """
        使用PUT方式将Clash配置上传到API

        Args:
            clash_config: Clash配置内容
            api_url: API地址
            api_key: API密钥（可选）
        """
        try:
            headers = {'Content-Type': 'application/json'}
            if api_key:
                headers['Authorization'] = f'ApiKey {api_key}'

            data = {
                'content': clash_config
            }

            logger.info(f"使用PUT方式上传Clash配置到API: {api_url}")
            logger.info(f"配置大小: {len(clash_config)} 字符")

            async with self.session.put(api_url, json=data, headers=headers) as response:
                if response.status in [200, 201, 204]:
                    try:
                        result = await response.json()
                        logger.info(f"API上传成功: {result}")
                    except:
                        logger.info("API上传成功")
                else:
                    error_text = await response.text()
                    logger.error(f"API上传失败: HTTP {response.status} - {error_text}")

        except Exception as e:
            logger.error(f"API上传异常: {e}")

    def save_clash_config(self, clash_config: str, filename: str = 'clash_config.yaml'):
        """
        保存Clash配置到文件

        Args:
            clash_config: Clash配置内容
            filename: 文件名
        """
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(clash_config)

            logger.info(f"Clash配置已保存到文件: {filename}")

        except Exception as e:
            logger.error(f"保存Clash配置失败: {e}")

    def save_nodes_to_file(self, nodes: List[Dict], filename: str = 'good_nodes.json'):
        """
        保存节点到JSON文件（用于调试和备份）

        Args:
            nodes: 节点列表
            filename: 文件名
        """
        try:
            data = {
                'timestamp': datetime.now().isoformat(),
                'total_count': len(nodes),
                'average_latency': sum(node.get('latency', 0) for node in nodes) / len(nodes) if nodes else 0,
                'nodes': nodes
            }

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"节点数据已保存到文件: {filename}")

        except Exception as e:
            logger.error(f"保存节点文件失败: {e}")

async def main():
    """主函数"""
    # 从环境变量读取配置
    config_file = os.getenv('CONFIG_FILE', 'config.txt')
    max_latency = int(os.getenv('MAX_LATENCY', '3000'))
    api_url = os.getenv('API_URL')
    api_key = os.getenv('API_KEY')
    output_file = os.getenv('OUTPUT_FILE', 'clash_config.yaml')
    should_keep_old = os.getenv('SHOULD_KEEP_OLD', false)

    logger.info("=== Clash节点测速筛选系统启动 ===")
    logger.info(f"配置文件: {config_file}")
    logger.info(f"最大延迟: {max_latency}ms")
    logger.info(f"输出文件: {output_file}")
    logger.info(f"是否保留旧配置: {should_keep_old}")

    async with ClashNodeTester(config_file, max_latency, should_keep_old) as tester:
        # 处理所有订阅
        good_nodes = await tester.process_all_subscriptions()

        if not good_nodes:
            logger.warning("未找到可用节点")
            return

        # 生成Clash配置
        logger.info("生成Clash配置文件...")
        clash_config = tester.generate_clash_config(good_nodes)

        # 保存Clash配置到文件
        tester.save_clash_config(clash_config, output_file)

        # 保存节点数据到JSON文件（用于调试）
        tester.save_nodes_to_file(good_nodes)

        # 上传到API（如果配置了）
        if api_url:
            await tester.upload_config_to_api(clash_config, api_url, api_key)
        else:
            logger.info("未配置API地址，跳过上传")

        # 输出统计信息
        logger.info("=== 处理完成 ===")
        logger.info(f"可用节点数量: {len(good_nodes)}")
        if good_nodes:
            avg_latency = sum(node.get('latency', 0) for node in good_nodes) / len(good_nodes)
            logger.info(f"平均延迟: {avg_latency:.1f}ms")
            logger.info(f"最佳节点: {good_nodes[0].get('name')} ({good_nodes[0].get('latency')}ms)")
            logger.info(f"Clash配置文件大小: {len(clash_config)} 字符")

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if __name__ == "__main__":

    asyncio.run(main())

