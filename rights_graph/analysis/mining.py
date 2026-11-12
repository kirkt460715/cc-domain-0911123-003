"""Two-stage association rule mining over frozen discovery candidates.

Candidates are selected only from the discovery window. The later validation
window reuses that exact family: no rule is re-selected, dropped because it is
insignificant, or added. Multiple-testing adjustment runs once over the whole
candidate family.
"""
from decimal import Decimal

from rights_graph.primitives import reject
from rights_graph.analysis import baskets as basket_module
from rights_graph.analysis import stats
from rights_graph.analysis.stats import metric_text


def normalize_request(value):
    from rights_graph.primitives import fields
    fields(
        value,
        {'analysis_id', 'plan_id', 'binding', 'field_map', 'discovery_window', 'validation_window',
         'min_support', 'max_rule_length', 'min_cooccurrence'},
    )
    windows = basket_module.validate_windows(value['discovery_window'], value['validation_window'])
    stats.validate_thresholds(value['min_support'], value['max_rule_length'], value['min_cooccurrence'])
    return value, windows


def ordered_payload(rows):
    return [row['payload'] for row in rows]


def discover(discovery, validation, parameters):
    min_support = Decimal(str(parameters['min_support']))
    max_rule_length = parameters['max_rule_length']
    min_cooccurrence = parameters['min_cooccurrence']
    total = discovery['population']
    if total == 0:
        reject('analysis_population', 'Discovery window contains no selected transactions')
    min_count = min_support * Decimal(total)
    support_floor = -(-min_count // Decimal(1))  # ceil, support must be at least min_support
    support_floor = int(support_floor)
    if support_floor < min_cooccurrence:
        support_floor = min_cooccurrence
    frequent = stats.apriori_counts(discovery['baskets'], support_floor, max_rule_length)
    rules = stats.generate_candidates(frequent, max_rule_length, min_cooccurrence)
    if not rules:
        reject('analysis_no_candidates', 'No closed frequent itemset produced an eligible rule')
    # Counts for every subset appearing in a rule are needed for the real 2x2 tables.
    needed = set()
    for antecedent, consequent in rules:
        union = frozenset(antecedent) | frozenset(consequent)
        needed.add(frozenset(antecedent))
        needed.add(frozenset(consequent))
        needed.add(union)
    discovery_tables = ensure_counts(discovery['baskets'], needed)
    validation_tables = ensure_counts(validation['baskets'], needed)
    records = []
    for index, rule in enumerate(rules):
        antecedent, consequent = rule
        discovery_metrics = stats.metrics(rule, discovery_tables, discovery['population'])
        validation_metrics = stats.metrics(rule, validation_tables, validation['population'])
        a, b, c, d = (validation_metrics['table'][key] for key in ('both', 'antecedent_only', 'consequent_only', 'neither'))
        p_value, fisher_reason = stats.fisher_one_sided(a, b, c, d)
        records.append({
            'rule_key': '|'.join(antecedent) + '=>' + '|'.join(consequent),
            'rank': index,
            'antecedent': list(antecedent),
            'consequent': list(consequent),
            'discovery': discovery_metrics,
            'validation': {
                **validation_metrics,
                'fisher_p': p_value,
                'fisher_reason': fisher_reason,
                'bh_adjusted_p': None,
                'bh_reason': fisher_reason,
                'bh_family_size': None,
                'bh_computable': None,
            },
        })
    stats.benjamini_hochberg(records)
    return {
        'format': 'rights-association-rules',
        'version': 1,
        'parameters': {
            'min_support': metric_text(Decimal(str(parameters['min_support']))),
            'max_rule_length': max_rule_length,
            'min_cooccurrence': min_cooccurrence,
            'support_floor_transactions': support_floor,
        },
        'discovery': window_summary(discovery),
        'validation': window_summary(validation),
        'candidate_count': len(records),
        'rules': records,
    }


def ensure_counts(baskets, itemsets):
    counts = {}
    for itemset in sorted(itemsets, key=lambda itemset: (len(itemset), sorted(itemset))):
        counts[itemset] = sum(1 for basket in baskets if itemset <= basket)
    return counts


def window_summary(summary):
    return {
        'population': summary['population'],
        'empty_baskets': summary['empty_baskets'],
        'nonempty_baskets': summary['nonempty_baskets'],
        'distinct_items': len(summary['items']),
        'excluded_rows': summary['excluded'],
    }
