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
  private rootType: string | undefined;

  // The instance id of the anchor for this state
  private anchorIds: number[] = [];

  // An index with references for all nodes in the state tree
  // Key is _instance_type:id properties of instance
  private index: { [key: string]: InstanceType } = {};

  // A track of all references to each instance, so that we can
  // recursively change references of objects that need to be triggered
  // in react
  private refs: { [key:string]: InstanceReference[] } = {};

  // A track of all anchors in context, to avoid duplication
  private anchorIndex: { [key:number]: boolean } = {};

  private many: boolean;

  constructor(model: Model, anchor: string, many: boolean) {
    this.model = model;
    this.anchor = anchor;
    this.many = many;
    this.rootType = undefined;
  }

  public prependAnchorId(anchorId: number) {
      this.anchorIds.unshift(anchorId);
      this.anchorIndex[anchorId] = true;
  }

  // Returns the current state. Every call returns a different reference.
  public get state(): T | T[] | undefined {
    if (this.anchorIds === undefined)
      return undefined;

    const stateKeys = this.anchorIds.map((anchorId: number) => {
      return `${this.anchor}:${anchorId}`;
    });
    if (!this.many) {
      return { ...this.index[stateKeys[0]] } as T;
    }
    return stateKeys.map((stateKey: string): T => {
      return { ...this.index[stateKey] } as T;
    });
  }

  // Receives an update from the server, with several instances
  public update(instances: TempInstance[] | number[]) {
    if (typeof instances[0] === 'object') {
      for (const instance of instances) {
        this.receiveInstance(instance as TempInstance);
      }
    } else {
      throw new Error('Expected array of instances');
    }
  }

  public getInstance(key: string) {
    if (!this.index[key]) {
      throw new Error(`Instance ${key} not found`);
    }

    return this.index[key];
  }

  private setAnchor(instance: TempInstance) {
    if (instance._instance_type !== this.anchor) {
      throw new Error(`Expected _instance_type to be ${this.anchor}, not ${instance._instance_type}`);
    }

    this.anchorIds = [instance.id];
  }

  public setAnchors(instanceIds: number[]) {
    this.anchorIds = instanceIds;
    this.anchorIds.forEach((anchorId) => {
      this.anchorIndex[anchorId] = true;
      const key = `${this.anchor}:${anchorId}`;
      this.index[key] ||= {
        id: anchorId,
        _instance_type: this.anchor,
        _operation: 'initial_state'
        , _tstamp: 0,
        _loaded: false,
      } as UnloadedInstance;
    });
  }

  // Handles data for one instance as received from the backend
  private receiveInstance(instance: TempInstance) {
    // The first thing backend sends must be the anchor
    if (this.many && instance._instance_type && this.rootType === undefined) {
      this.rootType = instance._instance_type;
    }
    else if (this.anchorIds.length === 0 && !this.many) {
      this.setAnchor(instance);
    }
    // Increase or decrease anchors if this is an anchor
    if (
      this.many &&
      instance._instance_type === this.rootType &&
      instance._operation === 'initial_state' &&
      !this.anchorIndex[instance.id]
    ) {
      this.anchorIds?.push(instance.id);
      this.anchorIndex[instance.id] = true;
    } else if (
      this.many &&
      instance._instance_type === this.rootType &&
      instance._operation === 'delete'
    ) {
      this.anchorIds = this.anchorIds?.filter(id => id !== instance.id);
      delete this.anchorIndex[instance.id];
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
