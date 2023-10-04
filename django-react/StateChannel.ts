import PersistentWebsocket from './PersistentWebsocket';
import StateBuilder from './StateBuilder';
import { InstanceType, Listener, Model } from './StateChannel.d';
import { Action, ActionResponse, ActionIndex } from './actions.d';


abstract class StateChannel<T> {
  private ws: PersistentWebsocket | undefined = undefined;
  private builder: StateBuilder;
  private listeners: Listener<T>[] = [];
  private activeCalls: ActionIndex = {};
  private token: string;

  abstract endpoint: string;
  abstract anchor: string;
  abstract baseURL: string;
  abstract model: Model;

  constructor(token: string) {
    this.token = token;
  }

  public init() {
    if (this.builder)
      return;

    this.builder = new StateBuilder<T>(this.model, this.anchor);

    const ws = new PersistentWebsocket(this.getEndpoint(), this.token);

    ws.onInstances = (instances) => {
      this.receiveInstances(instances);
    };

    ws.onActionResponse = (response) => {
      this.receiveActionResponse(response);
    }

    this.ws = ws;
  }

  private receiveInstances(instances: InstanceType[]) {
    this.builder.update(instances);
    this.notify();
  }

  private notify() {
    for (const listener of this.listeners) {
      if (this.builder.state) listener(this.builder.state);
    }
  }

  public subscribe(listener: Listener<T>): () => void {
    if (!this.ws)
      this.init();

    this.listeners.push(listener);

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

  protected async callAction<T>(action: string, params: any[]): Promise<T> {
    const callId = this.generateUniqueId();
    const cmd: Action = { callId, action, params };
    const activeCalls = this.activeCalls;
    return new Promise((resolve, reject) => {
      activeCalls[callId] = {resolve, reject} as CallPromise<T>;
      this.ws.send(JSON.stringify(cmd));
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

        // Substitute the placeholder with the property value, if it exists
        constructedEndpoint = constructedEndpoint.replace(match, this[propertyName]);
      });
    }

    return `${this.baseURL}${constructedEndpoint}`;
  }

  private idCounter: number = 0;

  private generateUniqueId(): number {
    const now = new Date().getTime();
    this.idCounter = (this.idCounter + 1) % 1000000;
    return now * 1000000 + this.idCounter;
  }

}

export default StateChannel;
