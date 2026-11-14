"""Route native RPC over standard input and output with separate client and internal request IDs."""
import json
import threading
import uuid
import subprocess
import time

PROTOCOL_META = 'io.modelcontextprotocol/protocolVersion'
CAPABILITIES_META = 'io.modelcontextprotocol/clientCapabilities'

class RPCError(Exception):
    def __init__(self, error):
        self.error = error
        super().__init__(error.get('message','Upstream error'))


class PendingReply:
    def __init__(self):
        self.ready = threading.Event()
        self.response = None

    def complete(self, response):
        if not self.ready.is_set():
            self.response = response
            self.ready.set()


class Bridge:
    def __init__(self, child, emit):
        self.child, self.emit = child, emit
        self.pending = {}
        self.client_ids = {}
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.closed = threading.Event()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def send(self, message):
        with self.write_lock:
            self.child.stdin.write(json.dumps(message, ensure_ascii=True)+'\n')
            self.child.stdin.flush()

    def request(self, method, params, client_id=None, timeout=180):
        if self.closed.is_set(): raise ConnectionError('StudioMCP transport is closed')
        request_id='extra-rpc-'+uuid.uuid4().hex
        reply=PendingReply()
        with self.lock:
            if self.closed.is_set():
                raise ConnectionError('StudioMCP transport is closed')
            self.pending[request_id]=reply
            if client_id is not None: self.client_ids[client_id]=request_id
        try:
            self.send({'jsonrpc':'2.0','id':request_id,'method':method,'params':params})
            if not reply.ready.wait(timeout):
                raise TimeoutError('StudioMCP timed out; dispatched code may still be running')
            response = reply.response
            if isinstance(response, ConnectionError):
                raise response
            if 'error' in response: raise RPCError(response['error'])
            return response.get('result',{})
        finally:
            with self.lock:
                self.pending.pop(request_id,None)
                if client_id is not None: self.client_ids.pop(client_id,None)

    def forward(self, message):
        if message.get('method')=='notifications/cancelled':
            params=dict(message.get('params',{}))
            with self.lock: mapped=self.client_ids.get(params.get('requestId'))
            if mapped: params['requestId']=mapped
            message={**message,'params':params}
        self.send(message)

    def read(self):
        try:
            for line in self.child.stdout:
                message=json.loads(line)
                if not isinstance(message,dict): raise ValueError('Invalid upstream JSON-RPC object')
                if 'method' not in message and 'id' in message:
                    with self.lock:
                        reply = self.pending.get(message['id'])
                        if reply is not None:
                            reply.complete(message)
                            continue
                    # Discard late internal replies rather than leaking private IDs.
                    if str(message['id']).startswith('extra-rpc-'): continue
                self.emit(message)
        except (ValueError, OSError) as error:
            reason='Invalid StudioMCP transport: '+str(error)
        else: reason='StudioMCP exited; restart the MCP connection'
        self.closed.set()
        with self.lock:
            for reply in self.pending.values():
                reply.complete(ConnectionError(reason))

class ResilientBridge:
    """Reconnect the upstream process, replay initialization, and retry explicitly read-only work."""
    supports_read_retry=True

    def __init__(self,command,emit,stderr=None):
        self.command=command;self.emit=emit;self.stderr=stderr
        self.restart_lock=threading.Lock();self.parameters=None;self.initialized=False
        self.generation=0;self.reconnections=0;self.closed=False
        self.stateless_version=None
        self._spawn()

    def _spawn(self):
        self.child=subprocess.Popen(self.command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,
                                    text=True,encoding='utf-8',bufsize=1)
        self.bridge=Bridge(self.child,self.emit)
        self.generation+=1

    def _dispose(self):
        try:self.child.stdin.close()
        except (OSError,ValueError):pass
        try:self.child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.child.terminate()
            try:self.child.wait(timeout=2)
            except subprocess.TimeoutExpired:self.child.kill();self.child.wait(timeout=2)
        self.bridge.reader.join(timeout=1)
        self.child.stdout.close()

    def restart(self,failed_generation,skip_initialize=False):
        with self.restart_lock:
            if self.closed:raise ConnectionError('MCP transport is shutting down')
            if self.generation!=failed_generation:return
            self._dispose();self._spawn()
            if self.parameters and not skip_initialize:
                self.bridge.request('initialize',self.parameters,timeout=20)
                if self.initialized:self.bridge.send({'jsonrpc':'2.0','method':'notifications/initialized'})
            self.reconnections+=1

    def request(self,method,params,client_id=None,timeout=180,read_only=False):
        if self.closed:raise ConnectionError('MCP transport is closed')
        generation=self.generation
        if method=='initialize':self.parameters=params
        else:
            metadata=params.get('_meta')
            version=metadata.get(PROTOCOL_META) if isinstance(metadata,dict) else None
            if isinstance(version,str) and version >= '2026-07-28':
                self.stateless_version=version
            elif self.stateless_version:
                params={**params,'_meta':{PROTOCOL_META:self.stateless_version,CAPABILITIES_META:{},
                                         **(metadata if isinstance(metadata,dict) else {})}}
        # A dead process can be replaced before dispatch, including for a new
        # mutation. A mutation with an uncertain previous dispatch is NOT retried.
        if self.bridge.closed.is_set():self.restart(generation,skip_initialize=method=='initialize');generation=self.generation
        try:return self.bridge.request(method,params,client_id,timeout)
        except (ConnectionError,BrokenPipeError) as error:
            if not read_only and method!='initialize':
                raise ConnectionError('Connection lost after possible dispatch; operation was not replayed. Reconcile the journal before retrying.') from error
            self.restart(generation,skip_initialize=method=='initialize')
            if method=='initialize':return self.bridge.request(method,params,client_id,timeout)
            return self.bridge.request(method,params,client_id,timeout)

    def forward(self,message):
        if message.get('method')=='notifications/initialized':self.initialized=True
        self.bridge.forward(message)

    def close(self):
        with self.restart_lock:
            self.closed=True;self._dispose()
