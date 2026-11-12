import json
from pathlib import Path

def load():
    return json.loads((Path(__file__).with_name("context.json")).read_text())

def run():
    from rights_graph.service import RightsService
    from rights_graph.catalog import assets
    data = load()
    with RightsService() as app:
        columns = [{'name': name, 'type': kind, 'classification': 'internal', 'nullable': name == 'item'} for name, kind in [('transaction', 'string'), ('item', 'string'), ('date', 'string'), ('returned', 'boolean')]]
        assets.create(app.store, 'supplies', 'source', 'station', columns, '合成维修站领用明细')
        app.preview.data.import_rows('autumn', 'supplies', 1, data['records'])
        verified = app.preview.data.verify('autumn')
        return {'ok': verified['ok'], 'records': verified['rows']}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
