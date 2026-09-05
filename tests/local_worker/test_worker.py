"""Offline worker tests. HCF_CONTRACT_DIR points to the unchanged supplied schemas."""
import asyncio
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr

from src.local_worker.contract import Contract, Refused, decode, sha
from src.local_worker.cli import perform, entry
from src.local_worker.runtime import Ollama, endpoint, prompt

CONTRACT_DIR=Path(os.environ.get('HCF_CONTRACT_DIR',Path(__file__).resolve().parent/'contracts'))


def packet(kind='challenge_claims'):
    content='Synthetic feature branch is open. Its author reports two passing tests; no execution capture is supplied.'
    return {'schema':'hcf.task/1','task_id':'synthetic_case','kind':kind,'data_classification':'synthetic',
            'policy':{'mode':'artifact_only','tools':False,'external_network':False,'cloud_fallback':False},
            'budget':{'max_model_calls':1,'timeout_seconds':1,'context_tokens':4096,'output_tokens':768},
            'sources':[{'source_id':'s1','kind':'synthetic_text','revision':'fixture','content':content,'sha256':sha(content.encode())}],
            'proposed_claims':[{'claim_id':'c1','text':'The source reports passing tests.'}] if kind=='challenge_claims' else []}


def response():
    return {'model':'gemma3:4b','done':True,'done_reason':'stop','prompt_eval_count':200,'eval_count':70,
            'message':{'role':'assistant','content':json.dumps({'observations':[{'text':'Tests are reported, not independently captured.','source_ids':['s1']}],
            'claim_checks':[{'claim_id':'c1','verdict':'SUPPORTED','reason':'The source reports two passing tests.','source_ids':['s1']}],
            'uncertainties':['Execution is unverified.']})}}


class Fake:
    def __init__(self, value=None, wait=False, offline=False):
        self.value=response() if value is None else value
        self.wait=wait; self.offline=offline
        self.started=False; self.finished=False; self.evidence={}; self.calls=0
        self.entered=asyncio.Event()

    async def run(self, task, contract):
        if self.offline: raise Refused('UNAVAILABLE_NODE')
        self.started=True; self.calls+=1; self.entered.set()
        self.evidence={'digest':'a'*64}
        if self.wait: await asyncio.Event().wait()
        self.finished=self.value.get('done') is True
        return self.value


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.contract=Contract(CONTRACT_DIR)

    def test_schema_hash_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ['task.schema.json','result.schema.json']:
                Path(directory,name).write_bytes((CONTRACT_DIR/name).read_bytes()+b' ')
            with self.assertRaises(Refused): Contract(directory)

    def test_valid_task(self): self.contract.task(json.dumps(packet()).encode())
    def test_duplicate_json(self):
        with self.assertRaises(Refused): decode(b'{"x":1,"x":2}')
    def test_nan(self):
        with self.assertRaises(Refused): decode(b'{"x":NaN}')
    def test_bytes_before_parse(self):
        with self.assertRaises(Refused): decode(b' '*65537)
    def test_unknown_fields_and_policy(self):
        for field,value in [('command','run'),('endpoint','http://example.invalid'),('model','other')]:
            task=packet(); task[field]=value
            with self.assertRaises(Refused): self.contract.task(json.dumps(task).encode())
        for value in [True,0,None,'false']:
            task=packet(); task['policy']['tools']=value
            with self.assertRaises(Refused): self.contract.task(json.dumps(task).encode())
    def test_limits(self):
        for field,value in [('context_tokens',4097),('output_tokens',769),('timeout_seconds',121),('max_model_calls',True),('max_model_calls',2)]:
            task=packet();task['budget'][field]=value
            with self.assertRaises(Refused): self.contract.task(json.dumps(task).encode())
    def test_duplicates_and_hash(self):
        for key in ['sources','proposed_claims']:
            task=packet();task[key].append(copy.deepcopy(task[key][0]))
            with self.assertRaises(Refused): self.contract.task(json.dumps(task).encode())
        task=packet();task['sources'][0]['content']+=' changed'
        with self.assertRaises(Refused): self.contract.task(json.dumps(task).encode())
    def test_combined_source_bytes(self):
        task=packet();task['sources']=[]
        for n in range(3):
            task['sources'].append({'source_id':f's{n}','kind':'synthetic_text','revision':'f','content':'x'*3000,'sha256':sha(b'x'*3000)})
        with self.assertRaises(Refused): self.contract.task(json.dumps(task).encode())
    def test_bounded_prompt(self):
        task=packet();task['budget']['context_tokens']=512
        with self.assertRaises(Refused): prompt(task)
    def test_missing_claims(self):
        task=packet();task['proposed_claims']=[]
        with self.assertRaises(Refused): self.contract.task(json.dumps(task).encode())
    def test_endpoint_is_fixed_loopback(self):
        for value in ['https://example.invalid','http://localhost:1@evil.invalid','http://127.0.0.1:11434/redirect','http://127.0.0.1:11434?x=1']:
            with patch.dict(os.environ,{'OLLAMA_BASE_URL':value}):
                with self.assertRaises(Refused): endpoint()
        with patch.dict(os.environ,{'OLLAMA_BASE_URL':'http://localhost:11434/v1'}):
            self.assertEqual(endpoint(),'http://127.0.0.1:11434')
    def test_cli_does_not_echo_diagnostics(self):
        sentinel='SYNTHETIC_PRIVATE_PATH_OR_TOKEN'
        sink=io.StringIO()
        with patch('sys.argv',['worker','--'+sentinel]),redirect_stderr(sink):
            self.assertEqual(entry(),2)
        self.assertNotIn(sentinel,sink.getvalue())
        self.assertEqual(json.loads(sink.getvalue()),{'error_code':'INVALID_TASK'})


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.contract=Contract(CONTRACT_DIR)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.blob=json.dumps(packet()).encode()

    async def run_worker(self, runtime, blob=None):
        return await perform(blob or self.blob,self.contract,'pc','gemma3:4b',self.temp.name,runtime)

    async def test_complete_and_dedup(self):
        first=Fake(); result=await self.run_worker(first)
        self.assertEqual(result['status'],'COMPLETED');self.assertEqual(first.calls,1)
        second=Fake();self.assertEqual(await self.run_worker(second),result);self.assertEqual(second.calls,0)
        self.assertFalse(Path(self.temp.name,'node.busy.json').exists())

    async def test_truncation(self):
        value=response();value['done_reason']='length'
        result=await self.run_worker(Fake(value))
        self.assertEqual(result['status'],'INVALID_OUTPUT');self.assertEqual(result['completion_state'],'TRUNCATED')
        self.assertEqual(result['metrics']['output_tokens'],70)

    async def test_missing_finish_metadata(self):
        value=response();del value['done_reason']
        result=await self.run_worker(Fake(value))
        self.assertEqual(result['completion_state'],'UNKNOWN');self.assertNotEqual(result['status'],'COMPLETED')

    async def test_incomplete_transport_keeps_node_busy(self):
        value=response();value['done']=False
        result=await self.run_worker(Fake(value))
        self.assertEqual(result['completion_state'],'UNKNOWN')
        self.assertTrue(Path(self.temp.name,'node.busy.json').exists())

    async def test_invalid_model_output(self):
        mutations=[lambda d:d.update(command='run'),lambda d:d['observations'][0].update(source_ids=['invented']),
                   lambda d:d.update(claim_checks=[]),lambda d:d['claim_checks'].append(copy.deepcopy(d['claim_checks'][0])),
                   lambda d:d.update(node_id='mac'),lambda d:d['observations'][0].update(text='x'*501)]
        for mutation in mutations:
            with tempfile.TemporaryDirectory() as directory:
                value=response();data=json.loads(value['message']['content']);mutation(data);value['message']['content']=json.dumps(data)
                result=await perform(self.blob,self.contract,'pc','gemma3:4b',directory,Fake(value))
                self.assertEqual(result['status'],'INVALID_OUTPUT');self.assertEqual(result['observations'],[])

    async def test_provider_tool_request(self):
        value=response();value['message']['tool_calls']=[{'function':{'name':'shell'}}]
        self.assertEqual((await self.run_worker(Fake(value)))['status'],'INVALID_OUTPUT')

    async def test_output_duplicate_json(self):
        value=response();value['message']['content']='{"observations":[],"observations":[]}'
        self.assertEqual((await self.run_worker(Fake(value)))['status'],'INVALID_OUTPUT')

    async def test_timeout_survives_rerun(self):
        fake=Fake(wait=True);result=await self.run_worker(fake)
        self.assertEqual(result['status'],'TIMED_OUT');self.assertEqual(fake.calls,1)
        self.assertTrue(Path(self.temp.name,'node.busy.json').exists())
        second=Fake();self.assertEqual(await self.run_worker(second),result);self.assertEqual(second.calls,0)

    async def test_cancel_and_concurrent_exclusion(self):
        fake=Fake(wait=True); running=asyncio.create_task(self.run_worker(fake));await fake.entered.wait()
        other=packet();other['task_id']='other';otherfake=Fake()
        with self.assertRaises(Refused): await self.run_worker(otherfake,json.dumps(other).encode())
        self.assertEqual(otherfake.calls,0)
        running.cancel();result=await running
        self.assertEqual(result['status'],'CANCELLED');self.assertTrue(Path(self.temp.name,'node.busy.json').exists())

    async def test_offline(self):
        fake=Fake(offline=True);result=await self.run_worker(fake)
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(fake.calls,0)
        self.assertFalse(Path(self.temp.name,'node.busy.json').exists())

    async def test_private_inputs_refused_before_network(self):
        task=packet();task['data_classification']='private_reviewed'
        runtime=Ollama('http://127.0.0.1:1','gemma3:4b')
        result=await self.run_worker(runtime,json.dumps(task).encode())
        self.assertEqual(result['error_code'],'UNVERIFIED_LOCAL_RUNTIME');self.assertFalse(runtime.started)

    async def test_result_write_failure_retains_reservation(self):
        with patch('src.local_worker.queue.os.replace',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError): await self.run_worker(Fake())
        self.assertTrue(Path(self.temp.name,'node.busy.json').exists())
        with self.assertRaises(Refused): await self.run_worker(Fake())

    async def test_known_token_overrun(self):
        value=response();value['eval_count']=769
        result=await self.run_worker(Fake(value))
        self.assertEqual(result['status'],'INVALID_OUTPUT')
        self.assertEqual(result['metrics']['output_tokens'],769)

    async def test_incomplete_journal_cannot_be_cached(self):
        await self.run_worker(Fake())
        for path in Path(self.temp.name).glob('*.runtime.json'): path.unlink()
        with self.assertRaises(OSError): await self.run_worker(Fake())

    async def test_old_python_refuses_before_reservation(self):
        with patch('src.local_worker.cli.sys.version_info',(3,10,0)):
            with self.assertRaises(Refused): await self.run_worker(Fake())
        self.assertEqual(list(Path(self.temp.name).iterdir()),[])

    async def test_known_context_overrun(self):
        value=response();value['prompt_eval_count']=4096
        self.assertEqual((await self.run_worker(Fake(value)))['status'],'INVALID_OUTPUT')

    async def test_runtime_journal_failure_keeps_cache_unpublished(self):
        from src.local_worker.queue import write_new
        def fail_runtime(path,value):
            if str(path).endswith('.runtime.json'): raise OSError('synthetic')
            write_new(path,value)
        with patch('src.local_worker.queue.write_new',side_effect=fail_runtime):
            with self.assertRaises(OSError): await self.run_worker(Fake())
        self.assertEqual(list(Path(self.temp.name).glob('*.result.json')),[])
        with self.assertRaises(Refused): await self.run_worker(Fake())


if __name__=='__main__': unittest.main()
