"""Container status, narrowed to what an IT operator needs to see.

The container runtime's list API returns far more than a health console should
ever publish: entrypoint commands, bind-mount sources, published ports, network
addresses and every label an image author cared to set. Rather than blocklisting
those, `_normalize` builds a fresh dict from an allowlist of fields — a new
field in the runtime's response can never appear in the API by accident.

Containers outside the ShopMock compose project are dropped before
normalization, so a co-tenant stack on the same host is never disclosed.
"""
import re
import time
from datetime import datetime, timezone

from django.conf import settings


class ContainerApiError(Exception):
    """The container status backend could not be read."""


# docker compose and podman compose label containers differently; the VM runs
# rootless podman through the Docker-compatible socket, so accept both.
_PROJECT_LABELS = ('com.docker.compose.project', 'io.podman.compose.project')
_SERVICE_LABELS = ('com.docker.compose.service', 'io.podman.compose.service')

# "Up 2 hours (healthy)" / "Up 5s (unhealthy)" / "Up 3s (health: starting)"
_HEALTH_RE = re.compile(r'\((?:health:\s*)?(unhealthy|healthy|starting)\)', re.IGNORECASE)
# "Exited (0) 3 hours ago"
_EXIT_RE = re.compile(r'Exited\s*\((\d+)\)')

_STATES_RUNNING = 'running'
_STATES_EXITED = ('exited', 'dead')


def _label(labels, names):
    for name in names:
        value = labels.get(name)
        if isinstance(value, str) and value:
            return value
    return ''


def _first_name(raw):
    names = raw.get('Names')
    if isinstance(names, list) and names and isinstance(names[0], str):
        return names[0].lstrip('/')
    name = raw.get('Name')
    return name.lstrip('/') if isinstance(name, str) else ''


def _health(status):
    match = _HEALTH_RE.search(status)
    return match.group(1).lower() if match else 'none'


def _exit_code(raw, state, status):
    if state not in _STATES_EXITED:
        return None
    code = raw.get('ExitCode')
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    match = _EXIT_RE.search(status)
    return int(match.group(1)) if match else None


def _created(raw, now):
    created = raw.get('Created')
    if not isinstance(created, int) or isinstance(created, bool):
        return None, None
    stamp = datetime.fromtimestamp(created, timezone.utc).isoformat()
    return stamp, max(0, now - created)


def _is_ok(state, health, exit_code):
    if state == _STATES_RUNNING:
        return health != 'unhealthy'
    if state in _STATES_EXITED:
        return exit_code == 0
    return False


def _normalize(raw, labels, now):
    state = str(raw.get('State') or 'unknown').lower()
    status = str(raw.get('Status') or '')
    health = _health(status)
    exit_code = _exit_code(raw, state, status)
    created, age = _created(raw, now)
    return {
        'id': str(raw.get('Id') or '')[:12],
        'name': _first_name(raw),
        'project': _label(labels, _PROJECT_LABELS),
        'service': _label(labels, _SERVICE_LABELS),
        'image': str(raw.get('Image') or ''),
        'state': state,
        'status': status,
        'health': health,
        'created': created,
        'age_seconds': age,
        'exit_code': exit_code,
        'ok': _is_ok(state, health, exit_code),
    }


def normalize_containers(raw, now=None):
    """Project-scoped, allowlisted view of a runtime container list."""
    if not isinstance(raw, list):
        return []
    project = settings.OE_PROJECT_NAME
    now = int(time.time()) if now is None else now

    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        labels = item.get('Labels')
        labels = labels if isinstance(labels, dict) else {}
        if _label(labels, _PROJECT_LABELS) != project:
            continue
        normalized.append(_normalize(item, labels, now))

    normalized.sort(key=lambda c: (c['service'], c['name']))
    return normalized


def summarize(containers):
    """Roll-up counts for the dashboard tiles.

    `failed` is the number an operator has to act on: running-but-unhealthy
    plus one-shot jobs that exited non-zero. `other` is everything neither
    running nor exited (created, paused, restarting, unknown). The stack is
    only `ok` when nothing has failed, nothing is pending, and it is not empty.
    """
    counts = {
        'total': len(containers),
        'running': 0, 'healthy': 0, 'unhealthy': 0, 'starting': 0,
        'exited_ok': 0, 'failed': 0, 'other': 0,
    }
    for container in containers:
        state, health = container['state'], container['health']
        if state == _STATES_RUNNING:
            counts['running'] += 1
            if health in ('healthy', 'unhealthy', 'starting'):
                counts[health] += 1
            if health == 'unhealthy':
                counts['failed'] += 1
        elif state in _STATES_EXITED:
            if container['exit_code'] == 0:
                counts['exited_ok'] += 1
            else:
                counts['failed'] += 1
        else:
            counts['other'] += 1

    counts['ok'] = bool(
        counts['total']
        and not counts['failed']
        and not counts['other']
        and not counts['starting']
    )
    return counts
