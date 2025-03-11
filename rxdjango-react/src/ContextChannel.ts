import PersistentWebsocket from './PersistentWebsocket';
import StateBuilder from './StateBuilder';
import { NoConnectionListener, TempInstance, Listener, Model, InstanceListener, InstanceType } from './ContextChannel.interfaces';
import { Action, ActionResponse, ActionIndex, CallPromise } from './actions.d';

abstract class ContextChannel<T, Y=unknown> {
  private ws: PersistentWebsocket | undefined;
  private builder: StateBuilder<T> | undefined;
  private listeners: Listener<T>[] = [];
  private instance_listeners: { [key: string]: InstanceListener } = {};
  private runtimeListeners: Listener<Y>[] = [];
  private noConnectionListeners: NoConnectionListener[] = [];
  private activeCalls: ActionIndex = {};
  private token: string;

  protected args: { [key: string]: number | string } = {};

  abstract endpoint: string;
  abstract anchor: string;
  abstract baseURL: string;
  abstract model: Model;
  abstract many: boolean;
  abstract runtimeState: Y | undefined | null;

  public connected: boolean = false;
  public onConnected: () => void = () => {};
  public onEmpty: () => void = () => {};

  constructor(token: string) {
    this.token = token;
  }

  public init() {
    if (this.builder)
      return;

    this.builder = new StateBuilder<T>(this.model, this.anchor, this.many);
    const ws = new PersistentWebsocket(this.getEndpoint(), this.token);

    ws.onClose = this.onclose.bind(this);
    ws.onOpen = this.onopen.bind(this);

    ws.onInstances = (instances) => {
      this.receiveInstances(instances);
    };

    ws.onActionResponse = (response) => {
      this.receiveActionResponse(response);
    };

    ws.onRuntimeStateChange = (message) => {
      const msg = message as { runtimeVar: keyof Y; value: unknown };
      const runtimeVar = msg.runtimeVar;
      const value = msg.value;
      this.receiveRuntimeState(runtimeVar, value);
    };

    ws.onAnchorPrepend = (anchorId) => {
      this.prependAnchor(anchorId);
    }

    ws.onInitialAnchors = (anchorIds) => {
      this.builder!.setAnchors(anchorIds);
      this.notify();
    }

    ws.onConnected = this.onConnected
    ws.onEmpty = this.onEmpty

    this.ws = ws;
  }

  private receiveInstances(instances: TempInstance[]) {
    this.builder!.update(instances);

    for (const instance of instances) {
      this.notifyInstance(instance);
    }

    this.notify();
  }

  private receiveRuntimeState(runtimeVar: keyof Y, value: unknown) {
    this.runtimeState = { ...this.runtimeState, [runtimeVar]: value } as Y;
    this.notifyRuntimeState();
  }

  private prependAnchor(anchorId: number) {
      this.builder!.prependAnchorId(anchorId);
      this.notify()
  }

  private notifyInstance(instance: InstanceType) {
    const key = `${instance._instance_type}:${instance.id}`;
    const listener = this.instance_listeners[key];
    if (listener) {
      listener(instance);
    }
  }

  private notify() {
    const state = this.builder!.state;
    for (const listener of this.listeners) {
      if (state) listener(state as T);
    }
  }

  private notifyRuntimeState() {
    for (const listener of this.runtimeListeners) {
      if (this.runtimeState) listener(this.runtimeState);
    }
  }

  public subscribe(listener: Listener<T>, noConnectionListener?: NoConnectionListener): () => void {
    if (!this.ws)
      this.init();

    this.listeners.push(listener);

    if (noConnectionListener) {
      this.noConnectionListeners.push(noConnectionListener);
    }

    if (this.listeners.length === 1)
      this.ws!.connect();

    const unsubscribe = () => {
      const index = this.listeners.indexOf(listener);
      if (index !== -1) {
        this.listeners.splice(index, 1);
        if (this.listeners.length === 0)
          this.ws!.disconnect();
      }
    };

    return unsubscribe;
  }

  public subscribeInstance(listener: InstanceListener, instance_id: number, instance_type: string) {
    const key = `${instance_type}:${instance_id}`;
    this.instance_listeners[key] = listener;
    const unsubscribe = () => {
      delete this.instance_listeners[key];
    };

    if (!this.builder) return unsubscribe;

    try {
      const instance = this.builder!.getInstance(key);
      this.notifyInstance(instance);
    } catch (e) {
      return unsubscribe;
    }

    return unsubscribe;
  }

  public subscribeRuntimeState(listener: Listener<Y>): () => void {
    this.runtimeListeners.push(listener);

    const unsubscribe = () => {
      const index = this.runtimeListeners.indexOf(listener);
      if (index !== -1) {
        this.runtimeListeners.splice(index, 1);
      }
    };
    return unsubscribe;
  }

  protected async callAction<T>(action: string, params: any[]): Promise<T> {
    const callId = this.generateUniqueId();
    const cmd: Action = { callId, action, params };
    const activeCalls = this.activeCalls;
    return new Promise((resolve, reject) => {
      activeCalls[callId] = {resolve, reject} as CallPromise<T>;
      this.ws?.send(JSON.stringify(cmd));
    });
  }

  private receiveActionResponse<T>(response: ActionResponse<T>) {
    const promise = this.activeCalls[response.callId];
    if (!promise) {
        console.error(`Received a response for unmatched callId: ${response.callId}`);
        return;
    }
    if (!response.error) {
      promise.resolve(response.result as T);
    } else {
      promise.reject(response.error);
    }
    delete this.activeCalls[response.callId];
  }

  private getEndpoint(): string {
    let constructedEndpoint = this.endpoint;

    // Use a regex to find all the placeholders
    const matches = this.endpoint.match(/{\w+}/g);

    if (matches) {
      matches.forEach((match) => {
        // Extract the property name from the placeholder
        const propertyName = match.replace(/[{}]/g, "");
        const propertyValue = this.args[propertyName];

        // Substitute the placeholder with the property value, if it exists
        constructedEndpoint = constructedEndpoint.replace(match, propertyValue.toString());
      });
    }

    return `${this.baseURL}${constructedEndpoint}`;
  }

  private onopen() {
    for (const listener of this.noConnectionListeners) {
      listener(undefined);
    }
  }

  private onclose() {
    const now = new Date();
    for (const listener of this.noConnectionListeners) {
      listener(now);
    }
  }

  private idCounter: number = 0;

  private generateUniqueId(): number {
    const now = new Date().getTime();
    this.idCounter = (this.idCounter + 1) % 1000000;
    return now * 1000000 + this.idCounter;
  }
}


export default ContextChannel;
