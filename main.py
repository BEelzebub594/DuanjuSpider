import tomllib
import os
import re
import aiohttp
import random
import asyncio
import json
from loguru import logger
from bs4 import BeautifulSoup
from utils.plugin_base import PluginBase
from utils.decorators import on_text_message
from WechatAPI import WechatAPIClient

class DuanjuSpider(PluginBase):
    description = "短剧搜索插件"
    author = "BEelzebub"
    version = "1.0.0"

    def __init__(self):
        super().__init__()
        self.plugin_dir = os.path.dirname(__file__)
        config_path = os.path.join(self.plugin_dir, "config.toml")
        self.urls_file = os.path.join(self.plugin_dir, "search_urls.json")
        
        try:
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
            config = config.get("DuanjuSpider", {})
            self.enable = config.get("enable", False)
            self.command = config.get("command", "短剧")
            self.whitelist_groups = config.get("whitelist_groups", [])
            
            # 从config.toml中读取URL配置
            config_base_urls = config.get("base_urls", ["https://a80.35240.com/search.php", "https://b.21410.com/search.php"])
            config_short_urls = config.get("short_urls", ["A80.CC", "A20.CC", "E50.CC", "47C.CC"])
            
            # 从JSON文件加载URL数据或创建默认值
            urls_data = self.load_urls(config_base_urls, config_short_urls)
            self.base_urls = urls_data.get("base_urls", config_base_urls)
            self.short_urls = urls_data.get("short_urls", config_short_urls)
            
        except Exception as e:
            logger.error(f"加载短剧插件配置文件失败: {str(e)}")
            self.enable = False
            self.command = "短剧"
            self.base_urls = ["https://a80.35240.com/search.php", "https://b.21410.com/search.php"]
            self.short_urls = ["A80.CC", "A20.CC", "E50.CC", "47C.CC"]
            self.whitelist_groups = []

    def load_urls(self, config_base_urls, config_short_urls):
        """从JSON文件加载URL列表，如果不存在则使用config.toml中的配置创建默认值"""
        default_urls = {
            "base_urls": config_base_urls,
            "short_urls": config_short_urls
        }
        
        if not os.path.exists(self.urls_file):
            with open(self.urls_file, 'w', encoding='utf-8') as f:
                json.dump(default_urls, f, ensure_ascii=False, indent=4)
            return default_urls
        
        try:
            with open(self.urls_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f'加载URL文件出错: {e}，使用config.toml中的配置')
            return default_urls

    def save_urls(self, urls_data):
        """保存URL列表到JSON文件"""
        try:
            with open(self.urls_file, 'w', encoding='utf-8') as f:
                json.dump(urls_data, f, ensure_ascii=False, indent=4)
            logger.info(f'URL列表已保存至 {self.urls_file}')
        except Exception as e:
            logger.error(f'保存URL文件出错: {e}')

    async def async_init(self):
        try:
            # 启动时更新URL列表
            await self.update_urls()
            logger.info("[短剧插件] 插件初始化完成")
        except Exception as e:
            logger.error(f"短剧插件异步初始化失败: {str(e)}")
            self.enable = False

    async def resolve_short_url(self, short_url, headers, max_retries=3):
        """解析短链接获取实际URL，处理HTTP 302跳转"""
        if not short_url.startswith('http'):
            short_url = f'http://{short_url}'
        
        retry_count = 0
        while retry_count < max_retries:
            try:
                logger.info(f'尝试解析短链接: {short_url}')
                async with aiohttp.ClientSession() as session:
                    async with session.get(short_url, headers=headers, timeout=10, allow_redirects=False) as response:
                        status_code = response.status
                        
                        if status_code in (301, 302, 303, 307, 308):
                            # 获取重定向URL
                            redirect_url = response.headers.get('Location')
                            logger.info(f'发现重定向: {status_code}, 目标URL: {redirect_url}')
                            
                            if redirect_url:
                                return redirect_url
                            else:
                                logger.warning(f'重定向响应中没有Location头: {response.headers}')
                        elif status_code == 200:
                            # 没有重定向但请求成功
                            logger.info(f'短链接没有重定向，返回原始URL: {short_url}')
                            return short_url
                        else:
                            logger.warning(f'短链接解析失败，HTTP状态码: {status_code}')
                
                retry_count += 1
                if retry_count < max_retries:
                    await asyncio.sleep(2)  # 等待一段时间后重试
                    
            except Exception as e:
                logger.error(f'解析短链接出错: {short_url}, 错误: {e}')
                retry_count += 1
                if retry_count < max_retries:
                    await asyncio.sleep(2)  # 等待一段时间后重试
                
        logger.error(f'短链接解析失败，已达到最大重试次数: {short_url}')
        return None

    async def update_urls(self):
        """更新URL列表，解析短链并保存"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        }
        
        # 加载URL数据
        urls_data = {
            "base_urls": self.base_urls,
            "short_urls": self.short_urls
        }
        
        # 规范化基础URL（用于比较）
        normalized_base_urls = [url.rstrip('/') for url in self.base_urls]
        
        # 存储已解析的URL
        new_urls = []
        
        logger.info('正在解析短链接...')
        for short_url in self.short_urls:
            logger.info(f'解析短链接: {short_url}')
            resolved_url = await self.resolve_short_url(short_url, headers)
            if resolved_url:
                logger.info(f'已解析为: {resolved_url}')
                
                # 确保URL包含search.php路径
                if not resolved_url.endswith('search.php'):
                    if resolved_url.endswith('/'):
                        resolved_url += 'search.php'
                    else:
                        resolved_url += '/search.php'
                
                # 检查是否为新URL
                normalized_url = resolved_url.rstrip('/')
                if normalized_url not in normalized_base_urls and normalized_url not in [u.rstrip('/') for u in new_urls]:
                    new_urls.append(resolved_url)
                    
        # 如果有新URL，添加并保存
        if new_urls:
            logger.info(f'新增 {len(new_urls)} 个搜索URL:')
            for url in new_urls:
                logger.info(f'- {url}')
                self.base_urls.append(url)
                normalized_base_urls.append(url.rstrip('/'))
            
            # 保存更新后的URL列表
            urls_data["base_urls"] = self.base_urls
            self.save_urls(urls_data)
        else:
            logger.info('没有新的URL需要添加')

    async def check_url_accessibility(self, url, headers):
        """测试URL是否可访问"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=5) as response:
                    return response.status == 200
        except:
            return False

    @on_text_message(priority=30)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return True
        content = str(message["Content"]).strip()
        chat_id = message["FromWxid"]
        sender = message["SenderWxid"]
        if not message["IsGroup"]:
            return True
        if chat_id not in self.whitelist_groups:
            return True
        if not content.startswith(self.command):
            return True
        drama_name = content[len(self.command):].strip()
        if not drama_name:
            await bot.send_at_message(chat_id, "请输入要搜索的剧名", [sender])
            return False
        try:
            results = await self.search_drama(drama_name)
            if not results:
                await bot.send_at_message(chat_id, f"未找到《{drama_name}》相关资源", [sender])
                return False
            response = f'《{drama_name}》搜索结果：\n\n'
            for result in results:
                response += f'标题：{result["title"]}\n网盘链接：{result["pan_link"]}\n\n'
            await bot.send_at_message(chat_id, response.strip(), [sender])
        except Exception as e:
            logger.error(f"短剧搜索异常: {str(e)}")
            await bot.send_at_message(chat_id, f"短剧搜索失败: {str(e)}", [sender])
        return False

    async def search_drama(self, keyword):
        logger.info(f'开始搜索短剧: {keyword}')
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive'
        }
        
        # 测试每个URL的可访问性
        for base_url in self.base_urls:
            logger.info(f'测试URL: {base_url}')
            if await self.check_url_accessibility(base_url, headers):
                logger.info(f'找到可用URL: {base_url}')
                try:
                    # 添加随机延迟，避免请求过于频繁
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    
                    url = f'{base_url}?q={keyword}'
                    logger.info(f'正在访问: {url}')
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, headers=headers, timeout=30) as response:
                            if response.status != 200:
                                logger.error(f"搜索页面请求失败，状态码: {response.status}")
                                continue
                            html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    links = soup.find_all('a', href=True)
                    results = []
                    for link in links:
                        href = link.get('href')
                        title = link.get('title', '')
                        title = re.sub(r'</?strong>', '', title)
                        if href and (href.startswith('http') or href.startswith('https')) and 'id=' in href:
                            pan_link = await self.get_pan_link(href, headers)
                            if pan_link:
                                results.append({'title': title, 'pan_link': pan_link})
                    logger.info(f'搜索完成，找到 {len(results)} 个有效结果')
                    if results:
                        return results
                except Exception as e:
                    logger.error(f'使用 {base_url} 搜索短剧时发生错误: {e}')
        
        logger.error('所有网站均无法访问或搜索失败')
        return None

    async def get_pan_link(self, url, headers):
        try:
            # 添加随机延迟，避免请求过于频繁
            await asyncio.sleep(random.uniform(0.5, 1.5))
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=30) as response:
                    if response.status != 200:
                        return None
                    html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            meta_desc = soup.find('meta', {'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                content = meta_desc['content']
                pan_link_match = re.search(r'链接：(https://pan\.quark\.cn/s/[a-zA-Z0-9]+)', content)
                if pan_link_match:
                    return pan_link_match.group(1)
        except Exception as e:
            logger.error(f'获取网盘链接时发生错误: {e}')
        return None