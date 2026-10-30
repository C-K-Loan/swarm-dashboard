import fs from 'node:fs';
import path from 'node:path';
export const ROOT = path.resolve('..');
// Single dataset registry: every labels dropdown and run picker reads this; no per-widget allowlists.
// hidden: kept for review/training links but never offered as labels (e.g. the 72-turn prototype).
export const RUNS: {name:string,label:string,prompt:'v0.2-full'|'v0.3-lean',model:string,hidden?:boolean}[] = [
  {name:'day1',label:'Luna (gpt-6)',prompt:'v0.2-full',model:'luna'},
  {name:'prototype',label:'Luna (gpt-6) prototype',prompt:'v0.2-full',model:'luna-prototype',hidden:true},
  {name:'haiku45-day1',label:'Haiku 4.5',prompt:'v0.2-full',model:'haiku'},
  {name:'sonnet45-day1',label:'Sonnet 4.5',prompt:'v0.2-full',model:'sonnet'},
  {name:'opus45-day1',label:'Opus 4.5',prompt:'v0.2-full',model:'opus'},
  {name:'nemotron-day1',label:'Nemotron Ultra',prompt:'v0.2-full',model:'nemotron-ultra'},
  {name:'nemotron-reasoning-day1',label:'Nemotron Ultra reasoning',prompt:'v0.2-full',model:'nemotron-ultra-reasoning'},
  {name:'nemotron-full',label:'Nemotron Ultra (full)',prompt:'v0.2-full',model:'nemotron-ultra-full'},
  {name:'nemotron35-super-vl-day1',label:'Nemotron Super VL',prompt:'v0.2-full',model:'nemotron-super-vl'},
  {name:'nemotron35-lightning-day1',label:'Nemotron Lightning',prompt:'v0.2-full',model:'nemotron-lightning'},
  {name:'nemotron3-nano-reasoning-day1',label:'Nemotron Nano reasoning',prompt:'v0.2-full',model:'nemotron-nano'},
  {name:'gemma4-31b-day1',label:'Gemma 4 31B',prompt:'v0.2-full',model:'gemma31'},
  {name:'gemma4-26b-day1',label:'Gemma 4 26B',prompt:'v0.2-full',model:'gemma26'},
  {name:'qwen30b-layoutg-day1',label:'Qwen3-30B (trimmed)',prompt:'v0.2-full',model:'qwen30b'},
  {name:'nemotron-ultra-lean-day1',label:'Nemotron Ultra',prompt:'v0.3-lean',model:'nemotron-ultra'},
  {name:'gemma4-31b-lean-day1',label:'Gemma 4 31B',prompt:'v0.3-lean',model:'gemma31'},
  {name:'qwen30b-lean-day1',label:'Qwen3-30B',prompt:'v0.3-lean',model:'qwen30b'},
  {name:'haiku45-lean-day1',label:'Haiku 4.5',prompt:'v0.3-lean',model:'haiku'},
  {name:'qwen35-4b-base-gamejam',label:'Qwen3.5-4B',prompt:'v0.3-lean',model:'qwen35-4b'},
  {name:'qwen35-9b-base-gamejam',label:'Qwen3.5-9B',prompt:'v0.3-lean',model:'qwen35-9b'},
  {name:'qwen30b-tf-lean-day1-full',label:'Qwen3-30B base (TF)',prompt:'v0.3-lean',model:'qwen30b-base-tf'},
  {name:'qwen30b-lora-r16-lean-day1',label:'Qwen3-30B LoRA r16',prompt:'v0.3-lean',model:'qwen30b-lora-r16'},
  {name:'qwen30b-bf16-base-lean-day1-modal',label:'Qwen3-30B base (modal-h100-bf16)',prompt:'v0.3-lean',model:'qwen30b-bf16-base-modal'},
  {name:'qwen30b-bf16-lora-r16-lean-day1-modal',label:'Qwen3-30B LoRA r16 (modal-h100-bf16)',prompt:'v0.3-lean',model:'qwen30b-bf16-lora-r16-modal'},
];
export const DATASETS = RUNS.map(r=>r.name);
export function dataDir(requested?: string | null) {
  const active=path.join(ROOT,'data/v02/active.json');
  const name=requested || process.env.CCT_V02_DATASET || (fs.existsSync(active)?JSON.parse(fs.readFileSync(active,'utf8')).dataset:'prototype');
  if(typeof name!=='string'||!DATASETS.includes(name))throw new Error('Invalid local dataset name');
  return path.join(ROOT,'data/v02',name);
}
export function jsonFile(name: string, fallback: any = null, dataset?: string | null) {
  const p = path.join(dataDir(dataset), name);
  return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : fallback;
}
export function reviews(dataset?: string | null) {
  const p = path.join(dataDir(dataset), 'reviews.jsonl');
  const rows = fs.existsSync(p) ? fs.readFileSync(p, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
  return Object.fromEntries(rows.map(row => [row.turn_id, row]));
}
export const headers = {'Content-Type':'application/json', 'Cache-Control':'no-store'};

export function availableDatasets() {
  return DATASETS.filter(name=>fs.existsSync(path.join(dataDir(name),'review.json'))).map(name=>({name,manifest:jsonFile('manifest.json',{},name),status:jsonFile('status.json',{},name)}));
}

// Runs the timeline can replay (turn-index.json written); same registry as availableDatasets().
// Labels dropdowns offer only visible runs with replayable data and at least one completed turn.
export function timelineRuns() {
  return RUNS.filter(r=>!r.hidden&&fs.existsSync(path.join(dataDir(r.name),'turn-index.json'))).map(r=>{
    const st=jsonFile('status.json',{},r.name)||{};
    return {...r,scope:r.name.endsWith('-gamejam')?'gamejam':'Day1',completed:st.completed??0,total:st.total??0,state:st.state??null};
  }).filter(r=>r.completed>0);
}
// Day1 is the default scope, so only other scopes (gamejam) are spelled out.
export const runText=(r:{label:string,scope:string,completed:number,total:number})=>`${r.label} · ${r.completed}/${r.total}${r.scope==='Day1'?'':' '+r.scope}`;
