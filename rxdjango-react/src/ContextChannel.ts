import PersistentWebsocket from './PersistentWebsocket';
import StateBuilder from './StateBuilder';
import { NoConnectionListener, TempInstance, Listener, Model } from './ContextChannel.interfaces';

abstract class ContextChannel<T> {
  private ws: PersistentWebsocket | undefined;
  private builder: StateBuilder<T> | undefined;
  private listeners: Listener<T>[] = [];
  private noConnectionListeners: NoConnectionListener[] = [];
  private token: string;

  protected args: { [key: string]: number | string } = {};

  abstract endpoint: string;
  abstract anchor: string;
  abstract baseURL: string;
  abstract model: Model;
  abstract many: boolean;

  constructor(token: string) {
    this.token = token;
  }

  public init() {
    if (this.builder)
      return;

    this.builder = new StateBuilder<T>(this.model, this.anchor, this.many);
    const ws = new PersistentWebsocket(this.getEndpoint(), this.token);

    ws.onclose = this.onclose.bind(this);
    ws.onopen = this.onopen.bind(this);
    
    ws.oninstances = (instances) => {
      this.receiveInstances(instances);
    };

    this.ws = ws;
  }

  private receiveInstances(instances: TempInstance[]) {
    this.builder!.update(instances);
    this.notify();
  }

  private notify() {
    const state = this.builder!.state;
    for (const listener of this.listeners) {
      if (state) listener(state as T);
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
}


export default ContextChannel;
