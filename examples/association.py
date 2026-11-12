"""Reproducible association rule demonstration on authorized consumable data.

The fixture is synthetic and fully self-contained. It deliberately includes
repeated detail rows, empty baskets, return rows, empty transaction keys and a
gasket/valve pair that looks perfect in the discovery window but disappears in
the validation window.
"""
import json
from pathlib import Path


def load():
    return json.loads(Path(__file__).resolve().parent.parent.joinpath('fixtures', 'association_demo.json').read_text(encoding='utf-8'))


def run():
    from rights_graph.cli import demonstrate_association
    from rights_graph.service import RightsService
    with RightsService() as app:
        return demonstrate_association(app, load())


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
