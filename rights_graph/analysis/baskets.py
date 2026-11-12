"""Turn authorized detail rows into transaction baskets.

Rows with an empty transaction key cannot be assigned to any transaction and are
excluded from every basket but counted. A transaction inside the selected date
window keeps its identity before row-level filtering, so a return-only
transaction survives as an empty basket even though its return rows describe
goods coming back rather than co-use. Missing item values likewise keep the
transaction identity alive (an empty basket), while repeated detail rows of the
same item collapse to a single membership. Empty baskets that belong to the
selected population always remain in the support denominator.
"""
import datetime as dt

from rights_graph.primitives import reject
from rights_graph.analysis import stats


def iso_date(value, field):
    if not isinstance(value, str):
        reject('analysis_window', 'Window boundary must be an ISO calendar date', field=field)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise _date_error(field) from exc
    if parsed.isoformat() != value:
        raise _date_error(field)
    return parsed


def _date_error(field):
    from rights_graph.primitives import GraphError
    return GraphError('analysis_window', 'Window boundary must be an ISO calendar date', field=field)


def validate_windows(discovery, validation):
    if not isinstance(discovery, (list, tuple)) or len(discovery) != 2 or \
            not isinstance(validation, (list, tuple)) or len(validation) != 2:
        reject('analysis_window', 'Each window must be a two-element [start, end] pair')
    discover_start, discover_end = iso_date(discovery[0], 'discovery_start'), iso_date(discovery[1], 'discovery_end')
    validate_start, validate_end = iso_date(validation[0], 'validation_start'), iso_date(validation[1], 'validation_end')
    if discover_start > discover_end or validate_start > validate_end:
        reject('analysis_window', 'Window start must not follow its end')
    if validate_start <= discover_end:
        reject('analysis_window', 'Validation window must start strictly after the discovery window ends')
    return (discover_start, discover_end), (validate_start, validate_end)


def validate_mapping(asset, field_map):
    required = {'transaction', 'item', 'date', 'returned'}
    if not isinstance(field_map, dict) or set(field_map) != required:
        reject('analysis_fields', 'Field map must name transaction, item, date and returned columns', required=sorted(required))
    by_name = {column['name']: column for column in asset['schema']}
    roles = {}
    for role, column_name in field_map.items():
        if not isinstance(column_name, str) or column_name not in by_name:
            reject('analysis_fields', 'Mapped column does not exist on the planned asset', role=role, column=column_name)
        roles[role] = by_name[column_name]
    if len({column['name'] for column in roles.values()}) != 4:
        reject('analysis_fields', 'Each role must map to a distinct column')
    if roles['transaction']['type'] != 'string' or roles['item']['type'] != 'string':
        reject('analysis_fields', 'Transaction and item keys must be string columns')
    if roles['date']['type'] != 'string':
        reject('analysis_fields', 'Transaction date must be a string column holding ISO calendar dates')
    if roles['returned']['type'] != 'boolean':
        reject('analysis_fields', 'Return indicator must be a boolean column')
    return roles


def _row_date(value):
    if not isinstance(value, str):
        reject('analysis_date', 'Transaction date value is not an ISO calendar date')
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise _date_error('date') from exc
    if parsed.isoformat() != value:
        raise _date_error('date')
    return parsed


def build_baskets(rows, field_map, window):
    """Build deduplicated baskets for one window and explain every exclusion."""
    start, end = window
    roles = field_map
    transactions = {}
    excluded = {
        'empty_transaction_key_rows': 0,
        'return_rows': 0,
        'rows_outside_window': 0,
        'missing_item_rows': 0,
        'duplicate_item_rows_collapsed': 0,
    }
    if len(rows) > stats.MAX_ROWS:
        reject('analysis_scale', 'Source row count exceeds declared limit', limit=stats.MAX_ROWS)
    for row in rows:
        key = row[roles['transaction']['name']]
        if key is None or (isinstance(key, str) and not key):
            excluded['empty_transaction_key_rows'] += 1
            continue
        business_date = _row_date(row[roles['date']['name']])
        if not start <= business_date <= end:
            excluded['rows_outside_window'] += 1
            continue
        # The transaction identity belongs to the selected population before any
        # row-level filtering, so a return-only transaction remains an empty basket.
        basket = transactions.setdefault(key, set())
        if bool(row[roles['returned']['name']]):
            excluded['return_rows'] += 1
            continue
        item = row[roles['item']['name']]
        if item is None or (isinstance(item, str) and not item):
            excluded['missing_item_rows'] += 1
            continue
        if item in basket:
            excluded['duplicate_item_rows_collapsed'] += 1
            continue
        basket.add(item)
    if len(transactions) > stats.MAX_TRANSACTIONS:
        reject('analysis_scale', 'Distinct transaction count exceeds declared limit', limit=stats.MAX_TRANSACTIONS)
    basket_list = [frozenset(transactions[key]) for key in sorted(transactions)]
    empty_baskets = sum(1 for basket in basket_list if not basket)
    nonempty = sum(1 for basket in basket_list if basket)
    return {
        'baskets': basket_list,
        'population': len(basket_list),
        'empty_baskets': empty_baskets,
        'nonempty_baskets': nonempty,
        'excluded': excluded,
        'items': sorted({item for basket in basket_list for item in basket}),
    }
