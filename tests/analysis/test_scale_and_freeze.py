from tests.support import Case, column, context, policy
from rights_graph.catalog import assets
from rights_graph.policy import grants
from rights_graph.planner import plans
from rights_graph.analysis import stats, mining, baskets


SCHEMA = [
    column('transaction', 'string', nullable=True),
    column('item', 'string', nullable=True),
    column('date', 'string'),
    column('returned', 'boolean'),
]
FIELD_MAP = {'transaction': 'transaction', 'item': 'item', 'date': 'date', 'returned': 'returned'}


class ScaleAndFreezeTests(Case):
    def env(self, rows):
        assets.create(self.store, 'supplies', 'source', 'station', SCHEMA, 'synthetic')
        grants.create(self.store, 'grant-supplies', 'supplies', policy(
            columns=['transaction', 'item', 'date', 'returned'],
            purposes=['stocking'], regions=['cn'], rights=['read', 'distribute'],
        ))
        self.app.preview.data.import_rows('autumn', 'supplies', 1, rows)
        return plans.create(self.store, 'supplies', context(purpose='stocking', right='read'))

    def run_mining(self, discovery_rows, validation_rows, parameters):
        roles = self._roles()
        discovery = baskets.build_baskets(discovery_rows, roles, self._window(9))
        validation = baskets.build_baskets(validation_rows, roles, self._window(10))
        return mining.discover(discovery, validation, parameters)

    def _roles(self):
        if self.store.one("SELECT 1 FROM assets WHERE asset_id='supplies'") is None:
            self.env([])
        asset = assets.get(self.store, 'supplies')
        return baskets.validate_mapping(asset, FIELD_MAP)

    def _window(self, month):
        return (baskets.iso_date(f'2026-{month:02d}-01', 's'),
                baskets.iso_date(f'2026-{month:02d}-30', 'e'))

    def test_distinct_item_limit_is_refused_not_truncated(self):
        original = stats.MAX_DISTINCT_ITEMS
        stats.MAX_DISTINCT_ITEMS = 3
        try:
            many = []
            for index in range(4):
                many.append({'transaction': f't{index}', 'item': f'item{index}', 'date': '2026-09-10', 'returned': False})
            summary = baskets.build_baskets(many, self._roles(), self._window(9))
            with self.assertRaises(Exception) as caught:
                stats.apriori_counts(summary['baskets'], 1, 2)
            self.assertEqual(caught.exception.code, 'analysis_scale')
        finally:
            stats.MAX_DISTINCT_ITEMS = original

    def test_validation_never_adds_or_removes_candidates(self):
        discovery_rows = [
            {'transaction': 'a', 'item': 'x', 'date': '2026-09-02', 'returned': False},
            {'transaction': 'a', 'item': 'y', 'date': '2026-09-02', 'returned': False},
            {'transaction': 'b', 'item': 'x', 'date': '2026-09-03', 'returned': False},
            {'transaction': 'b', 'item': 'y', 'date': '2026-09-03', 'returned': False},
        ]
        # Validation contains a strong new pair (q,r) that discovery never saw: it must not appear.
        validation_rows = [
            {'transaction': 'c', 'item': 'q', 'date': '2026-10-02', 'returned': False},
            {'transaction': 'c', 'item': 'r', 'date': '2026-10-02', 'returned': False},
            {'transaction': 'd', 'item': 'q', 'date': '2026-10-03', 'returned': False},
            {'transaction': 'd', 'item': 'r', 'date': '2026-10-03', 'returned': False},
        ]
        parameters = {'min_support': 0.5, 'max_rule_length': 2, 'min_cooccurrence': 2}
        result = self.run_mining(discovery_rows, validation_rows, parameters)
        keys = {rule['rule_key'] for rule in result['rules']}
        self.assertEqual(keys, {'x=>y', 'y=>x'})
        for rule in result['rules']:
            self.assertEqual(rule['validation']['table']['both'], 0)
            self.assertEqual(rule['validation']['reason'], 'antecedent_never_observed')
            self.assertIsNone(rule['validation']['fisher_p'])
            self.assertEqual(rule['validation']['fisher_reason'], 'degenerate_margins')
            self.assertIsNone(rule['validation']['bh_adjusted_p'])

    def test_empty_validation_population_is_reported_with_reason(self):
        discovery_rows = [
            {'transaction': 'a', 'item': 'x', 'date': '2026-09-02', 'returned': False},
            {'transaction': 'a', 'item': 'y', 'date': '2026-09-02', 'returned': False},
            {'transaction': 'b', 'item': 'x', 'date': '2026-09-03', 'returned': False},
            {'transaction': 'b', 'item': 'y', 'date': '2026-09-03', 'returned': False},
        ]
        parameters = {'min_support': 0.5, 'max_rule_length': 2, 'min_cooccurrence': 2}
        roles = self._roles()
        discovery = baskets.build_baskets(discovery_rows, roles, self._window(9))
        validation = baskets.build_baskets([], roles, self._window(10))
        result = mining.discover(discovery, validation, parameters)
        self.assertEqual(result['validation']['population'], 0)
        for rule in result['rules']:
            self.assertEqual(rule['validation']['reason'], 'empty_population')
            self.assertIsNone(rule['validation']['fisher_p'])
            self.assertEqual(rule['validation']['fisher_reason'], 'degenerate_margins')
            self.assertIsNone(rule['validation']['bh_adjusted_p'])

    def test_max_rule_length_limits_itemset_generation(self):
        rows = []
        for index, items in enumerate([('a', 'b', 'c'), ('a', 'b', 'c'), ('a', 'b', 'c')]):
            for item in items:
                rows.append({'transaction': f't{index}', 'item': item, 'date': '2026-09-10', 'returned': False})
        summary = baskets.build_baskets(rows, self._roles(), self._window(9))
        counts = stats.apriori_counts(summary['baskets'], 2, 2)
        self.assertNotIn(frozenset(('a', 'b', 'c')), counts)
        counts_long = stats.apriori_counts(summary['baskets'], 2, 3)
        self.assertIn(frozenset(('a', 'b', 'c')), counts_long)

    def test_declared_itemset_and_rule_limits_refuse_oversized_runs(self):
        rows = []
        for index in range(6):
            for item in ('a', 'b', 'c', 'd'):
                rows.append({'transaction': f't{index}', 'item': item, 'date': '2026-09-10', 'returned': False})
        summary = baskets.build_baskets(rows, self._roles(), self._window(9))
        original_itemsets, original_rules = stats.MAX_ITEMSETS, stats.MAX_RULES
        try:
            stats.MAX_ITEMSETS = 2
            with self.assertRaises(Exception) as caught:
                stats.apriori_counts(summary['baskets'], 1, 3)
            self.assertEqual(caught.exception.code, 'analysis_scale')
        finally:
            stats.MAX_ITEMSETS = original_itemsets
        counts = stats.apriori_counts(summary['baskets'], 1, 3)
        try:
            stats.MAX_RULES = 2
            with self.assertRaises(Exception) as caught:
                stats.generate_candidates(counts, 3, 1)
            self.assertEqual(caught.exception.code, 'analysis_scale')
        finally:
            stats.MAX_RULES = original_rules
