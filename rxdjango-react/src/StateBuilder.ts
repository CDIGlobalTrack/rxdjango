import {
  InstanceType,
  Model,
  TempInstance,
  UnloadedInstance,
  InstanceReference,
} from './StateChannel.interfaces';


export default class StateBuilder<T> {
  public state: T | undefined;

  private model: Model;
  private anchor: string;
  private anchorId: number | undefined;
  private index: { [key: string]: InstanceType } = {};
  private refs: { [key:string]: InstanceReference[] } = {};

  constructor(model: Model, anchor: string) {
    this.model = model;
    this.anchor = anchor;
  }

  public update(instances: TempInstance[]) {
    for (const instance of instances) {
      this.receiveInstance(instance);
    }

    const key = `${this.anchor}:${this.anchorId}`;
    this.state = { ...this.index[key] } as T;
  }

  private receiveInstance(instance: TempInstance) {
    const _instance = this.buildInstance(instance);

    if (this.state === undefined) {
      if (instance._instance_type !== this.anchor) {
        throw new Error(`Expected _instance_type to be ${this.anchor}, not ${instance._instance_type}`);
      }

      this.anchorId = instance.id;
      this.state = _instance as T;
      const key = `${_instance._instance_type}:${_instance.id}`;
      this.index[key] = this.state as InstanceType;
      return;
    };

    if (instance._instance_type === this.anchor && instance.id === this.anchorId) {
      this.state = _instance as T;
    }
  }

  private buildInstance(instance: TempInstance): InstanceType {
    const _instance = instance as unknown as { [key: string]: any };
    const key = `${_instance._instance_type}:${_instance.id}`;
    let newInstance = (this.index[key] || _instance) as TempInstance;
    newInstance = {
      ...newInstance,
      _loaded: true,
    };
    this.index[key] = newInstance as InstanceType;
    const model = this.model[_instance._instance_type];

    for (const [property, value] of Object.entries(_instance)) {
      if (model[property]) {
        // This is a relation, replace ids with instances
        const instanceType = model[property];
        if (Array.isArray(_instance[property])) {
          const ids = _instance[property] as number[];
          newInstance[property] = ids.map((id, index) => this.getOrCreate(instanceType, id, `${property}:${index}`, key));
        } else {
          newInstance[property] = this.getOrCreate(instanceType, value, property, key);
        }
      } else {
        newInstance[property] = value;
      }
    }

    if (!this.refs[key]) {
      this.refs[key] = [];
    } else {
      this.changeRef(key, newInstance);
    }

    return newInstance as unknown as InstanceType;
  }

  private changeRef(key:string, newInstance: TempInstance) {
    const changes = {};
    for (const ref of this.refs[key]) {
      const instance = this.index[ref.instanceKey] as any;
      const property = ref.referenceKey;
      const [ prop, index ] = property.split(':');
      if (index) {
        instance[prop][parseInt(index)] = newInstance;
        const propKey = `${ref.instanceKey}|${prop}`;
        changes{[ref.instanceKey, prop]} = 1;
      } else {
        instance[property] = newInstance;
      }
    }

    // Trigger reference change in array properties
    for (const [instanceKey, prop] in changes) {
      const instance = this.index[ref.instanceKey];
      instance[prop] = [...instance[prop]];
    }
  }

  private getOrCreate(instanceType: string, id: number, referenceKey: string, instanceKey: string) {
    const pkey = `${instanceType}:${id}`;
    this.index[pkey] ||= {
      id, _instance_type: instanceType, _operation: 'create', _tstamp: 0, _loaded: false
    } as UnloadedInstance;
    this.refs[pkey] ||= [];
    this.refs[pkey].push({ referenceKey, instanceKey });
    return this.index[pkey];
  }

}
