import InstanceHandler from './InstanceHandler';
import PersistentWebsocket from './PersistentWebsocket';
import { HandlerIndex } from './InstanceHandler.d';
import { InstanceType, Listener, Model, ModelEntry } from './StateChannel.d';

abstract class StateChannel<T> {
  protected state: T | undefined = undefined;

  private ws: PersistentWebsocket | undefined = undefined;
  private listeners: Listener<T>[] = [];
  private token: string;

  private handlers: HandlerIndex = {};
  private anchorHandler: InstanceHandler | undefined = undefined;

  abstract endpoint: string;
  abstract anchor: string;
  abstract baseURL: string;
  abstract model: Model;

  constructor(token: string) {
    this.token = token;
  }

  private handleMessage(instances: InstanceType[]) {
    for (const instance of instances) {
      this.receiveInstance(instance);
    }
  }

  private async receiveInstance(instance: InstanceType) {
    const key = `${instance._instance_type}:${instance.id}`;
    console.log('RECEIVEINSTANCE')

    if (instance._instance_type === this.anchor) {
      // This is an anchor
      this.receiveAnchorInstance(key, instance);
      return;
    }

    if (this.handlers[key]) {
      // This is an nested instance
      this.receiveNestedInstance(key, instance);
      return;
    }
  }

  private receiveAnchorInstance(key: string, instance: InstanceType) {
    if (this.handlers[key]) {
      // anchor already loadded preveously
      this.handlers[key].setData(instance);
      this.rebuildState();
    } else {
      this.anchorHandler = this.makeInstanceHandler(key);
      const model = this.model[instance._instance_type];
      this.makeNestedIntanceHandlers(model, instance);

      this.anchorHandler?.subscribe(() => this.rebuildState());
      this.anchorHandler?.setData(instance);
    }
  }

  private receiveNestedInstance(key: string, instance: InstanceType) {
    const handler = this.handlers[key];
    handler.setData(instance);

    const model = this.model[instance._instance_type];
    this.makeNestedIntanceHandlers(model, instance);
  }

  private makeNestedIntanceHandlers(model: ModelEntry, instance: InstanceType) {
    for (const [property, instanceType] of Object.entries(model)) {
      if (Array.isArray(instance[property])) {
        // this is a list
        const ids = instance[property];

        for (const id of ids) {
          const key = `${instanceType}:${id}`;

          if (!this.handlers[key]) {
            const handler = this.makeInstanceHandler(key, property);
            this.handlers[key] = handler;
          }

          this.handlers[key].subscribe(() => this.rebuildState());
        }
      } else {
        // this is a foreing key
        const key = `${instanceType}:${instance[property]}`;

        if (!this.handlers[key]) {
          const handler = this.makeInstanceHandler(key, property);
          this.handlers[key] = handler;
        }

        this.handlers[key].subscribe(() => this.rebuildState());
      }
    }
  }

  private makeInstanceHandler(key: string, property?: string) {
    const handler = new InstanceHandler(property);
    this.handlers[key] = handler;
    return handler;
  }

  private getCascadeInstance(model: ModelEntry, instance: InstanceType) {
    const state = { ...instance };

    for (const [property, instanceType] of Object.entries(model)) {
      if (Array.isArray(instance[property])) {
        // this is a list
        state[property] = instance[property].map((id: number) => {
          const key = `${instanceType}:${id}`;
          const data = this.handlers[key]?.data;

          if (!data) {
            // it means that instance still not loadded
            return undefined;
          }

          const cascadeModel = this.model[instanceType];
          return this.getCascadeInstance(cascadeModel, data);
        });
      } else {
        // this is a foreing key
        const key = `${instanceType}:${state[property]}`;
        const data = this.handlers[key]?.data;

        if (!data) {
          // it means that instance still not loadded
          state[property] = undefined;
          continue;
        }

        const cascadeModel = this.model[instanceType];
        state[property] = this.getCascadeInstance(cascadeModel, data) as T;
      }
    }

    return state;
  }

  private notify() {
    for (const listener of this.listeners) {
      if (this.state) listener(this.state);
    }
  }

  private getEndpoint(): string {
    let constructedEndpoint = this.endpoint;

    // Use a regex to find all the placeholders
    const matches = this.endpoint.match(/{\w+}/g);

    if (matches) {
      matches.forEach((match) => {
        // Extract the property name from the placeholder
        const propertyName = match.replace(/[{}]/g, "");

        // this.hasOwnProperty this check was removed cause, hasOwnProperty
        // function not exist

        // Substitute the placeholder with the property value, if it exists
        constructedEndpoint = constructedEndpoint.replace(match, this[propertyName]);
      });
    }

    return `${this.baseURL}${constructedEndpoint}`;
  }

  private rebuildState() {
    const data = this.anchorHandler!.data as InstanceType;
    this.state = this.getCascadeInstance(this.model[this.anchor], data) as T;
    this.notify();
  }

  public init() {
    if (this.ws)
      return;

    const ws = new PersistentWebsocket(this.getEndpoint(), this.token);

    ws.onmessage = (instances) => {
      this.handleMessage(instances);
    };

    this.ws = ws;
  }

  public subscribe(listener: Listener<T>): () => void {
    if (!this.ws) throw 'ERROR! Websocket not initialized.';

    this.listeners.push(listener);

    if (this.listeners.length === 1)
      this.ws.connect();

    return () => {
      const index = this.listeners.indexOf(listener);
      if (index !== -1) {
        this.listeners.splice(index, 1);
        if (this.listeners.length === 0)
          this.ws!.disconnect();
      }
    };
  }


  public getWebSocket() {
    return this.ws;
  }
}

export default StateChannel;
