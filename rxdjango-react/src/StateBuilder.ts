import { InstanceType, Model, TempInstance, UnloadedInstance } from './StateChannel.d';

export default class StateBuilder<T> {
  public state: T | undefined;

  private model: Model;
  private anchor: string;
  private anchorId: number | undefined;
  private index: { [key: string]: InstanceType } = {};

  constructor(model: Model, anchor: string) {
    this.model = model;
    this.anchor = anchor;
  }

  public update(instances: TempInstance[]) {
    for (const instance of instances) {
      this.receiveInstance(instance);
    }

    this.state = { ...this.state } as T;
  }

  private receiveInstance(instance: TempInstance) {
    if (instance._instance_type === this.anchor && instance.id === this.anchorId) {
      debugger;
    }
    
    const _instance = this.buildInstance(instance);

    if (this.state === undefined) {
      if (instance._instance_type !== this.anchor) {
        throw new Error(`Expected _instance_type to be ${this.anchor}, not ${instance._instance_type}`);
      }

      this.anchorId = instance.id;
      this.state = _instance as T;
      return;
    };

    if (instance._instance_type === this.anchor && instance.id === this.anchorId) {
      this.state = _instance as T;
    }
  }

  private buildInstance(instance: TempInstance): InstanceType {
    const _instance = instance as unknown as { [key: string]: any };
    const key = `${_instance._instance_type}:${_instance.id}`;
    const newInstance = (this.index[key] || _instance) as TempInstance;
    newInstance._loaded = true;
    const model = this.model[_instance._instance_type];
    this.index[key] = newInstance as InstanceType;
    for (const [property, value] of Object.entries(_instance)) {
      if (model[property]) {
        // This is a relation, replace ids with instances
        const instanceType = model[property];
        if (Array.isArray(_instance[property])) {
          const ids = _instance[property] as number[];
          newInstance[property] = ids.map(id => this.getOrCreate(instanceType, id));
        } else {
          newInstance[property] = this.getOrCreate(instanceType, value);
        }
      } else {
        newInstance[property] = value;
      }
    }

    return newInstance as unknown as InstanceType;
  }

  private getOrCreate(instanceType: string, id: number) {
    const pkey = `${instanceType}:${id}`;
    this.index[pkey] ||= {
      id, _instance_type: instanceType, _operation: 'create', _tstamp: 0, _loaded: false
    } as UnloadedInstance;
    return this.index[pkey];
  }

}
