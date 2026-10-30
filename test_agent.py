"""Offline HTTP integration checks; no paid calls or credentials."""
import json
import unittest

import httpx2 as httpx
import openai
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from agent import build_agent


class AgentIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_skill_cli_and_native_tool_payload(self):
        requests = []
        calls = [
            ('read_file', {'file_path': '/skills/flyai/SKILL.md'}),
            ('read_file', {'file_path': '/skills/flyai/references/keyword-search.md'}),
            ('flyai', {'command': 'keyword-search', 'arguments': ['--help']}),
        ]

        def respond(request):
            self.assertEqual(request.url.path, '/compatible-mode/v1/responses')
            payload = json.loads(request.content)
            requests.append(payload)
            self.assertEqual(payload['model'], 'qwen3.8-flash')
            self.assertTrue(payload['enable_thinking'])
            self.assertNotIn('previous_response_id', payload)
            native = [t for t in payload['tools'] if t['type'] != 'function']
            self.assertEqual(native, [{'type': 'web_search'}])
            names = {t['name'] for t in payload['tools'] if t['type'] == 'function'}
            self.assertIn('flyai', names)
            self.assertNotIn('task', names)
            self.assertNotIn('execute', names)
            self.assertNotIn('cc_entrypoint', json.dumps(payload))
            index = len(requests) - 1
            if index < len(calls):
                name, inputs = calls[index]
                output = [{'type': 'function_call', 'id': f'fc_{index}',
                           'call_id': f'call_{index}', 'name': name,
                           'arguments': json.dumps(inputs), 'status': 'completed'}]
            else:
                # Native search results and source citations parse correctly.
                output = [
                    {'type': 'web_search_call', 'id': 'ws_1', 'status': 'completed',
                     'action': {'type': 'search', 'query': '杭州旅游'}},
                    {'type': 'message', 'id': 'msg_final', 'role': 'assistant', 'status': 'completed',
                     'content': [{'type': 'output_text', 'text': '旅行工具检查完成',
                                  'annotations': [{'type': 'url_citation', 'url': 'https://example.com/',
                                                   'title': '来源', 'start_index': 0, 'end_index': 4}]}]},
                ]
            return httpx.Response(200, json={
                'id': f'resp_{index}', 'object': 'response', 'created_at': 0,
                'status': 'completed', 'error': None, 'incomplete_details': None,
                'model': 'qwen3.8-flash', 'output': output,
                'usage': {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20,
                          'input_tokens_details': {'cached_tokens': 0},
                          'output_tokens_details': {'reasoning_tokens': 0}},
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = openai.AsyncOpenAI(api_key='offline-test',
                base_url='https://example.com/compatible-mode/v1', http_client=http)
            model = ChatOpenAI(model='qwen3.8-flash', api_key='offline-test',
                root_async_client=client, async_client=client.chat.completions,
                use_responses_api=True, extra_body={'enable_thinking': True},
                disable_streaming=True, use_previous_response_id=False)
            agent = build_agent(model, [])
            result = await agent.ainvoke({'messages': [HumanMessage(content='检查旅行工具')]})
        self.assertEqual(len(requests), 4)
        outputs = [m.content for m in result['messages'] if m.type == 'tool']
        self.assertIn('FlyAI', outputs[0])
        self.assertIn('--query', outputs[1])
        self.assertIn('Usage: flyai', outputs[2])
        final = json.dumps(result['messages'][-1].content, ensure_ascii=False)
        self.assertIn('旅行工具检查完成', final)
        self.assertIn('https://example.com/', final)

if __name__ == '__main__':
    unittest.main()
