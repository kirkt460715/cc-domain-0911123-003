from tests.support import Case, column
from rights_graph.analysis import baskets
from rights_graph.primitives import GraphError


SCHEMA = [
    column('transaction', 'string', nullable=True),
    column('item', 'string', nullable=True),
    column('date', 'string'),
    column('returned', 'boolean'),
]

FIELD_MAP = {'transaction': 'transaction', 'item': 'item', 'date': 'date', 'returned': 'returned'}


def row(transaction, item, date='2026-09-10', returned=False):
    return {'transaction': transaction, 'item': item, 'date': date, 'returned': returned}


class BasketTests(Case):
    def roles(self):
        from rights_graph.catalog import assets
        asset = assets.create(self.store, 'supplies', 'source', 'owner', SCHEMA, 'synthetic')
        return baskets.validate_mapping(asset, FIELD_MAP)

    def test_duplicate_detail_rows_collapse_to_one_membership(self):
        roles = self.roles()
        summary = baskets.build_baskets([
            row('t1', 'filter'), row('t1', 'filter'), row('t1', 'seal'),
        ], roles, self.window())
        self.assertEqual(summary['baskets'], [frozenset(('filter', 'seal'))])
        self.assertEqual(summary['excluded']['duplicate_item_rows_collapsed'], 1)

    def window(self):
        return tuple(baskets.iso_date(value, field) for value, field in
                     [('2026-09-01', 'start'), ('2026-09-30', 'end')])

    def test_empty_key_return_rows_and_missing_items_are_explained_separately(self):
        roles = self.roles()
        summary = baskets.build_baskets([
            row('t1', 'filter'),
            row(None, 'seal'),
            row('', 'bolt'),
            row('t2', 'seal', returned=True),
            row('t3', None),
            row('t4', 'filter', date='2026-08-31'),
        ], roles, self.window())
        self.assertEqual(summary['excluded']['empty_transaction_key_rows'], 2)
        self.assertEqual(summary['excluded']['return_rows'], 1)
        self.assertEqual(summary['excluded']['missing_item_rows'], 1)
        self.assertEqual(summary['excluded']['rows_outside_window'], 1)
        identities = {tuple(sorted(basket)) for basket in summary['baskets']}
        # t3 keeps its identity as an empty basket; t2 is a return-only empty basket.
        self.assertEqual(summary['population'], 3)
        self.assertEqual(summary['empty_baskets'], 2)
        self.assertIn((), identities)

    def test_empty_baskets_stay_in_denominator(self):
        roles = self.roles()
        summary = baskets.build_baskets([row('empty', None), row('full', 'filter')], roles, self.window())
        self.assertEqual(summary['population'], 2)
        self.assertEqual(summary['empty_baskets'], 1)
        self.assertEqual(summary['nonempty_baskets'], 1)

    def test_window_and_mapping_validation(self):
        with self.assertRaises(GraphError) as caught:
            baskets.validate_windows(['2026-09-01', '2026-09-30'], ['2026-09-30', '2026-10-31'])
        self.assertEqual(caught.exception.code, 'analysis_window')
        with self.assertRaises(GraphError) as caught:
            baskets.validate_windows(['2026-09-10', '2026-09-01'], ['2026-10-01', '2026-10-31'])
        self.assertEqual(caught.exception.code, 'analysis_window')
        with self.assertRaises(GraphError) as caught:
            baskets.validate_windows(['2026-09-01'], ['2026-10-01', '2026-10-31'])
        self.assertEqual(caught.exception.code, 'analysis_window')
        with self.assertRaises(GraphError) as caught:
            baskets.iso_date('09/01/2026', 'date')
        self.assertEqual(caught.exception.code, 'analysis_window')

    def test_field_map_must_cover_distinct_typed_columns(self):
        from rights_graph.catalog import assets
        asset = assets.create(self.store, 'other', 'source', 'owner', SCHEMA, 'synthetic')
        with self.assertRaises(GraphError) as caught:
            baskets.validate_mapping(asset, {**FIELD_MAP, 'item': 'missing'})
        self.assertEqual(caught.exception.code, 'analysis_fields')
        with self.assertRaises(GraphError) as caught:
            baskets.validate_mapping(asset, {**FIELD_MAP, 'item': 'returned'})
        self.assertEqual(caught.exception.code, 'analysis_fields')
        bad_type = [
            column('transaction', 'integer'),
            column('item', 'string', nullable=True),
            column('date', 'string'),
            column('returned', 'boolean'),
        ]
        typed = assets.create(self.store, 'typed', 'source', 'owner', bad_type, 'synthetic')
        with self.assertRaises(GraphError) as caught:
            baskets.validate_mapping(typed, FIELD_MAP)
        self.assertEqual(caught.exception.code, 'analysis_fields')
