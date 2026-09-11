import json, os, secrets, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

CLIENT_TOKEN = os.environ.get('BRIDGE_CLIENT_TOKEN', '')
DEVICE_TOKEN = os.environ.get('BRIDGE_DEVICE_TOKEN', '')
TTL = int(os.environ.get('TASK_TTL_SECONDS', '600'))
PORT = int(os.environ.get('PORT', '10000'))
TASKS = {}
LOCK = threading.Lock()
ALLOWED_KINDS = {'sms.search','sms.get','sms.stats'}


def now(): return time.time()

def expire_tasks():
    t = now()
    for task in TASKS.values():
        if task['status'] in ('pending','claimed') and t - task['created_at'] > TTL:
            task['status'] = 'expired'; task['completed_at'] = t; task['error'] = 'task expired before completion'

def public(task, include_result=False):
    d = {k: task.get(k) for k in ('task_id','kind','status','created_at','claimed_at','completed_at')}
    if include_result:
        d['result'] = task.get('result'); d['error'] = task.get('error')
    return d

class H(BaseHTTPRequestHandler):
    server_version = 'XinyunBridge/0.4.0-dev'
    def log_message(self, fmt, *args):
        print('%s - - [%s] %s' % (self.client_address[0], self.log_date_time_string(), fmt%args), flush=True)
    def sendj(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def bearer(self):
        a=self.headers.get('Authorization','')
        return a[7:] if a.startswith('Bearer ') else ''
    def readj(self):
        try:
            n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
        except Exception: return None
    def need(self, which):
        token = CLIENT_TOKEN if which=='client' else DEVICE_TOKEN
        if not token or self.bearer()!=token:
            self.sendj(401, {'detail':'unauthorized'}); return False
        return True
    def do_GET(self):
        p=urlparse(self.path); path=p.path
        if path=='/health': return self.sendj(200, {'ok':True,'version':'0.4.0-dev'})
        if path=='/v1/device/tasks/next':
            if not self.need('device'): return
            with LOCK:
                expire_tasks(); pending=sorted((x for x in TASKS.values() if x['status']=='pending'), key=lambda x:x['created_at'])
                if not pending: return self.sendj(200, {'task':None})
                x=pending[0]; x['status']='claimed'; x['claimed_at']=now()
                return self.sendj(200, {'task':{'task_id':x['task_id'],'kind':x['kind'],'payload':x['payload']}})
        if path.startswith('/v1/tasks/'):
            if not self.need('client'): return
            tid=path.split('/')[-1]
            with LOCK:
                expire_tasks(); x=TASKS.get(tid)
                if not x: return self.sendj(404, {'detail':'task not found'})
                data=public(x, True)
                if parse_qs(p.query).get('consume',['false'])[0].lower()=='true' and x['status'] in ('completed','failed','expired'):
                    del TASKS[tid]
                return self.sendj(200, data)
        return self.sendj(404, {'detail':'not found'})
    def do_POST(self):
        p=urlparse(self.path); path=p.path; body=self.readj()
        if body is None: return self.sendj(400, {'detail':'invalid json'})
        if path=='/v1/tasks':
            if not self.need('client'): return
            kind=body.get('kind'); payload=body.get('payload')
            if kind not in ALLOWED_KINDS or not isinstance(payload,dict): return self.sendj(422, {'detail':'invalid task'})
            tid=secrets.token_urlsafe(18); x={'task_id':tid,'kind':kind,'payload':payload,'created_at':now(),'claimed_at':None,'completed_at':None,'status':'pending','result':None,'error':None}
            with LOCK: expire_tasks(); TASKS[tid]=x
            return self.sendj(200, public(x))
        if path.startswith('/v1/device/tasks/') and path.endswith('/result'):
            if not self.need('device'): return
            parts=path.strip('/').split('/'); tid=parts[3]
            with LOCK:
                expire_tasks(); x=TASKS.get(tid)
                if not x: return self.sendj(404, {'detail':'task not found'})
                if x['status'] not in ('pending','claimed'): return self.sendj(409, {'detail':'task is '+x['status']})
                x['completed_at']=now()
                if body.get('ok') is True:
                    x['status']='completed'; x['result']=body.get('result') or {}; x['error']=None
                else:
                    x['status']='failed'; x['result']=None; x['error']=body.get('error') or 'device returned failure'
                return self.sendj(200, public(x, True))
        return self.sendj(404, {'detail':'not found'})

if __name__=='__main__':
    print(f'Xinyun Phone Bridge relay listening on {PORT}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
