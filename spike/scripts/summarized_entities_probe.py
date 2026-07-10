#!/usr/bin/env python3
"""Diagnostic-only reproduction of Graphiti's SummarizedEntities local LLM call."""
import argparse,asyncio,json,sys
from pathlib import Path
from openai import AsyncOpenAI
sys.path.insert(0,str(Path(__file__).parents[1]))
from graphiti_core.prompts.extract_nodes import SummarizedEntities,extract_summaries_batch
from graphiti_core.llm_client.config import LLMConfig
from src.graphiti_adapter import local_profile
from src.local_llm import LocalOpenAIGenericClient,clean_json_content
async def main():
 p=argparse.ArgumentParser();p.add_argument('--mode',choices=('json_object','json_schema'),default='json_object');args=p.parse_args();c=local_profile();config=LLMConfig(api_key=c['api_key'],base_url=c['base_url'],model=c['llm_model'],small_model=c['small_model']);client=LocalOpenAIGenericClient(config=config,structured_output_mode=args.mode)
 messages=extract_summaries_batch({'previous_episodes':[],'episode_content':'Synthetic note: Project Atlas uses SQLite journal.','entities':[{'name':'Project Atlas','summary':''}],'entity_type_descriptions':{}})
 schema=SummarizedEntities.model_json_schema()
 if args.mode=='json_object':messages[-1].content += '\n\nRespond with a JSON object in the following format:\n\n'+json.dumps(schema)
 request_messages=[{'role':m.role,'content':m.content} for m in messages];response_format=client._build_response_format(SummarizedEntities)
 raw=await AsyncOpenAI(api_key=c['api_key'],base_url=c['base_url']).chat.completions.create(model=c['llm_model'],messages=request_messages,temperature=client.temperature,max_tokens=client.max_tokens,response_format=response_format)
 content=raw.choices[0].message.content or ''
 out={'response_model':'SummarizedEntities','mode':args.mode,'request':{'messages':request_messages,'response_format':response_format,'schema':schema},'raw_response_content':content}
 try:
  cleaned=clean_json_content(content);out['after_fence_stripping']=cleaned;out['parsed_json']=json.loads(cleaned);out['parsed_has_summaries']='summaries' in out['parsed_json'];out['classification_hint']='model_echoed_schema' if '$defs' in out['parsed_json'] and 'summaries' not in out['parsed_json'] else 'model_returned_instance'
 except Exception as exc:out['parse_error']=f'{type(exc).__name__}: {exc}'
 print(json.dumps(out,indent=2,ensure_ascii=False));return 0 if out.get('parsed_has_summaries') else 2
if __name__=='__main__':raise SystemExit(asyncio.run(main()))
