import {
  AuthStatus,
  TempInstance,
  SystemMessage,
} from './ContextChannel.interfaces';


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

  public authStatus: AuthStatus | undefined;

  public onopen: () => void = () => {};
  public onclose: (event: CloseEvent) => void = () => {};
  public onauth: (authStatus: AuthStatus) => void = () => {};
  public oninstances: (instances: TempInstance[]) => void = () => {};
  public onsystem: (message: SystemMessage) => void = () => {};

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

  connect() {
    if (this.ws)
      return;

    this.timer = undefined;
    this.ws = new WebSocket(this.url, this.protocols);

    this.ws.onopen = () => {
      this.ws!.send(JSON.stringify({ token: this.token }));
      this.reconnectInterval = this.initialReconnectInterval;
      this.onopen();
    };

    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data);

      if (!this.authStatusReceived) {
        this.authStatusReceived = true;
        this.authStatus = message as AuthStatus;
        this.onauth(this.authStatus);
        if (this.authStatus.error) {
          console.error("Authentication Error:", this.authStatus.error);
          this.ws!.close();
        }
        return;
      }

      if (event.data[0] == '[') {
        this.oninstances(message as TempInstance[]);
      } else if (event.data[0] != '{') {
        return;
      }

      if (message['source'] == 'system') {
        this.onsystem(message as SystemMessage);
      }

      if (message['source'] == 'maintenance') {
        this.disconnect();
        this.persistentReconnect();
      }

    };

    this.ws.onclose = (event) => {
      this.persistentReconnect(event.wasClean);
      this.onclose(event);
    };
  }

  send(data: string) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(data);
    } else {
      console.error("WebSocket is not open. Ready state:", this.ws?.readyState);
    }
  }

  persistentReconnect(wasClean: boolean = false) {
    this.ws = undefined;
    if (!this.authStatusReceived || !wasClean) {
      console.warn("WebSocket disconnected. Reconnecting in", this.reconnectInterval, "ms");
      this.timer = setTimeout(() => this.connect(), this.reconnectInterval);
      // Double the reconnect interval for the next potential reconnection, but cap it at the max value
      this.reconnectInterval = Math.min(this.reconnectInterval * 2, this.maxReconnectInterval);
    }
  }

  disconnect() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = undefined;
    } else {
      this.ws?.close();
    }
  }
}
