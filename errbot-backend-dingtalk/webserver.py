#coding=utf-8

from functools import partial
from flask import Flask, jsonify, request, abort
from gevent import pywsgi
import json
import logging
from typing import Mapping
from errbot.backends.base import Identifier, Message, ONLINE, Person
from errbot.core import ErrBot

app = Flask(__name__)
LOG = logging.getLogger(__name__)

try:
    import errbot_backend_webapp
except ImportError:
    sys.path.append(str(pathlib.Path(__file__).parents[1]))
finally:
    from errbot_backend_webapp.config import (
        DingtalkConfig
    )

class DingtalkPerson(Person):

    def __init__(self, sender_id: str, 
                        staff_id: str, 
                        conversation_type: str,
                        conversation_id: str, 
                        sender_nick: str,
                        sender_corpid: str =  '',
                        conversation_title: str = '',
                        **kwargs):
        """
            :param sender_id: 加密的发送者ID
            :param staff_id: 发送者在企业内部的userid （企业内部群才有）
            :param sender_nick: 发送者昵称
            :param conversation_id: 加密的会话ID
            :param conversation_type: 1-单聊、2-群聊
            :param conversation_title: 会话名称，仅群聊有
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
        return self.sender_id

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
        return self.staff_id
    
    @property
    def fullname(self):
        return self.sender_id
    

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
        flow=None,
    ):
        super().__init__(body, frm, to, parent, delayed, partial, extras, flow)

    @property
    def robot(self):
        return self._extras.get('chatbotUserId')

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
    
    def build_identifier(self, text_reprensentation: str) -> Identifier:
        return DingtalkPerson(text_reprensentation)

    def build_message(self, text:str) -> DingtalkMessage:
        return DingtalkMessage(text)
    
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
        self.webserver.run(self.bot_config)
    
    def callback_message(self, msg: DingtalkMessage):
        super().callback_message(msg)
    
    def send_message(self, partial_message: DingtalkMessage):
        super().send_message(partial_message)
        conversation_id = partial_message.to.conversation_id
        robot_id = partial_message.robot
    
        #TODO: get access token from database
        dingtalk_url = None
        #TODO: sent message body



class WebServer(object):

    def __init__(self, errbot):
        self._app: Flask = Flask(__name__)
        self._errbot = errbot
    
    def run(self, config=None):

        self._app.route('/robot/cicd', methods=['POST'])(self.cicdRobot)
        server = pywsgi.WSGIServer(
            (config.host, config.port),
            self._app
        )
        server.serve_forever()

    def cicdRobot(self):
        req_body = json.loads(request.get_data())
        msg = self._errbot.build_message(req_body)
        self._errbot.callback_message(msg)
        return jsonify({
            "msgtype": "empty"
        })

