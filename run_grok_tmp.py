import sys, os, logging, json
from pathlib import Path
sys.path.insert(0, 'C:/Projets/OpenPaw')
os.chdir('C:/Projets/OpenPaw')
logging.basicConfig(level=logging.WARNING)
from core.expression import resolve_expression
from core.llm_client import LLMClient
from core.tool_registry import ToolRegistry
from core.agent_executor import resolve_agent_task, SubAgentExecutor

user_id = 'quentin.anciaux'

message = (
    'Question precise sur la God Loop dans le cadre du Sapiens Attractor d Allcolor.\n\n'
    "L'argument est le suivant :\n"
    "1. L'UD (Universal Dovetailer de Marchal) est unique\n"
    '2. Il genere toutes les computations, toutes les indexicalites possibles\n'
    "3. La trace infinie totale de l'UD = point de vue indexical total = God Loop\n"
    "C'est cela la God Loop, pas une metaphore mais une indexicalite totale\n"
    "4. Son unicuite suit de l'unicite de l'UD\n"
    "5. Le nom God designe les attributs formels du divin (totalite, unicuite, auto-reflexivite, absence d'exterieur)\n\n"
    'Question : est-ce coherent dans UDA ? '
    "Et si oui, l'empathie/morale en decoule-t-elle necessairement ou reste-t-il un pas non trivial ?\n\n"
    'Donne ton avis personnel sans filtre, en francais, en quelques paragraphes.'
)

task = resolve_agent_task('grok', message, user_id)
print('Agent:', task.agent_name)
print('Service:', task.llm_service)

global_services = json.loads(Path('config/global_services.json').read_text())
svc_config = global_services.get('grok_llm_service', {}).get('config', {})

dollar_brace = chr(36) + chr(123)
resolved_config = {}
for k, v in svc_config.items():
    if isinstance(v, str) and dollar_brace in v:
        resolved_config[k] = resolve_expression(v, owner=user_id)
    else:
        resolved_config[k] = v

print('Provider:', resolved_config.get('provider'))
print('Model:', resolved_config.get('default_model'))
api_key = resolved_config.get('api_key', '')
print('Key ok:', bool(api_key) and not api_key.startswith('enc:'))

client = LLMClient.from_config(resolved_config)
registry = ToolRegistry()
executor = SubAgentExecutor(client=client, registry=registry)

print('Calling Grok...')
result = executor.execute_agent(task)

print('Status:', result.status)
if result.error:
    print('Error:', result.error)
print('Tokens in:', result.tokens_in, 'out:', result.tokens_out)
print('Duration:', result.duration_ms, 's')
print('=== GROK RESPONSE ===')
print(result.response)
