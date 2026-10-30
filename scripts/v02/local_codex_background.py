#!/usr/bin/env python3
"""Supervise only frozen Day1 subscription labeling; never recover invalid labels silently."""
import argparse,fcntl,json,os,subprocess,time
from pathlib import Path
import pipeline as p
import quota as q

def alive(pid):
 try:os.kill(pid,0);return True
 except ProcessLookupError:return False

def status(state,**kw):p.atomic(p.DATA/'local-background-status.json',{'state':state,'at':time.time(),'pid':os.getpid(),**kw})

def run(adopt=None):
 with (p.DATA/'.local-supervisor.lock').open('w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if adopt:
   status('running',child_pid=adopt)
   while alive(adopt):time.sleep(10)
  deadline=time.time()+14*86400
  while time.time()<deadline:
   count=len(p.ledger());total=len(p.read('turns.json'))
   if count==total:status('complete',completed=count,total=total);return
   if (p.DATA/'STOP_LOCAL').exists():status('stopped',reason='STOP_LOCAL');return
   meter=q.measure()
   if not meter['allowed']:
    if meter.get('reason')!='Included Codex quota exhausted; wait for reset, no paid fallback':status('stopped',reason=meter.get('reason'));return
    status('waiting_subscription_reset',completed=count,total=total)
    adopt=None
    time.sleep(60);continue
   # An adopted worker only stops unexpectedly on validation/runtime failure.
   # Such failures require inspection, rather than an unbounded retry loop.
   if adopt:status('stopped',reason='Adopted worker exited before completion; inspect local-background.log',completed=count);return
   env=os.environ.copy();env['CCT_V02_DATASET']='day1'
   with (p.DATA/'local-background.log').open('a') as log:
    child=subprocess.Popen(['python3','-u',str(p.ROOT/'scripts/v02/run_local_codex.py'),'--max-turns','1902'],cwd=p.ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
    status('running',child_pid=child.pid,completed=count,total=total);child.wait()
   if child.returncode:status('stopped',reason='Worker failure; inspect local-background.log',returncode=child.returncode);return
  status('stopped',reason='Supervisor reached 14-day lifetime bound')

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--adopt-pid',type=int);args=ap.parse_args();run(args.adopt_pid)
