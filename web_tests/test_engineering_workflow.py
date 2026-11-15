"""Engineering ownership, revision-safe feedback and immutable candidate baselines."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from webapp.app import create_app
from webapp.engineering_workflow import parameter_ownership, fingerprint, workflow_catalog
from webapp.ui_schema import design_ui_schema
from webapp.store import Store
from web_tests.test_throttle_analysis import source_fixture
from web_tests.test_workspace import client, TEST_PASSWORD


class EngineeringWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'workspace.sqlite3'
        self.app = create_app(self.path, queue_enabled=False, private_workspace=True)
        self.client = client(self.app)
        self.addCleanup(self.client.close)
        self.assertEqual(self.client.post('/api/auth/setup', json={
            'username': 'admin', 'display_name': '流程测试', 'password': TEST_PASSWORD}).status_code, 200)
        self.source = source_fixture()
        self.source['inputs']['combustion_loss_model'] = 'adiabatic'
        self.source['result']['calculation']['inputs']['combustion_loss_model'] = 'adiabatic'
        self.project = self.client.post('/api/projects', json={
            'name': '工程流程', 'inputs': self.source['inputs']}).json()['project']
        self.url = f"/api/projects/{self.project['id']}"
        response = self.client.post(self.url+'/jobs', json={'revision': 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.job = response.json()['id']
        self.store = self.app.state.store
        self.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",
                           (json.dumps(self.source['result']), self.job))
        self.before_job = self.store.one('SELECT * FROM jobs WHERE id=?', (self.job,))
        self.key = next(f['key'] for g in design_ui_schema()['system_groups'] if g['id']=='pumps'
                        for f in g['fields'] if f['key']=='lox_pump_n_stages')

    def feedback(self, **overrides):
        body = {'revision': 1, 'source_job_id': self.job, 'component': 'pumps',
                'patch': {self.key: 2}}
        body.update(overrides)
        return self.client.post(self.url+'/engineering/iterations', json=body)

    def test_parameter_owners_and_model_choices_keep_legacy_values(self):
        schema = design_ui_schema()
        owners = parameter_ownership(schema['system_groups'])
        self.assertEqual(owners['expansion_ratio']['group'], 'nozzle_performance')
        self.assertEqual(owners[self.key]['component'], 'pumps')
        self.assertEqual(owners['cooling_bartz_mode']['stage'], 'concept')
        self.assertEqual(len(workflow_catalog()['stages']), 5)
        cooling = next(g for g in schema['system_groups'] if g['id']=='cooling')
        field = next(f for f in cooling['fields'] if f['key']=='cooling_bartz_mode')
        self.assertFalse(field['read_only'])
        self.assertEqual([c['value'] for c in field['choices']], [2, 1, 0])
        self.assertEqual(self.client.get(self.url).json()['inputs'], self.project['inputs'])

    def test_component_feedback_is_atomic_scoped_and_preserves_history(self):
        response = self.feedback()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['project']['revision'], 2)
        expected = deepcopy(self.project['inputs']); expected[self.key] = 2
        self.assertEqual(data['project']['inputs'], expected)
        self.assertEqual(data['snapshot']['target_inputs_sha256'], fingerprint(expected))
        self.assertEqual(self.store.one('SELECT * FROM jobs WHERE id=?', (self.job,)), self.before_job)
        self.assertEqual(self.client.get(self.url+'/revisions/1').json()['inputs'], self.project['inputs'])
        self.assertEqual(self.feedback().status_code, 409)
        listing = self.client.get(self.url+'/engineering').json()
        self.assertEqual(len(listing['iterations']), 1)
        self.assertEqual(listing['iterations'][0]['status'], 'awaiting_recompute')
        job = self.client.post(self.url+'/jobs', json={'revision': 2}).json()['id']
        self.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",
                           (json.dumps(self.source['result']), job))
        iteration = self.client.get(self.url+'/engineering').json()['iterations'][0]
        self.assertEqual(iteration['review_job_id'], job)
        self.assertEqual(iteration['status'], 'recomputed')
        self.assertEqual(iteration['numerical_review']['status'], 'pass')

    def test_completed_run_with_failed_residuals_does_not_pass_integration_review(self):
        self.assertEqual(self.feedback().status_code, 200)
        job = self.client.post(self.url+'/jobs', json={'revision': 2}).json()['id']
        failed = deepcopy(self.source['result'])
        failed['calculation']['summary']['solver_residual_norm'] = 1
        self.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?", (json.dumps(failed), job))
        iteration = self.client.get(self.url+'/engineering').json()['iterations'][0]
        self.assertEqual(iteration['status'], 'recomputed')
        self.assertEqual(iteration['numerical_review']['status'], 'fail')
        baseline = self.client.post(self.url+'/engineering/baselines', json={'source_job_id': job}).json()
        self.assertEqual(baseline['snapshot']['payload']['numerical_evidence']['status'], 'fail')
        self.assertFalse(baseline['snapshot']['steady_runnable'])

    def test_saved_component_changes_can_be_registered_without_extra_revision(self):
        edited = {k: deepcopy(self.project[k]) for k in ('name','description','revision','inputs')}
        edited['inputs'][self.key] = 2
        self.assertEqual(self.client.put(self.url, json=edited).status_code, 200)
        response = self.feedback(revision=2)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['project']['revision'], 2)
        self.assertEqual(response.json()['snapshot']['saved_parameter_changes'], [])
        self.assertEqual(response.json()['snapshot']['changes'][0]['before'], self.project['inputs'][self.key])
        retry = self.feedback(revision=2)
        self.assertEqual(retry.json()['id'], response.json()['id'])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM component_iterations')['n'], 1)

    def test_later_integrated_run_can_review_multiple_edits_without_rerunning_intermediate_versions(self):
        self.assertEqual(self.feedback().status_code, 200)
        edited = self.client.get(self.url).json()
        payload = {key: deepcopy(edited[key]) for key in ('name', 'description', 'revision', 'inputs')}
        payload['inputs']['thrust'] *= .99
        self.assertEqual(self.client.put(self.url, json=payload).status_code, 200)
        job = self.client.post(self.url+'/jobs', json={'revision': 3}).json()['id']
        self.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",
                           (json.dumps(self.source['result']), job))
        iteration = self.client.get(self.url+'/engineering').json()['iterations'][0]
        self.assertEqual(iteration['review_job_id'], job)
        self.assertEqual(iteration['status'], 'recomputed_with_changes')
        # A later run that discarded this component edit is not its review.
        changed = json.loads(self.store.one('SELECT inputs FROM jobs WHERE id=?', (job,))['inputs'])
        changed[self.key] = self.project['inputs'][self.key]
        self.store.execute('UPDATE jobs SET inputs=? WHERE id=?', (json.dumps(changed), job))
        self.assertEqual(self.client.get(self.url+'/engineering').json()['iterations'][0]['status'], 'awaiting_recompute')

    def test_component_feedback_can_restore_a_saved_value_to_its_source(self):
        self.assertEqual(self.feedback().status_code, 200)
        original = self.project['inputs'][self.key]
        response = self.feedback(revision=2, patch={self.key: original})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['project']['revision'], 3)
        self.assertEqual(result['project']['inputs'][self.key], original)
        self.assertEqual(result['snapshot']['previous_saved_values'], {self.key: 2})
        self.assertEqual(result['snapshot']['changes'], [{'key': self.key, 'before': original, 'after': original}])
        self.assertEqual(self.store.one('SELECT * FROM jobs WHERE id=?', (self.job,)), self.before_job)

    def test_feedback_rejects_other_owners_invalid_numbers_and_incomplete_sources(self):
        for patch in ({'pc': 1e7}, {'expansion_ratio': 40}, {self.key: -1}, {'unknown': 1}, {}):
            with self.subTest(patch=patch):
                self.assertEqual(self.feedback(patch=patch).status_code, 422)
        self.store.execute("UPDATE jobs SET status='failed' WHERE id=?", (self.job,))
        self.assertIn(self.feedback().status_code, (409,422))
        self.assertEqual(self.client.get(self.url).json()['revision'], 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM component_iterations')['n'], 0)

    def test_candidate_snapshot_is_idempotent_immutable_and_not_a_runnable_baseline(self):
        capture = lambda: self.client.post(self.url+'/engineering/baselines', json={'source_job_id': self.job})
        response = capture()
        self.assertEqual(response.status_code, 200, response.text)
        baseline = response.json()
        self.assertTrue(baseline['integrity'])
        self.assertFalse(baseline['snapshot']['steady_runnable'])
        self.assertEqual(baseline['snapshot']['nominal_verification'], 'unavailable')
        self.assertTrue(baseline['snapshot']['blockers'])
        self.assertEqual(capture().json(), baseline)
        self.feedback()
        url = self.url+'/engineering/baselines/'+baseline['id']
        self.assertEqual(self.client.get(url).json(), baseline)
        self.assertEqual(self.client.get(url+'/download').json(), baseline)
        self.assertEqual(self.client.put(url, json={'snapshot': {}}).status_code, 405)
        self.assertEqual(self.store.one('SELECT * FROM jobs WHERE id=?', (self.job,)), self.before_job)
        restarted = Store(self.path)
        self.assertEqual(json.loads(restarted.one('SELECT snapshot FROM hardware_baselines')['snapshot']), baseline['snapshot'])

    def test_source_and_baseline_cannot_cross_project_boundaries(self):
        other = self.client.post('/api/projects', json={'name':'另一个方案','inputs':self.project['inputs']}).json()['project']
        url = f"/api/projects/{other['id']}/engineering"
        for suffix,body in [('/baselines',{'source_job_id':self.job}),('/iterations',{
            'revision':1,'source_job_id':self.job,'component':'pumps','patch':{self.key:2}})]:
            self.assertIn(self.client.post(url+suffix,json=body).status_code,(404,422))
        baseline = self.client.post(self.url+'/engineering/baselines',json={'source_job_id':self.job}).json()
        self.assertEqual(self.client.get(url+'/baselines/'+baseline['id']).status_code,404)
        with client(self.app) as guest:
            self.assertEqual(guest.get(self.url+'/engineering').status_code,401)

    def test_private_member_can_access_only_their_own_baselines(self):
        self.assertEqual(self.client.post('/api/members', json={'username':'member',
            'display_name':'成员','password':TEST_PASSWORD}).status_code,200)
        baseline = self.client.post(self.url+'/engineering/baselines',json={'source_job_id':self.job}).json()
        with client(self.app) as member:
            self.assertEqual(member.post('/api/auth/login',json={'username':'member','password':TEST_PASSWORD}).status_code,200)
            for suffix in ('/engineering','/engineering/baselines/'+baseline['id'],
                           '/engineering/baselines/'+baseline['id']+'/download'):
                self.assertEqual(member.get(self.url+suffix).status_code,404)
            own = member.post('/api/projects',json={'name':'成员方案','inputs':self.project['inputs']}).json()['project']
            url = f"/api/projects/{own['id']}"
            job = member.post(url+'/jobs',json={'revision':1}).json()['id']
            self.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",(json.dumps(self.source['result']),job))
            response = member.post(url+'/engineering/baselines',json={'source_job_id':job})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(member.get(url+'/engineering/baselines/'+response.json()['id']).status_code,200)


if __name__ == '__main__':
    unittest.main()
