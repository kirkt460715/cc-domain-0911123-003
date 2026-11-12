import json

from tests.support import Case, column, context, policy
from rights_graph.catalog import assets
from rights_graph.policy import grants
from rights_graph.planner import plans
from rights_graph.primitives import GraphError


SCHEMA = [
    column('transaction', 'string', nullable=True),
    column('item', 'string', nullable=True),
    column('date', 'string'),
    column('returned', 'boolean'),
]

FIELD_MAP = {'transaction': 'transaction', 'item': 'item', 'date': 'date', 'returned': 'returned'}


def r(transaction, item, date='2026-09-10', returned=False):
    return {'transaction': transaction, 'item': item, 'date': date, 'returned': returned}


class ReportWorkflowTests(Case):
    def prepare(self, rows=None, grant_policy=None, columns=None):
        assets.create(self.store, 'supplies', 'source', 'station', columns or SCHEMA, 'synthetic consumables')
        grants.create(self.store, 'grant-supplies', 'supplies', grant_policy or policy(
            columns=['transaction', 'item', 'date', 'returned'],
            purposes=['stocking'], regions=['cn'], rights=['read', 'transform', 'distribute'],
        ))
        rows = rows if rows is not None else [
            r('d0', 'filter', '2026-09-02'), r('d0', 'seal', '2026-09-02'), r('d0', 'filter', '2026-09-02'),
            r('d1', 'filter', '2026-09-05'), r('d1', 'seal', '2026-09-05'),
            r('d2', 'filter', '2026-09-08'), r('d2', 'seal', '2026-09-08'),
            r('d3', 'gasket', '2026-09-12'), r('d3', 'valve', '2026-09-12'),
            r('d4', 'gasket', '2026-09-15'), r('d4', 'valve', '2026-09-15'),
            r('d5', 'gasket', '2026-09-18'), r('d5', 'valve', '2026-09-18'),
            r('d6', None, '2026-09-20'),
            r('v0', 'filter', '2026-10-02'), r('v0', 'seal', '2026-10-02'),
            r('v1', 'filter', '2026-10-05'), r('v1', 'seal', '2026-10-05'),
            r('v2', 'filter', '2026-10-08'),
            r('v3', 'valve', '2026-10-12'), r('v4', 'gasket', '2026-10-15'),
            r('v5', None, '2026-10-20'),
        ]
        self.app.preview.data.import_rows('autumn', 'supplies', 1, rows)
        plan = plans.create(self.store, 'supplies', context(purpose='stocking', right='read'))
        return plan

    def request(self, plan_id, **changes):
        value = {
            'analysis_id': 'rules-1', 'plan_id': plan_id, 'binding': 'autumn', 'field_map': FIELD_MAP,
            'discovery_window': ['2026-09-01', '2026-09-30'],
            'validation_window': ['2026-10-01', '2026-10-31'],
            'min_support': 0.2, 'max_rule_length': 2, 'min_cooccurrence': 2,
        }
        value.update(changes)
        return value

    def rule(self, payload, key):
        return next(rule for rule in payload['rules'] if rule['rule_key'] == key)

    def test_duplicate_rows_do_not_inflate_confidence_and_empty_baskets_count(self):
        plan = self.prepare()
        report = self.app.analysis.run(self.request(plan['plan_id']))
        payload = report['payload']
        # d0 has filter twice on the detail ledger, yet the basket contains it once.
        filter_seal = self.rule(payload, 'filter=>seal')
        self.assertEqual(filter_seal['discovery']['table']['both'], 3)
        self.assertEqual(filter_seal['discovery']['table']['antecedent_only'], 0)
        # The missing-item basket d6 remains in the discovery denominator.
        self.assertEqual(payload['discovery']['population'], 7)
        self.assertEqual(payload['discovery']['empty_baskets'], 1)
        self.assertEqual(payload['discovery']['excluded_rows']['duplicate_item_rows_collapsed'], 1)

    def test_candidates_are_frozen_before_validation_and_spurious_pair_vanishes(self):
        plan = self.prepare()
        payload = self.app.analysis.run(self.request(plan['plan_id']))['payload']
        keys = {rule['rule_key'] for rule in payload['rules']}
        # Every rule comes from the discovery window; the validation window adds none.
        self.assertIn('gasket=>valve', keys)
        spurious = self.rule(payload, 'gasket=>valve')
        self.assertEqual(spurious['discovery']['confidence'], '1.0000000000')
        self.assertEqual(spurious['validation']['table']['both'], 0)
        self.assertEqual(spurious['validation']['support'], '0.0000000000')
        self.assertEqual(spurious['validation']['fisher_p'], '1.000000000000')
        # The failed rule is still reported, with the same BH family size as survivors.
        for rule in payload['rules']:
            self.assertEqual(rule['validation']['bh_family_size'], payload['candidate_count'])
            self.assertIsNotNone(rule['validation']['bh_adjusted_p'])

    def test_deterministic_ordering_and_digest(self):
        plan = self.prepare()
        first = self.app.analysis.run(self.request(plan['plan_id']))
        second = self.app.analysis.run(self.request(plan['plan_id']))
        self.assertEqual(first['sha256'], second['sha256'])
        keys = [rule['rule_key'] for rule in first['payload']['rules']]
        self.assertEqual(keys, sorted(keys))
        self.fails('analysis_conflict', self.app.analysis.run,
                   self.request(plan['plan_id'], min_cooccurrence=3))

    def test_delivery_carries_only_aggregates_and_revocation_blocks_retrieval(self):
        plan = self.prepare()
        report = self.app.analysis.run(self.request(plan['plan_id']))
        delivery = self.app.analysis.deliver('delivery-1', 'rules-1', 'buyer')
        self.assertNotIn('rows', delivery['payload']['aggregate_payload'])
        self.assertEqual(delivery['payload']['recipient'], 'buyer')
        grant = grants.get(self.store, 'grant-supplies')
        grants.revoke(self.store, grant['grant_id'], grant['revision'], 'withdraw source')
        self.fails('report_unavailable', self.app.analysis.get, 'rules-1')
        self.fails('report_unavailable', self.app.analysis.deliver, 'delivery-2', 'rules-1', 'buyer')
        self.assertEqual(self.app.analysis.listing(), [])
        # The ledger row still exists and system check remains green.
        self.assertEqual(self.store.scalar('SELECT COUNT(*) FROM analysis_reports'), 1)
        self.assertTrue(self.app.dispatch('system.check', {})['ok'])

    def test_authorization_failures_block_run_and_delivery(self):
        plan = self.prepare()
        grant = grants.get(self.store, 'grant-supplies')
        grants.revoke(self.store, grant['grant_id'], grant['revision'], 'remove coverage')
        self.fails('stale_plan', self.app.analysis.run, self.request(plan['plan_id'], analysis_id='rules-revoked'))
        grants.create(self.store, 'grant-supplies-2', 'supplies', policy(
            columns=['transaction', 'item', 'date', 'returned'],
            purposes=['stocking'], regions=['cn'], rights=['read'],
        ))
        readable = plans.create(self.store, 'supplies', context(purpose='stocking', right='read'))
        self.app.analysis.run(self.request(readable['plan_id'], analysis_id='rules-read-only'))
        self.fails('report_delivery_denied', self.app.analysis.deliver, 'd-denied', 'rules-read-only', 'buyer')

    def test_field_mapping_and_binding_must_match_the_plan(self):
        plan = self.prepare()
        bad_mapping = {**FIELD_MAP, 'item': 'date'}
        self.fails('analysis_fields', self.app.analysis.run,
                   self.request(plan['plan_id'], field_map=bad_mapping, analysis_id='rules-bad'))
        self.fails('unknown_data_batch', self.app.analysis.run,
                   self.request(plan['plan_id'], binding='missing-batch', analysis_id='rules-binding'))
        # A plan that selects fewer columns cannot authorize a mapping needing all four.
        narrowed = plans.create(self.store, 'supplies', context(purpose='stocking', right='read'),
                                columns=['transaction', 'item', 'date'])
        self.fails('analysis_columns', self.app.analysis.run,
                   self.request(narrowed['plan_id'], analysis_id='rules-narrow'))

    def test_scale_limits_refuse_instead_of_truncating(self):
        plan = self.prepare()
        # A co-occurrence floor beyond the data yields no candidates rather than partial output.
        self.fails('analysis_no_candidates', self.app.analysis.run,
                   self.request(plan['plan_id'], min_cooccurrence=99, analysis_id='rules-empty'))
        self.assertIsNone(self.store.one('SELECT report_id FROM analysis_reports WHERE report_id=?', ('rules-empty',)))
        # Oversized rule length is rejected up front.
        self.fails('invalid_count', self.app.analysis.run,
                   self.request(plan['plan_id'], max_rule_length=99, analysis_id='rules-long'))
        # Empty discovery population is explained, not silently treated as zero rules.
        self.fails('analysis_population', self.app.analysis.run, self.request(
            plan['plan_id'], discovery_window=['2025-01-01', '2025-01-31'], analysis_id='rules-no-pop'))

    def test_report_corruption_is_detected(self):
        plan = self.prepare()
        self.app.analysis.run(self.request(plan['plan_id']))
        self.store.execute("UPDATE analysis_reports SET payload='{}' WHERE report_id='rules-1'")
        self.fails('report_corrupt', self.app.analysis.get, 'rules-1')

    def test_dispatch_registers_operations(self):
        plan = self.prepare()
        result = self.app.dispatch('analysis.run', self.request(plan['plan_id']))
        self.assertEqual(result['payload']['report_id'], 'rules-1')
        listing = self.app.dispatch('analysis.list', {})
        self.assertEqual([item['report_id'] for item in listing], ['rules-1'])
        with self.assertRaises(GraphError) as caught:
            self.app.dispatch('analysis.run', {'plan_id': plan['plan_id']})
        self.assertEqual(caught.exception.code, 'invalid_fields')
