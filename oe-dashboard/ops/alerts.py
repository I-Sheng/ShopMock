"""Wazuh alerts, narrowed to what an IT operator needs to see.

A Wazuh alert document is a far richer object than a health console should ever
publish: `full_log` carries the verbatim log line (passwords, session cookies
and tokens routinely land there), `data.*` carries whatever the decoder lifted
out of it, and `previous_output` can carry several more lines of the same.

So the same rule as `containers.py` applies: `_normalize` builds a fresh dict
from an allowlist, and the raw `_source` never leaves this module. A new field
in a future Wazuh ruleset cannot reach the browser by accident.
"""

class AlertApiError(Exception):
    """The security alert backend could not be read."""


# The query asks OpenSearch for these and nothing else, so an unexpected field
# is dropped at the source as well as here.
SOURCE_FIELDS = (
    'timestamp', '@timestamp',
    'rule.id', 'rule.level', 'rule.description',
    'agent.id', 'agent.name',
    'location',
)

# Hard ceiling on the page size, independent of OE_ALERT_LIMIT: a misconfigured
# setting cannot turn this endpoint into a bulk export of the SIEM.
MAX_LIMIT = 100


def _bounded(limit):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return 1
    return max(1, min(limit, MAX_LIMIT))


# Rule levels and free text come from decoders and from the logged event itself,
# so both are treated as untrusted: levels are clamped to Wazuh's 0-15 scale and
# text is cut to a fixed width rather than passed through at whatever length an
# attacker-controlled log line happened to be.
MAX_LEVEL = 15
MAX_TEXT = 256


def _text(value):
    return value[:MAX_TEXT] if isinstance(value, str) else ''


def _level(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value.lstrip('-').isdigit():
            return None
        value = int(value)
    if not isinstance(value, int):
        return None
    return max(0, min(value, MAX_LEVEL))


def _section(source, name):
    value = source.get(name)
    return value if isinstance(value, dict) else {}


def _normalize(source):
    rule = _section(source, 'rule')
    agent = _section(source, 'agent')
    timestamp = source.get('timestamp') or source.get('@timestamp')
    return {
        'timestamp': _text(timestamp) or None,
        'rule_id': _text(rule.get('id')),
        'rule_level': _level(rule.get('level')),
        'rule_description': _text(rule.get('description')),
        'agent_id': _text(agent.get('id')),
        'agent_name': _text(agent.get('name')),
        'location': _text(source.get('location')),
    }


def normalize_alerts(hits, limit):
    """Allowlisted view of an OpenSearch hit list. The raw `_source` stays here."""
    if not isinstance(hits, list):
        return []
    alerts = []
    for item in hits[:_bounded(limit)]:
        if not isinstance(item, dict):
            continue
        alerts.append(_normalize(_section(item, '_source')))
    return alerts


# Wazuh's own severity bands. Operators read the tiles in these terms, and it
# keeps the endpoint from publishing a per-level histogram of the estate.
_BANDS = (('critical', 12), ('high', 8), ('medium', 4), ('low', 0))


def _band(level):
    for name, floor in _BANDS:
        if level >= floor:
            return name
    return None


def _empty_summary():
    summary = {'total': 0}
    summary.update({name: 0 for name, _ in _BANDS})
    summary['highest_level'] = None
    return summary


def empty_model():
    """What an operator sees before the first alert is ever indexed."""
    return {'summary': _empty_summary(), 'alerts': []}


def _total(hits, fallback):
    total = hits.get('total')
    if isinstance(total, dict):
        total = total.get('value')
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total
    return fallback


def _summarize(aggregations, hits, alerts):
    summary = _empty_summary()
    summary['total'] = _total(hits, len(alerts))

    buckets = _section(aggregations, 'severity').get('buckets')
    buckets = buckets if isinstance(buckets, list) else []
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        level = _level(bucket.get('key'))
        count = bucket.get('doc_count')
        if level is None or not isinstance(count, int) or isinstance(count, bool):
            continue
        summary[_band(level)] += count
        if summary['highest_level'] is None or level > summary['highest_level']:
            summary['highest_level'] = level
    return summary


def model_from_response(response, limit):
    """The only thing this module hands upward: summary counts + an alert page.

    Everything else OpenSearch returned — shard failures, timings, the raw
    documents — is dropped here rather than filtered downstream.
    """
    if not isinstance(response, dict):
        return empty_model()
    hits = _section(response, 'hits')
    alerts = normalize_alerts(hits.get('hits'), limit)
    return {
        'summary': _summarize(_section(response, 'aggregations'), hits, alerts),
        'alerts': alerts,
    }


def build_query(limit):
    """A bounded, newest-first search body. Never assembled from request input."""
    return {
        'size': _bounded(limit),
        'track_total_hits': True,
        '_source': list(SOURCE_FIELDS),
        'query': {'match_all': {}},
        # `unmapped_type` keeps the sort valid while no wazuh-alerts index has
        # been created yet; `_doc` makes the order of same-second alerts stable
        # between polls instead of shuffling under the operator.
        'sort': [
            {'timestamp': {'order': 'desc', 'unmapped_type': 'date'}},
            {'_doc': {'order': 'asc'}},
        ],
        # Severity counts come from the whole match set, not just the page above.
        # Wazuh rule levels are 0-15, so 16 buckets is the complete domain.
        'aggs': {
            'severity': {
                'terms': {'field': 'rule.level', 'size': 16, 'order': {'_key': 'desc'}},
            },
        },
    }
