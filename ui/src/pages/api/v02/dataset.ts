import type { APIRoute } from 'astro';
import path from 'node:path';
import { readFileSync } from 'node:fs';
import { jsonFile, reviews, headers, dataDir } from '../../../lib/v02-store';
// Real sender of input turns the indexer labeled "human" (scripts/v02/sender_attribution.py, same classifier as the timeline).
const senderOverlay=(dir:string,ds:string)=>{try{return JSON.parse(readFileSync(path.join(path.dirname(dir),'_sender-attribution',ds+'.json'),'utf8')).turns||{};}catch{return {};}};
export const prerender = false;
export const GET: APIRoute = ({url}) => {
  try {
    const dir=dataDir(url.searchParams.get('dataset')),selected=path.basename(dir),senders=senderOverlay(dir,selected);
    const data = jsonFile('turn-index.json',null,selected) || jsonFile('review.json',null,selected);
    if (!data) return new Response(JSON.stringify({error:'Dataset is being prepared.'}), {status:503,headers});
    const id=url.searchParams.get('turn');
    if (id!==null) {
      if (!/^[A-Za-z0-9_-]+$/.test(id)) return new Response(JSON.stringify({error:'Invalid turn ID.'}),{status:400,headers});
      const entry=data.turns.find((t:any)=>t.id===id);
      if (!entry) return new Response(JSON.stringify({error:'Turn not found.'}),{status:404,headers});
      let turn;
      if (data.layout_version===2) {
        if (entry.has_prediction) {
          turn=jsonFile('turn-states/'+id+'.json',null,selected);
          if (!turn) return new Response(JSON.stringify({error:'Turn state is being prepared. Retry shortly.'}),{status:503,headers});
        } else {
          const source=jsonFile('turns.json',[],selected).find((t:any)=>t.id===id);
          if (!source) return new Response(JSON.stringify({error:'Source turn not found.'}),{status:404,headers});
          turn={...source,prediction:null,resolved_prediction:null,generated_at:null,task_states:{},actor_state_after:null,task_names:{},state_after:null,llm_input:null};
        }
      } else turn=entry;
      const {_publication,...publicTurn}=turn;
      if (senders[id]) publicTurn.sender_attribution=senders[id];
      return new Response(JSON.stringify({dataset:selected,turn:publicTurn}),{headers});
    }
    const quota = jsonFile('quota.json', {},selected);
    const {_publication,...publicData}=data;
    publicData.turns=(publicData.turns||[]).map((t:any)=>senders[t.id]?{...t,sender_attribution:senders[t.id]}:t);
    return new Response(JSON.stringify({...publicData, status:jsonFile('status.json',data.status,selected), dataset:selected, reviews:reviews(selected), quota}), {headers});
  } catch {
    return new Response(JSON.stringify({error:'Could not load the prototype dataset. Retry shortly.'}), {status:500,headers});
  }
};
