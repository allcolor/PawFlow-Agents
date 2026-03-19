import sys, os, json, logging, concurrent.futures
from pathlib import Path
sys.path.insert(0, 'C:/Projets/OpenPaw')
os.chdir('C:/Projets/OpenPaw')
logging.basicConfig(level=logging.WARNING)
from core.expression import resolve_expression
from core.llm_client import LLMClient
from core.tool_registry import ToolRegistry
from core.agent_executor import resolve_agent_task, SubAgentExecutor

user_id = 'quentin.anciaux'
QUESTION = 'In the Allcolor debate: Grok position assumes affect is a separable module from world-modeling, detachable by architectural choice. Claude map/mirror framing suggests affect may be emergent from sufficiently deep state-modeling, not separable. Which view is defensible, and what would settle it? One key argument only.'

global_services = json.loads(Path('config/global_services.json').read_text())
dollar_brace = chr(36) + chr(123)

def build_client(service_name):
    svc_config = global_services.get(service_name, {}).get('config', {})
    resolved = {}
    for k, v in svc_config.items():
        if isinstance(v, str) and dollar_brace in v:
            resolved[k] = resolve_expression(v, owner=user_id)
        else:
            resolved[k] = v
    return LLMClient.from_config(resolved)

grok_client = build_client('grok_llm_service')
claude_client = build_client('claude_llm_service')

grok_task = resolve_agent_task('grok', QUESTION, user_id)
grok_task.id = 'modularity_grok'
grok_task.llm_service = ''

claude_task = resolve_agent_task('claude', QUESTION, user_id)
claude_task.id = 'modularity_claude'
claude_task.llm_service = ''

def run_agent(client, task):
    executor = SubAgentExecutor(client=client, registry=ToolRegistry())
    return executor.execute_agent(task)

print('Spawning grok and claude agents in parallel...')
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    grok_future = pool.submit(run_agent, grok_client, grok_task)
    claude_future = pool.submit(run_agent, claude_client, claude_task)
    grok_result = grok_future.result(timeout=180)
    claude_result = claude_future.result(timeout=180)

for r in [grok_result, claude_result]:
    sep = '============================================================'
    print()
    print(sep)
    print('TASK ID:', r.task_id, '| AGENT:', r.agent_name, '| STATUS:', r.status)
    print(sep)
    if r.error:
        print('ERROR:', r.error)
    else:
        print(r.response)
