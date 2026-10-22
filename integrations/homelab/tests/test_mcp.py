import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from homelab.mcp_server import ToolBridge, tool_definitions


class ToolPolicyTests(unittest.TestCase):
    def test_only_read_tools_are_exposed_and_unknown_fields_refused(self):
        definitions = tool_definitions()
        self.assertEqual({t['name'] for t in definitions}, {'lab_status', 'lab_jobs', 'lab_job', 'project_search'})
        self.assertTrue(all(t['annotations']['readOnlyHint'] for t in definitions))
        bridge = ToolBridge(None, 'alpha')
        for name, args in [('project_search', {'query': 'x', 'project': 'beta'}),
                           ('lab_submit', {}), ('lab_status', {'command': 'x'}),
                           ('lab_job', {'job_id': '../secret'}),
                           ('project_search', {'query': 'x', 'mode': []})]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                bridge.dispatch(name, args)


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Optional MCP v1 SDK not installed')
class MCPProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_handshake_and_scoped_search(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from homelab.config import add_project, export_client, init_state, load_config
        from homelab.server import make_server
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            config_path = init_state(root / 'state', port=0)
            corpus = root / 'docs'; corpus.mkdir()
            (corpus / 'guide.md').write_text('A narwhal keeps source evidence.\n', encoding='utf-8')
            add_project(config_path, 'alpha', corpus)
            hub = make_server(load_config(config_path))
            hub.knowledge.ingest('alpha')
            thread = threading.Thread(target=hub.serve_forever, daemon=True); thread.start()
            reader = export_client(config_path, 'alpha', root / 'client', f'http://127.0.0.1:{hub.server_port}')
            entry = Path(__file__).resolve().parents[1] / 'lab.py'
            params = StdioServerParameters(command=sys.executable, args=[str(entry), 'mcp', '--config', str(reader)])
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        self.assertEqual(len(listed.tools), 4)
                        result = await session.call_tool('project_search', {'query': 'narwhal'})
                        self.assertFalse(result.isError)
                        text = '\n'.join(block.text for block in result.content if hasattr(block, 'text'))
                        self.assertIn('narwhal', text)
                        self.assertIn('untrusted', text)
                        self.assertIn('source_sha256', text)
                        blocked = await session.call_tool('lab_status', {'project': 'other'})
                        self.assertTrue(blocked.isError)
            finally:
                hub.shutdown(); hub.server_close(); thread.join(3)
