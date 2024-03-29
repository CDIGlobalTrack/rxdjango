import PersistentWebsocket from './PersistentWebsocket';
import StateBuilder from './StateBuilder';
import { InstanceType, Listener, Model } from './StateChannel.d';

abstract class StateChannel<T> {
  private ws: PersistentWebsocket | undefined;
  private builder: StateBuilder<T> | undefined;
  private listeners: Listener<T>[] = [];
  private token: string;

  protected args: { [key: string]: number | string } = {};

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

    ws.oninstances = (instances) => {
      this.receiveInstances(instances);
    };

    this.ws = ws;
  }

  private receiveInstances(instances: InstanceType[]) {
    this.builder!.update(instances);
    this.notify();
  }

  private notify() {
    for (const listener of this.listeners) {
      if (this.builder!.state) listener(this.builder!.state);
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

}

export default StateChannel;
