"""Wazuh alert shaping — the query that goes out and the model that comes back.

Same contract as `test_containers.py`, applied to the SIEM: these tests pin the
shape that IS exposed (timestamp, rule id/level/description, agent id/name,
location) and, at least as importantly, everything a Wazuh alert document
carries that must NOT be — `full_log`, decoded `data.*`, the raw `_source`, the
predecoded credentials some decoders lift out of a log line.
"""
import json

from django.test import SimpleTestCase, override_settings

from ops.alerts import (MAX_LIMIT, build_query, empty_model, model_from_response,
                        normalize_alerts)

# What a Wazuh 4.8 alert actually looks like on the wire, including the fields
# that must never reach the browser. Values are marked so a leak is unambiguous.
LEAKY = {
    'full_log': 'sshd[1]: Accepted password for root LEAK-FULLLOG SUPERSECRET-LAB-TOKEN',
    'previous_output': 'LEAK-PREVIOUS',
    'data': {'srcip': '10.202.0.9', 'srcuser': 'LEAK-DATA', 'password': 'LEAK-PASSWORD'},
    'predecoder': {'hostname': 'LEAK-PREDECODER'},
    'decoder': {'name': 'LEAK-DECODER'},
    'manager': {'name': 'LEAK-MANAGER'},
    'input': {'type': 'LEAK-INPUT'},
    'GeoLocation': {'city_name': 'LEAK-GEO'},
    'syscheck': {'path': '/home/shop/ShopMock/.env', 'sha256_after': 'LEAK-SYSCHECK'},
}


def hit(*, level=7, rule_id='5716', description='sshd: authentication failed',
        agent_id='001', agent_name='edge', location='/var/log/auth.log',
        timestamp='2026-09-14T18:03:11.114+0000', extra=None):
    source = {
        'timestamp': timestamp,
        '@timestamp': timestamp,
        'rule': {'id': rule_id, 'level': level, 'description': description,
                 'groups': ['syslog', 'sshd'], 'mail': False},
        'agent': {'id': agent_id, 'name': agent_name, 'ip': '10.202.0.9'},
        'location': location,
        **LEAKY,
    }
    source.update(extra or {})
    return {'_index': 'wazuh-alerts-4.x-2026.09.14', '_id': 'LEAK-DOCID',
            '_score': None, 'sort': [1757873000000], '_source': source}


@override_settings(OE_ALERT_LIMIT=25)
class QueryTests(SimpleTestCase):
    def test_query_is_bounded_by_the_configured_limit(self):
        self.assertEqual(build_query(10)['size'], 10)

    def test_limit_is_clamped_to_a_hard_ceiling(self):
        self.assertEqual(build_query(10_000)['size'], MAX_LIMIT)

    def test_limit_is_clamped_upward_from_nonsense(self):
        self.assertEqual(build_query(0)['size'], 1)
        self.assertEqual(build_query(-5)['size'], 1)

    def test_newest_first_with_a_deterministic_tiebreak(self):
        sort = build_query(5)['sort']

        self.assertEqual(sort[0]['timestamp']['order'], 'desc')
        # Alerts minted in the same second must not reorder between polls.
        self.assertEqual(sort[1], {'_doc': {'order': 'asc'}})

    def test_query_asks_only_for_the_allowlisted_source_fields(self):
        self.assertEqual(sorted(build_query(5)['_source']), [
            '@timestamp', 'agent.id', 'agent.name', 'location',
            'rule.description', 'rule.id', 'rule.level', 'timestamp',
        ])

    def test_severity_aggregation_is_bounded(self):
        aggs = build_query(5)['aggs']

        self.assertEqual(aggs['severity']['terms']['field'], 'rule.level')
        self.assertLessEqual(aggs['severity']['terms']['size'], 16)


class NormalizeTests(SimpleTestCase):
    def test_alert_is_rebuilt_from_the_allowlist(self):
        [alert] = normalize_alerts([hit()], 10)

        self.assertEqual(alert, {
            'timestamp': '2026-09-14T18:03:11.114+0000',
            'rule_id': '5716',
            'rule_level': 7,
            'rule_description': 'sshd: authentication failed',
            'agent_id': '001',
            'agent_name': 'edge',
            'location': '/var/log/auth.log',
        })

    def test_no_log_content_or_document_metadata_survives(self):
        serialized = json.dumps(normalize_alerts([hit()], 10))

        for marker in ('LEAK-FULLLOG', 'LEAK-PREVIOUS', 'LEAK-DATA', 'LEAK-PASSWORD',
                       'LEAK-PREDECODER', 'LEAK-DECODER', 'LEAK-MANAGER', 'LEAK-INPUT',
                       'LEAK-GEO', 'LEAK-SYSCHECK', 'LEAK-DOCID', 'SUPERSECRET-LAB-TOKEN',
                       '_source', 'wazuh-alerts-4.x'):
            self.assertNotIn(marker, serialized)

    def test_page_is_bounded_even_if_the_backend_overruns(self):
        self.assertEqual(len(normalize_alerts([hit()] * 50, 5)), 5)
        self.assertEqual(len(normalize_alerts([hit()] * 500, 10_000)), MAX_LIMIT)

    def test_missing_and_malformed_documents_degrade_instead_of_raising(self):
        hits = [
            {'_source': {}},
            {'_source': {'rule': 'not-a-dict', 'agent': ['nope'], 'location': 42}},
            'not-a-hit',
            {'no_source': True},
        ]

        alerts = normalize_alerts(hits, 10)

        self.assertEqual(len(alerts), 3)
        self.assertEqual(alerts[0], {
            'timestamp': None, 'rule_id': '', 'rule_level': None,
            'rule_description': '', 'agent_id': '', 'agent_name': '', 'location': '',
        })
        self.assertEqual(alerts[1]['rule_id'], '')
        self.assertEqual(alerts[1]['location'], '')

    def test_level_is_coerced_and_clamped_to_the_wazuh_range(self):
        levels = [normalize_alerts([hit(level=v)], 1)[0]['rule_level']
                  for v in ('12', 12, 99, -3, True, None, 'high')]

        self.assertEqual(levels, [12, 12, 15, 0, None, None, None])

    def test_free_text_fields_are_truncated(self):
        [alert] = normalize_alerts(
            [hit(description='x' * 900, location='/var/log/' + 'y' * 900)], 1)

        self.assertEqual(len(alert['rule_description']), 256)
        self.assertEqual(len(alert['location']), 256)

    def test_timestamp_falls_back_to_the_beats_field(self):
        document = hit()
        document['_source'].pop('timestamp')

        self.assertEqual(normalize_alerts([document], 1)[0]['timestamp'],
                         '2026-09-14T18:03:11.114+0000')

    def test_a_non_list_response_is_empty_rather_than_an_error(self):
        self.assertEqual(normalize_alerts(None, 10), [])
        self.assertEqual(normalize_alerts({'hits': 'nope'}, 10), [])


def response(hits=(), buckets=(), total=None):
    """An OpenSearch `_search` reply, shaped the way 2.x actually returns one."""
    return {
        'took': 4,
        'timed_out': False,
        '_shards': {'total': 1, 'successful': 1, 'skipped': 0, 'failed': 0},
        'hits': {
            'total': {'value': len(hits) if total is None else total, 'relation': 'eq'},
            'max_score': None,
            'hits': list(hits),
        },
        'aggregations': {
            'severity': {
                'doc_count_error_upper_bound': 0,
                'sum_other_doc_count': 0,
                'buckets': [{'key': k, 'doc_count': c} for k, c in buckets],
            },
        },
    }


class SummaryTests(SimpleTestCase):
    def summary_for(self, **kwargs):
        return model_from_response(response(**kwargs), 10)['summary']

    def test_levels_roll_up_into_wazuh_severity_bands(self):
        summary = self.summary_for(
            buckets=[(15, 1), (12, 2), (10, 3), (8, 1), (7, 4), (4, 1), (3, 5), (0, 2)],
            total=19)

        self.assertEqual(summary['critical'], 3)
        self.assertEqual(summary['high'], 4)
        self.assertEqual(summary['medium'], 5)
        self.assertEqual(summary['low'], 7)
        self.assertEqual(summary['total'], 19)
        self.assertEqual(summary['highest_level'], 15)

    def test_an_empty_index_summarizes_to_zeros_not_an_error(self):
        summary = self.summary_for()

        self.assertEqual(summary, {'total': 0, 'critical': 0, 'high': 0,
                                   'medium': 0, 'low': 0, 'highest_level': None})

    def test_unusable_buckets_are_ignored(self):
        summary = self.summary_for(buckets=[('x', 3), (7, 'many'), (9, 2)], total=2)

        self.assertEqual(summary['high'], 2)
        self.assertEqual(summary['medium'], 0)

    def test_total_survives_a_missing_or_legacy_hits_total(self):
        legacy = response(hits=[hit(), hit()])
        legacy['hits']['total'] = 2
        self.assertEqual(model_from_response(legacy, 10)['summary']['total'], 2)

        bare = response(hits=[hit()])
        bare['hits'].pop('total')
        self.assertEqual(model_from_response(bare, 10)['summary']['total'], 1)


class ModelTests(SimpleTestCase):
    def test_model_carries_only_a_summary_and_an_alert_page(self):
        model = model_from_response(response(hits=[hit()], total=1), 10)

        self.assertEqual(sorted(model), ['alerts', 'summary'])
        self.assertEqual(len(model['alerts']), 1)

    def test_backend_internals_never_reach_the_model(self):
        reply = response(hits=[hit()], total=1)
        reply['_shards']['failures'] = [{'reason': {'reason': 'LEAK-SHARD'}}]

        serialized = json.dumps(model_from_response(reply, 10))

        for marker in ('LEAK-SHARD', 'took', '_shards', 'aggregations', 'LEAK-FULLLOG'):
            self.assertNotIn(marker, serialized)

    def test_a_garbage_reply_becomes_an_empty_model_not_a_crash(self):
        for reply in (None, [], 'boom', {'hits': None}, {}):
            self.assertEqual(model_from_response(reply, 10), empty_model())

    def test_empty_model_is_a_real_empty_result(self):
        self.assertEqual(empty_model(),
                         {'summary': {'total': 0, 'critical': 0, 'high': 0, 'medium': 0,
                                      'low': 0, 'highest_level': None},
                          'alerts': []})
