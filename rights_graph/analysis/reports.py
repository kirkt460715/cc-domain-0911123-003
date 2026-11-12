"""Persisted association rule deliverables bound to the authorization chain.

A report can only be produced from an approved plan. The plan pins the source
asset version and selected columns; the bound batch must match that version and
the request context must pass every grant, region, purpose and obligation check
already used by lineage artifacts. Only aggregate rule output is stored and
delivered — transaction detail rows never leave the analysis boundary.
"""
import json

from rights_graph.primitives import canonical, checksum, name, reject
from rights_graph.catalog import assets
from rights_graph.planner import plans, artifacts
from rights_graph.analysis import baskets as basket_module
from rights_graph.analysis import mining
from rights_graph.journal import audit

SCHEMA = '''
CREATE TABLE IF NOT EXISTS analysis_reports(
 report_id TEXT PRIMARY KEY,artifact_id TEXT NOT NULL,plan_id TEXT NOT NULL,
 batch_id TEXT NOT NULL,specification TEXT NOT NULL,payload TEXT NOT NULL,
 digest TEXT NOT NULL,created_at TEXT NOT NULL);
'''


class AnalysisDesk:
    def __init__(self, store, data_catalog=None):
        self.store = store
        self._injected_catalog = data_catalog
        if store.depth:
            reject('transaction_scope', 'Initialize analysis desk outside transaction')
        store.db.executescript(SCHEMA)

    def run(self, request):
        specification, windows = mining.normalize_request(request)
        name(specification['analysis_id'], 'analysis_id')
        name(specification['binding'], 'binding')
        with self.store.atomic():
            existing = self.store.one('SELECT specification FROM analysis_reports WHERE report_id=?', (specification['analysis_id'],))
            if existing is not None:
                if existing['specification'] != canonical(specification):
                    reject('analysis_conflict', 'Report identifier already refers to a different frozen specification',
                           report_id=specification['analysis_id'])
                return self.get(specification['analysis_id'])
            plan = plans.get(self.store, specification['plan_id'])
            plans.require_fresh(self.store, specification['plan_id'])
            steps = plan['payload']['steps']
            source_steps = [step for step in steps if step['kind'] == 'source']
            if len(steps) != 1 or len(source_steps) != 1:
                reject('analysis_plan', 'Association analysis requires a plan over a single source asset')
            step = source_steps[0]
            asset = assets.get(self.store, step['asset_id'], step['version'])
            roles = basket_module.validate_mapping(asset, specification['field_map'])
            mapped_columns = sorted({column['name'] for column in roles.values()})
            if any(column not in step['columns'] for column in mapped_columns):
                reject('analysis_columns', 'Mapped column is not authorized by the approved plan', columns=mapped_columns)
            batch = self._load_batch(step, specification['binding'])
            evidence = self._authorize(plan, step, batch)
            rows = mining.ordered_payload(batch['rows'])
            discovery = basket_module.build_baskets(rows, roles, windows[0])
            validation = basket_module.build_baskets(rows, roles, windows[1])
            result = mining.discover(discovery, validation, specification)
            result['report_id'] = specification['analysis_id']
            result['plan_id'] = plan['plan_id']
            result['artifact_id'] = evidence['artifact_id']
            result['batch_id'] = batch['batch']['batch_id']
            result['source'] = {
                'asset_id': step['asset_id'],
                'asset_version': step['version'],
                'content_hash': step['content_hash'],
                'columns': step['columns'],
            }
            result['field_map'] = specification['field_map']
            result['windows'] = {
                'discovery': specification['discovery_window'],
                'validation': specification['validation_window'],
            }
            result['context'] = plan['context']
            result['lineage'] = evidence['lineage']
            result['policy'] = evidence['policy']
            sha = checksum(result)
            self.store.execute(
                'INSERT INTO analysis_reports(report_id,artifact_id,plan_id,batch_id,specification,payload,digest,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (specification['analysis_id'], evidence['artifact_id'], plan['plan_id'], batch['batch']['batch_id'],
                 canonical(specification), canonical(result), sha, self.store.clock()),
            )
            audit.append(self.store, 'analysis.report_created', {
                'report_id': specification['analysis_id'],
                'artifact_id': evidence['artifact_id'],
                'candidates': result['candidate_count'],
                'digest': sha,
            })
            return self.get(specification['analysis_id'])

    def _load_batch(self, step, batch_id):
        data = self._catalog()
        batch = data.get(batch_id)
        if batch['asset_id'] != step['asset_id'] or batch['asset_version'] != step['version']:
            reject('analysis_version', 'Bound batch does not match planned source version', asset_id=step['asset_id'])
        data.verify(batch_id)
        rows = data.rows(batch_id, limit=100000)
        if len(rows) != batch['row_count']:
            reject('analysis_batch', 'Bound batch is incomplete', batch_id=batch_id)
        return {'batch': batch, 'rows': rows}

    def _catalog(self):
        if self._injected_catalog is not None:
            return self._injected_catalog
        from rights_graph.catalog.records import DataCatalog
        if not hasattr(self, '_data'):
            self._data = DataCatalog(self.store)
        return self._data

    def _authorize(self, plan, step, batch):
        artifact_id = self._ensure_artifact(plan, step)
        decision = artifacts.verify(self.store, artifact_id, plan['context'])
        if not decision['valid']:
            reject('analysis_unauthorized', 'Plan context is not authorized for the planned source columns', failures=decision['failures'])
        artifact = artifacts.get(self.store, artifact_id)
        return {
            'artifact_id': artifact['artifact_id'],
            'lineage': json.loads(artifact['lineage']) if isinstance(artifact['lineage'], str) else artifact['lineage'],
            'policy': artifact['policy'],
        }

    def _ensure_artifact(self, plan, step):
        """Reuse an existing valid artifact for this plan step, else build one."""
        build_id = 'analysis-build-' + checksum({'plan_id': plan['plan_id'], 'step': step['signature']})[:48]
        from rights_graph.planner import builds
        existing = self.store.one('SELECT build_id FROM builds WHERE build_id=?', (build_id,))
        if existing is None:
            builds.create(self.store, build_id, plan['plan_id'])
            builds.execute(self.store, build_id, 'analysis-worker')
        completed = builds.get(self.store, build_id)
        if completed['state'] != 'complete':
            builds.execute(self.store, build_id, 'analysis-worker')
            completed = builds.get(self.store, build_id)
        return completed['result']['target_artifact']

    def get(self, report_id):
        name(report_id, 'report_id')
        row = self.store.one('SELECT * FROM analysis_reports WHERE report_id=?', (report_id,))
        if row is None:
            reject('unknown_report', 'Association analysis report does not exist')
        payload = json.loads(row['payload'])
        if checksum(payload) != row['digest']:
            reject('report_corrupt', 'Stored report checksum mismatch')
        decision = artifacts.verify(self.store, row['artifact_id'])
        if not decision['valid']:
            reject('report_unavailable', 'Report authorization is no longer valid', failures=decision['failures'])
        return {'report_id': report_id, 'artifact_id': row['artifact_id'], 'payload': payload, 'sha256': row['digest'], 'authorization': decision}

    def listing(self):
        rows = self.store.all('SELECT report_id FROM analysis_reports ORDER BY created_at,report_id')
        available = []
        for row in rows:
            try:
                available.append(self.get(row['report_id']))
            except Exception:
                # Revoked or invalidated reports remain in the ledger but cannot be retrieved.
                continue
        return available

    def deliver(self, delivery_id, report_id, recipient):
        """Package only the aggregate payload for distribution under a live grant."""
        name(delivery_id, 'delivery_id')
        name(recipient, 'recipient')
        report = self.get(report_id)
        artifact = artifacts.get(self.store, report['artifact_id'])
        from rights_graph.policy import algebra
        request = {**report['payload']['context'], 'right': 'distribute'}
        decision = algebra.evaluate(artifact['policy'], request)
        if not decision['allowed']:
            reject('report_delivery_denied', 'Distribution is not authorized for the report context', failures=decision['reasons'])
        envelope = {
            'format': 'rights-association-delivery',
            'version': 1,
            'delivery_id': delivery_id,
            'recipient': recipient,
            'report_id': report_id,
            'aggregate_payload': report['payload'],
            'authorization': {
                'artifact_id': artifact['artifact_id'],
                'policy': artifact['policy'],
                'lineage': artifact['lineage'],
                'context': decision['context'],
            },
        }
        sha = checksum(envelope)
        audit.append(self.store, 'analysis.report_delivered', {'delivery_id': delivery_id, 'report_id': report_id, 'digest': sha})
        return {'delivery_id': delivery_id, 'payload': envelope, 'sha256': sha}
