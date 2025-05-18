import tomllib
import os
import re
import aiohttp
import random
import asyncio
import json
import requests
import urllib.parse
import time
from loguru import logger
from bs4 import BeautifulSoup
from utils.plugin_base import PluginBase
from utils.decorators import on_text_message
from WechatAPI import WechatAPIClient

class DuanjuSpider(PluginBase):
    description = "短剧搜索插件"
    author = "BEelzebub"
    version = "1.2.0"

    def __init__(self):
        super().__init__()
        self.plugin_dir = os.path.dirname(__file__)
        config_path = os.path.join(self.plugin_dir, "config.toml")
        self.urls_file = os.path.join(self.plugin_dir, "search_urls.json")
        
        # 搜索结果缓存，格式为 {用户ID: {'results': [...], 'keyword': '...', 'timestamp': ...}}
        self.search_cache = {}
        # 缓存过期时间（秒）
        self.cache_expire_time = 300  # 5分钟
        
        # 加剧命令
        self.add_drama_command = "加剧"
        
        try:
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
            config = config.get("DuanjuSpider", {})
            self.enable = config.get("enable", False)
            self.command = config.get("command", "短剧")
            self.add_drama_command = config.get("add_drama_command", "加剧")
            self.whitelist_groups = config.get("whitelist_groups", [])
            self.max_results = config.get("max_results", 10)  # 最大显示结果数
            
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
            self.add_drama_command = "加剧"
            self.base_urls = ["https://a80.35240.com/search.php", "https://b.21410.com/search.php"]
            self.short_urls = ["A80.CC", "A20.CC", "E50.CC", "47C.CC"]
            self.whitelist_groups = []
            self.max_results = 10

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
            
    def _clean_expired_cache(self):
        """清理过期的缓存"""
        current_time = asyncio.get_event_loop().time()
        expired_keys = []
        
        for key, cache_data in self.search_cache.items():
            if current_time - cache_data['timestamp'] > self.cache_expire_time:
                expired_keys.append(key)
                
        for key in expired_keys:
            del self.search_cache[key]
            
        if expired_keys:
            logger.info(f"[短剧插件] 已清理 {len(expired_keys)} 条过期缓存")

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

    # 生成评论所需的commentKey
    def generate_comment_key(self):
        """
        生成评论所需的commentKey
        格式为：当前毫秒级时间戳.随机数(保留12位小数)
        """
        # 获取当前毫秒级时间戳
        timestamp = int(time.time() * 1000)
        # 生成0-1之间的随机数
        random_num = random.random()
        # 拼接为指定格式的字符串
        comment_key = f"{timestamp}.{str(random_num).split('.')[1][:12]}"
        return comment_key

    # 获取评论key
    async def get_comment_key(self):
        """
        从页面获取评论所需的key
        
        Returns:
            str: 评论key
        """
        if not hasattr(self, 'last_used_domain') or not self.last_used_domain:
            self.last_used_domain = "https://a80.35240.com"
            logger.warning(f"未找到可用域名，使用默认域名: {self.last_used_domain}")
        
        url = f"{self.last_used_domain}/?id=2"
        logger.info(f"获取评论key，访问URL: {url}")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as response:
                    if response.status != 200:
                        logger.error(f"获取评论页面失败，状态码: {response.status}")
                        return None
                    
                    html = await response.text()
                    
            # 使用正则表达式提取key
            key_pattern = r'action="[^"]*cmd\.php\?act=cmt&amp;postid=2&amp;key=([a-zA-Z0-9]+)"'
            key_match = re.search(key_pattern, html)
            
            if key_match:
                key = key_match.group(1)
                logger.info(f"成功获取评论key: {key}")
                return key
            else:
                # 尝试使用BeautifulSoup解析
                soup = BeautifulSoup(html, 'html.parser')
                form = soup.find('form', id='frmSumbit')
                
                if form and 'action' in form.attrs:
                    action_url = form['action']
                    key_match = re.search(r'key=([a-zA-Z0-9]+)', action_url)
                    
                    if key_match:
                        key = key_match.group(1)
                        logger.info(f"通过BeautifulSoup获取评论key: {key}")
                        return key
                
                logger.error("未能在页面中找到评论key")
                return None
                
        except Exception as e:
            logger.error(f"获取评论key时出错: {str(e)}")
            return None

    # 发送评论请求
    async def send_comment(self, content):
        """
        发送评论请求
        
        Args:
            content: 评论内容
        
        Returns:
            响应结果
        """
        # 如果没有可用域名，使用默认域名
        if not hasattr(self, 'last_used_domain') or not self.last_used_domain:
            self.last_used_domain = "https://a80.35240.com"
            logger.warning(f"未找到可用域名，使用默认域名: {self.last_used_domain}")
        
        # 获取评论key
        key = await self.get_comment_key()
        if not key:
            return {
                "success": False,
                "message": "获取评论key失败"
            }
        
        # 生成commentKey
        comment_key = self.generate_comment_key()
        
        # 请求URL
        url = f"{self.last_used_domain}/zb_system/cmd.php?act=cmt&postid=2&key={key}"
        logger.info(f"使用评论URL: {url}")
        
        # 请求头
        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Cookie": "timezone=8",
            "Origin": self.last_used_domain,
            "Referer": f"{self.last_used_domain}/?id=2",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest"
        }
        
        # 构建请求数据 - 按照正确的载荷格式
        data = {
            "name": "访客",
            "homepage": "http://undefined",
            "postid": "2",
            "content": content,
            "replyid": "0",
            "format": "json",
            "commentKey": comment_key
        }
        
        logger.info(f"发送评论请求数据: {data}")
        
        try:
            # 发送POST请求
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url,
                    headers=headers,
                    data=data,
                    timeout=10
                ) as response:
                    status = response.status
                    logger.info(f"评论请求响应状态码: {status}")
                    
                    # 获取响应内容
                    try:
                        # 尝试解析为JSON
                        result = await response.json()
                        logger.info(f"评论请求响应JSON: {result}")
                        
                        # 检查响应状态码
                        if status == 200:
                            return {
                                "success": True,
                                "data": result
                            }
                        else:
                            return {
                                "success": False,
                                "message": f"请求失败，状态码: {status}",
                                "response": result
                            }
                    except Exception as e:
                        # 如果不是JSON，获取文本内容
                        text = await response.text()
                        logger.info(f"评论请求响应内容前100字符: {text[:100]}")
                        
                        # 检查响应状态码
                        if status == 200 or status == 302:
                            # 检查是否包含成功提示
                            if "评论发表成功" in text or "提交成功" in text or "success" in text.lower():
                                return {
                                    "success": True,
                                    "data": "评论发表成功"
                                }
                            else:
                                # 如果状态码是200，认为可能成功了
                                if status == 200:
                                    return {
                                        "success": True,
                                        "data": "评论可能已提交，但无法确认结果"
                                    }
                                
                                return {
                                    "success": False,
                                    "message": f"评论提交失败，响应内容不包含成功提示",
                                    "response": text[:200]  # 只返回前200个字符，避免日志过长
                                }
                        else:
                            return {
                                "success": False,
                                "message": f"请求失败，状态码: {status}",
                                "response": text[:200]  # 只返回前200个字符
                            }
        except Exception as e:
            logger.error(f"发送评论请求时出错: {str(e)}")
            return {
                "success": False,
                "message": f"发送请求时出错: {str(e)}"
            }

    @on_text_message(priority=30)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        """处理用户消息"""
        if not self.enable:
            return True
            
        content = str(message["Content"]).strip()
        chat_id = message["FromWxid"]
        sender = message["SenderWxid"]
        
        # 检查是否为群聊
        if not message["IsGroup"]:
            return True
            
        # 检查群组白名单
        if chat_id not in self.whitelist_groups:
            return True
            
        # 定义用户缓存键（群ID+发送者ID）
        cache_key = f"{chat_id}_{sender}"
        
        # 清理过期缓存
        self._clean_expired_cache()
        
        # 检查是否为加剧请求
        if content.startswith(self.add_drama_command):
            logger.info(f"[短剧插件] 收到加剧请求: {content}")
            
            # 提取剧名
            drama_name = content[len(self.add_drama_command):].strip()
            if not drama_name:
                await bot.send_at_message(chat_id, "请输入要加入的剧名", [sender])
                return False
                
            # 执行加剧操作
            try:
                logger.info(f"[短剧插件] 开始加剧: {drama_name}")
                result = await self.send_comment(drama_name)
                
                # 根据HTTP状态码判断是否成功
                if isinstance(result, dict) and "status_code" in result:
                    status_code = result["status_code"]
                    if status_code == 200 or status_code == 302:
                        await bot.send_at_message(chat_id, f"加剧成功，请在1天后重新搜索《{drama_name}》", [sender])
                        logger.info(f"[短剧插件] 加剧成功: {drama_name}")
                    else:
                        await bot.send_at_message(chat_id, f"加剧失败: HTTP状态码 {status_code}", [sender])
                        logger.error(f"[短剧插件] 加剧失败: HTTP状态码 {status_code}")
                # 根据success字段判断是否成功
                elif isinstance(result, dict) and "success" in result:
                    if result["success"]:
                        await bot.send_at_message(chat_id, f"加剧成功，请在1天后重新搜索《{drama_name}》", [sender])
                        logger.info(f"[短剧插件] 加剧成功: {drama_name}")
                    else:
                        # 如果错误信息包含JSON解析错误，但状态码是200，认为是成功的
                        error_msg = result.get("message", "")
                        if "200" in error_msg and ("JSON" in error_msg or "mimetype" in error_msg):
                            await bot.send_at_message(chat_id, f"加剧成功，请在1天后重新搜索《{drama_name}》", [sender])
                            logger.info(f"[短剧插件] 加剧成功(忽略JSON错误): {drama_name}")
                        else:
                            await bot.send_at_message(chat_id, f"加剧失败: {result.get('message', '未知错误')}", [sender])
                            logger.error(f"[短剧插件] 加剧失败: {result.get('message', '未知错误')}")
                else:
                    await bot.send_at_message(chat_id, f"加剧失败: 返回结果格式异常", [sender])
                    logger.error(f"[短剧插件] 加剧失败: 返回结果格式异常 {result}")
            except Exception as e:
                logger.error(f"加剧操作异常: {str(e)}")
                await bot.send_at_message(chat_id, f"加剧操作异常: {str(e)}", [sender])
                
            return False
        
        # 检查是否为获取详情的请求（格式：短剧# 编号）
        if content.startswith(f"{self.command}#"):
            logger.info(f"[短剧插件] 收到详情请求: {content}")
            
            # 提取编号
            index_str = content[len(self.command)+1:].strip()
            logger.info(f"[短剧插件] 解析到编号: {index_str}")
            
            # 验证输入是否为数字
            if not index_str.isdigit():
                logger.warning(f"[短剧插件] 无效的编号格式: {index_str}")
                await bot.send_at_message(chat_id, f"请输入正确的编号，如：{self.command}# 1", [sender])
                return False
                
            index = int(index_str)
            
            # 检查该用户是否有缓存的搜索结果
            if cache_key not in self.search_cache:
                logger.warning(f"[短剧插件] 用户 {cache_key} 没有缓存的搜索结果")
                await bot.send_at_message(chat_id, "请先搜索短剧，再获取详情", [sender])
                return False
                
            # 获取缓存结果
            cached_data = self.search_cache[cache_key]
            results = cached_data['results']
            drama_name = cached_data['keyword']
            logger.info(f"[短剧插件] 获取到缓存结果，关键词: {drama_name}, 结果数: {len(results)}")
            
            # 验证编号是否有效
            if index < 1 or index > len(results):
                logger.warning(f"[短剧插件] 编号超出范围: {index}, 有效范围: 1-{len(results)}")
                await bot.send_at_message(chat_id, f"无效的编号，请输入1-{len(results)}之间的数字", [sender])
                return False
                
            # 获取选定的结果
            selected_result = results[index-1]
            logger.info(f"[短剧插件] 选择了结果: #{index}, 标题: {selected_result['title']}")
            
            # 发送详细信息
            detail_response = f"《{drama_name}》 - {selected_result['title']}\n"
            detail_response += f"网盘链接: {selected_result['pan_link']}\n"
            
            await bot.send_at_message(chat_id, detail_response, [sender])
            logger.info(f"[短剧插件] 已发送详情结果")
            
            return False
            
        # 检查消息是否以命令开头
        elif content.startswith(self.command):
            # 提取关键词
            drama_name = content[len(self.command):].strip()
            if not drama_name:
                await bot.send_at_message(chat_id, "请输入要搜索的剧名", [sender])
                return False
                
            # 执行搜索
            try:
                logger.info(f"[短剧插件] 收到搜索请求: {drama_name}")
                results = await self.search_drama(drama_name)
                
                # 修改判断条件，检查结果是否为空列表或None
                if not results or len(results) == 0:
                    # 未找到资源，提示可以使用加剧功能
                    response = f"未找到《{drama_name}》相关资源\n\n"
                    response += f"您可以使用 {self.add_drama_command} {drama_name} 来提交加剧请求"
                    logger.info(f"[短剧插件] 未找到资源，发送加剧提示: {response}")
                    await bot.send_at_message(chat_id, response, [sender])
                    return False
                    
                # 缓存结果
                self.search_cache[cache_key] = {
                    'results': results,
                    'keyword': drama_name,
                    'timestamp': asyncio.get_event_loop().time()
                }
                logger.info(f"[短剧插件] 已缓存搜索结果，用户: {cache_key}, 结果数: {len(results)}")
                
                # 组装第一步回复内容（只包含标题和编号）
                max_show = min(len(results), self.max_results)
                response = f'《{drama_name}》搜索结果：\n\n'
                
                for i, result in enumerate(results[:max_show], 1):
                    response += f'【{i}】{result["title"]}\n'
                
                # 如果结果超过最大显示数，添加提示
                if len(results) > max_show:
                    response += f"\n还有 {len(results) - max_show} 条结果未显示...\n"
                
                # 添加使用详情命令的提示
                response += f"\n获取网盘链接请发送：{self.command}# 编号 (例如: {self.command}# 1)"
                
                await bot.send_at_message(chat_id, response.strip(), [sender])
                logger.info(f"[短剧插件] 已发送搜索预览结果")
                
            except Exception as e:
                logger.error(f"短剧搜索异常: {str(e)}")
                await bot.send_at_message(chat_id, f"短剧搜索失败: {str(e)}", [sender])
                
            return False
            
        return True

    async def search_drama(self, keyword):
        logger.info(f'开始搜索短剧: {keyword}')
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive'
        }
        
        # 用于存储最后使用的可用URL的域名部分
        self.last_used_domain = None
        
        # 测试每个URL的可访问性
        for base_url in self.base_urls:
            logger.info(f'测试URL: {base_url}')
            if await self.check_url_accessibility(base_url, headers):
                logger.info(f'找到可用URL: {base_url}')
                try:
                    # 保存当前使用的域名
                    parsed_url = urllib.parse.urlparse(base_url)
                    self.last_used_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
                    logger.info(f'保存当前域名: {self.last_used_domain}')
                    
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
        return []  # 确保返回空列表而不是None

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
