import {
  InstanceType,
  Model,
  TempInstance,
  UnloadedInstance,
  InstanceReference,
  Writable,
  WriteCallbacks,
} from './ContextChannel.interfaces';

interface ParentInfo {
  parentType: string;
  parentId: number;
  relationName: string;
}


/**
 * Reconstructs nested state from flat server instances.
 *
 * The backend sends instances in a flat format with `_instance_type` fields.
 * StateBuilder maintains an instance registry and rebuilds the nested
 * structure expected by React components. It simulates reducer behavior:
 * on every instance update, all references to that instance are changed,
 * and parent references are recursively updated to trigger React re-renders.
 *
 * @template T - The type of the root/anchor instance
 *
 * @example
 * ```typescript
 * const builder = new StateBuilder<Project>(model, 'myapp.serializers.ProjectSerializer', false);
 *
 * // Handle incoming instances from server
 * builder.update([
 *   { id: 1, _instance_type: 'myapp.serializers.ProjectSerializer', name: 'Project 1', ... },
 *   { id: 1, _instance_type: 'myapp.serializers.TaskSerializer', title: 'Task 1', ... }
 * ]);
 *
 * // Get rebuilt nested state
 * const state = builder.state;
 * // { id: 1, name: 'Project 1', tasks: [{ id: 1, title: 'Task 1' }] }
 * ```
 */
export default class StateBuilder<T> {

  /** The model definition mapping instance types to their relational fields. */
  private model: Model;

  /** The `_instance_type` string of the root/anchor serializer. */
  private anchor: string;
  private rootType: string | undefined;

  /** Ordered list of anchor instance IDs. */
  private anchorIds: number[] = [];

  /** Index of all instances by `_instance_type:id` key. */
  private index: { [key: string]: InstanceType } = {};

  /** Tracks all references to each instance for recursive change propagation. */
  private refs: { [key:string]: InstanceReference[] } = {};

  /** Tracks which anchor IDs are loaded to avoid duplication. */
  private anchorIndex: { [key:number]: boolean } = {};

  /** Whether this channel uses many=True (list of anchors). */
  private many: boolean;

  /** Writable declaration mapping instance types to allowed operations. */
  private writable: Writable;

  /** Write callbacks for attaching save/create/delete methods. */
  private writeCallbacks: WriteCallbacks | null;

  /**
   * @param model - The model definition from the generated channel
   * @param anchor - The `_instance_type` of the root serializer
   * @param many - Whether the channel state is a list of anchors
   * @param writable - Optional writable declaration
   * @param writeCallbacks - Optional write callbacks for attaching methods
   */
  constructor(model: Model, anchor: string, many: boolean, writable?: Writable, writeCallbacks?: WriteCallbacks) {
    this.model = model;
    this.anchor = anchor;
    this.many = many;
    this.writable = writable || {};
    this.writeCallbacks = writeCallbacks || null;
    this.rootType = undefined;
  }

  /** Prepend an anchor ID to the front of the list (for newly added instances). */
  public prependAnchorId(anchorId: number) {
      this.anchorIds.unshift(anchorId);
      this.anchorIndex[anchorId] = true;
  }

  /**
   * Returns the current rebuilt nested state.
   * Each call returns a new reference to trigger React re-renders.
   *
   * @returns The nested state object (single or array), or undefined if no anchors loaded.
   */
  public get state(): T | T[] | undefined {
    if (!this.many && !this.anchorIds.length)
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

  /**
   * Process a batch of instance updates from the server.
   *
   * @param instances - Array of flat instances with `_instance_type` and `_operation` fields
   */
  public update(instances: TempInstance[] | number[]) {
    if (typeof instances[0] === 'object') {
      for (const instance of instances) {
        this.receiveInstance(instance as TempInstance);
      }
    } else {
      throw new Error('Expected array of instances');
    }
  }

  /**
   * Get an instance by its key.
   *
   * @param key - Instance key in format `_instance_type:id`
   * @returns The instance object
   * @throws Error if instance not found
   */
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

  /**
   * Set the initial anchor IDs received from the server.
   * Creates placeholder (unloaded) instances for each anchor.
   */
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
    } else if (instance._operation === 'delete') {
      return this.deleteInstance(instance._instance_type, instance.id);
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
        const childType = model[property];
        if (Array.isArray(_instance[property])) {
          const ids = _instance[property] as number[];
          const array = ids.map((id, _index) => this.getOrCreate(childType, id, key, property));
          this.attachArrayCreate(array, childType, _instance._instance_type, _instance.id, property);
          newInstance[property] = array;
        } else {
          newInstance[property] = this.getOrCreate(childType, value, key, property);
        }
      } else {
        newInstance[property] = value;
      }
    }

    this.attachInstanceMethods(newInstance as InstanceType);

    if (!this.refs[key]) {
      this.refs[key] = [];
    } else {
      this.changeRef(key);
    }

    return newInstance as unknown as InstanceType;
  }

  private deleteInstance(type: string, id: number) {
    const key = `${type}:${id}`;
    const refs = this.refs[key];

    for (const ref of refs) {
      const instance = this.index[ref.referrerKey] as any;
      const property = ref.property
      if (!Array.isArray(instance[property])) {
        instance[property] = null;
      } else {
        instance[property] = instance[property].filter((obj: any) => obj.id != id);
      }
    }
    delete this.index[key];
    delete this.refs[key];
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
        const array = related.map(
          rel => this.index[`${rel._instance_type}:${rel.id}`]
        );
        const model = this.model[instance._instance_type];
        if (model && model[property]) {
          this.attachArrayCreate(array, model[property], instance._instance_type, instance.id, property);
        }
        instance[property] = array;
      }
      const newInstance = {...instance};
      this.attachInstanceMethods(newInstance);
      this.index[ref.referrerKey] = newInstance;
      this.changeRef(ref.referrerKey, track);
    }
  }

  // Attach .save() and .delete() to an instance if its type is writable
  private attachInstanceMethods(instance: InstanceType): void {
    if (!this.writeCallbacks) return;
    const ops = this.writable[instance._instance_type];
    if (!ops) return;

    const callbacks = this.writeCallbacks;
    const instanceType = instance._instance_type;
    const instanceId = instance.id;

    if (ops.includes('save') && !(instance as any).save) {
      (instance as any).save = (data: Record<string, unknown>) =>
        callbacks.saveInstance(instanceType, instanceId, data);
    }
    if (ops.includes('delete') && !(instance as any).delete) {
      (instance as any).delete = () =>
        callbacks.deleteInstance(instanceType, instanceId);
    }
  }

  // Attach .create() to an array if the child type is writable with 'create'
  private attachArrayCreate(
    array: InstanceType[],
    childType: string,
    parentType: string,
    parentId: number,
    relationName: string,
  ): void {
    if (!this.writeCallbacks) return;
    const ops = this.writable[childType];
    if (!ops || !ops.includes('create')) return;

    const callbacks = this.writeCallbacks;
    (array as any).create = (data: Record<string, unknown> = {}) =>
      callbacks.createInstance(childType, parentType, parentId, relationName, data);
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

  public getRawInstance(key: string): InstanceType | null {
    return this.index[key] || null;
  }

  public applyOptimisticUpdate(instanceType: string, instanceId: number, data: Record<string, unknown>): void {
    const key = `${instanceType}:${instanceId}`;
    const instance = this.index[key];
    if (!instance) return;

    const model = this.model[instanceType];
    for (const [property, value] of Object.entries(data)) {
      if (model && model[property]) {
        const relatedType = model[property];
        if (Array.isArray(value)) {
          const ids = value as number[];
          (instance as any)[property] = ids.map((id, _index) =>
            this.getOrCreate(relatedType, id, key, property)
          );
        } else if (typeof value === 'number') {
          (instance as any)[property] = this.getOrCreate(relatedType, value, key, property);
        }
      } else {
        (instance as any)[property] = value;
      }
    }
    this.index[key] = { ...instance };
    this.changeRef(key);
  }

  public rollbackOptimisticUpdate(instanceType: string, instanceId: number, previousState: InstanceType): void {
    const key = `${instanceType}:${instanceId}`;
    this.index[key] = { ...previousState };
    this.changeRef(key);
  }

  public addTempInstance(
    instanceType: string,
    tempId: number,
    parentType: string,
    parentId: number,
    relationName: string,
    data: Record<string, unknown>,
  ): void {
    const tempKey = `${instanceType}:${tempId}`;
    const parentKey = `${parentType}:${parentId}`;

    const tempInstance: TempInstance = {
      id: tempId,
      _instance_type: instanceType,
      _operation: 'create',
      _tstamp: Date.now() / 1000,
      _loaded: true,
      ...data,
    } as TempInstance;

    this.index[tempKey] = tempInstance as InstanceType;
    this.refs[tempKey] = [];

    const parent = this.index[parentKey];
    if (parent) {
      const relation = (parent as any)[relationName];
      if (Array.isArray(relation)) {
        (parent as any)[relationName] = [tempInstance as InstanceType, ...relation];
        this.refs[tempKey].push({ property: relationName, referrerKey: parentKey });
      }
      this.index[parentKey] = { ...parent };
      this.changeRef(parentKey);
    }
  }

  public removeTempInstance(instanceType: string, tempId: number): void {
    const tempKey = `${instanceType}:${tempId}`;
    const refs = this.refs[tempKey];

    if (refs) {
      for (const ref of refs) {
        const parent = this.index[ref.referrerKey];
        if (parent) {
          const relation = (parent as any)[ref.property];
          if (Array.isArray(relation)) {
            const filtered = relation.filter(
              (obj: InstanceType) => obj.id !== tempId
            );
            const model = this.model[(parent as any)._instance_type];
            if (model && model[ref.property]) {
              this.attachArrayCreate(filtered, model[ref.property], (parent as any)._instance_type, parent.id, ref.property);
            }
            (parent as any)[ref.property] = filtered;
          }
          this.index[ref.referrerKey] = { ...parent };
          this.changeRef(ref.referrerKey);
        }
      }
    }

    delete this.index[tempKey];
    delete this.refs[tempKey];
  }

  public applyOptimisticDelete(instanceType: string, instanceId: number): void {
    const key = `${instanceType}:${instanceId}`;
    const refs = this.refs[key];

    if (refs) {
      for (const ref of refs) {
        const parent = this.index[ref.referrerKey];
        if (parent) {
          const relation = (parent as any)[ref.property];
          if (Array.isArray(relation)) {
            const filtered = relation.filter(
              (obj: InstanceType) => obj.id !== instanceId
            );
            const model = this.model[(parent as any)._instance_type];
            if (model && model[ref.property]) {
              this.attachArrayCreate(filtered, model[ref.property], (parent as any)._instance_type, parent.id, ref.property);
            }
            (parent as any)[ref.property] = filtered;
          } else {
            (parent as any)[ref.property] = null;
          }
          this.index[ref.referrerKey] = { ...parent };
          this.changeRef(ref.referrerKey);
        }
      }
    }

    delete this.index[key];
    delete this.refs[key];
  }

  public getParentInfo(key: string): ParentInfo | null {
    const refs = this.refs[key];
    if (!refs || refs.length === 0) return null;

    const ref = refs[0];
    const parentKey = ref.referrerKey;
    const [parentType, parentIdStr] = parentKey.split(':');
    const parentId = parseInt(parentIdStr, 10);

    return {
      parentType,
      parentId,
      relationName: ref.property,
    };
  }

  public rollbackOptimisticDelete(
    instanceType: string,
    instanceId: number,
    previousState: InstanceType,
    parentType: string,
    parentId: number,
    relationName: string,
  ): void {
    const key = `${instanceType}:${instanceId}`;
    const parentKey = `${parentType}:${parentId}`;

    this.index[key] = { ...previousState };
    this.refs[key] = [];

    const parent = this.index[parentKey];
    if (parent) {
      const relation = (parent as any)[relationName];
      if (Array.isArray(relation)) {
        (parent as any)[relationName] = [...relation, previousState];
      } else {
        (parent as any)[relationName] = previousState;
      }
      this.refs[key].push({ property: relationName, referrerKey: parentKey });
      this.index[parentKey] = { ...parent };
      this.changeRef(parentKey);
    }
  }

}
