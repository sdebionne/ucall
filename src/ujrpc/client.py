import json
import errno
import socket
import random
import base64
from io import BytesIO
from typing import Union
import numpy as np
from PIL import Image


def _socket_is_closed(sock: socket.socket) -> bool:
    """
    Returns True if the remote side did close the connection
    """
    if sock is None:
        return True
    try:
        buf = sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        if buf == b'':
            return True
    except BlockingIOError as exc:
        if exc.errno != errno.EAGAIN:
            raise
    return False


def _make_tcp_socket(ip: str, port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((ip, port))
    return sock


def _recvall(sock, buffer_size=4096):
    data = b''
    while True:
        chunk = sock.recv(buffer_size)
        if not chunk:
            break
        data += chunk
    return data


class Response:
    data: json

    def __init__(self, data):
        self.data = data

    def raise_or_get(self) -> Union[bool, int, float, str]:
        self.raise_error()
        return self.data['result']

    def raise_error(self):
        if 'error' in self.data:
            raise RuntimeError(self.data['error'])

    def as_bytes(self) -> bytes:
        return base64.b64decode(self.raise_or_get())

    def as_numpy(self) -> np.ndarray:
        buf = BytesIO(self.as_bytes())
        return np.load(buf, allow_pickle=True)

    def as_image(self) -> Image.Image:
        buf = BytesIO(self.as_bytes())
        return Image.open(buf)


class Request:
    data: dict
    packed: dict

    def __init__(self, json):
        self.data = json
        self.packed = self.pack(json)

    def _pack_numpy(self, array):
        buf = BytesIO()
        np.save(buf, array)
        buf.seek(0)
        return base64.b64encode(buf.getvalue()).decode()

    def _pack_pillow(self, image):
        buf = BytesIO()
        if not image.format:
            image.format = 'tiff'
        image.save(buf, image.format,  compression='raw', compression_level=0)
        buf.seek(0)
        return base64.b64encode(buf.getvalue()).decode()

    def pack(self, req):
        keys = None
        if isinstance(req['params'], dict):
            keys = req['params'].keys()
        else:
            keys = range(0, len(req['params']))

        for k in keys:
            if isinstance(req['params'][k], np.ndarray):
                req['params'][k] = self._pack_numpy(req['params'][k])

            elif isinstance(req['params'][k], Image.Image):
                req['params'][k] = self._pack_pillow(req['params'][k])

        return req


class Client:
    """JSON-RPC Client that uses classic sync Python `requests` to pass JSON calls over HTTP"""
    uri: str
    port: int
    sock: socket
    use_http: bool
    http_template: str

    def __init__(self, uri: str = '127.0.0.1', port: int = 8545, use_http: bool = True) -> None:
        self.uri = uri
        self.port = port
        self.use_http = use_http
        self.sock = None
        self.http_template = f'POST / HTTP/1.1\r\nHost: {uri}:{port}\r\nUser-Agent: py-ujrpc\r\nAccept: */*\r\nConnection: keep-alive\r\nContent-Length: %i\r\nContent-Type: application/json\r\n\r\n'

    def __getattr__(self, name):
        def call(*args, **kwargs):
            params = kwargs
            if len(args) != 0:
                assert len(
                    kwargs) == 0, 'Can\'t mix positional and keyword parameters!'
                params = args

            return self.__call__({
                'method': name,
                'params': params,
                'jsonrpc': '2.0',
            })

        return call

    def _send(self, json_data: dict):
        json_data['id'] = random.randint(1, 2**16)
        req_obj = Request(json_data)
        request = json.dumps(req_obj.packed)
        if self.use_http:
            request = self.http_template % (len(request)) + request

        self.sock = _make_tcp_socket(self.uri, self.port) if _socket_is_closed(
            self.sock) else self.sock
        self.sock.send(request.encode())

    def _recv(self) -> Response:
        response_bytes = _recvall(self.sock)
        response = None
        if self.use_http:
            response = json.loads(
                response_bytes[response_bytes.index(b'\r\n\r\n'):])
        else:
            response = json.loads(response_bytes)
        return Response(response)

    def __call__(self, jsonrpc: object) -> Response:
        self._send(jsonrpc)
        return self._recv()
