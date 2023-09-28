import InstanceHandler from './InstanceHandler';
import { HandlerIndex } from './InstanceHandler.d';
import { InstanceType, Model, ModelEntry } from './StateChannel.d';

export default class StateBuilder<T> {
  public state: T | undefined = {};

  private model: Model;
  private anchor: string;
  private handlers: HandlerIndex = {};
  private anchorHandler: InstanceHandler | undefined = undefined;

  constructor(model: Model, anchor: string) {
    this.model = model;
    this.anchor = anchor;
  }

  public update(instances: InstanceType[]) {
    for (const instance of instances) {
      this.receiveInstance(instance);
    }
  }

  private makeInstanceHandler(key: string, property?: string) {
    const handler = new InstanceHandler(property);
    this.handlers[key] = handler;
    return handler;
  }

  private receiveInstance(instance: InstanceType) {
    const key = `${instance._instance_type}:${instance.id}`;

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

  private rebuildState() {
    const data = this.anchorHandler!.data as InstanceType;
    this.state = this.getCascadeInstance(this.model[this.anchor], data) as T;
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

}
