"""Container-status normalization.

The dashboard's job is to answer "is the ShopMock stack healthy" — nothing
else. These tests pin both halves of that contract: the shape that IS exposed
(service, state, health, image, age) and, at least as importantly, everything
that is NOT (commands, mounts, ports, network settings, arbitrary labels, and
any container belonging to another compose project).
"""
import json

from django.test import SimpleTestCase, override_settings

from ops.containers import normalize_containers, summarize

PROJECT = 'shopmock'

# Fields a real Docker/Podman `GET /containers/json` returns that must never
# reach the browser. Values are marked so a leak is unambiguous in a diff.
LEAKY = {
    'Command': "/bin/sh -c 'psql -c \"ALTER USER postgres PASSWORD LEAK-COMMAND\"'",
    'Mounts': [
        {'Source': '/home/shop/ShopMock/.env', 'Destination': '/run/LEAK-MOUNT', 'RW': True},
    ],
    'Ports': [{'PrivatePort': 5432, 'PublicPort': 55432, 'Type': 'LEAK-PORT'}],
    'NetworkSettings': {'Networks': {'LEAK-NETWORK': {'IPAddress': '10.202.0.9'}}},
    'HostConfig': {'NetworkMode': 'LEAK-HOSTCONFIG'},
    'SizeRw': 1234,
}


def raw(service, *, state='running', status=None, image=None, created=1755859200,
        project=PROJECT, label_key='com.docker.compose.project', extra=None):
    labels = {
        'LEAK-LABEL-secret': 'SUPERSECRET-LAB-TOKEN',
        'org.opencontainers.image.vendor': 'LEAK-LABEL-vendor',
    }
    if project is not None:
        labels[label_key] = project
        labels[label_key.replace('project', 'service')] = service
    container = {
        'Id': f'{service:0<64}'.replace(' ', '0')[:64],
        'Names': [f'/{project}-{service}-1' if project else f'/{service}'],
        'Image': image or f'docker.io/library/{service}:1.0',
        'ImageID': 'sha256:' + 'ab' * 32,
        'Created': created,
        'State': state,
        'Status': status if status is not None else 'Up 2 hours',
        'Labels': labels,
        **LEAKY,
    }
    container.update(extra or {})
    return container


def live_stack():
    """The stack as it normally runs: 21 long-lived services + 2 one-shot seeds."""
    running = [
        'edge', 'storefront', 'search', 'search-dashboard', 'identity',
        'catalog-svc', 'order-svc', 'checkout-svc', 'customer-svc', 'seller-svc',
        'internal-ops-svc', 'internal-service-backend', 'seller-backend',
        'customer-db', 'catalog-db', 'orders-db', 'finance-db', 'vault',
        'ipa', 'paw', 'wazuh', 'oe-dashboard', 'oe-socket-proxy',
    ]
    containers = [raw(s, status='Up 3 hours') for s in running]
    containers += [
        raw('vault-seed', state='exited', status='Exited (0) 3 hours ago'),
        raw('search-seed', state='exited', status='Exited (0) 3 hours ago'),
    ]
    return containers


@override_settings(OE_PROJECT_NAME=PROJECT)
class NormalizeTests(SimpleTestCase):
    def test_keeps_only_the_shopmock_compose_project(self):
        containers = normalize_containers([
            raw('edge'),
            raw('nginx', project='someone-elses-stack'),
            raw('orphan', project=None),
        ])

        self.assertEqual([c['service'] for c in containers], ['edge'])

    def test_accepts_the_podman_compose_project_label(self):
        containers = normalize_containers([
            raw('edge', label_key='io.podman.compose.project'),
        ])

        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0]['project'], PROJECT)
        self.assertEqual(containers[0]['service'], 'edge')

    def test_normalizes_the_fields_the_dashboard_displays(self):
        [container] = normalize_containers([
            raw('identity', image='quay.io/keycloak/keycloak:24.0',
                status='Up 2 hours (healthy)', created=1755859200),
        ], now=1755866400)

        self.assertEqual(container['name'], 'shopmock-identity-1')
        self.assertEqual(container['service'], 'identity')
        self.assertEqual(container['project'], PROJECT)
        self.assertEqual(container['image'], 'quay.io/keycloak/keycloak:24.0')
        self.assertEqual(container['state'], 'running')
        self.assertEqual(container['status'], 'Up 2 hours (healthy)')
        self.assertEqual(container['health'], 'healthy')
        self.assertEqual(container['created'], '2025-08-22T10:40:00+00:00')
        self.assertEqual(container['age_seconds'], 7200)
        self.assertTrue(container['ok'])
        self.assertEqual(len(container['id']), 12)

    def test_never_exposes_env_commands_mounts_or_arbitrary_labels(self):
        payload = json.dumps(normalize_containers(live_stack()))

        for marker in ('LEAK-COMMAND', 'LEAK-MOUNT', 'LEAK-PORT', 'LEAK-NETWORK',
                       'LEAK-HOSTCONFIG', 'LEAK-LABEL', 'SUPERSECRET-LAB-TOKEN'):
            self.assertNotIn(marker, payload, f'{marker} leaked into the API payload')

    def test_exposes_only_the_allowlisted_keys(self):
        allowed = {
            'id', 'name', 'project', 'service', 'image', 'state', 'status',
            'health', 'created', 'age_seconds', 'exit_code', 'ok',
        }

        for container in normalize_containers(live_stack()):
            self.assertEqual(set(container) - allowed, set())

    def test_reads_health_out_of_the_status_line(self):
        cases = {
            'Up 2 hours (healthy)': 'healthy',
            'Up 5 seconds (unhealthy)': 'unhealthy',
            'Up 3 seconds (health: starting)': 'starting',
            'Up 2 hours': 'none',
            'Exited (0) 3 hours ago': 'none',
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                [container] = normalize_containers([raw('edge', status=status)])
                self.assertEqual(container['health'], expected)

    def test_one_shot_seed_jobs_that_exited_cleanly_count_as_ok(self):
        by_service = {c['service']: c for c in normalize_containers([
            raw('vault-seed', state='exited', status='Exited (0) 3 hours ago'),
            raw('search-seed', state='exited', status='Exited (1) 3 hours ago'),
        ])}

        good, bad = by_service['vault-seed'], by_service['search-seed']
        self.assertEqual((good['exit_code'], good['ok']), (0, True))
        self.assertEqual((bad['exit_code'], bad['ok']), (1, False))

    def test_prefers_a_structured_exit_code_when_podman_supplies_one(self):
        [container] = normalize_containers([
            raw('vault-seed', state='exited', status='Exited () ago',
                extra={'ExitCode': 0}),
        ])

        self.assertEqual(container['exit_code'], 0)
        self.assertTrue(container['ok'])

    def test_running_but_unhealthy_is_not_ok(self):
        [container] = normalize_containers([
            raw('identity', status='Up 2 hours (unhealthy)'),
        ])

        self.assertFalse(container['ok'])

    def test_sorted_by_service_for_a_stable_table(self):
        services = [c['service'] for c in normalize_containers([
            raw('wazuh'), raw('edge'), raw('identity'),
        ])]

        self.assertEqual(services, ['edge', 'identity', 'wazuh'])

    def test_tolerates_missing_and_malformed_fields(self):
        containers = normalize_containers([
            {'Labels': {'com.docker.compose.project': PROJECT}},
            {'Labels': {'com.docker.compose.project': PROJECT},
             'Names': [], 'Created': 'not-a-number', 'State': None, 'Status': None},
            'not-a-container',
            None,
        ])

        self.assertEqual(len(containers), 2)
        for container in containers:
            self.assertEqual(container['state'], 'unknown')
            self.assertEqual(container['health'], 'none')
            self.assertIsNone(container['age_seconds'])
            self.assertFalse(container['ok'])
        self.assertEqual(containers[0]['name'], '')

    def test_ignores_a_non_list_payload(self):
        self.assertEqual(normalize_containers(None), [])
        self.assertEqual(normalize_containers({'message': 'nope'}), [])


@override_settings(OE_PROJECT_NAME=PROJECT)
class SummaryTests(SimpleTestCase):
    def test_counts_the_live_stack(self):
        summary = summarize(normalize_containers(live_stack()))

        self.assertEqual(summary['total'], 25)
        self.assertEqual(summary['running'], 23)
        self.assertEqual(summary['exited_ok'], 2)
        self.assertEqual(summary['failed'], 0)
        self.assertTrue(summary['ok'])

    def test_counts_health_and_failures(self):
        summary = summarize(normalize_containers([
            raw('edge', status='Up 2 hours (healthy)'),
            raw('identity', status='Up 2 hours (unhealthy)'),
            raw('ipa', status='Up 9 seconds (health: starting)'),
            raw('storefront', status='Up 2 hours'),
            raw('vault-seed', state='exited', status='Exited (0) 1 hour ago'),
            raw('search-seed', state='exited', status='Exited (2) 1 hour ago'),
            raw('paw', state='created', status='Created'),
        ]))

        self.assertEqual(summary['total'], 7)
        self.assertEqual(summary['running'], 4)
        self.assertEqual(summary['healthy'], 1)
        self.assertEqual(summary['unhealthy'], 1)
        self.assertEqual(summary['starting'], 1)
        self.assertEqual(summary['exited_ok'], 1)
        self.assertEqual(summary['failed'], 2)   # the unhealthy one + Exited (2)
        self.assertEqual(summary['other'], 1)    # created
        self.assertFalse(summary['ok'])

    def test_empty_stack_is_not_reported_as_healthy(self):
        summary = summarize([])

        self.assertEqual(summary['total'], 0)
        self.assertFalse(summary['ok'])
