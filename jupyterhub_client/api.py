import uuid
import logging

import yarl
import aiohttp

logger = logging.getLogger(__name__)


class JupyterHubAPI:
    def __init__(self, hub_url, api_token):
        self.hub_url = yarl.URL(hub_url)
        self.api_url = self.hub_url / 'hub/api'
        self.api_token = api_token

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers={
            'Authorization': f'token {self.api_token}'
        })
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.session.close()

    async def get_user(self, username):
        async with self.session.get(self.api_url / 'users' / username) as response:
            if response.status == 200:
                return await response.json()
            elif response.status == 404:
                logger.info('username={self.username} does not exist')
                return None

    async def create_user(self, username):
        async with self.session.post(self.api_url / 'users' / username) as response:
            if response.status == 201:
                logger.info(f'created username={username}')
                return await response.json()
            elif response.status == 409:
                raise ValueError(f'username={username} already exists')
            print(response.status, await response.content.read())

    async def delete_user(self, username):
        async with self.session.delete(self.api_url / 'users' / username) as response:
            if response.status == 204:
                logger.info(f'deleted username={username}')
            elif response.status == 404:
                raise ValueError(f'username={username} does not exist cannot delete')

    async def create_server(self, username, user_options=None):
        user_options = user_options or {}
        async with self.session.post(self.api_url / 'users' / username / 'server') as response:
            if response.status == 400:
                raise ValueError(f'server for username={username} is already running')
            elif response.status == 201:
                return JupyterAPI(self.hub_url / 'user' / username, self.api_token)

    async def delete_server(self, username):
        await self.session.delete(self.api_url / 'users' / username / 'server')

    async def info(self):
        async with self.session.post(self.api_url / 'info') as response:
            return await response.json()

    async def list_users(self):
        async with self.session.get(self.api_url / 'users') as response:
            return await response.json()

    async def list_proxy(self):
        async with self.session.get(self.api_url / 'proxy') as response:
            return await response.json()


class JupyterAPI:
    def __init__(self, notebook_url, api_token):
        self.api_url = yarl.URL(notebook_url) / 'api'
        self.api_token = api_token

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers={
            'Authorization': f'token {self.api_token}'
        })
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.session.close()

    async def create_kernel(self):
        async with self.session.post(self.api_url / 'kernels') as response:
            return await response.json()

    async def list_kernels(self):
        async with self.session.get(self.api_url / 'kernels') as response:
            return await response.json()

    async def get_kernel(self, kernel_id):
        async with self.session.get(self.api_url / 'kernels' / kernel_id) as response:
            if response.status == 404:
                return None
            elif response.status == 200:
                return await response.json()

    async def delete_kernel(self, kernel_id):
        async with self.session.delete(self.api_url / 'kernels' / kernel_id) as response:
            if response.status == 404:
                raise ValueError(f'failed to delete kernel_id={kernel_id} does not exist')
            elif response.status == 204:
                return True


class JupyterKernelAPI:
    def __init__(self, kernel_url, api_token):
        self.api_url = kernel_url
        self.api_token = api_token

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers={
            'Authorization': f'token {self.api_token}'
        })
        self.websocket = await self.session.ws_connect(self.api_url / 'channels')
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.session.close()

    def request_execute_code(self, msg_id, username, code):
        return {
            "header": {
                "msg_id": msg_id,
                "username": username,
                "msg_type": "execute_request",
                "version": "5.2"
            },
            "metadata": {},
            "content": {
                "code": code,
                "silent": False,
                "store_history": True,
                "user_expressions": {},
                "allow_stdin": True,
                "stop_on_error": True
            },
            "buffers": [],
            "parent_header": {},
            "channel": "shell"
        }

    async def send_code(self, username, code):
        msg_id = str(uuid.uuid4())
        await self.websocket.send_json(self.request_execute_code(msg_id, username, code))
        async for msg_text in self.websocket:
            if msg_text.type != aiohttp.WSMsgType.TEXT:
                return False

            msg = msg_text.json()

            if 'parent_header' in msg and msg['parent_header'].get('msg_id') == msg_id:
                # These are responses to our request
                if msg['channel'] == 'iopub':
                    response = None
                    if msg['msg_type'] == 'execute_result':
                        response = msg['content']['data']['text/plain']
                    elif msg['msg_type'] == 'stream':
                        response = msg['content']['text']

                    if response:
                        return response