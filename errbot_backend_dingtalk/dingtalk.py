#coding=utf-8

from functools import partial
from os import access
import pathlib
from sqlite3.dbapi2 import Error
from flask import Flask, jsonify, request, abort
from gevent import pywsgi
import json
import logging
from typing import Mapping

import requests
from errbot.backends.base import Identifier, Message, ONLINE, Person, Card
from errbot.core import ErrBot
from errbot.templating import tenv
import sqlite3
import sys
import time
import re

app = Flask(__name__)
LOG = logging.getLogger(__name__)

try:
    import errbot_backend_dingtalk
except ImportError:
    sys.path.append(str(pathlib.Path(__file__).parents[1]))


class DingtalkPerson(Person):

    text_pat = re.compile(r'^([\da-zA-Z]+)@([\da-zA-Z]+)$')

    def __init__(self, sender_id: str,
                        conversation_id: str,
                        sender_nick: str = '',
                        staff_id: str = '',
                        conversation_type: str = '2',
                        sender_corpid: str = '',
                        conversation_title: str = '',
                        **kwargs):
        """
            :param sender_id: 加密的发送者ID
            :param staff_id: 发送者在企业内部的userid （企业内部群才有）, 实际上现在都没有
            :param sender_nick: 发送者昵称
            :param conversation_id: 加密的会话ID
            :param conversation_type: 1-单聊、2-群聊
            :param conversation_title: 会话名称，仅群聊有
            :param sender_corpid: 企业ID, 实际上现在都没有
        """
        self.sender_id = sender_id
        self.staff_id = staff_id
        self.sender_corpid = sender_corpid
        self.sender_nick = sender_nick
        self.conversation_type = conversation_type
        self.conversation_id = conversation_id
        self.conversation_title = conversation_title
        self._opts = kwargs

    def __str__(self):
        return f'{self.sender_id}:{self.sender_nick}@{self.conversation_id}'
    
    @classmethod
    def fromString(cls, text_reprensentation: str):
        m = cls.text_pat.match(text_reprensentation)
        if m:
            return cls(
                sender_id=m.group(1),
                conversation_id=m.group(3),
                sender_nick=m.group(2)
            )
        else:
            return None

    @property
    def person(self):
        return self.sender_id

    @property
    def client(self):
        return self.sender_id

    @property
    def nick(self):
        return self.sender_nick

    @property
    def aclattr(self):
        return self.sender_id

    @property
    def fullname(self):
        return self.sender_id


class DingtalkRobot(DingtalkPerson):

    def __init__(self, robot_id, conversation_id):
        super().__init__(robot_id, conversation_id)


class DingtalkMessage(Message):

    def __init__(
        self,
        body: str = '',
        frm: Identifier = None,
        to: Identifier = None,
        parent: Message = None,
        delayed: bool = False,
        partial: bool = False,
        extras: Mapping = None,
        flow=None
    ):
        super().__init__(body, frm, to, parent, delayed, partial, extras, flow)

    @property
    def robot(self):
        return self._extras.get('chatbotUserId')
        
    @classmethod
    def fromMessageBody(cls, messageBody):
        """
            根据钉钉webhook接收到的请求体构建消息
        """
        if isinstance(messageBody, str):
            return DingtalkMessage(messageBody)
        else:
            from_person = DingtalkPerson(sender_id=messageBody['senderId'], staff_id=messageBody.get('senderStaffId'), 
                                            conversation_type=messageBody.get('conversationType'),
                                            conversation_id=messageBody.get('conversationId'), 
                                            sender_nick=messageBody.get('senderNick'), 
                                            sender_corp_id=messageBody.get('senderCorpId'),
                                            conversation_title=messageBody.get('conversationTitle'))
            to_person = DingtalkRobot(messageBody['chatbotUserId'], messageBody['conversationId'], messageBody['conversationTitle'])
            return DingtalkMessage(
                        messageBody['text']['content'], 
                        from_person, 
                        to_person, extras={'atUsers': messageBody.get('atUsers')})

    @property
    def atUsers(self):

        class AtUser(object):
            def __init__(self, dingtalk_id, staff_id):
                """
                    :param dingtalk_id: 加密的发送者id
                    :param staff_id: 发送者在企业内的userid， 企业内部群有
                """
                self.dingtalk_id = dingtalk_id
                self.staff_id = staff_id

        return [AtUser(item.get('dingtalkId'), item.get('staffId')) for item in self._extras.get('at_users', [])]
    

class DingtalkBackend(ErrBot):

    def __init__(self, config):
        super().__init__(config)
        LOG.debug('Initializing Dingtalk backend')

        self.webserver = None

        self.bot_identifier = DingtalkRobot('Norobot', 'Noconversation')
    
    def getSendWebHook(self, robot_id, conversation_id):
        """
            获取发送消息的webhook, 若设置了accesstoken则使用固定accesstoken,否则使用临时webhook
        """
        token = self.getAccessToken(robot_id, conversation_id)
        if not token:
            return f'https://oapi.dingtalk.com/robot/send?access_token={token}'
        else:
            return self.getTempWebhook(robot_id, conversation_id)
    
    def setTempWebhook(self, robot_id, conversation_id, webhook, expire_time):
        """
            设置临时webhook
        """
        try:
            with self.mutable('temp_robot_webhook') as store:
                store[(robot_id, conversation_id)] = (webhook, expire_time)
        except:
            return False
        else:
            return True
    
    def getTempWebhook(self, robot_id, conversation_id):
        """
            获取临时webhook
        """
        try:
            webhook, expire_time = self['temp_robot_webhook'][(robot_id, conversation_id)]
            # 假设时间误差最大10分钟, 超过10分钟认为hook已过期
            if expire_time > time.time() + 600 * 1000:
                return None
            else:
                return webhook
        except KeyError:
            return None

    def getConf(self, key, default=None):
        conf = getattr(self.bot_config, 'BOT_CONFIG', {})
        return conf.get(key, default)

    def getAccessToken(self, robot_id, conversation_id):
        """
            get access_token of dingtalk robot
        """
        try:
            return self['robot_token'][(robot_id, conversation_id)]
        except KeyError:
            return None

    def setAccessToken(self, robot_id, conversation_id, access_token):
        """
            set token of robot in conversation
        """
        try:
            with self.mutable('robot_token') as store:
                store[(robot_id, conversation_id)] = access_token
        except:
            return False
        else:
            return True

    def build_identifier(self, text_reprensentation: str) -> Identifier:
        return DingtalkPerson.fromString(text_reprensentation)

    def build_message(self, messageBody) -> DingtalkMessage:
        return DingtalkMessage.fromMessageBody(messageBody)

    def build_reply(self, msg: DingtalkMessage, text: str, private: bool=False, threaded: bool=False) -> DingtalkMessage:
        reply = self.build_message(text)
        reply.frm = msg.to
        reply.to = msg.frm
        return reply

    @property
    def rooms(self):
        return []

    def serve_forever(self):
        self.connect_callback()
        self.webserver = WebServer(self)
        self.webserver.run()

    def callback_message(self, msg: DingtalkMessage):
        super().callback_message(msg)

    def send_message(self, partial_message: DingtalkMessage):
        super().send_message(partial_message)
        conversation_id = partial_message.to.conversation_id
        robot_id = partial_message.to.sender_id
        LOG.info(f'Trying to send to [{partial_message.to.conversation_id}:{partial_message.to.conversation_title}], Message Body:\n{partial_message.body}')

        dingtalk_url = self.getSendWebhook(robot_id, conversation_id)
        if not dingtalk_url:
            raise ValueError('cannot get webhook')
        
        if self.getConf('keyword'):
            msgbody = partial_message.body + '\n' + self.getConf('keyword')
        else:
            msgbody = partial_message.body
        data = {
            'msgtype': 'text',
            'text': {
                'content': msgbody
            }
        }
        try:
            requests.post(dingtalk_url, json=data)
        except Error as e:
            logging.exception(e)
    
    def send_markdown(self, title, body, in_reply_to):
        """
            发送markdown消息
        """
        robot_id = in_reply_to.to.sender_id
        conversation_id = in_reply_to.to.conversation_id
        dingtalk_url = self.getSendWebhook(robot_id, conversation_id)
        if not dingtalk_url:
            raise ValueError('cannot get webhook')
        
        if self.getConf('keyword'):
            msgbody = body + '\n' + self.getConf('keyword')
        else:
            msgbody = body
        data = {
            'msgtype': 'markdown',
            'markdown': {
                'title': title,
                'text': msgbody
            }
        }
        try:
            requests.post(dingtalk_url, json=data)
        except Error as e:
            logging.exception(e)


    def query_room(self, room):
        return None

    def change_presence(self, status, message):
        pass

    @property
    def mode(self):
        return 'dingtalk'


class WebServer(object):

    def __init__(self, errbot):
        self._app: Flask = Flask(__name__)
        self._errbot = errbot

    def run(self, config=None):

        self._app.route('/robot/cicd', methods=['POST'])(self.cicdRobot)
        server = pywsgi.WSGIServer(
            (self._errbot.getConf('host', '0.0.0.0'),  self._errbot.getConf('port', 80)),
            self._app
        )
        server.serve_forever()

    def cicdRobot(self):
        import re
        req_body = json.loads(request.get_data())
        LOG.info(json.dumps(req_body, ensure_ascii=False))
        msg = self._errbot.build_message(req_body)
        session_webhook = req_body.get('sessionWebhook')
        expire_time = req_body.get('sessionWebhookExpiredTime')
        conversation_id = msg.frm.conversation_id
        robot_id = msg.robot
        # 保存会话的临时webhook用于响应消息
        if session_webhook and expire_time:
            self._errbot.setTempWebhook(robot_id, conversation_id, session_webhook, expire_time)
        '''
        if not access_token:
            # TODO: 使用sessionWebhook作为临时发送token,有效期90分钟, 超过有效期机器人将无法发送消息
            LOG.warning('access token is not set')
            token_pat = re.compile(r'(本群)?(机器人)?(T|t)oken(是|:)(.*)$')
            m = token_pat.match(msg.body)
            return_msg = "AccessToken 未设置， 机器人需要通过webhook进行回复, 请说:\n本群机器人Token是xxxx"
            if m:
                token = m.group(5).strip()
                if self._errbot.setAccessToken(robot_id, conversation_id, token):
                    return_msg = f"Token 设置成功，当前token为{token}"

            return jsonify({
                "msgtype": "text",
                "text": {
                    "content": return_msg
                }
            })
        '''
        self._errbot.callback_message(msg)
        return jsonify({
            "msgtype": "empty"
        })

