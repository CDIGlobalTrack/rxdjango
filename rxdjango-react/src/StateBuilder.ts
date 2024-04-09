import InstanceHandler from './InstanceHandler';
import { HandlerIndex } from './InstanceHandler.d';
import { InstanceType, Model, ModelEntry } from './StateChannel.d';

export default class StateBuilder<T> {
  public state: T | undefined;

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

  private receiveInstance(instance: InstanceType) {
    if (this.state === undefined) {
      if (instance._instance_type !== this.anchor) {
        console.log(instance);
        throw new Error(`Expected _instance_type to be ${this.anchor}, not ${instance._instance_type}`);
      }
      this.state = this.convert(instance) as T;
    }
  }

  private convert(instance: InstanceType): InstanceType {
    const _instance = instance as unknown as { [key: string]: any };
    const newInstance = { ..._instance };
    const model = this.model[_instance._instance_type];

    for (const [property, instanceType] of Object.entries(model)) {
      if (Array.isArray(_instance[property])) {
        const ids = _instance[property] as number[];
        newInstance[property] = new Array(ids.length).fill(null);
      }
    }
    return newInstance as unknown as InstanceType;
  }

}
