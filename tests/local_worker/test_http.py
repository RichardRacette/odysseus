"""Actual HTTP and production CLI boundaries against a synthetic loopback server."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from test_worker import packet, response, CONTRACT_DIR
from src.local_worker.contract import Contract
from src.local_worker.cli import perform
from src.local_worker.runtime import Ollama

def http_packet():
    task=packet()
    # Transport success is independent of cold dependency-import speed. The
    # dedicated timeout/cancel tests retain their one-second deadline.
    task['budget']['timeout_seconds']=5
    return task


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls=[]; self.remote=False;self.overflow=False;self.redirect=False
        self.server=await asyncio.start_server(self.handle,'127.0.0.1',0)
        self.base='http://127.0.0.1:'+str(self.server.sockets[0].getsockname()[1])
        self.contract=Contract(CONTRACT_DIR)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)

    async def asyncTearDown(self):
        self.server.close();await self.server.wait_closed()

    async def handle(self,reader,writer):
        try:
            header=await reader.readuntil(b'\r\n\r\n')
            lines=header.decode().split('\r\n');method,path,_=lines[0].split()
            length=next((int(line.split(':',1)[1]) for line in lines if line.lower().startswith('content-length:')),0)
            data=json.loads(await reader.readexactly(length)) if length else None
            self.calls.append((path,data))
            if path=='/api/tags': value={'models':[{'name':'gemma3:4b','digest':'a'*64,'size':123,'remote_host':'example.invalid' if self.remote else ''}]}
            elif path=='/api/show': value={'capabilities':['completion'],'details':{'family':'gemma3'}}
            elif path=='/api/version': value={'version':'fixture'}
            elif path=='/api/ps': value={'models':[]}
            else: value=response()
            blob=b'x'*32769 if self.overflow and path=='/api/chat' else json.dumps(value).encode()
            status='302 Found\r\nLocation: http://example.invalid' if self.redirect else '200 OK'
            writer.write(('HTTP/1.1 '+status+'\r\nContent-Type: application/json\r\nContent-Length: '+str(len(blob))+'\r\nConnection: close\r\n\r\n').encode()+blob)
            await writer.drain()
        finally:
            writer.close();await writer.wait_closed()

    async def run_worker(self):
        return await perform(json.dumps(http_packet()).encode(),self.contract,'pc','gemma3:4b',self.temp.name,Ollama(self.base,'gemma3:4b'))

    async def test_actual_transport_and_fixed_payload(self):
        result=await self.run_worker();self.assertEqual(result['status'],'COMPLETED')
        requests=[body for path,body in self.calls if path=='/api/chat']
        self.assertEqual(len(requests),1);body=requests[0]
        self.assertFalse(body['stream']);self.assertNotIn('tools',body)
        self.assertEqual(body['format']['type'],'object')
        self.assertEqual(set(body['format']['properties']),{'observations','claim_checks','uncertainties'})
        self.assertEqual(body['options']['num_ctx'],4096);self.assertEqual(body['options']['num_predict'],768)

    async def test_cloud_descriptor_blocks_before_inference(self):
        self.remote=True;result=await self.run_worker()
        self.assertEqual(result['error_code'],'UNVERIFIED_LOCAL_RUNTIME')
        self.assertEqual([path for path,_ in self.calls],['/api/tags'])

    async def test_redirect_is_not_followed(self):
        self.redirect=True;result=await self.run_worker()
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(len(self.calls),1)

    async def test_response_overflow_retains_ambiguity(self):
        self.overflow=True;result=await self.run_worker()
        self.assertEqual(result['status'],'INVALID_OUTPUT')
        self.assertTrue(Path(self.temp.name,'node.busy.json').exists())

    async def test_production_cli_and_restart_dedup(self):
        file=Path(self.temp.name,'task.json');file.write_text(json.dumps(http_packet()))
        env=dict(os.environ,OLLAMA_BASE_URL=self.base,ODYSSEUS_DATA_DIR=str(Path(self.temp.name,'queue')),HTTPS_PROXY='http://example.invalid',HTTP_PROXY='http://example.invalid')
        command=[sys.executable,'-B','scripts/odysseus-local-worker','--task',str(file),'--contract-dir',str(CONTRACT_DIR),
                 '--node','mac' if sys.platform=='darwin' else 'pc','--model','gemma3:4b']
        for _ in range(2):
            child=await asyncio.create_subprocess_exec(*command,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            stdout,stderr=await asyncio.wait_for(child.communicate(),10)
            self.assertEqual(child.returncode,0);self.assertEqual(stderr,b'')
            self.assertEqual(json.loads(stdout)['status'],'COMPLETED')
        self.assertEqual(sum(path=='/api/chat' for path,_ in self.calls),1)
