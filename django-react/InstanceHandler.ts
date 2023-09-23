import { HandlerListener } from './InstanceHandler.d';
import { InstanceType } from './StateChannel.d';

class InstanceHandler {
  private listeners: HandlerListener[];
  public property: string | undefined;
  public data: InstanceType | undefined;

  constructor(property?: string) {
    this.property = property;
    this.listeners = [];
  }

  setData(data: any) {
    if (this.data) {
      this.data = { ...this.data, ...data };
    } else {
      this.data = data;
    }

    this.notify();
  }

  subscribe(listener: HandlerListener) {
    this.listeners.push(listener)
  }

  notify() {
    for (const listener of this.listeners) {
      listener(this.data);
    }
  }
}

export default InstanceHandler;
