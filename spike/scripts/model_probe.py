#!/usr/bin/env python3
"""Probe an OpenAI-compatible local model without logging credentials or raw content."""
import asyncio,json,sys
from pathlib import Path
from pydantic import BaseModel
from openai import AsyncOpenAI
sys.path.insert(0,str(Path(__file__).parents[1]))
from src.graphiti_adapter import local_profile
from src.local_llm import LocalOpenAIGenericClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.prompts.models import Message
class ProbeResponse(BaseModel): status:str
async def main():
 c=local_profile();client=AsyncOpenAI(api_key=c['api_key'],base_url=c['base_url']);out={'profile':{k:c[k] for k in ('profile','base_url','llm_model','small_model','embedder_model','embedding_dim')}}
 async def raw(name,response_format=None):
  try:
   kwargs={'model':c['llm_model'],'messages':[{'role':'user','content':'Reply with exactly a JSON object {"status":"ok"}.'}]}
   if response_format:kwargs['response_format']=response_format
   answer=await client.chat.completions.create(**kwargs);json.loads(answer.choices[0].message.content or '');out[name]={'status':'pass'}
  except Exception as exc:out[name]={'status':'fail','error':f'{type(exc).__name__}: {exc}'}
 await raw('plain_chat');await raw('json_object',{'type':'json_object'})
 try:
  llm=LocalOpenAIGenericClient(config=LLMConfig(api_key=c['api_key'],base_url=c['base_url'],model=c['llm_model'],small_model=c['small_model']),structured_output_mode='json_object')
  response=await llm.generate_response([Message(role='system',content='Return only JSON.'),Message(role='user',content='Return status ok.')],response_model=ProbeResponse);ProbeResponse.model_validate(response);out['graphiti_structured']={'status':'pass'}
 except Exception as exc:out['graphiti_structured']={'status':'fail','error':f'{type(exc).__name__}: {exc}'}
 print(json.dumps(out,indent=2));return 0 if all(out[x]['status']=='pass' for x in ('plain_chat','json_object','graphiti_structured')) else 2
if __name__=='__main__':raise SystemExit(asyncio.run(main()))
