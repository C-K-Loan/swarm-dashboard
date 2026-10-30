"""Select a local V0.2 dataset without changing or recharging earlier runs."""
import json,os,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def data_dir():
 name=os.environ.get('CCT_V02_DATASET')
 if not name:
  active=ROOT/'data/v02/active.json'
  name=json.loads(active.read_text())['dataset'] if active.exists() else 'prototype'
 if not re.fullmatch(r'[a-zA-Z0-9_-]+',name):raise ValueError('Invalid local dataset name')
 return ROOT/'data/v02'/name
DATA=data_dir()
