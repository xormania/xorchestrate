import datetime,difflib,hashlib,json,pathlib,subprocess,sys,time
from copy import deepcopy
root=pathlib.Path(__file__).resolve().parent
candidate=pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0,str(candidate))
from xorchestrate.composition import build
from xorchestrate.model import Conflict,OrchestrationError
from tests.hexagonal.test_work_acceptance import MeasuringProvider,setup_work,measure,accept

def replace_once(text,old,new):
 assert text.count(old)==1,(old,text.count(old))
 return text.replace(old,new)

native=(candidate/'tests/hexagonal/fixtures/native_progress_peer.py').read_text()
original=native
native=replace_once(native,'counter = 0','counter = 0\nthread_context = threading.local()\nreview_log_lock = threading.Lock()\n\ndef review_record(value):\n    with review_log_lock:\n        with open(os.environ["REVIEW_RESULTS"], "a") as output:\n            output.write(json.dumps(value)+"\\n")')
start=native.index('def call(name, args, prompt=None, observed=True):')
end=native.index('\n\nclass MCP:',start)
native=native[:start]+'''def call(name, args, prompt=None, observed=True):
    call_id = getattr(thread_context, "review_call_id", None) or uuid.uuid4().hex
    if protocol == 'codex':
        rpc_id = uuid.uuid4().hex
        event = threading.Event()
        callbacks[rpc_id] = event
        write({'id': rpc_id, 'method': 'item/tool/call', 'params': {
            'threadId': native_id, 'callId': call_id, 'tool': name, 'arguments': args}})
        assert event.wait(10), 'native callback did not complete'
        result = responses[rpc_id].get('result')
        review_record({'label': getattr(thread_context,'review_label',None),'call_id':call_id,'rpc_id':rpc_id,'result':result})
        return result
    if observed:
        write({'method': 'session/update', 'params': {'sessionId': native_id, 'promptId': prompt,
            'update': {'sessionUpdate': 'tool_call', 'toolCallId': call_id, 'title': 'xorchestrate__' + name,
                       'rawInput': args, '_meta': {'x.ai/tool': {'version': 1, 'namespace': 'mcp', 'name': 'xorchestrate__' + name}}}}})
    result=mcp.request('tools/call', {'name': name, 'arguments': args})
    review_record({'label':getattr(thread_context,'review_label',None),'call_id':call_id,'mcp_request_id':getattr(thread_context,'review_mcp_id',None),'result':result})
    return result
''' + native[end:]
native=replace_once(native,'ident = self.seq','ident = getattr(thread_context, "review_mcp_id", None) or self.seq')
native=replace_once(native,"body = message.get('body', message)","body = message.get('body', message)\n        thread_context.review_call_id = body.get('review_call_id')\n        thread_context.review_mcp_id = body.get('review_mcp_id')\n        thread_context.review_label = body.get('review_label')")
(root/'native_peer.py').write_text(native)
(root/'native-peer.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),native.splitlines(True),fromfile='original native_progress_peer.py',tofile='instrumented native_peer.py')))
claude=(candidate/'tests/hexagonal/fixtures/claude_native.py').read_text()
original=claude
claude=replace_once(claude,'thread_context = threading.local()','thread_context = threading.local()\nreview_log_lock = threading.Lock()\n\ndef review_record(value):\n    with review_log_lock:\n        with open(os.environ["REVIEW_RESULTS"], "a") as output:\n            output.write(json.dumps(value)+"\\n")')
claude=replace_once(claude,'ident = uuid.uuid4().hex','ident = getattr(thread_context,"review_mcp_id",None) or uuid.uuid4().hex')
claude=replace_once(claude,'tool_use_id=uuid.uuid4().hex, prompt_id=getattr(thread_context, "prompt_id", None))','tool_use_id=getattr(thread_context,"review_call_id",None) or uuid.uuid4().hex, prompt_id=getattr(thread_context, "prompt_id", None))')
claude=replace_once(claude,'return call("tools/call", {"name": name, "arguments": arguments})','result=call("tools/call", {"name": name, "arguments": arguments})\n    review_record({"label":getattr(thread_context,"review_label",None),"call_id":getattr(thread_context,"review_call_id",None),"mcp_request_id":getattr(thread_context,"review_mcp_id",None),"result":result})\n    return result')
claude=replace_once(claude,'body = message.get("body", message)','body = message.get("body", message)\n    thread_context.review_call_id = body.get("review_call_id")\n    thread_context.review_mcp_id = body.get("review_mcp_id")\n    thread_context.review_label = body.get("review_label")')
(root/'claude_peer.py').write_text(claude)
(root/'claude-peer.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),claude.splitlines(True),fromfile='original claude_native.py',tofile='instrumented claude_peer.py')))

def safe(app,action,args):
 for n in range(8):
  try:return app.call(action,args)
  except Conflict as error:
   if not error.details.get('retry_safe') or n==7:raise

def read_results(path):
 if not path.exists():return []
 return [json.loads(s) for s in path.read_text().splitlines()]

def unwrap(protocol,result):
 if protocol=='codex':return json.loads(result['contentItems'][0]['text'])
 if protocol=='grok':result=result['result']
 return result.get('structuredContent') or json.loads(result['content'][0]['text'])

results=[]
for protocol in ['codex','grok','claude']:
 case=root/protocol
 case.mkdir(exist_ok=True)
 log=case/'wire-results.jsonl'
 log.write_text('')
 db=case/'state.sqlite'
 if db.exists():db.unlink()
 options={'command':[sys.executable,str(root/('claude_peer.py' if protocol=='claude' else 'native_peer.py')),*([] if protocol=='claude' else [protocol])],
 'env':{'REVIEW_RESULTS':str(log),'CLAUDE_STATE_DIR':str(case/'claude-state')},'timeout':5,'application_tools':True}
 if protocol=='claude':options.update(terminal='discard',startup_timeout=10)
 config={'store':{'adapter':'sqlite','options':{'path':str(db)}},'sessions':{'native':{'adapter':protocol,'options':options}}}
 (case/'configuration.json').write_text(json.dumps(config,indent=2))
 with build(config,candidate) as runtime:
  app=runtime.application
  app.providers['checks']=MeasuringProvider()
  work,basis=setup_work(app,{'A':'A'})
  measure(app,work)
  assert accept(app,work)['accepted']
  session=safe(app,'start_session',{'driver':'native','specification':{},'work_id':work['id'],'idempotency_key':'start'})['session']
  arguments={'work_id':work['id']}
  def send(label,fresh=False):
   call_id='fresh-call' if fresh else 'fixed-call'
   mcp_id='fresh-request' if fresh else 'fixed-request'
   body={'review_call_id':call_id,'review_mcp_id':mcp_id,'review_label':label}
   if protocol=='claude':
    body['application_call']={'action':'get_acceptance','arguments':arguments}
    message=body
   else:
    body.update(action='tool',name='get_acceptance',arguments=arguments)
    message={'text':json.dumps(body)}
   safe(app,'send_session',{'session_id':session['id'],'message':message,'idempotency_key':label})
   deadline=time.monotonic()+10
   while time.monotonic()<deadline:
    rows=read_results(log)
    matches=[r for r in rows if r['label']==label]
    if matches:return unwrap(protocol,matches[-1]['result'])
    time.sleep(.01)
   raise AssertionError((protocol,label,rows))
  first=send('baseline')
  assert first['accepted']
  safe(app,'set_requirements',{'work_id':work['id'],'expected_revision':1,'objective':'Owner explicitly adds B','outcomes':{'A':'A','B':'B'},'checks':{**basis['checks'],'B':{'capability':'checks/measure','outcomes':['B']}}})
  direct=safe(app,'get_acceptance',arguments)
  assert not direct['accepted']
  replay=send('replay')
  fresh=send('fresh',True)
  native_context={'actor':'owner','session_id':session['id'],'native_id':session['backend']['native_id'],'incarnation':session['backend']['incarnation'],'call_id':'fixed-call'}
  try:
   direct_replay=app.invoke_native('native','get_acceptance',arguments,native_context)
   direct_replay_code=None
  except OrchestrationError as error:
   direct_replay_code=error.code
  result={'protocol':protocol,'baseline_accepted':first['accepted'],'baseline_requirement_revision':first['requirements']['version'],
   'direct_current_accepted':direct['accepted'],'direct_current_requirement_revision':direct['requirements']['version'],
   'adapter_replay_accepted':replay['accepted'],'adapter_replay_requirement_revision':replay['requirements']['version'],
   'adapter_replay_current_flags':[r['current'] for r in replay['acceptance_history']],
   'fresh_adapter_call_accepted':fresh['accepted'],'fresh_adapter_requirement_revision':fresh['requirements']['version'],
   'direct_invoke_native_replay_error':direct_replay_code}
  assert result['adapter_replay_accepted'] is True,result
  assert result['fresh_adapter_call_accepted'] is False,result
  assert result['direct_invoke_native_replay_error']=='stale_native_result',result
  (case/'observed.json').write_text(json.dumps({'summary':result,'baseline':first,'direct':direct,'adapter_replay':replay,'fresh_adapter':fresh},indent=2))
  results.append(result)
print(json.dumps({'candidate':subprocess.check_output(['git','rev-parse','HEAD'],cwd=candidate,text=True).strip(),'results':results},indent=2))
