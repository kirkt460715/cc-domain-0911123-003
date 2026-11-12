import contextlib
import io
import json

from tests.support import Case
from rights_graph.cli import main


class AssociationCliTests(Case):
    def test_association_demo_command(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['--db', ':memory:', 'association-demo', '--fixture', 'fixtures/association_demo.json'])
        self.assertEqual(code, 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value['candidate_count'], 4)
        self.assertFalse(value['delivery_contains_detail_rows'])
        self.assertEqual(value['spurious_rule']['rule_key'], 'gasket=>valve')
        self.assertEqual(value['spurious_rule']['validation_both'], 0)
        self.assertEqual(value['spurious_rule']['validation_bh_adjusted_p'], '1.000000000000')
        self.assertEqual(value['retrieval_after_revocation'], 'report_unavailable')
        self.assertTrue(value['integrity'])
        self.assertTrue(value['repeated_digest_matches'])
        # Repeated detail rows were collapsed; empty and return-only baskets stayed in the denominator.
        self.assertGreaterEqual(value['discovery_population']['excluded_rows']['duplicate_item_rows_collapsed'], 1)
        self.assertGreaterEqual(value['validation_population']['empty_baskets'], 2)

    def test_examples_module_runs_same_demo(self):
        from examples import association
        value = association.run()
        self.assertEqual(value['report_id'], 'stocking-rules-autumn')
        self.assertEqual(value['persistent_rule']['validation_both'], 3)
