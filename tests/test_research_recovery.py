"""Independent failure probes. All evidence and authorities are synthetic."""
import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
from src.deep_research import DeepResearcher
from src import research_handler as rh

SOURCE = {'url': 'https://example.test/source', 'title': 'Synthetic source',
          'summary': 'The fictional Atlas study measured ten units of rainfall.'}

@pytest.fixture
def handler(monkeypatch, tmp_path):
    monkeypatch.setattr(rh, 'RESEARCH_DATA_DIR', tmp_path)
    monkeypatch.setattr(rh.ResearchHandler, '_initialize_legacy_engine', lambda self: None)
    monkeypatch.setattr('src.event_bus.fire_event', lambda *a: None)
    return rh.ResearchHandler()

def researcher():
    r = DeepResearcher('http://fixture.invalid/v1', 'fixture', max_rounds=1,
                      category='factcheck', extraction_concurrency=1)
    r._start_time = time.time()
    return r

@pytest.mark.asyncio
async def test_completed_extraction_survives_cancelled_sibling(monkeypatch):
    r = researcher()
    blocked, drained = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(r, '_search', AsyncMock(return_value=[SOURCE, {'url':'https://example.test/blocked'}]))
    async def extract(url, *args):
        if url == SOURCE['url']: return dict(SOURCE)
        blocked.set()
        try: await asyncio.Event().wait()
        finally: drained.set()
    monkeypatch.setattr(r, '_fetch_and_extract', extract)
    task = asyncio.create_task(r._search_and_extract(['fixture'], 'Atlas rainfall'))
    await asyncio.wait_for(blocked.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert drained.is_set()
    assert r.findings == [SOURCE]

@pytest.mark.asyncio
async def test_cooperative_cancel_never_starts_final_synthesis(monkeypatch):
    r = researcher()
    r.cancel()
    model = AsyncMock(return_value='unwanted work')
    monkeypatch.setattr(r, '_llm', model)
    with pytest.raises(asyncio.CancelledError):
        await r.research('Atlas', prior_report='prior evidence', prior_findings=[SOURCE])
    model.assert_not_awaited()

@pytest.mark.asyncio
async def test_continuation_query_does_not_receive_private_prior_context(monkeypatch):
    r=researcher()
    captured=[]
    async def llm(messages, **kwargs):
        captured.extend(messages)
        return '["Atlas rainfall"]'
    monkeypatch.setattr(r,'_llm',llm)
    await r._generate_queries('Atlas rainfall', 'SYNTHETIC_PRIVATE_SENTINEL_QUERY', 2)
    assert 'SYNTHETIC_PRIVATE_SENTINEL_QUERY' not in json.dumps(captured)

@pytest.mark.asyncio
async def test_synthesis_retains_untrusted_provenance(monkeypatch):
    r=researcher()
    captured=[]
    async def llm(messages,**kwargs):
        captured.extend(messages); return 'cited report'
    monkeypatch.setattr(r,'_llm',llm)
    await r._synthesize('Atlas', [SOURCE], 'SYNTHETIC_PRIOR_EVIDENCE')
    evidence=[m for m in captured if 'SYNTHETIC_PRIOR_EVIDENCE' in m['content'] or SOURCE['summary'] in m['content']]
    assert evidence and all(m.get('metadata',{}).get('trusted') is False for m in evidence)

def entry():
    return {'query':'Atlas', 'status':'done', 'result':'new report', 'raw_report':'new report',
            'started_at':1, 'owner':'fixture-owner'}

@pytest.mark.parametrize('failure',[OSError,TimeoutError])
def test_atomic_write_failure_preserves_prior_report(handler, monkeypatch, tmp_path,failure):
    path=tmp_path/'fixture.json'
    prior=json.dumps({'owner':'fixture-owner','result':'prior good report','status':'done'})
    path.write_text(prior)
    def fail_replace(*args): raise failure('synthetic replace failure')
    monkeypatch.setattr('os.replace',fail_replace)
    e=entry()
    assert handler._save_result('fixture',e) is False
    assert path.read_text()==prior
    assert e['status']=='error' and e['persistence_error'] is True

@pytest.mark.asyncio
@pytest.mark.parametrize('hard_cap',[0,1800])
async def test_inner_deadline_saves_partial_with_observed_elapsed(handler,monkeypatch,tmp_path,hard_cap):
    monkeypatch.setattr('src.settings.get_setting',lambda key,default=None:hard_cap if key=='research_run_timeout_seconds' else default)
    async def service(*args,**kwargs):
        r=researcher();r.findings=[SOURCE];r.evolving_report=''
        kwargs['_task_entry']['researcher']=r
        raise asyncio.TimeoutError
    monkeypatch.setattr(handler,'call_research_service',service)
    handler.start_research('fixture','Atlas','http://fixture.invalid','fixture',max_time=1,owner='fixture-owner')
    await handler._active_tasks['fixture']['task']
    saved=json.loads((tmp_path/'fixture.json').read_text())
    assert saved['partial'] is True and SOURCE['url'] in saved['raw_report']
    assert 'None' not in saved['raw_report'] and '1800s' not in saved['raw_report']

@pytest.mark.asyncio
async def test_final_generation_cannot_hide_expired_deadline(monkeypatch):
    r=researcher()
    monkeypatch.setattr(r,'_create_plan',AsyncMock(return_value='synthetic plan'))
    monkeypatch.setattr(r,'_generate_queries',AsyncMock(return_value=[]))
    async def final(*args):
        r._deadline=time.monotonic()-1
        return 'obsolete completion'
    monkeypatch.setattr(r,'_final_report',final)
    with pytest.raises(asyncio.TimeoutError):
        await r.research('Atlas',prior_report='prior report',prior_findings=[SOURCE])

@pytest.mark.asyncio
async def test_dependency_failure_preserves_evidence_and_notifies_once(handler,monkeypatch,tmp_path):
    async def service(*args,**kwargs):
        r=researcher();r.findings=[SOURCE];r.evolving_report=''
        kwargs['_task_entry']['researcher']=r
        raise ConnectionError('synthetic offline dependency')
    monkeypatch.setattr(handler,'call_research_service',service)
    notified=[]
    handler.start_research('fixture','Atlas','http://fixture.invalid','fixture',hard_timeout=10,owner='fixture-owner',on_complete=lambda *args:notified.append(args))
    await handler._active_tasks['fixture']['task']
    saved=json.loads((tmp_path/'fixture.json').read_text())
    assert saved['partial'] is True and saved['completion_reason']=='dependency_failure'
    assert saved['sources'][0]['url']==SOURCE['url'] and len(notified)==1

@pytest.mark.asyncio
async def test_late_replaced_run_cannot_overwrite_newer_completion(handler,monkeypatch,tmp_path):
    entered=asyncio.Event()
    async def service(query,*args,**kwargs):
        if query=='old':
            entered.set()
            try: await asyncio.Event().wait()
            except asyncio.CancelledError: return 'obsolete report'
        return 'new report'
    monkeypatch.setattr(handler,'call_research_service',service)
    handler.start_research('fixture','old','http://fixture.invalid','fixture',hard_timeout=10,owner='fixture-owner')
    old=handler._active_tasks['fixture']['task'];await entered.wait()
    handler.start_research('fixture','new','http://fixture.invalid','fixture',hard_timeout=10,owner='fixture-owner')
    await asyncio.gather(old,handler._active_tasks['fixture']['task'],return_exceptions=True)
    assert json.loads((tmp_path/'fixture.json').read_text())['result']=='new report'

@pytest.mark.asyncio
async def test_saved_partial_survives_consumption_reload_and_owner_routes(handler,monkeypatch,tmp_path):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from routes.research import research_routes as routes
    e=entry();e.update(partial=True,completion_reason='timeout',
        raw_report='_Partial research: deadline reached._\n\n[Atlas](https://example.test/source)',
        result='_Partial research: deadline reached._\n\n[Atlas](https://example.test/source)')
    assert handler._save_result('fixture',e)
    handler.clear_result('fixture')
    reloaded=rh.ResearchHandler()
    assert reloaded.get_status('fixture')['partial'] is True
    monkeypatch.setattr(routes,'DEEP_RESEARCH_DIR',tmp_path)
    router=routes.setup_research_routes(reloaded)
    for path,method in [('/api/research/status/{session_id}','GET'),('/api/research/result-peek/{session_id}','POST'),('/api/research/report/{session_id}','GET')]:
        target=next(r.endpoint for r in router.routes if r.path==path and method in r.methods)
        request=SimpleNamespace(state=SimpleNamespace(current_user='fixture-owner'))
        result=await target('fixture',request)
        if 'report/' in path:
            assert b'Partial research' in result.body and b'https://example.test/source' in result.body
        else: assert result['partial'] is True
        request.state.current_user='fixture-other'
        with pytest.raises(HTTPException) as denied: await target('fixture',request)
        assert denied.value.status_code==404

def test_new_run_cannot_overwrite_final_without_explicit_continuation(handler,tmp_path):
    (tmp_path/'fixture.json').write_text(json.dumps(entry()))
    with pytest.raises(ValueError,match='continuation'):
        handler.start_research('fixture','different query','http://fixture.invalid','fixture',owner='fixture-owner')

@pytest.mark.asyncio
async def test_cancel_suppressing_completion_cannot_publish(handler,monkeypatch,tmp_path):
    entered=asyncio.Event()
    async def service(*args,**kwargs):
        entered.set()
        try: await asyncio.Event().wait()
        except asyncio.CancelledError: return 'obsolete report'
    monkeypatch.setattr(handler,'call_research_service',service)
    callbacks=[]
    handler.start_research('fixture','Atlas','http://fixture.invalid','fixture',hard_timeout=10,
                           owner='fixture-owner',on_complete=lambda *a:callbacks.append(a))
    e=handler._active_tasks['fixture']
    await entered.wait()
    handler.cancel_research('fixture')
    try: await e['task']
    except asyncio.CancelledError: pass
    assert e['status']=='cancelled'
    assert not (tmp_path/'fixture.json').exists() and callbacks==[]

@pytest.mark.parametrize('value', [[], None, 1, 'bad', {'owner':'other','status':'done','raw_report':'private'}])
def test_malformed_or_other_owner_cannot_seed_continuation(handler,tmp_path,value):
    (tmp_path/'fixture.json').write_text(json.dumps(value))
    assert handler._get_session_json('fixture',owner='fixture-owner') is None

@pytest.mark.asyncio
async def test_real_worker_is_reaped_on_repeated_cancellation():
    import sys
    from src.research_io import run_process, _active_workers
    task=asyncio.create_task(run_process([sys.executable,'-c','import time; time.sleep(60)'],b'',60))
    for _ in range(1000):
        if _active_workers: break
        await asyncio.sleep(0.001)
    assert _active_workers
    workers=list(_active_workers)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task,3)
    assert not _active_workers and all(p.returncode is not None for p in workers)

@pytest.mark.asyncio
@pytest.mark.parametrize('interruption',['cancel','deadline'])
async def test_blocked_save_worker_never_replaces_prior_report(handler,monkeypatch,tmp_path,interruption):
    import sys
    from pathlib import Path
    from src import research_io
    prior=json.dumps({'owner':'fixture-owner','status':'done','result':'prior good report'})
    path=tmp_path/'fixture.json';path.write_text(prior)
    real_process=research_io.run_process
    async def blocked_process(command,payload,timeout):
        # The actual module worker and atomic helper reach fsync after creating
        # a candidate. The worker never receives the final destination.
        bootstrap='import os,time; os.fsync=lambda fd:time.sleep(60); from src.research_io import _worker; _worker()'
        child=asyncio.create_task(real_process([sys.executable,'-B','-c',bootstrap],payload,10))
        if interruption=='deadline':
            while not list(tmp_path.glob('fixture.json.pending-*.tmp.*')):
                if child.done(): await child
                await asyncio.sleep(0.001)
            await asyncio.wait_for(child,timeout=0)
        else:
            await child
    monkeypatch.setattr(research_io,'run_process',blocked_process)
    e=entry();handler._active_tasks['fixture']=e
    task=asyncio.create_task(handler._save_result_async('fixture',e));e['task']=task
    if interruption=='cancel':
        async def wait_for_candidate():
            while not list(tmp_path.glob('fixture.json.pending-*.tmp.*')):
                if task.done(): await task
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait_for_candidate(),10)
        assert handler.cancel_research('fixture')
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task,3)
    else:
        assert await asyncio.wait_for(task,3) is False
        assert e['persistence_error'] is True
    assert path.read_text()==prior and not research_io._active_workers
    assert not list(tmp_path.glob('fixture.json.pending-*'))

@pytest.mark.asyncio
async def test_owner_rename_during_save_refuses_stale_candidate(handler,monkeypatch,tmp_path):
    from pathlib import Path
    async def prepare(kind,args,timeout):
        Path(args[0]).write_text(json.dumps(args[1]))
        handler.rename_owner('fixture-owner','fixture-new')
        return True
    monkeypatch.setattr('src.research_io.run_research_io',prepare)
    e=entry();handler._active_tasks['fixture']=e
    assert await handler._save_result_async('fixture',e) is False
    assert e['persistence_error'] and not (tmp_path/'fixture.json').exists()

@pytest.mark.asyncio
async def test_canonical_worker_search_fetch_and_handler_timeout(handler,monkeypatch,tmp_path):
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from src import research_io
    blocked, release = threading.Event(), threading.Event()
    calls=[]
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            calls.append(self.path.split('?')[0])
            if self.path.startswith('/search'):
                body=json.dumps({'results':[{'url':SOURCE['url'],'title':SOURCE['title'],'content':'fixture'}]}).encode()
                content_type='application/json'
            elif self.path=='/blocked':
                blocked.set();release.wait(5);return
            else:
                body=SOURCE['summary'].encode();content_type='text/plain'
            self.send_response(200);self.send_header('Content-Type',content_type);self.end_headers()
            self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    # -I prevents inheriting test environment hooks. The child installs its own
    # exact loopback-only socket boundary before importing canonical providers.
    bootstrap=f'''
import sys,os,socket,ipaddress
sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r})
os.environ['ODYSSEUS_DATA_DIR']={str(tmp_path/'worker-data')!r}
connect=socket.socket.connect;dns=socket.getaddrinfo
def only_connect(sock,address):
    if address != ('127.0.0.1',{server.server_port}): raise OSError('Fixture network boundary')
    return connect(sock,address)
def only_dns(host,*args,**kwargs):
    if host!='127.0.0.1': raise OSError('Fixture DNS boundary')
    return dns(host,*args,**kwargs)
socket.socket.connect=only_connect;socket.getaddrinfo=only_dns
import httpx
import services.search.providers as providers
providers._get_search_instance=lambda:{origin!r}
import services.search.content as content
content._resolve_public_ips=lambda url:[ipaddress.ip_address('1.1.1.1')]
def fixture_response(request):
    assert request.url.host=='example.test'
    response=httpx.get({origin!r}+request.url.path,trust_env=False,timeout=5)
    return httpx.Response(response.status_code,headers=response.headers,content=response.content,request=request)
content._PinnedTransport=lambda ip:httpx.MockTransport(fixture_response)
from src.research_io import _worker
_worker()
'''
    real_process=research_io.run_process
    async def fixture_process(command,payload,timeout):
        return await real_process([sys.executable,'-I','-B','-c',bootstrap],payload,timeout)
    monkeypatch.setattr(research_io,'run_process',fixture_process)
    monkeypatch.setattr('src.search.providers._get_search_settings',lambda:{'search_provider':'searxng'})
    monkeypatch.setattr('src.search.core._build_provider_chain',lambda provider:[provider])
    async def service(*args,**kwargs):
        r=researcher();kwargs['_task_entry']['researcher']=r
        results=await r._search('fictional Atlas')
        assert results[0]['url']==SOURCE['url']
        from src.search import fetch_webpage_content
        page=await r._blocking_io(fetch_webpage_content,SOURCE['url'],5)
        assert page['success'] and SOURCE['summary'] in page['content']
        r.findings=[SOURCE]
        pending=asyncio.create_task(r._blocking_io(fetch_webpage_content,'https://example.test/blocked',5))
        while not blocked.is_set():
            if pending.done(): await pending
            await asyncio.sleep(0.001)
        await asyncio.wait_for(pending,timeout=0)
    monkeypatch.setattr(handler,'call_research_service',service)
    try:
        handler.start_research('fixture','Atlas','http://fixture.invalid','fixture',hard_timeout=60,owner='fixture-owner')
        task=handler._active_tasks['fixture']['task']
        await asyncio.wait_for(task,20)
        saved=json.loads((tmp_path/'fixture.json').read_text())
        assert blocked.is_set() and saved['partial'] and saved['completion_reason']=='timeout'
        assert saved['sources'][0]['url']==SOURCE['url']
        assert calls==['/search','/source','/blocked'] and not research_io._active_workers
    finally:
        release.set();server.shutdown();server.server_close();thread.join(2)

@pytest.mark.parametrize('value',[{'owner':'other','raw_report':[]}, {'owner':'fixture-owner','raw_findings':[1]}])
def test_malformed_existing_file_is_never_overwritten(handler,tmp_path,value):
    path=tmp_path/'fixture.json';prior=json.dumps(value);path.write_text(prior)
    with pytest.raises(ValueError):
        handler.start_research('fixture','Atlas','http://fixture.invalid','fixture',owner='fixture-owner')
    assert path.read_text()==prior

@pytest.mark.asyncio
async def test_budget_expiry_during_search_prevents_extraction(monkeypatch):
    r=researcher();r._deadline=time.monotonic()+100
    async def search(query):
        r._deadline=time.monotonic()-1
        return [SOURCE]
    extract=AsyncMock()
    monkeypatch.setattr(r,'_search',search)
    monkeypatch.setattr(r,'_fetch_and_extract',extract)
    with pytest.raises(asyncio.TimeoutError): await r._search_and_extract(['Atlas'],'Atlas')
    extract.assert_not_awaited()
