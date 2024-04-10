import { InstanceType, Model, TempInstance, UnloadedInstance } from './StateChannel.d';



export default class StateBuilder<T> {
  public state: T | undefined;

  private model: Model;
  private anchor: string;
  private index: { [key: string]: InstanceType } = {};

  constructor(model: Model, anchor: string) {
    this.model = model;
    this.anchor = anchor;
  }

  public update(instances: TempInstance[]) {
    for (const instance of instances) {
      this.receiveInstance(instance);
    }
  }

  private receiveInstance(instance: TempInstance) {
    const _instance = this.buildInstance(instance);
    
    if (this.state === undefined) {
      if (instance._instance_type !== this.anchor) {
        throw new Error(`Expected _instance_type to be ${this.anchor}, not ${instance._instance_type}`);
      }

      this.state = _instance as T;
      return;
    };

    //Object.assign(_instance, instance);
  }

  private getUnloadedInstance(id: number, instanceType: string) {
    return { id, _instance_type: instanceType, _operation: 'create', _tstamp: 0, _loaded: false } as UnloadedInstance;
  }

  private buildInstance(instance: TempInstance): InstanceType {
    const _instance = instance as unknown as { [key: string]: any };
    const key = `${_instance._instance_type}:${_instance.id}`;
    const newInstance = (this.index[key] || _instance) as TempInstance;
    const model = this.model[_instance._instance_type];
    this.index[key] = newInstance as InstanceType;
    for (const [property, value] of Object.entries(_instance)) {
      const _property = property as keyof InstanceType;
      if (model[property]) {
        const instanceType = model[property];
        if (Array.isArray(_instance[property])) {
          const ids = _instance[property] as number[];
          newInstance[property] = ids.map((id) => {
            const pkey = `${instanceType}:${id}`;
            return this.index[pkey] || this.getUnloadedInstance(id, instanceType);
          });
        } else {
          const pkey = `${instanceType}:${value}`;
          newInstance[property] = this.index[pkey] || this.getUnloadedInstance(value, instanceType);
        } 
      } else {
        newInstance[property] = value;
      }
    }
    
    return newInstance as unknown as InstanceType;
  }

}
