import json
import plugins
from bridge.reply import Reply, ReplyType
from bridge.context import ContextType
from channel.chat_message import ChatMessage
from plugins import *
from common.log import logger
from common.expired_dict import ExpiredDict
import os
from dbutils.pooled_db import PooledDB
import pymysql
from datetime import datetime, timezone

@plugins.register(
    name="membercheck",
    desire_priority=99,
    desc="A plugin to check member credit in group",
    version="0.0.1",
    author="davexxx",
)

class membercheck(Plugin):
    def __init__(self):
        super().__init__()
        try:
            curdir = os.path.dirname(__file__)
            config_path = os.path.join(curdir, "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            else:
                # 使用父类的方法来加载配置
                self.config = super().load_config()

                if not self.config:
                    raise Exception("config.json not found")
            
            # 设置事件处理函数
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
            # 从配置中提取所需的设置
            self.db_host = self.config.get("db_host","")
            self.db_user = self.config.get("db_user","")
            self.db_password = self.config.get("db_password","")
            self.db_name = self.config.get("db_name", "")
            self.credit_prefix = self.config.get("credit_prefix", "余额")
            self.reg_url = self.config.get("reg_url", "")
            self.suno_prefix = self.config.get("suno_prefix", [])
            self.params_cache = ExpiredDict(500)
            # 初始化成功日志

            # 初始化连接池
            self.pool = PooledDB(
                creator=pymysql,  # 使用链接数据库的模块
                maxconnections=5,  # 连接池允许的最大连接数，0和None表示不限制连接数
                mincached=1,  # 初始化时，链接池中至少创建的空闲的链接，0表示不创建
                maxcached=5,  # 链接池中最多闲置的链接，0和None不限制
                maxshared=3,  # 链接池中最多共享的链接数量，0和None表示全部共享。PS: 无用，因为pymysql和MySQLdb等模块的 threadsafety都为1，所有值无论设置为多少，_maxcached永远为0，所以永远是所有链接都共享。
                blocking=True,  # 连接池中如果没有可用连接后，是否阻塞等待。True，等待；False，不等待然后报错
                maxusage=None,  # 一个链接最多被重复使用的次数，None表示无限制
                setsession=[],  # 开始会话前执行的命令列表
                ping=0,  # ping MySQL服务端，检查是否服务可用。
                host=self.db_host,
                port=3306,
                user=self.db_user,
                password=self.db_password,
                database=self.db_name,
                charset='utf8mb4'
            )

            # 获取连接
            self.db = self.pool.connection()
            logger.info("数据库连接成功")
            logger.info("[membercheck] inited.")
        except Exception as e:
            # 初始化失败日志
            logger.warn(f"membercheck init failed: {e}")

    def on_handle_context(self, e_context: EventContext):
        context = e_context["context"]
        if context.type not in [ContextType.TEXT, ContextType.SHARING,ContextType.FILE,ContextType.IMAGE]:
            return
        msg: ChatMessage = e_context["context"]["msg"]
        user_id = msg.from_user_id
        content = context.content

        # 将用户信息存储在params_cache中
        if user_id not in self.params_cache:
            self.params_cache[user_id] = {}
            self.params_cache[user_id]['credit'] = 3
            logger.info('Added new user to params_cache. user id = ' + user_id)

        success, send_url, is_vip, balance, balance_draw,balance_video,balance_gpt4,vip_time,vip_tip = self.getcredit(user_id)

        if not success:
            tip = f"💡服务器无法连接，请稍后再试"
            reply = Reply(type=ReplyType.TEXT, content= tip)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return

        if e_context['context'].type == ContextType.TEXT:
             
            if content.startswith(self.credit_prefix):   
                if(is_vip):
                    vip_tip = f"您已经开通会员。会员到期时间为：{vip_time}"

                if send_url:
                    tip = f"💡没有查询到用户信息，请前往{self.reg_url}注册，并使用微信登录"
                    reply = Reply(type=ReplyType.TEXT, content= tip)
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                else:    
                    tip = f"💡尊敬的用户，您目前的余额如下：\n✨普通对话条数：{balance}\n✨绘画额度：{balance_draw}\n✨视频余额：{balance_video}\n✨高级版剩余字数：{balance_gpt4}\n✨会员状态：{vip_tip}"
                    reply = Reply(type=ReplyType.TEXT, content= tip)
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS

            elif any(content.startswith(prefix) for prefix in self.suno_prefix):
                if(is_vip):
                    e_context.action = EventAction.CONTINUE
                else:
                    tip = f"💡非常抱歉，此功能为会员功能，请加入会员再重试，感谢您的支持！"
                    reply = Reply(type=ReplyType.TEXT, content= tip)
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS

    def getcredit(self, user_id):
        try:
            # 创建cursor以执行查询
            cursor = self.db.cursor()
            # ... 剩下的代码部分保持不变

            # SQL 查询语句
            sql = "SELECT balance, balance_draw, balance_video, balance_gpt4, vip_expire_time FROM fox_chatgpt_user WHERE openid_mp = %s"
            
            # 执行SQL语句
            cursor.execute(sql, (user_id,))
            logger.info("SQL查询执行成功")

            # 获取所有记录列表
            results = cursor.fetchall()
            send_url = False
            balance = 0
            balance_draw = 0
            balance_video = 0
            balance_gpt4 = 0
            vip_expire_time = 0
            is_vip = False
            vip_tip = '没有开通会员'
            vip_time = ''
            if results:
                for row in results:
                    # 通过索引访问行中的数据
                    balance = row[0]
                    balance_draw = row[1]
                    balance_video = row[2]
                    balance_gpt4 = row[3]
                    vip_expire_time = row[4]

                    # 打印获得的值
                    logger.info(f"balance = {balance}")
                    logger.info(f"balance_draw = {balance_draw}")
                    logger.info(f"balance_video = {balance_video}")
                    logger.info(f"balance_gpt4 = {balance_gpt4}")
                    if vip_expire_time > 0:
                        # 将时间戳转换为 datetime 对象
                        is_vip = True
                        dt_object = datetime.fromtimestamp(vip_expire_time, tz=timezone.utc)
                        vip_time = dt_object.strftime('%Y-%m-%d %H:%M:%S')
                        logger.info(dt_object.strftime('%Y-%m-%d %H:%M:%S'))
            else:
                logger.info("没有查询到任何数据。")
                send_url = True
            success = True

        except Exception as e:
            logger.info(f"查询失败: {e}")
            success = False
    
        return success, send_url, is_vip, balance, balance_draw,balance_video,balance_gpt4,vip_time,vip_tip

           