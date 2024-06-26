import {
  InstanceType,
  Model,
  TempInstance,
  UnloadedInstance,
  InstanceReference,
} from './ContextChannel.interfaces';


export default class StateBuilder<T> {
  /***
   * StateBuilder simulates the behavior of a reducer.
   * On every instance update, all references to that instance are changed,
   * and all references to those instances will recusiverly have their
   * references changed too.
   * This is expected to do the exact same thing as a reducer would do,
   * dispatching { ...state, updated_variable } for each update.
   */

  // The model of the channel with this state
  private model: Model;

  // Anchor is the _instance_type property of the root model
  private anchor: string;

  // The instance id of the anchor for this state
  private anchorId: number | undefined;

  // An index with references for all nodes in the state tree
  // Key is _instance_type:id properties of instance
  private index: { [key: string]: InstanceType } = {};

  // A track of all references to each instance, so that we can
  // recursively change references of objects that need to be triggered
  // in react
  private refs: { [key:string]: InstanceReference[] } = {};

  constructor(model: Model, anchor: string) {
    this.model = model;
    this.anchor = anchor;
  }

  // Returns the current state. Every call returns a different reference.
  public get state(): T | undefined {
    if (this.anchorId === undefined)
      return undefined;

    const stateKey = `${this.anchor}:${this.anchorId}`;
    return { ...this.index[stateKey] } as T;
  }

  // Receives an update from the server, with several instances
  public update(instances: TempInstance[]) {
    for (const instance of instances) {
      this.receiveInstance(instance);
    }
  }

  private setAnchor(instance: TempInstance) {
    if (instance._instance_type !== this.anchor) {
      throw new Error(`Expected _instance_type to be ${this.anchor}, not ${instance._instance_type}`);
    }

    this.anchorId = instance.id;
  }

  // Handles data for one instance as received from the backend
  private receiveInstance(instance: TempInstance) {
    // The first thing backend sends must be the anchor
    if (this.anchorId === undefined) {
      this.setAnchor(instance);
    }

    const _instance = instance as unknown as { [key: string]: any };
    // The key in the index for this instance
    const key = `${_instance._instance_type}:${_instance.id}`;

    let newInstance = (this.index[key] || _instance) as TempInstance;
    // Change the reference to trigger react
    newInstance = {
      ...newInstance,
      _loaded: true,
    };
    this.index[key] = newInstance as InstanceType;

    // Now change all object ids to objects from index
    const model = this.model[_instance._instance_type];

    for (const [property, value] of Object.entries(_instance)) {
      if (model && model[property]) {
        // This is a relation, replace ids with instances
        const instanceType = model[property];
        if (Array.isArray(_instance[property])) {
          const ids = _instance[property] as number[];
          newInstance[property] = ids.map((id, index) => this.getOrCreate(instanceType, id, key, property));
        } else {
          newInstance[property] = this.getOrCreate(instanceType, value, key, property);
        }
      } else {
        newInstance[property] = value;
      }
    }

    if (!this.refs[key]) {
      this.refs[key] = [];
    } else {
      this.changeRef(key);
    }

    return newInstance as unknown as InstanceType;
  }

  // Recursively propagate a reference change to all instances affected by an updated
  // Key is the instance key being changed
  // Track keeps track of passed instances to avoid infinite recursion
  private changeRef(key: string, track: {[key: string]: true}|null=null) {
    if (track === null) {
      track = {key: true};
    } else if (track[key]) {
      // avoid infinite recursion
      return;
    }
    for (const ref of this.refs[key]) {
      const instance = this.index[ref.referrerKey] as any;
      const property = ref.property;
      if (!Array.isArray(instance[property])) {
        instance[property] = this.index[key];
      } else {
        const related = instance[property] as unknown as InstanceType[];
        instance[property] = related.map(
          rel => this.index[`${rel._instance_type}:${rel.id}`]
        );
      }
      this.index[ref.referrerKey] = {...instance};
      this.changeRef(ref.referrerKey, track);
    }
  }

  // Gets an instance from index, or create an unloaded one if non-existing
  // referrerKey and property tracks which instance is linking to this one,
  // so we track all references.
  private getOrCreate(instanceType: string, id: number, referrerKey: string, property: string) {
    const pkey = `${instanceType}:${id}`;
    this.index[pkey] ||= {
      id, _instance_type: instanceType, _operation: 'create', _tstamp: 0, _loaded: false
    } as UnloadedInstance;
    this.refs[pkey] ||= [];
    this.refs[pkey].push({ property, referrerKey });
    return this.index[pkey];
  }

}
