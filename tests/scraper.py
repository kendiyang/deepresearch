import urllib
import socks
import http.client
import logging
import traceback
from urllib.error import URLError
import ssl
from urllib.request import build_opener, HTTPHandler, HTTPSHandler

# logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - [%(levelname)s] - %(message)s')
logger = logging.getLogger(__name__)

def merge_dict(a, b):
    d = a.copy()
    d.update(b)
    return d

class SocksiPyConnection(http.client.HTTPConnection):
    def __init__(self, proxytype, proxyaddr, proxyport=None, rdns=True, username=None, password=None, *args, **kwargs):
        self.proxyargs = (proxytype, proxyaddr, proxyport, rdns, username, password)
        http.client.HTTPConnection.__init__(self, *args, **kwargs)

    def connect(self):
        logger.debug("SocksiPyConnection.connect: host=%s port=%s proxyargs=%s timeout=%s", self.host, self.port, self.proxyargs, getattr(self, 'timeout', None))
        try:
            self.sock = socks.socksocket()
            self.sock.setproxy(*self.proxyargs)
            if type(self.timeout) in (int, float):
                self.sock.settimeout(self.timeout)
            self.sock.connect((self.host, self.port))
            logger.debug("SocksiPyConnection: connected socket to %s:%s", self.host, self.port)
        except Exception as e:
            logger.exception("SocksiPyConnection.connect failed: %s", e)
            raise

class SocksiPyConnectionS(http.client.HTTPSConnection):
    def __init__(self, proxytype, proxyaddr, proxyport=None, rdns=True, username=None, password=None, *args, **kwargs):
        self.proxyargs = (proxytype, proxyaddr, proxyport, rdns, username, password)
        http.client.HTTPSConnection.__init__(self, *args, **kwargs)

    def connect(self):
        logger.debug("SocksiPyConnectionS.connect: host=%s port=%s proxyargs=%s timeout=%s", self.host, self.port, self.proxyargs, getattr(self, 'timeout', None))
        try:
            sock = socks.socksocket()
            sock.setproxy(*self.proxyargs)
            if type(self.timeout) in (int, float):
                sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))
            logger.debug("SocksiPyConnectionS: TCP connected, wrapping SSL")
            self.sock = ssl.wrap_socket(sock, self.key_file, self.cert_file)
            logger.debug("SocksiPyConnectionS: SSL wrap complete")
        except Exception as e:
            logger.exception("SocksiPyConnectionS.connect failed: %s", e)
            raise

class SocksiPyHandler(HTTPHandler, HTTPSHandler):
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kw = kwargs
        HTTPHandler.__init__(self)
        HTTPSHandler.__init__(self)
        # ensure attribute expected by HTTPSHandler / urllib
        self._context = None
        logger.debug("SocksiPyHandler init args=%s kwargs=%s", args, kwargs)

    def http_open(self, req):
        logger.debug("http_open called for %s", req.full_url)
        def build(host, port=None, timeout=0, **kwargs):
            kw = merge_dict(self.kw, kwargs)
            conn = SocksiPyConnection(*self.args, host=host, port=port, timeout=timeout, **kw)
            return conn

        return self.do_open(build, req)
    def https_open(self, req):
        logger.debug("https_open called for %s", req.full_url)
        def build(host, port=None, timeout=0, **kwargs):
            kw = merge_dict(self.kw, kwargs)
            conn = SocksiPyConnectionS(*self.args, host=host, port=port, timeout=timeout, **kw)
            return conn

        return self.do_open(build, req)

username = "DAVTUCTN"
password = "SEASA8HJ"
ip = "107.150.104.94"
port = 1558
proxy = "socks5://{username}:{password}@{ip}:{port}".format(username=username, password=password, ip=ip, port=port)
# socks.set_default_proxy(socks.SOCKS5, ip, port,username=username,password=password)
# socket.socket = socks.socksocket

url = 'https://mayips.com/'

try:
    req = urllib.request.Request(url=url, headers={'User-Agent':'Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36'})
    opener = build_opener(SocksiPyHandler(socks.SOCKS5, ip, port, username=username, password=password))
    logger.debug("Opening URL via SOCKS proxy: %s", url)
    response = opener.open(req)
    body = response.read().decode('utf-8')
    logger.debug("Response received, length=%d", len(body))
    print(body)
except URLError as e:
    logger.exception("URLError during opener.open: %s", e)
    print(e)
except Exception as e:
    logger.exception("Unexpected exception during request: %s", e)
    traceback.print_exc()