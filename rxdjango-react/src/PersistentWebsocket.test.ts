import PersistentWebSocket from './PersistentWebsocket';

// Mock WebSocket
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;

  url: string;
  protocols: string[];
  readyState: number = MockWebSocket.OPEN;

  onopen: (() => void) | null = null;
  onclose: ((event: any) => void) | null = null;
  onmessage: ((event: any) => void) | null = null;

  sent: string[] = [];
  closed = false;

  constructor(url: string, protocols: string[] = []) {
    this.url = url;
    this.protocols = protocols;
    // Auto-trigger onopen on next tick
    setTimeout(() => this.onopen?.(), 0);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.closed = true;
    this.readyState = MockWebSocket.CLOSED;
  }
}

// Install mock
(global as any).WebSocket = MockWebSocket;

function createWs(token = 'test-token') {
  return new PersistentWebSocket('ws://localhost/ws/', token, [], 10, 100);
}

function getSocket(ws: PersistentWebSocket): MockWebSocket {
  return (ws as any).ws as MockWebSocket;
}

describe('PersistentWebSocket', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('sends auth token on open', () => {
    const ws = createWs('my-token');
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    expect(socket.sent[0]).toBe(JSON.stringify({ token: 'my-token', lastUpdate: null }));
  });

  it('calls onOpen callback', () => {
    const ws = createWs();
    const onOpen = jest.fn();
    ws.onOpen = onOpen;
    ws.connect();
    jest.runAllTimers();

    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('does not create duplicate connections', () => {
    const ws = createWs();
    ws.connect();
    const socket1 = getSocket(ws);
    ws.connect(); // second call should be no-op
    const socket2 = getSocket(ws);
    expect(socket1).toBe(socket2);
  });

  it('routes auth status on first message', () => {
    const ws = createWs();
    const onAuth = jest.fn();
    ws.onAuth = onAuth;
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });

    expect(onAuth).toHaveBeenCalledWith({ type: 'auth', statusCode: 200 });
    expect(ws.authStatus).toEqual({ type: 'auth', statusCode: 200 });
  });

  it('calls onConnected for status 200', () => {
    const ws = createWs();
    const onConnected = jest.fn();
    ws.onConnected = onConnected;
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });

    expect(onConnected).toHaveBeenCalledTimes(1);
  });

  it('disconnects on auth error', () => {
    const ws = createWs();
    const onError = jest.fn();
    ws.onError = onError;
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 401, error: 'error/unauthorized' }) });

    expect(onError).toHaveBeenCalledWith(expect.any(Error));
    expect(socket.closed).toBe(true);
  });

  it('routes instance arrays after auth', () => {
    const ws = createWs();
    const onInstances = jest.fn();
    ws.onInstances = onInstances;
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    // First message is auth
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });
    // Second message is instances (array)
    const instances = [{ id: 1, _instance_type: 'test.Ser', _operation: 'create' }];
    socket.onmessage!({ data: JSON.stringify(instances) });

    expect(onInstances).toHaveBeenCalledWith(instances);
  });

  it('routes action responses by callId', () => {
    const ws = createWs();
    const onAction = jest.fn();
    ws.onActionResponse = onAction;
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });
    socket.onmessage!({ data: JSON.stringify({ type: 'actionResponse', callId: 42, result: 'ok' }) });

    expect(onAction).toHaveBeenCalledWith({ type: 'actionResponse', callId: 42, result: 'ok' });
  });

  it('routes runtimeVar messages', () => {
    const ws = createWs();
    const onRuntime = jest.fn();
    ws.onRuntimeStateChange = onRuntime;
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });
    socket.onmessage!({ data: JSON.stringify({ type: 'runtimeVar', var: 'mode', value: 'edit' }) });

    expect(onRuntime).toHaveBeenCalledWith({ type: 'runtimeVar', var: 'mode', value: 'edit' });
  });

  it('routes initialAnchors message', () => {
    const ws = createWs();
    const onAnchors = jest.fn();
    ws.onInitialAnchors = onAnchors;
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });
    socket.onmessage!({ data: JSON.stringify({ type: 'initialAnchors', anchorIds: [1, 2, 3] }) });

    expect(onAnchors).toHaveBeenCalledWith([1, 2, 3]);
  });

  it('calls onEmpty for empty initialAnchors', () => {
    const ws = createWs();
    const onEmpty = jest.fn();
    ws.onEmpty = onEmpty;
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });
    socket.onmessage!({ data: JSON.stringify({ type: 'initialAnchors', anchorIds: [] }) });

    expect(onEmpty).toHaveBeenCalledTimes(1);
  });

  it('routes prependAnchor message', () => {
    const ws = createWs();
    const onPrepend = jest.fn();
    ws.onAnchorPrepend = onPrepend;
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });
    socket.onmessage!({ data: JSON.stringify({ type: 'prependAnchor', anchorId: 99 }) });

    expect(onPrepend).toHaveBeenCalledWith(99);
  });

  it('routes system messages', () => {
    const ws = createWs();
    const onSystem = jest.fn();
    ws.onSystem = onSystem;
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });
    socket.onmessage!({ data: JSON.stringify({ type: 'system', source: 'system' }) });

    expect(onSystem).toHaveBeenCalledWith({ type: 'system', source: 'system' });
  });

  it('triggers reconnect on maintenance message', () => {
    const ws = createWs();
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });
    socket.onmessage!({ data: JSON.stringify({ type: 'maintenance' }) });

    // After maintenance, ws should be cleared for reconnection
    expect(getSocket(ws)).toBeUndefined();
  });

  it('send logs error when socket not open', () => {
    const ws = createWs();
    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();
    ws.send('test');
    expect(consoleSpy).toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it('send transmits data when socket is open', () => {
    const ws = createWs();
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    ws.send('hello');
    // sent[0] is the auth token, sent[1] is our message
    expect(socket.sent[1]).toBe('hello');
  });

  it('disconnect prevents reconnection with reason', () => {
    const ws = createWs();
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    ws.disconnect('manual-disconnect');
    expect(socket.closed).toBe(true);
    expect(getSocket(ws)).toBeUndefined();
  });

  it('reconnects with exponential backoff on unclean close', () => {
    const ws = createWs();
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    // Simulate unclean close (wasClean=false)
    socket.onclose!({ wasClean: false } as any);

    // Should schedule reconnection
    expect(getSocket(ws)).toBeUndefined(); // ws cleared
    jest.advanceTimersByTime(10); // initial interval
    // New socket should be created
    expect(getSocket(ws)).toBeDefined();
  });

  it('ignores non-object non-array messages after auth', () => {
    const ws = createWs();
    const onInstances = jest.fn();
    const onAction = jest.fn();
    ws.onInstances = onInstances;
    ws.onActionResponse = onAction;
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });
    // Send a plain string (not JSON object or array)
    socket.onmessage!({ data: '"just a string"' });

    expect(onInstances).not.toHaveBeenCalled();
    expect(onAction).not.toHaveBeenCalled();
  });

  it('disconnect clears pending reconnect timer', () => {
    const ws = createWs();
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    // Trigger reconnect schedule (onclose sets ws=undefined and schedules timer)
    socket.onclose!({ wasClean: false } as any);
    expect(getSocket(ws)).toBeUndefined(); // ws cleared after close

    // Now disconnect with reason before timer fires - should clear the timer
    ws.disconnect('manual-disconnect');

    jest.advanceTimersByTime(1000);
    // Should not have reconnected - still undefined
    expect(getSocket(ws)).toBeUndefined();
  });

  it('does not reconnect after auth error disconnect', () => {
    const ws = createWs();
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    // Auth error
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 401, error: 'error/unauthorized' }) });
    // Close event fires
    socket.onclose!({ wasClean: true } as any);

    jest.advanceTimersByTime(1000);
    // Should not reconnect
    expect(getSocket(ws)).toBeUndefined();
  });

  it('logs warning for unknown message type in development', () => {
    const ws = createWs();
    ws.connect();
    jest.runAllTimers();

    const socket = getSocket(ws);
    socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });

    const warnSpy = jest.spyOn(console, 'warn').mockImplementation();
    socket.onmessage!({ data: JSON.stringify({ type: 'unknownType', payload: 42 }) });

    expect(warnSpy).toHaveBeenCalledWith(
      'RxDjango: Unknown message type:',
      'unknownType',
      expect.objectContaining({ type: 'unknownType' }),
    );
    warnSpy.mockRestore();
  });

  it('tracks lastUpdate from received instances and sends it on reconnect', () => {
    const ws = createWs('token');
    ws.connect();
    jest.runAllTimers();

    const socket1 = getSocket(ws);
    socket1.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });

    // Receive instances with _tstamp values; lastUpdate should track the max
    socket1.onmessage!({ data: JSON.stringify([
      { id: 1, _instance_type: 'test.Ser', _operation: 'initial_state', _tstamp: 500 },
      { id: 2, _instance_type: 'test.Ser', _operation: 'initial_state', _tstamp: 700 },
    ]) });

    // Simulate unclean close to trigger reconnect
    socket1.onclose!({ wasClean: false } as any);
    jest.advanceTimersByTime(10); // fire reconnect timer
    jest.runAllTimers();          // fire new socket's onopen (0ms setTimeout)

    // New socket should send lastUpdate: 700 (the maximum _tstamp seen)
    const socket2 = getSocket(ws);
    expect(socket2!.sent[0]).toBe(JSON.stringify({ token: 'token', lastUpdate: 700 }));
  });

  it('resets lastUpdate to null after intentional disconnect', () => {
    const ws = createWs('token');
    ws.connect();
    jest.runAllTimers();

    const socket1 = getSocket(ws);
    socket1.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });

    // Receive instance with _tstamp to set lastUpdate
    socket1.onmessage!({ data: JSON.stringify([
      { id: 1, _instance_type: 'test.Ser', _operation: 'initial_state', _tstamp: 500 },
    ]) });

    // Intentional disconnect must reset lastUpdate
    ws.disconnect('manual-disconnect');

    // Reconnect manually; auth message should have lastUpdate: null
    ws.connect();
    jest.runAllTimers();

    const socket2 = getSocket(ws);
    expect(socket2!.sent[0]).toBe(JSON.stringify({ token: 'token', lastUpdate: null }));
  });
});
