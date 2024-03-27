import { HandlerListener } from './InstanceHandler.d';
import { InstanceType, Delta } from './StateChannel.d';

class InstanceHandler {
  private listeners: HandlerListener[];
  public property: string | undefined;
  public data: InstanceType | undefined;

  constructor(property?: string) {
    this.property = property;
    this.listeners = [];
  }

  setData(data: any) {
    if (this.data && !data._delta) {
      this.data = { ...this.data, ...data };
    } else if (this.data && data._delta) {
      this.patchDeltas(data);
    } else if (!data._delta) {
      this.data = data;
    } else {
      console.log('ERROR: got delta without state');
      console.log(this.data, data);
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

  private patchDeltas(data: Delta) {
    this.data!._tstamp = data._tstamp
    for (const [k, v] of data._delta) {
      (this.data! as any)[k] = v;
    }
  }
}

export default InstanceHandler;
