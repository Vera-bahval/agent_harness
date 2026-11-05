import importlib
import unittest
R=importlib.import_module('services.llm-proxy.runtime.responses')


class ResponsesTests(unittest.TestCase):
    def test_tool_observation_and_encrypted_reasoning_roundtrip(self):
        items=[{'type':'reasoning','id':'rs_1','summary':[],'encrypted_content':'PRIVATE_REASONING'},
               {'type':'function_call','id':'fc_1','call_id':'call_1','name':'ReadFile','arguments':'{"path":"x"}','status':'completed'}]
        value=R.response({'status':'completed','model':'sol-version','output':items,'usage':{'input_tokens':4,'output_tokens':7}},'fallback')
        self.assertEqual(value['message']['tool_calls'][0]['id'],'call_1')
        self.assertEqual(value['usage'],{'tokens_in':4,'tokens_out':7})
        messages=[{'role':'system','content':'system'},{'role':'user','content':'task'},value['message'],{'role':'tool','tool_call_id':'call_1','content':'observed'}]
        payload=R.payload('sol',messages,{'reasoning_effort':'low','max_completion_tokens':8192})
        self.assertEqual(payload['input'][2:4],items)
        self.assertEqual(payload['input'][-1],{'type':'function_call_output','call_id':'call_1','output':'observed'})
        self.assertFalse(payload['store']);self.assertEqual(payload['reasoning'],{'effort':'low'})

    def test_final_usage_and_reject_incomplete_or_unexpected_output(self):
        body={'status':'completed','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Done'}]}]}
        self.assertEqual(R.response(body,'sol')['message']['content'],'Done')
        self.assertIsNone(R.response(body,'sol')['usage']['tokens_in'])
        for bad in ({**body,'status':'incomplete'},{**body,'output':[{'type':'shell_call'}]}):
            with self.assertRaises(R.ModelError):R.response(bad,'sol')
