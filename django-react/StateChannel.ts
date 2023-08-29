type Listener<T> = (state: T) => void;

class ExpectedInstance {
  data: object | null = null;

  available: Promise<object | null>;
  private _resolve!: (value: object) => void;

  constructor() {
    this.available = new Promise((resolve, reject) => {
      this._resolve = resolve;
    });
  }

  setData(data: object) {
    if (this.data) {
      Object.assign(this.data, data);
    } else {
      this.data = data;
      this._resolve(this.data);
    }
  }
}

type InstanceIndex = {
  [key: string]: ExpectedInstance
};

type ModelEntry = {
  [key: string]: string;
};

type Model = {
  [key: string]: ModelEntry;
}

abstract class StateChannel<T> {
  protected state: T | null;
  private listeners: Listener<T>[] = [];
  private ws: WebSocket;

  private instances: InstanceIndex = {};

  abstract endpoint: string;
  abstract baseURL: string;
  abstract many: boolean;
  abstract model: Model;

  constructor(baseURL: string) {
    this.ws = new WebSocket(this.getEndpoint());
    this.state = null;

    this.ws.onmessage = (event: MessageEvent) => {
      this.handleMessage(event);
    };

    this.ws.onerror = (errorEvent) => {
      console.log('ERROR! Could not connect to websocket');
    };
  }

  private handleMessage(event: MessageEvent) {
    const data = JSON.parse(event.data);
    data.forEach(this.receiveInstance);
    this.notify();
  }

  private receiveInstance(instance: object): void {
    const key = `${instance._instance_type}:${instance.id}`;
    if (!this.instances[key]) {
      // This is an anchor
      const slot = this.makeInstance(key);
      if (this.state === null) {
        this.state = this.many ? [slot.data] : slot.data;
      } else if (this.many) {
        this.state.push(slot.data);
      } else {
        // This should never happen.
        console.log('Error! Received two anchors')
        return;
      }
      this.indexRelated(instance);
    }

    this.instances[key].setData(instance);
  }

  private indexRelated(instance) {
    const model = this.model[instance._instance_type];
    for (const [property, related_type] of Object.entries(model)) {
      if (Array.isArray(instance[property])) {
        // This is a list
        const property_index = `_${property}_index`;
        const property_ids = `_${property}_ids`;

        instance[property_index] = {};
        instance[property_ids] = instance[property];
        instance[property] = instance[property_ids].map(() => undefined);

        for (const [index, related_id] of instance[property_ids].entries()) {
          const key = `${related_type}:${related_id}`;
          this.makeInstance(key).available.then((related_instance) => {
            instance[property][index] = related_instance.data;
            instance[property_index][related_id] = related_instance.data;
          });
          instance[property][index] = undefined;
        }
      } else {
        // This is a foreign key
        property_id = `_${property}_id`;

        const related_id = instance[property];
        const key = `${related_type}:${related_id}`;
        this.makeInstance(key).available.then((related_instance) => {
          instance[property] = related_instance.data;
        });
        instance[property_id] = related_id;
        instance[property] = undefined;
      }
    }
  }

  private makeInstance(key: string) {
    if (this.instances[key]) {
      return this.instances[key];
    }
    const instance = new ExpectedInstance();
    this.instances[key] = instance;
    return instance;
  }

  private notify() {
    for (let listener of this.listeners) {
      listener(this.state);
    }
  }

  subscribe(listener: Listener<T>): () => void {
    this.listeners.push(listener);
    return () => {
      const index = this.listeners.indexOf(listener);
      if (index !== -1) {
        this.listeners.splice(index, 1);
      }
    };
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
        if (this.hasOwnProperty(propertyName)) {
          constructedEndpoint = constructedEndpoint.replace(match, this[propertyName]);
        } else {
          console.error(`Property ${propertyName} not found in the instance.`);
        }
      });
    }

    return `${this.baseURL}${constructedEndpoint}`;
  }
}

export default StateChannel;
