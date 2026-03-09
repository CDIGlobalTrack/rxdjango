import ContextChannel from './ContextChannel';
import { Model } from './ContextChannel.interfaces';

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
    setTimeout(() => this.onopen?.(), 0);
  }
  send(data: string) { this.sent.push(data); }
  close() { this.closed = true; this.readyState = MockWebSocket.CLOSED; }
}

(global as any).WebSocket = MockWebSocket;

// Concrete test channel
const TEST_MODEL: Model = {
  'test.ProjectSerializer': {
    'tasks': 'test.TaskSerializer',
  },
  'test.TaskSerializer': {},
};

class TestChannel extends ContextChannel<any> {
  endpoint = '/ws/project/{projectId}/';
  anchor = 'test.ProjectSerializer';
  baseURL = 'ws://localhost:8000';
  model = TEST_MODEL;
  many = false;
  runtimeState: any = undefined;

  setArgs(args: { [key: string]: number | string }) {
    this.args = args;
  }
}

function getWs(channel: TestChannel): any {
  return (channel as any).ws;
}

function getSocket(channel: TestChannel): MockWebSocket | undefined {
  const ws = getWs(channel);
  return ws ? (ws as any).ws : undefined;
}

function simulateAuth(channel: TestChannel) {
  const socket = getSocket(channel)!;
  socket.onmessage!({ data: JSON.stringify({ type: 'auth', statusCode: 200 }) });
}

function simulateMessage(channel: TestChannel, data: any) {
  const socket = getSocket(channel)!;
  socket.onmessage!({ data: JSON.stringify(data) });
}

describe('ContextChannel', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('constructs with token', () => {
    const channel = new TestChannel('test-token');
    expect(channel.connected).toBe(false);
  });

  it('getEndpoint substitutes args', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 42 });
    // Access private method via any
    const endpoint = (channel as any).getEndpoint();
    expect(endpoint).toBe('ws://localhost:8000/ws/project/42/');
  });

  it('subscribe triggers connect on first subscriber', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    const listener = jest.fn();
    channel.subscribe(listener);
    jest.runAllTimers();

    // Should have a websocket now
    expect(getSocket(channel)).toBeDefined();
  });

  it('unsubscribe disconnects on last subscriber', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    const unsub = channel.subscribe(jest.fn());
    jest.runAllTimers();

    const socket = getSocket(channel);
    unsub();
    expect(socket?.closed).toBe(true);
    expect(getSocket(channel)).toBeUndefined();
  });

  it('multiple subscribers keep connection alive', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    const unsub1 = channel.subscribe(jest.fn());
    const unsub2 = channel.subscribe(jest.fn());
    jest.runAllTimers();

    unsub1();
    expect(getSocket(channel)?.closed).toBeFalsy();

    const socket = getSocket(channel);
    unsub2();
    expect(socket?.closed).toBe(true);
    expect(getSocket(channel)).toBeUndefined();
  });

  it('notifies listeners when instances arrive', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    const listener = jest.fn();
    channel.subscribe(listener);
    jest.runAllTimers();

    simulateAuth(channel);
    simulateMessage(channel, { type: 'initialAnchors', anchorIds: [1] });
    simulateMessage(channel, [
      { id: 1, _instance_type: 'test.ProjectSerializer', _operation: 'initial_state', _tstamp: 1, name: 'Test' },
    ]);

    expect(listener).toHaveBeenCalled();
    const state = listener.mock.calls[listener.mock.calls.length - 1][0];
    expect(state.id).toBe(1);
    expect(state.name).toBe('Test');
  });

  it('callAction sends JSON over websocket and resolves on response', async () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    channel.subscribe(jest.fn());
    jest.runAllTimers();
    simulateAuth(channel);

    // Call action (use any to access protected method)
    const promise = (channel as any).callAction('doSomething', ['arg1']);

    // Find the sent action message (skip auth token)
    const socket = getSocket(channel)!;
    const actionMsg = JSON.parse(socket.sent[socket.sent.length - 1]);
    expect(actionMsg.action).toBe('doSomething');
    expect(actionMsg.params).toEqual(['arg1']);

    // Simulate response
    simulateMessage(channel, { type: 'actionResponse', callId: actionMsg.callId, result: { ok: true } });

    const result = await promise;
    expect(result).toEqual({ ok: true });
  });

  it('callAction rejects on error response', async () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    channel.subscribe(jest.fn());
    jest.runAllTimers();
    simulateAuth(channel);

    const promise = (channel as any).callAction('failAction', []);
    const socket = getSocket(channel)!;
    const actionMsg = JSON.parse(socket.sent[socket.sent.length - 1]);

    simulateMessage(channel, { type: 'actionResponse', callId: actionMsg.callId, error: { code: 500, message: 'boom' } });

    await expect(promise).rejects.toEqual({ code: 500, message: 'boom' });
  });

  it('subscribeInstance notifies on specific instance update', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    channel.subscribe(jest.fn());
    jest.runAllTimers();
    simulateAuth(channel);

    // Set up initial state
    simulateMessage(channel, { type: 'initialAnchors', anchorIds: [1] });
    simulateMessage(channel, [
      { id: 1, _instance_type: 'test.ProjectSerializer', _operation: 'initial_state', _tstamp: 1, tasks: [10] },
    ]);
    simulateMessage(channel, [
      { id: 10, _instance_type: 'test.TaskSerializer', _operation: 'initial_state', _tstamp: 1, name: 'Task' },
    ]);

    const instanceListener = jest.fn();
    channel.subscribeInstance(instanceListener, 10, 'test.TaskSerializer');

    // Should have been called with current state
    expect(instanceListener).toHaveBeenCalled();

    // Update the task
    simulateMessage(channel, [
      { id: 10, _instance_type: 'test.TaskSerializer', _operation: 'update', _tstamp: 2, name: 'Updated' },
    ]);

    expect(instanceListener).toHaveBeenCalledTimes(2);
  });

  it('getInstance returns instance or null', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    channel.subscribe(jest.fn());
    jest.runAllTimers();
    simulateAuth(channel);

    simulateMessage(channel, { type: 'initialAnchors', anchorIds: [1] });
    simulateMessage(channel, [
      { id: 1, _instance_type: 'test.ProjectSerializer', _operation: 'initial_state', _tstamp: 1 },
    ]);

    const found = channel.getInstance('test.ProjectSerializer', 1);
    expect(found).toBeDefined();
    expect(found!.id).toBe(1);

    const missing = channel.getInstance('test.TaskSerializer', 999);
    expect(missing).toBeNull();
  });

  it('subscribeRuntimeState notifies on runtimeVar changes', () => {
    const channel = new TestChannel('token');
    channel.runtimeState = {};
    channel.setArgs({ projectId: 1 });
    channel.subscribe(jest.fn());
    jest.runAllTimers();
    simulateAuth(channel);

    const runtimeListener = jest.fn();
    channel.subscribeRuntimeState(runtimeListener);

    simulateMessage(channel, { type: 'runtimeVar', var: 'mode', value: 'edit' });

    expect(runtimeListener).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'edit' })
    );
  });

  it('noConnectionListener called on close and open', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    const noConnListener = jest.fn();
    channel.subscribe(jest.fn(), noConnListener);
    jest.runAllTimers();

    // onopen should call with undefined (connected)
    expect(noConnListener).toHaveBeenCalledWith(undefined);

    // Simulate close
    const socket = getSocket(channel)!;
    socket.onclose!({ wasClean: true } as any);

    expect(noConnListener).toHaveBeenCalledWith(expect.any(Date));
  });

  it('disconnect method closes websocket', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    channel.subscribe(jest.fn());
    jest.runAllTimers();

    const socket = getSocket(channel);
    channel.disconnect();
    expect(socket?.closed).toBe(true);
    expect(getSocket(channel)).toBeUndefined();
  });

  it('init is idempotent', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    channel.init();
    const builder1 = (channel as any).builder;
    channel.init(); // second call should be no-op
    const builder2 = (channel as any).builder;
    expect(builder1).toBe(builder2);
  });

  it('prependAnchor adds to state via onAnchorPrepend', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    const listener = jest.fn();
    channel.subscribe(listener);
    jest.runAllTimers();
    simulateAuth(channel);

    simulateMessage(channel, { type: 'initialAnchors', anchorIds: [1] });
    simulateMessage(channel, [
      { id: 1, _instance_type: 'test.ProjectSerializer', _operation: 'initial_state', _tstamp: 1, name: 'First' },
    ]);

    simulateMessage(channel, { type: 'prependAnchor', anchorId: 2 });
    // listener should have been called for the prepend
    expect(listener).toHaveBeenCalled();
  });

  it('unsubscribeInstance removes listener', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    channel.subscribe(jest.fn());
    jest.runAllTimers();
    simulateAuth(channel);

    simulateMessage(channel, { type: 'initialAnchors', anchorIds: [1] });
    simulateMessage(channel, [
      { id: 1, _instance_type: 'test.ProjectSerializer', _operation: 'initial_state', _tstamp: 1, tasks: [10] },
    ]);
    simulateMessage(channel, [
      { id: 10, _instance_type: 'test.TaskSerializer', _operation: 'initial_state', _tstamp: 1, name: 'Task' },
    ]);

    const instanceListener = jest.fn();
    const unsub = channel.subscribeInstance(instanceListener, 10, 'test.TaskSerializer');
    expect(instanceListener).toHaveBeenCalledTimes(1);

    unsub();

    // Update after unsubscribe should not call listener again
    simulateMessage(channel, [
      { id: 10, _instance_type: 'test.TaskSerializer', _operation: 'update', _tstamp: 2, name: 'Updated' },
    ]);
    expect(instanceListener).toHaveBeenCalledTimes(1);
  });

  it('subscribeInstance returns unsubscribe when instance not found', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    channel.subscribe(jest.fn());
    jest.runAllTimers();
    simulateAuth(channel);

    simulateMessage(channel, { type: 'initialAnchors', anchorIds: [1] });
    simulateMessage(channel, [
      { id: 1, _instance_type: 'test.ProjectSerializer', _operation: 'initial_state', _tstamp: 1 },
    ]);

    const instanceListener = jest.fn();
    const unsub = channel.subscribeInstance(instanceListener, 999, 'test.TaskSerializer');
    // Should not have been called since instance doesn't exist
    expect(instanceListener).not.toHaveBeenCalled();
    expect(typeof unsub).toBe('function');
  });

  it('unsubscribeRuntimeState stops notifications', () => {
    const channel = new TestChannel('token');
    channel.runtimeState = {};
    channel.setArgs({ projectId: 1 });
    channel.subscribe(jest.fn());
    jest.runAllTimers();
    simulateAuth(channel);

    const runtimeListener = jest.fn();
    const unsub = channel.subscribeRuntimeState(runtimeListener);

    simulateMessage(channel, { type: 'runtimeVar', var: 'mode', value: 'edit' });
    expect(runtimeListener).toHaveBeenCalledTimes(1);

    unsub();

    simulateMessage(channel, { type: 'runtimeVar', var: 'mode', value: 'view' });
    expect(runtimeListener).toHaveBeenCalledTimes(1); // not called again
  });

  it('callAction rejects when response has neither result nor error', async () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    channel.subscribe(jest.fn());
    jest.runAllTimers();
    simulateAuth(channel);

    const promise = (channel as any).callAction('doSomething', []);
    const socket = getSocket(channel)!;
    const actionMsg = JSON.parse(socket.sent[socket.sent.length - 1]);

    // Send a response that has neither result nor error
    simulateMessage(channel, { type: 'actionResponse', callId: actionMsg.callId });

    await expect(promise).rejects.toEqual({
      code: 500,
      message: 'Malformed action response: missing both result and error',
    });
  });

  it('receiveActionResponse logs error for unmatched callId', () => {
    const channel = new TestChannel('token');
    channel.setArgs({ projectId: 1 });
    channel.subscribe(jest.fn());
    jest.runAllTimers();
    simulateAuth(channel);

    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();
    simulateMessage(channel, { type: 'actionResponse', callId: 99999, result: 'orphan' });
    expect(consoleSpy).toHaveBeenCalledWith(expect.stringContaining('99999'));
    consoleSpy.mockRestore();
  });
});
