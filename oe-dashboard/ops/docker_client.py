"""The one call this service makes against the container runtime.

`GET /containers/json?all=1` is a module constant, never assembled from request
input, so there is no path by which a caller can steer this client at another
endpoint — no `/exec`, no `/containers/{id}/json` (which would carry `Config.Env`),
no arbitrary Docker API proxying.

In the deployed stack `OE_CONTAINER_API` points at `oe-socket-proxy`, a
read-only, container-endpoints-only proxy that holds the socket mount; this
container never sees the socket. The `unix://` transport below exists for a
dev machine that would rather bind the socket directly, and is documented in
docker-compose.yml as the less-isolated option.
"""
import http.client
import json
import socket
from urllib.parse import urlsplit

from django.conf import settings

from .containers import ContainerApiError

_PATH = '/v1.41/containers/json?all=1'
_MAX_BYTES = 8 * 1024 * 1024


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path, timeout):
        super().__init__('localhost', timeout=timeout)
        self._socket_path = socket_path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


def _connection(endpoint, timeout):
    parts = urlsplit(endpoint)
    if parts.scheme in ('unix', 'unix+http', 'http+unix'):
        return _UnixHTTPConnection(parts.path, timeout)
    if parts.scheme == 'http':
        return http.client.HTTPConnection(
            parts.hostname, parts.port or 80, timeout=timeout)
    raise ContainerApiError(f'unsupported container API endpoint: {parts.scheme}://')


def list_containers():
    """Raw container list from the runtime. Normalization happens elsewhere."""
    endpoint = settings.OE_CONTAINER_API
    timeout = settings.OE_CONTAINER_API_TIMEOUT
    connection = _connection(endpoint, timeout)
    try:
        connection.request('GET', _PATH, headers={'Accept': 'application/json',
                                                  'Host': 'localhost'})
        response = connection.getresponse()
        body = response.read(_MAX_BYTES + 1)
        if len(body) > _MAX_BYTES:
            raise ContainerApiError('container API response too large')
        if response.status != 200:
            raise ContainerApiError(
                f'container API returned HTTP {response.status}')
        return json.loads(body)
    except ContainerApiError:
        raise
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        raise ContainerApiError(f'{endpoint}: {exc}')
    finally:
        connection.close()
