import {
  AuthStatus,
  TempInstance,
  SystemMessage,
} from './ContextChannel.interfaces';
import { ActionResponse } from './actions.d';


/** Reasons that permanently prevent WebSocket reconnection. */
const preventReconnectionReasons = {
  'authentication-error': 'authentication-error',
  'protocol-error': 'protocol-error',
  'no-subscribers': 'no-subscribers',
  'manual-disconnect': 'manual-disconnect'
};

/**
 * WebSocket wrapper with automatic reconnection and message routing.
 *
 * Handles connection drops gracefully with exponential backoff retry.
 * Routes incoming messages to appropriate handlers based on message type
 * (instances, action responses, runtime state changes, etc.).
 *
 * @example
 * ```typescript
 * const ws = new PersistentWebSocket('wss://example.com/ws/', authToken);
 * ws.onInstances = (instances) => { ... };
 * ws.onConnected = () => { ... };
 * ws.connect();
 * ```
 */
export default class PersistentWebSocket {

  private url: string;
  private protocols: string[];
  private initialReconnectInterval: number;
  private reconnectInterval: number;
  private maxReconnectInterval: number;
  private token: string;
  private ws: WebSocket | undefined;
  private authStatusReceived: boolean;
  private timer: NodeJS.Timeout | undefined;
  private reason: keyof typeof preventReconnectionReasons | undefined;

  public authStatus: AuthStatus | undefined;

  public onOpen: () => void = () => {};
  public onClose: (event: CloseEvent) => void = () => {};
  public onAuth: (authStatus: AuthStatus) => void = () => {};
  public onRuntimeStateChange: (runtimeState: unknown) => void = () => {};
  public onInitialAnchors: (anchors: number[]) => void = () => {};
  public onInstances: (instances: TempInstance[]) => void = () => {};
  public onActionResponse: (response: ActionResponse<unknown>) => void = () => {};
  public onAnchorPrepend: (anchorId: number) => void = () => {};
  public onSystem: (message: SystemMessage) => void = () => {};
  public onConnected: () => void = () => {};
  public onEmpty: () => void = () => {};
  public onError: (error: Error) => void = () => {};

  /**
   * @param url - WebSocket URL to connect to
   * @param token - Auth token sent as the first message after connection
   * @param protocols - Optional WebSocket sub-protocols
   * @param initialReconnectInterval - Initial reconnect delay in ms (doubles on each retry)
   * @param maxReconnectInterval - Maximum reconnect delay in ms
   */
  constructor(
    url: string,
    token: string,
    protocols = [],
    initialReconnectInterval = 50,
    maxReconnectInterval = 5000,
  ) {
    this.url = url;
    this.protocols = protocols;
    this.initialReconnectInterval = initialReconnectInterval;
    this.reconnectInterval = initialReconnectInterval;
    this.maxReconnectInterval = maxReconnectInterval;
    this.token = token;
    this.authStatusReceived = false;
  }

  /** Initiate WebSocket connection. Sends auth token on open. */
  connect() {
    if (this.ws)
      return;

    this.timer = undefined;
    this.ws = new WebSocket(this.url, this.protocols);

    this.ws.onopen = () => {
      this.ws!.send(JSON.stringify({ token: this.token }));
      this.reconnectInterval = this.initialReconnectInterval;
      this.onOpen();
    };

    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data);

      if (message['status_code'] && message['status_code'] == 200) {
        this.onConnected();
      }

      if (!this.authStatusReceived) {
        this.authStatusReceived = true;
        this.authStatus = message as AuthStatus;
        this.onAuth(this.authStatus);
        if (this.authStatus.error) {
          console.error("Authentication Error:", this.authStatus.error);
          this.onError(new Error(this.authStatus.error));
          this.disconnect('authentication-error');
        }
        return;
      }

      if (event.data[0] == '[') {
        this.onInstances(message as TempInstance[]);
      } else if (event.data[0] != '{') {
        return;
      }

      if (message['callId']) {
        this.onActionResponse(message as ActionResponse<unknown>);
        return;
      }

      if (message['runtimeVar']) {
        this.onRuntimeStateChange(message);
        return;
      }

      if (message['initialAnchors']) {
        if (message['initialAnchors'].length > 0) {
          this.onInitialAnchors(message['initialAnchors'] as number[]);
        } else {
          this.onEmpty();
        }
        return;
      }

      if (message['prependAnchor']) {
        this.onAnchorPrepend(message['prependAnchor'] as number);
        return;
      }

      if (message['source'] == 'system') {
        this.onSystem(message as SystemMessage);
        return;
      }

      if (message['source'] == 'maintenance') {
        this.persistentReconnect();
        return;
      }
    };

    this.ws.onclose = (event) => {
      if (this.reason && preventReconnectionReasons[this.reason]) {
        this.onClose(event);
        return;
      }
      
      this.persistentReconnect(event.wasClean);
      this.onClose(event);
    };
  }

  /** Send a string message through the WebSocket. Logs error if not connected. */
  send(data: string) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(data);
    } else {
      console.error("WebSocket is not open. Ready state:", this.ws?.readyState);
    }
  }

  /** Attempt to reconnect with exponential backoff. */
  persistentReconnect(wasClean: boolean = false) {
    this.ws = undefined;
    if (!this.authStatusReceived || !wasClean) {
      console.warn("WebSocket disconnected. Reconnecting in", this.reconnectInterval, "ms");
      this.timer = setTimeout(() => this.connect(), this.reconnectInterval);
      // Double the reconnect interval for the next potential reconnection, but cap it at the max value
      this.reconnectInterval = Math.min(this.reconnectInterval * 2, this.maxReconnectInterval);
    }
  }

  /**
   * Close the WebSocket connection.
   *
   * @param reason - If provided, prevents automatic reconnection
   */
  disconnect(reason?: keyof typeof preventReconnectionReasons) {
    if (this.timer && reason) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }

    this.reason = reason;
    this.ws?.close();
  }
}
