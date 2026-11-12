"""Association rule statistics implemented only with the Python standard library.

Every probability-like metric is carried as a decimal string so that ordering and
boundary decisions are independent of the host binary floating point environment.
"""
import math
from decimal import Decimal, localcontext, getcontext

from rights_graph.primitives import count, reject

getcontext().prec = 60

QUANT = Decimal('0.0000000001')          # ten decimal places for rule metrics
PVALUE_QUANT = Decimal('0.000000000001')  # twelve decimal places for p-values

# Declared scale limits; requests beyond these are refused rather than truncated.
MAX_ROWS = 100_000
MAX_TRANSACTIONS = 50_000
MAX_DISTINCT_ITEMS = 10_000
MAX_ITEMSETS = 100_000
MAX_RULES = 50_000


def decimal_ratio(numerator, denominator):
    with localcontext() as arithmetic:
        arithmetic.prec = 60
        return (Decimal(numerator) / Decimal(denominator)).quantize(QUANT)


def metric_text(value):
    """Render a Decimal metric as a fixed-point decimal string."""
    return format(value.quantize(QUANT), 'f')


def pvalue_text(value):
    return format(value.quantize(PVALUE_QUANT), 'f')


def validate_thresholds(min_support, max_rule_length, min_cooccurrence):
    if type(min_support) is bool or type(min_support) not in (float, int):
        reject('analysis_threshold', 'min_support must be a number')
    if not 0.0 < float(min_support) <= 1.0:
        reject('analysis_threshold', 'min_support must lie inside (0, 1]')
    count(max_rule_length, 'max_rule_length', 2, 10)
    count(min_cooccurrence, 'min_cooccurrence', 1, MAX_TRANSACTIONS)


def apriori_counts(baskets, min_count, max_length):
    """Return every itemset whose support count reaches ``min_count``.

    ``baskets`` is a list of frozensets. Itemsets are frozensets; the returned
    mapping is built in a deterministic, lexicographically ordered pass so that
    identical inputs always produce identical iteration order.
    """
    total_items = set()
    counts = {}
    for basket in baskets:
        for item in basket:
            total_items.add(item)
    if len(total_items) > MAX_DISTINCT_ITEMS:
        reject('analysis_scale', 'Distinct item count exceeds declared limit', limit=MAX_DISTINCT_ITEMS)
    level = []
    for item in sorted(total_items):
        itemset = frozenset((item,))
        occurrences = sum(1 for basket in baskets if itemset <= basket)
        if occurrences >= min_count:
            counts[itemset] = occurrences
            level.append(itemset)
    length = 1
    while level and length < max_length:
        next_level = []
        prefix_join = {}
        for itemset in level:
            ordered = sorted(itemset)
            prefix_join.setdefault(tuple(ordered[:-1]), []).append(ordered[-1])
        candidates = set()
        for prefix, tails in prefix_join.items():
            if len(tails) < 2:
                continue
            for index, left in enumerate(sorted(tails)):
                for right in tails[index + 1:]:
                    candidates.add(frozenset(prefix + (left, right)))
        for candidate in sorted(candidates, key=lambda itemset: sorted(itemset)):
            if len(counts) + len(next_level) >= MAX_ITEMSETS:
                reject('analysis_scale', 'Frequent itemset count exceeds declared limit', limit=MAX_ITEMSETS)
            occurrences = sum(1 for basket in baskets if candidate <= basket)
            if occurrences >= min_count:
                counts[candidate] = occurrences
                next_level.append(candidate)
        level = next_level
        length += 1
    return counts


def closed_itemsets(counts):
    """Keep itemsets with no same-count strict superset."""
    ordered = sorted(counts, key=lambda itemset: (len(itemset), sorted(itemset)))
    result = {}
    for itemset in ordered:
        closed = True
        own_count = counts[itemset]
        for other, other_count in counts.items():
            if len(other) > len(itemset) and other_count == own_count and itemset < other:
                closed = False
                break
        if closed:
            result[itemset] = own_count
    return result


def generate_candidates(frequent_counts, max_rule_length, min_cooccurrence):
    """Produce candidate rules from closed frequent itemsets.

    Rules have nonempty, mutually exclusive antecedent and consequent whose union
    is a closed frequent itemset. Duplicates generated through different closed
    supersets are collapsed by a stable rule key.
    """
    rules = {}
    for itemset in sorted(closed_itemsets(frequent_counts), key=lambda itemset: (len(itemset), sorted(itemset))):
        members = sorted(itemset)
        if len(members) < 2:
            continue
        for mask in range(1, (1 << len(members)) - 1):
            antecedent = frozenset(members[index] for index in range(len(members)) if mask & (1 << index))
            consequent = itemset - antecedent
            if not antecedent or not consequent or len(antecedent) + len(consequent) > max_rule_length:
                continue
            if frequent_counts[itemset] < min_cooccurrence:
                continue
            key = (tuple(sorted(antecedent)), tuple(sorted(consequent)))
            rules.setdefault(key, itemset)
    ordered = sorted(rules, key=lambda key: (key[0], key[1]))
    if len(ordered) > MAX_RULES:
        reject('analysis_scale', 'Candidate rule count exceeds declared limit', limit=MAX_RULES)
    return ordered


def rule_counts(rule, counts_table):
    """Actual 2x2 contingency counts for a fixed rule."""
    antecedent, consequent = rule
    left = frozenset(antecedent)
    right = frozenset(consequent)
    both = left | right
    a = counts_table.get(both, 0)
    b = counts_table.get(left, 0) - a
    c = counts_table.get(right, 0) - a
    return a, b, c


def metrics(rule, counts_table, total):
    """Support, confidence, lift and leverage for one rule on one population."""
    a, b, c = rule_counts(rule, counts_table)
    d = total - a - b - c
    result = {
        'n': total,
        'table': {'both': a, 'antecedent_only': b, 'consequent_only': c, 'neither': d},
        'support': None,
        'confidence': None,
        'lift': None,
        'leverage': None,
        'reason': None,
    }
    if total <= 0:
        result['reason'] = 'empty_population'
        return result
    n = Decimal(total)
    both = Decimal(a)
    left_n = Decimal(a + b)
    right_n = Decimal(a + c)
    result['support'] = metric_text(decimal_ratio(a, total))
    if left_n == 0:
        result['reason'] = 'antecedent_never_observed'
        return result
    confidence = both / left_n
    result['confidence'] = metric_text(confidence)
    if right_n == 0:
        result['reason'] = 'consequent_never_observed'
        return result
    with localcontext() as arithmetic:
        arithmetic.prec = 60
        lift = confidence * n / right_n
        leverage = both / n - (left_n / n) * (right_n / n)
    result['lift'] = metric_text(lift)
    result['leverage'] = metric_text(leverage)
    return result


def log_choose(n, k):
    if k < 0 or k > n:
        return float('-inf')
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_one_sided(a, b, c, d):
    """Upper-tail Fisher exact test for positive association.

    Returns the decimal string of P(X >= a) for the fixed margins of the 2x2
    table, or ``None`` with a reason when the test is not computable.
    """
    total = a + b + c + d
    row_left = a + b
    row_right = c + d
    col_both = a + c
    if total <= 0 or min(row_left, row_right, col_both, total - col_both) <= 0:
        return None, 'degenerate_margins'
    upper = min(row_left, col_both)
    log_denominator = log_choose(total, row_left)
    log_terms = []
    for observed in range(a, upper + 1):
        log_probability = (
            log_choose(col_both, observed)
            + log_choose(total - col_both, row_left - observed)
            - log_denominator
        )
        log_terms.append(log_probability)
    if not log_terms:
        return None, 'fisher_uncomputable'
    floor = max(log_terms)
    p_value = sum(math.exp(value - floor) for value in log_terms) * math.exp(floor)
    if p_value <= 0.0 or not math.isfinite(p_value):
        return None, 'fisher_uncomputable'
    return pvalue_text(Decimal(repr(min(1.0, p_value)))), None


def benjamini_hochberg(entries):
    """BH adjustment over the whole frozen candidate family.

    ``entries`` is the list of rule records in stable order. Every candidate is
    retained, including non-computable and non-significant ones; adjustment is
    never repeated after discarding members.
    """
    computable = [entry for entry in entries if entry['validation']['fisher_p'] is not None]
    family_size = len(entries)
    m = len(computable)
    ranked = sorted(
        computable,
        key=lambda entry: (Decimal(entry['validation']['fisher_p']), entry['rule_key']),
    )
    adjusted = {}
    running = Decimal(1)
    for reverse_rank, entry in enumerate(reversed(ranked), start=1):
        rank = m - reverse_rank + 1
        raw = Decimal(entry['validation']['fisher_p'])
        value = raw * Decimal(m) / Decimal(rank)
        running = min(running, value)
        adjusted[entry['rule_key']] = pvalue_text(min(Decimal(1), running))
    for entry in entries:
        entry['validation']['bh_adjusted_p'] = adjusted.get(entry['rule_key'])
        entry['validation']['bh_family_size'] = family_size
        entry['validation']['bh_computable'] = m
        if entry['validation']['fisher_p'] is None and entry['validation']['bh_reason'] is None:
            entry['validation']['bh_reason'] = 'p_value_uncomputable'
    return entries
