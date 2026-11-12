from decimal import Decimal

from tests.support import Case
from rights_graph.analysis import stats
from rights_graph.analysis.mining import ensure_counts
from rights_graph.primitives import GraphError


class StatsTests(Case):
    def baskets(self):
        return [
            frozenset(('filter', 'seal')),
            frozenset(('filter', 'seal')),
            frozenset(('filter', 'seal', 'bolt')),
            frozenset(('filter', 'bolt')),
            frozenset(('seal',)),
            frozenset(),
        ]

    def test_apriori_dedupes_itemsets_and_respects_floor(self):
        counts = stats.apriori_counts(self.baskets(), 2, 2)
        self.assertEqual(counts[frozenset(('filter',))], 4)
        self.assertEqual(counts[frozenset(('seal',))], 4)
        self.assertEqual(counts[frozenset(('bolt',))], 2)
        self.assertEqual(counts[frozenset(('filter', 'seal'))], 3)
        self.assertNotIn(frozenset(('seal', 'bolt')), counts)
        closed = stats.closed_itemsets(counts)
        # filter/seal pair has no same-count superset and must remain closed.
        self.assertIn(frozenset(('filter', 'seal')), closed)

    def test_rules_require_nonempty_disjoint_sides_and_min_cooccurrence(self):
        counts = stats.apriori_counts(self.baskets(), 2, 2)
        rules = stats.generate_candidates(counts, 2, 3)
        keys = set(rules)
        self.assertIn((('filter',), ('seal',)), keys)
        self.assertIn((('seal',), ('filter',)), keys)
        for antecedent, consequent in rules:
            self.assertTrue(antecedent)
            self.assertTrue(consequent)
            self.assertFalse(set(antecedent) & set(consequent))
        stricter = stats.generate_candidates(counts, 2, 4)
        self.assertEqual(stricter, [])

    def test_metrics_match_hand_computed_counts(self):
        tables = ensure_counts(self.baskets(), {
            frozenset(('filter',)), frozenset(('seal',)), frozenset(('filter', 'seal')),
        })
        result = stats.metrics((('filter',), ('seal',)), tables, 6)
        self.assertEqual(result['table'], {'both': 3, 'antecedent_only': 1, 'consequent_only': 1, 'neither': 1})
        self.assertEqual(result['support'], '0.5000000000')
        self.assertEqual(result['confidence'], '0.7500000000')
        # lift = 0.75 / (4/6) = 1.125 ; leverage = 3/6 - (4/6)(4/6)
        self.assertEqual(result['lift'], '1.1250000000')
        self.assertEqual(result['leverage'], str(Decimal('0.0555555556')))

    def test_metrics_explain_degenerate_populations(self):
        empty = stats.metrics((('a',), ('b',)), {}, 0)
        self.assertEqual(empty['reason'], 'empty_population')
        self.assertIsNone(empty['lift'])
        tables = {frozenset(('a',)): 0, frozenset(('b',)): 2, frozenset(('a', 'b')): 0}
        no_left = stats.metrics((('a',), ('b',)), tables, 4)
        self.assertEqual(no_left['reason'], 'antecedent_never_observed')

    def test_fisher_one_sided_known_values(self):
        # Perfect independence cell pattern gives an upper tail of one.
        p_value, reason = stats.fisher_one_sided(0, 3, 2, 3)
        self.assertIsNone(reason)
        self.assertEqual(p_value, '1.000000000000')
        p_value, _ = stats.fisher_one_sided(3, 0, 0, 3)
        self.assertEqual(p_value, '0.050000000000')
        p_value, reason = stats.fisher_one_sided(0, 0, 4, 4)
        self.assertIsNone(p_value)
        self.assertEqual(reason, 'degenerate_margins')

    def test_bh_adjusts_whole_family_and_keeps_uncomputable_members(self):
        entries = [
            {'rule_key': 'a', 'validation': {'fisher_p': '0.010000000000', 'bh_reason': None}},
            {'rule_key': 'b', 'validation': {'fisher_p': '0.040000000000', 'bh_reason': None}},
            {'rule_key': 'c', 'validation': {'fisher_p': None, 'bh_reason': 'degenerate_margins'}},
            {'rule_key': 'd', 'validation': {'fisher_p': '0.030000000000', 'bh_reason': None}},
        ]
        stats.benjamini_hochberg(entries)
        by_key = {entry['rule_key']: entry['validation'] for entry in entries}
        self.assertEqual(by_key['a']['bh_family_size'], 4)
        self.assertEqual(by_key['a']['bh_computable'], 3)
        # Standard BH step-down: raw rank values are 0.03, 0.045, 0.04; walking
        # from the largest rank makes the adjusted sequence monotone (0.03, 0.04, 0.04).
        self.assertEqual(by_key['a']['bh_adjusted_p'], '0.030000000000')
        self.assertEqual(by_key['d']['bh_adjusted_p'], '0.040000000000')
        self.assertEqual(by_key['b']['bh_adjusted_p'], '0.040000000000')
        self.assertIsNone(by_key['c']['bh_adjusted_p'])
        self.assertEqual(by_key['c']['bh_reason'], 'degenerate_margins')

    def test_threshold_validation(self):
        with self.assertRaises(GraphError) as caught:
            stats.validate_thresholds(0.0, 2, 1)
        self.assertEqual(caught.exception.code, 'analysis_threshold')
        with self.assertRaises(GraphError) as caught:
            stats.validate_thresholds(0.5, 1, 1)
        self.assertEqual(caught.exception.code, 'invalid_count')
        with self.assertRaises(GraphError) as caught:
            stats.validate_thresholds(1.1, 2, 1)
        self.assertEqual(caught.exception.code, 'analysis_threshold')
